from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests

from data_sources.budgets import BudgetGuard, BudgetPolicy
from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.models import BillingModel, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.provider_registry import FreemiumEntitlementResolver
from data_sources.usage_store import UsageStore


NOW = datetime(2025, 7, 2, 12, tzinfo=timezone.utc)
SECRET = "alpha-secret-value"


class FakeHttp:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeBudget:
    def __init__(self):
        self.calls = []

    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now))
        raise AssertionError("authorization must not run without a supported credential transport")


def credentials(configured=True):
    store = MemoryCredentialStore({"alpha-vantage": ("ALPHA_VANTAGE_API_KEY",)})
    if configured:
        store.set("alpha-vantage", "ALPHA_VANTAGE_API_KEY", SECRET)
    return store


def resolver(**overrides):
    record = {
        "adapter_id": "alpha-vantage", "capability_id": "stock_history", "billing_model": "freemium",
        "plan_name": "deterministic-fixture", "available": True, "quota_remaining": 24,
        "estimated_cost": Decimal("0"), "actual_cost": Decimal("0"),
        "observed_at": NOW - timedelta(minutes=5), "expires_at": NOW + timedelta(hours=1),
        "provenance": "deterministic_test_fixture",
    }
    record.update(overrides)
    return FreemiumEntitlementResolver.from_test_records((record,))


def request(**overrides):
    parameters = {"symbol": "IBM", "function": "TIME_SERIES_DAILY", "interval": "daily"}
    parameters.update(overrides)
    return ProviderRequest("stock_history", parameters)


def fixture():
    return {
        "Meta Data": {"1. Information": "Daily Prices", "2. Symbol": "IBM", "3. Last Refreshed": "2025-07-01", "5. Time Zone": "US/Eastern"},
        "Time Series (Daily)": {"2025-07-01": {"4. close": "123.45", "5. volume": "1000"}},
    }


def adapter(http=None, *, configured=True, entitlement_resolver=None, guard=None):
    from data_sources.providers.alpha_vantage import AlphaVantageAdapter
    return AlphaVantageAdapter(
        http=http or FakeHttp(), credentials=credentials(configured), budget_guard=guard,
        entitlement_resolver=entitlement_resolver, fetched_at=lambda: NOW,
    )


def parse(payload, req=None, *, cached=False, entitlement_resolver=None):
    active_resolver = entitlement_resolver or resolver()
    active = adapter(entitlement_resolver=active_resolver)
    reason, snapshot = active._trusted_entitlement("stock_history", NOW)
    assert reason is None and snapshot is not None
    return active._parse(payload, req or request(), now=NOW, entitlement=snapshot, cached=cached)


def test_alpha_vantage_no_key_short_circuits_before_request_budget_and_transport():
    http, guard = FakeHttp(), FakeBudget()
    active = adapter(http, configured=False, entitlement_resolver=resolver(), guard=guard)
    hostile = ProviderRequest("stock_history", {"symbol": object()})
    assert active.probe("stock_history", parameters=hostile.parameters) == {"status": "unconfigured", "connected": False, "health_failure": False}
    with pytest.raises(ProviderUnavailable, match="unconfigured"):
        active.fetch(hostile)
    assert guard.calls == [] and http.calls == []


def test_alpha_vantage_configured_query_only_auth_is_explicitly_unsupported():
    http, guard = FakeHttp(), FakeBudget()
    active = adapter(http, entitlement_resolver=resolver(), guard=guard)
    assert active.probe("stock_history")["status"] == "unsupported_credential_transport"
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"):
        active.fetch(request())
    assert guard.calls == [] and http.calls == []


def test_alpha_vantage_pure_parser_preserves_low_frequency_fields_without_secret():
    row = parse(fixture())[0]
    assert row.value == {"close": Decimal("123.45"), "volume": Decimal("1000")}
    assert row.as_of_date.isoformat() == "2025-07-01"
    assert row.unit == "unknown" and row.frequency == "daily"
    assert row.source_metadata == {
        "function": "TIME_SERIES_DAILY", "interval": "daily", "symbol": "IBM", "timezone": "US/Eastern",
        "plan_name": "deterministic-fixture", "quota_remaining": "24", "source_reference": "https://www.alphavantage.co/",
    }
    assert SECRET not in repr(row)


@pytest.mark.parametrize("payload,error_type,code", [
    ({}, ProviderSchemaChanged, "schema_changed"),
    ({"Meta Data": fixture()["Meta Data"], "Time Series (Daily)": {}}, ProviderUnavailable, "empty_result"),
    ({"Note": "rate limit reached"}, ProviderRateLimited, "rate_limited"),
    ({"Error Message": "Invalid API call"}, ProviderUnavailable, "authentication"),
])
def test_alpha_vantage_parser_distinguishes_schema_empty_rate_and_auth(payload, error_type, code):
    with pytest.raises(error_type) as captured:
        parse(payload)
    assert captured.value.code == code and SECRET not in str(captured.value)


@pytest.mark.parametrize("payload", [
    {**fixture(), "Time Series (Daily)": {"2099-01-01": {"4. close": "1", "5. volume": "1"}}},
    {**fixture(), "Time Series (Daily)": {"2025-07-01": {"4. close": float("nan"), "5. volume": "1"}}},
    {**fixture(), "Time Series (Daily)": {"2025-07-01": {"4. close": Decimal("1e999999"), "5. volume": "1"}}},
    {**fixture(), "Time Series (Daily)": {"2025-07-01": {"4. close": "1", "5. volume": 1 << 20000}}},
    {**fixture(), "Time Series (Daily)": {"2025-07-01": {"4. close": {"nested": 1}, "5. volume": "1"}}},
])
def test_alpha_vantage_parser_rejects_future_unbounded_nonfinite_and_nested_values(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        parse(payload)


def test_alpha_vantage_parser_marks_cached_and_stale_observations_explicitly():
    assert parse(fixture(), cached=True)[0].data_status == "cached"
    assert parse(fixture(), request(max_age_days=0))[0].data_status == "stale"


def test_alpha_vantage_request_builder_is_bounded_and_secret_free():
    params = adapter()._request(request(outputsize="compact"))
    prepared = requests.Request("GET", "https://www.alphavantage.co/query", params=params).prepare()
    assert prepared.url is not None and SECRET not in prepared.url and "apikey" not in prepared.url.lower()
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        adapter()._request(request(symbol=object()))


def test_freemium_resolver_rejects_caller_authority_stale_mismatch_and_mutation():
    active_resolver = resolver()
    active = adapter(entitlement_resolver=active_resolver)
    reason, snapshot = active._trusted_entitlement("stock_history", NOW)
    assert reason is None and snapshot is not None
    assert not active_resolver.validate_snapshot({"plan_name": "lookalike"}, "alpha-vantage", "stock_history", BillingModel.FREEMIUM, now=NOW)
    assert not active_resolver.validate_snapshot(snapshot, "alpha-vantage", "wrong", BillingModel.FREEMIUM, now=NOW)
    object.__setattr__(snapshot, "available", False)
    with pytest.raises(ProviderUnavailable, match="entitlement_invalid"):
        active._parse(fixture(), request(), now=NOW, entitlement=snapshot, cached=False)
    stale = resolver(expires_at=NOW - timedelta(seconds=1), observed_at=NOW - timedelta(hours=1))
    assert stale.resolve("alpha-vantage", "stock_history", BillingModel.FREEMIUM, now=NOW)[0] == "entitlement_stale"


@pytest.mark.parametrize(("change", "reason"), [
    ({"available": False}, "plan_unavailable"),
    ({"quota_remaining": 0}, "quota_exhausted"),
    ({"estimated_cost": None, "actual_cost": None}, "cost_unknown"),
])
def test_freemium_resolver_fails_closed_on_plan_quota_and_unknown_cost(change, reason):
    assert resolver(**change).resolve("alpha-vantage", "stock_history", BillingModel.FREEMIUM, now=NOW) == (reason, None)


@pytest.mark.parametrize("record_change", [
    {"estimated_cost": Decimal("0.01"), "actual_cost": None},
    {"billing_model": "paid"},
    {"provenance": "caller_claim"},
    {"expires_at": NOW + timedelta(days=32)},
])
def test_freemium_resolver_rejects_untrusted_or_incomplete_entitlement_records(record_change):
    with pytest.raises(ValueError):
        resolver(**record_change)


def test_test_fixture_provenance_cannot_be_constructed_as_server_evidence():
    with pytest.raises(ValueError, match="provenance"):
        FreemiumEntitlementResolver.from_server_records(({
            "adapter_id": "alpha-vantage", "capability_id": "stock_history", "billing_model": "freemium",
            "plan_name": "deterministic-fixture", "available": True, "quota_remaining": 24,
            "estimated_cost": Decimal("0"), "actual_cost": Decimal("0"),
            "observed_at": NOW - timedelta(minutes=5), "expires_at": NOW + timedelta(hours=1),
            "provenance": "deterministic_test_fixture",
        },))


def test_resolver_canonical_record_map_is_immutable_after_validation():
    active = resolver()
    with pytest.raises(TypeError):
        active._records[("alpha-vantage", "stock_history")] = ()


def test_alpha_vantage_catalog_is_stable_disabled_low_frequency_fallback():
    row = build_catalog({"sources": []}).adapter("alpha-vantage")
    assert row.source_family_id == "alpha_vantage"
    assert row.source_roles == (SourceRole.FALLBACK_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK)
    assert row.capability_ids == ("stock_history",)
    assert row.credential_env_names == ("ALPHA_VANTAGE_API_KEY",)
    assert row.billing_model.value == "freemium" and row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert "未知" in row.cost_policy


def test_default_registry_with_configured_fake_alpha_key_makes_zero_network_calls():
    from data_sources.provider_registry import ProviderRegistry
    http = FakeHttp()
    registry = ProviderRegistry(build_catalog({"sources": []}), http_factory=lambda: http, credential_factory=lambda _scope: credentials(True))
    assert registry.adapter("alpha-vantage").probe("stock_history")["status"] == "unsupported_credential_transport"
    assert http.calls == []


def test_registry_composition_is_the_only_adapter_resolver_injection_boundary():
    from data_sources.provider_registry import ProviderRegistry
    active_resolver = resolver()
    registry = ProviderRegistry(
        build_catalog({"sources": []}), http_factory=FakeHttp,
        credential_factory=lambda _scope: credentials(True), entitlement_resolver=active_resolver,
    )
    active = registry.adapter("alpha-vantage")
    reason, snapshot = active._trusted_entitlement("stock_history", NOW)
    assert reason is None and snapshot is not None
    assert active_resolver.validate_snapshot(snapshot, "alpha-vantage", "stock_history", BillingModel.FREEMIUM, now=NOW)


def test_alpha_vantage_query_only_credential_is_unsupported_before_budget_or_transport(tmp_path):
    descriptor = build_catalog({"sources": []}).adapter("alpha-vantage")
    usage = UsageStore(tmp_path / "alpha-usage")
    guard = BudgetGuard(usage, {"alpha-vantage": BudgetPolicy("alpha-vantage", True, True, False, Decimal("1"), Decimal("5"), Decimal("1"))}, trusted_adapters={"alpha-vantage": descriptor})
    http = FakeHttp()
    active = adapter(http, entitlement_resolver=resolver(estimated_cost=Decimal("0.01"), actual_cost=Decimal("0.01")), guard=guard)
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"):
        active.fetch(request())
    assert http.calls == [] and usage.records(now=NOW) == ()
