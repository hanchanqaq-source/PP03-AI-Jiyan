from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from data_sources.budgets import BudgetDecision
from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable


NOW = datetime(2025, 7, 2, 12, tzinfo=timezone.utc)
SECRET = "twelve-secret-value"


class FakeHttp:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params)); value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


class FakeBudget:
    def __init__(self, decision=None): self.decision, self.calls = decision or BudgetDecision(True, "authorized", None, Decimal("0")), []
    def authorize(self, descriptor, *, estimated_cost, now): self.calls.append((descriptor.adapter_id, estimated_cost, now)); return self.decision


def credentials(configured=True):
    store = MemoryCredentialStore({"twelve-data": ("TWELVE_DATA_API_KEY",)})
    if configured: store.set("twelve-data", "TWELVE_DATA_API_KEY", SECRET)
    return store


def entitlement(**overrides):
    from data_sources.providers.twelve_data import TwelveDataEntitlement
    values = dict(capability_id="stock_history", plan_name="fixture-basic", available=True, estimated_cost=Decimal("0"), quota_remaining=7)
    values.update(overrides); return TwelveDataEntitlement(**values)


def request(**overrides):
    values = {"symbol": "AAPL", "interval": "1day", "outputsize": 2}
    values.update(overrides); return ProviderRequest("stock_history", values)


def fixture():
    return {
        "meta": {"symbol": "AAPL", "interval": "1day", "currency": "USD", "exchange_timezone": "America/New_York"},
        "values": [
            {"datetime": "2025-07-01", "open": "208", "high": "212", "low": "207", "close": "210.5", "volume": "1000"},
            {"datetime": "2025-06-30", "open": "206", "high": "209", "low": "205", "close": "208", "volume": "900"},
        ],
        "status": "ok", "credits": 2,
    }


def adapter(http, *, configured=True, guard=None, entitlements=(), cache_getter=None):
    from data_sources.providers.twelve_data import TwelveDataAdapter
    return TwelveDataAdapter(http=http, credentials=credentials(configured), budget_guard=guard, entitlements=entitlements, cache_getter=cache_getter, fetched_at=lambda: NOW)


def test_twelve_data_credentials_guard_plan_and_quota_all_precede_request_construction():
    http, guard = FakeHttp([]), FakeBudget()
    assert adapter(http, configured=False, guard=guard).probe("stock_history", parameters={"symbol": object()})["status"] == "unconfigured"
    assert guard.calls == [] and http.calls == []
    assert adapter(http, entitlements=(entitlement(),)).probe("stock_history")["status"] == "budget_guard_unavailable"
    assert adapter(http, guard=guard).probe("stock_history")["status"] == "cost_unknown"
    assert adapter(http, guard=guard, entitlements=(entitlement(available=False),)).probe("stock_history")["status"] == "plan_unavailable"
    assert adapter(http, guard=guard, entitlements=(entitlement(quota_remaining=0),)).probe("stock_history")["status"] == "quota_exhausted"
    assert http.calls == []


def test_twelve_data_preserves_symbol_interval_timezone_currency_and_actual_credit_use():
    http = FakeHttp([fixture()])
    rows = adapter(http, guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())
    assert len(rows) == 2
    row = rows[0]
    assert row.value == {"open": Decimal("208"), "high": Decimal("212"), "low": Decimal("207"), "close": Decimal("210.5"), "volume": Decimal("1000")}
    assert row.as_of_date.isoformat() == "2025-07-01"
    assert row.unit == "USD" and row.frequency == "1day"
    assert row.source_metadata == {
        "symbol": "AAPL", "interval": "1day", "timezone": "America/New_York", "credits_used": "2",
        "plan_name": "fixture-basic", "quota_remaining": "7", "source_reference": "https://api.twelvedata.com/",
    }
    assert http.calls[0][0] == "https://api.twelvedata.com/time_series"
    assert http.calls[0][2]["apikey"] == SECRET and SECRET not in repr(rows)


@pytest.mark.parametrize(
    "payload,error_type,code",
    [
        ({"status": "ok", "meta": fixture()["meta"], "values": [], "credits": 0}, ProviderUnavailable, "empty_result"),
        ({"status": "error", "code": 401, "message": "invalid key"}, ProviderUnavailable, "authentication"),
        ({"status": "error", "code": 403, "message": "plan does not include endpoint"}, ProviderUnavailable, "plan_unavailable"),
        ({"status": "error", "code": 429, "message": "rate limit", "retry_after": 11}, ProviderRateLimited, "rate_limited"),
        ({"status": "ok", "values": "bad"}, ProviderSchemaChanged, "schema_changed"),
    ],
)
def test_twelve_data_distinguishes_empty_auth_plan_rate_and_schema(payload, error_type, code):
    with pytest.raises(error_type) as captured:
        adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())
    assert captured.value.code == code
    if code == "rate_limited": assert captured.value.retry_after_seconds == 11


def test_twelve_data_transient_cache_is_explicit_and_does_not_hide_auth_or_plan_errors():
    rows = adapter(
        FakeHttp([ProviderUnavailable("tls", reference="https://api.twelvedata.com/")]),
        guard=FakeBudget(), entitlements=(entitlement(),), cache_getter=lambda _: fixture(),
    ).fetch(request())
    assert rows[0].data_status == "cached" and rows[0].source_metadata["cache_status"] == "fallback"
    with pytest.raises(ProviderUnavailable, match="authentication"):
        adapter(FakeHttp([ProviderUnavailable("authentication", reference="https://api.twelvedata.com/")]), guard=FakeBudget(), entitlements=(entitlement(),), cache_getter=lambda _: fixture()).fetch(request())


@pytest.mark.parametrize(
    "payload",
    [
        {**fixture(), "values": [{**fixture()["values"][0], "datetime": "2099-01-01"}]},
        {**fixture(), "credits": -1},
        {**fixture(), "credits": True},
        {**fixture(), "values": [{**fixture()["values"][0], "close": float("inf")}]},
        {**fixture(), "values": [{**fixture()["values"][0], "close": Decimal("1e999999")}]},
        {**fixture(), "values": [{**fixture()["values"][0], "close": {"nested": 1}}]},
        {**fixture(), "values": fixture()["values"] * 501},
    ],
)
def test_twelve_data_rejects_future_invalid_credit_nonfinite_unbounded_and_nested_payload(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())


def test_twelve_data_unknown_blank_currency_is_explicit_not_fabricated():
    payload = fixture(); payload["meta"] = {**payload["meta"], "currency": ""}
    row = adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())[0]
    assert row.unit == "unknown"


def test_twelve_data_marks_old_observation_stale_by_explicit_max_age():
    row = adapter(FakeHttp([fixture()]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request(max_age_days=0))[0]
    assert row.data_status == "stale"


def test_twelve_data_catalog_is_stable_default_disabled_and_plan_dependent():
    row = build_catalog({"sources": []}).adapter("twelve-data")
    assert row.source_family_id == "twelve_data" and row.capability_ids == ("stock_history",)
    assert row.credential_env_names == ("TWELVE_DATA_API_KEY",)
    assert row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert "实际套餐" in row.quota_policy and "未知" in row.cost_policy


def test_twelve_data_copies_trusted_entitlement_before_caller_mutation():
    trusted = entitlement()
    active = adapter(FakeHttp([fixture()]), guard=FakeBudget(), entitlements=(trusted,))
    object.__setattr__(trusted, "estimated_cost", None)
    assert len(active.fetch(request())) == 2
