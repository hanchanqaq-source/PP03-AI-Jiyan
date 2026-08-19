from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from data_sources.budgets import BudgetDecision
from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.models import SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable


NOW = datetime(2025, 7, 2, 12, tzinfo=timezone.utc)
SECRET = "alpha-secret-value"


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeBudget:
    def __init__(self, decision=None):
        self.decision = decision or BudgetDecision(True, "authorized", None, Decimal("0"))
        self.calls = []

    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now))
        return self.decision


def credentials(configured=True):
    store = MemoryCredentialStore({"alpha-vantage": ("ALPHA_VANTAGE_API_KEY",)})
    if configured:
        store.set("alpha-vantage", "ALPHA_VANTAGE_API_KEY", SECRET)
    return store


def entitlement(**overrides):
    from data_sources.providers.alpha_vantage import AlphaVantageEntitlement

    values = {
        "capability_id": "stock_history",
        "plan_name": "deterministic-fixture",
        "available": True,
        "estimated_cost": Decimal("0"),
        "quota_remaining": 24,
    }
    values.update(overrides)
    return AlphaVantageEntitlement(**values)


def request(**overrides):
    parameters = {"symbol": "IBM", "function": "TIME_SERIES_DAILY", "interval": "daily"}
    parameters.update(overrides)
    return ProviderRequest("stock_history", parameters)


def fixture():
    return {
        "Meta Data": {
            "1. Information": "Daily Prices",
            "2. Symbol": "IBM",
            "3. Last Refreshed": "2025-07-01",
            "5. Time Zone": "US/Eastern",
        },
        "Time Series (Daily)": {
            "2025-07-01": {"4. close": "123.45", "5. volume": "1000"},
        },
    }


def adapter(http, *, configured=True, guard=None, entitlements=None, cache_getter=None):
    from data_sources.providers.alpha_vantage import AlphaVantageAdapter

    return AlphaVantageAdapter(
        http=http,
        credentials=credentials(configured),
        budget_guard=guard,
        entitlements=() if entitlements is None else entitlements,
        cache_getter=cache_getter,
        fetched_at=lambda: NOW,
    )


def test_alpha_vantage_no_key_and_missing_authorization_short_circuit_before_request_building():
    http, guard = FakeHttp([]), FakeBudget()
    hostile = ProviderRequest("stock_history", {"symbol": object()})
    assert adapter(http, configured=False, guard=guard).probe("stock_history", parameters=hostile.parameters) == {
        "status": "unconfigured", "connected": False, "health_failure": False
    }
    assert guard.calls == [] and http.calls == []

    assert adapter(http, entitlements=(entitlement(),)).probe("stock_history", parameters=request().parameters)["status"] == "budget_guard_unavailable"
    assert http.calls == []

    assert adapter(http, guard=guard).probe("stock_history", parameters=request().parameters)["status"] == "cost_unknown"
    assert guard.calls == [] and http.calls == []


def test_alpha_vantage_trusted_entitlement_still_obeys_plan_quota_and_budget():
    http = FakeHttp([])
    denied = adapter(http, guard=FakeBudget(), entitlements=(entitlement(available=False),))
    assert denied.probe("stock_history")["status"] == "plan_unavailable"
    exhausted = adapter(http, guard=FakeBudget(), entitlements=(entitlement(quota_remaining=0),))
    assert exhausted.probe("stock_history")["status"] == "quota_exhausted"
    free_only = FakeBudget(BudgetDecision(False, "free_only", None, Decimal("0.01")))
    paid = entitlement(estimated_cost=Decimal("0.01"))
    assert adapter(http, guard=free_only, entitlements=(paid,)).probe("stock_history")["status"] == "free_only"
    assert http.calls == []


def test_alpha_vantage_parses_low_frequency_series_without_inventing_unit_or_leaking_secret():
    http, guard = FakeHttp([fixture()]), FakeBudget()
    rows = adapter(http, guard=guard, entitlements=(entitlement(),)).fetch(request())
    row = rows[0]
    assert row.value == {"close": Decimal("123.45"), "volume": Decimal("1000")}
    assert row.as_of_date.isoformat() == "2025-07-01"
    assert row.unit == "unknown" and row.frequency == "daily"
    assert row.source_metadata == {
        "function": "TIME_SERIES_DAILY",
        "interval": "daily",
        "symbol": "IBM",
        "timezone": "US/Eastern",
        "plan_name": "deterministic-fixture",
        "quota_remaining": "24",
        "source_reference": "https://www.alphavantage.co/",
    }
    assert http.calls[0][0] == "https://www.alphavantage.co/query"
    assert http.calls[0][2]["apikey"] == SECRET
    assert SECRET not in repr(rows) and SECRET not in repr(row.source_metadata)


@pytest.mark.parametrize(
    "payload,error_type,code",
    [
        ({}, ProviderSchemaChanged, "schema_changed"),
        ({"Meta Data": fixture()["Meta Data"], "Time Series (Daily)": {}}, ProviderUnavailable, "empty_result"),
        ({"Note": "rate limit reached"}, ProviderRateLimited, "rate_limited"),
        ({"Error Message": "Invalid API call"}, ProviderUnavailable, "authentication"),
    ],
)
def test_alpha_vantage_distinguishes_schema_empty_rate_and_auth(payload, error_type, code):
    with pytest.raises(error_type) as captured:
        adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())
    assert captured.value.code == code and SECRET not in str(captured.value)


@pytest.mark.parametrize("failure", ["timeout", "tls"])
def test_alpha_vantage_uses_explicit_cached_fallback_for_transient_transport_only(failure):
    rows = adapter(
        FakeHttp([ProviderUnavailable(failure, reference="https://www.alphavantage.co/")]),
        guard=FakeBudget(), entitlements=(entitlement(),), cache_getter=lambda _request: fixture(),
    ).fetch(request())
    assert rows[0].data_status == "cached" and rows[0].source_metadata["cache_status"] == "fallback"


@pytest.mark.parametrize(
    "payload",
    [
        {**fixture(), "Time Series (Daily)": {"2099-01-01": {"4. close": "1", "5. volume": "1"}}},
        {**fixture(), "Time Series (Daily)": {"2025-07-01": {"4. close": float("nan"), "5. volume": "1"}}},
        {**fixture(), "Time Series (Daily)": {"2025-07-01": {"4. close": Decimal("1e999999"), "5. volume": "1"}}},
        {**fixture(), "Time Series (Daily)": {"2025-07-01": {"4. close": "1", "5. volume": 1 << 20000}}},
        {**fixture(), "Time Series (Daily)": {"2025-07-01": {"4. close": {"nested": 1}, "5. volume": "1"}}},
    ],
)
def test_alpha_vantage_rejects_future_unbounded_nonfinite_and_nested_values(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())


def test_alpha_vantage_marks_observation_stale_by_explicit_max_age():
    row = adapter(FakeHttp([fixture()]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request(max_age_days=0))[0]
    assert row.data_status == "stale"


def test_alpha_vantage_catalog_is_stable_disabled_low_frequency_fallback():
    row = build_catalog({"sources": []}).adapter("alpha-vantage")
    assert row.source_family_id == "alpha_vantage"
    assert row.source_roles == (SourceRole.FALLBACK_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK)
    assert row.capability_ids == ("stock_history",)
    assert row.credential_env_names == ("ALPHA_VANTAGE_API_KEY",)
    assert row.billing_model.value == "freemium"
    assert row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert "未知" in row.cost_policy


def test_alpha_vantage_copies_trusted_entitlement_before_caller_mutation():
    trusted = entitlement()
    active = adapter(FakeHttp([fixture()]), guard=FakeBudget(), entitlements=(trusted,))
    object.__setattr__(trusted, "available", False)
    assert len(active.fetch(request())) == 1


def test_alpha_vantage_cost_bearing_authorization_requires_real_reservation_before_transport():
    paid = entitlement(estimated_cost=Decimal("0.01"))
    guard = FakeBudget(BudgetDecision(True, "authorized", None, Decimal("0.01")))
    http = FakeHttp([fixture()])
    with pytest.raises(ProviderUnavailable, match="budget_status_invalid"):
        adapter(http, guard=guard, entitlements=(paid,)).fetch(request())
    assert http.calls == []


def test_default_registry_with_configured_fake_alpha_key_still_makes_zero_network_calls():
    from data_sources.provider_registry import ProviderRegistry

    http = FakeHttp([])
    registry = ProviderRegistry(
        build_catalog({"sources": []}), http_factory=lambda: http,
        credential_factory=lambda _scope: credentials(True),
    )
    assert registry.adapter("alpha-vantage").probe("stock_history")["status"] == "budget_guard_unavailable"
    assert http.calls == []
