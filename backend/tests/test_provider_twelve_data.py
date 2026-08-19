from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests

from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.provider_registry import FreemiumEntitlementResolver


NOW = datetime(2025, 7, 2, 12, tzinfo=timezone.utc)
SECRET = "twelve-secret-value"


class FakeHttp:
    def __init__(self, responses=()): self.responses, self.calls = list(responses), []
    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params)); value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


class FakeBudget:
    def __init__(self): self.calls = []
    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now))
        raise AssertionError("authorization must not run without a supported credential transport")


def credentials(configured=True):
    store = MemoryCredentialStore({"twelve-data": ("TWELVE_DATA_API_KEY",)})
    if configured: store.set("twelve-data", "TWELVE_DATA_API_KEY", SECRET)
    return store


def resolver(**overrides):
    record = {
        "adapter_id": "twelve-data", "capability_id": "stock_history", "billing_model": "freemium",
        "plan_name": "fixture-basic", "available": True, "quota_remaining": 7,
        "estimated_cost": Decimal("0"), "actual_cost": Decimal("0"),
        "observed_at": NOW - timedelta(minutes=5), "expires_at": NOW + timedelta(hours=1), "provenance": "deterministic_test_fixture",
    }
    record.update(overrides)
    return FreemiumEntitlementResolver.from_test_records((record,))


def request(**overrides):
    values = {"symbol": "AAPL", "interval": "1day", "outputsize": 2}; values.update(overrides)
    return ProviderRequest("stock_history", values)


def fixture():
    return {
        "meta": {"symbol": "AAPL", "interval": "1day", "currency": "USD", "exchange_timezone": "America/New_York"},
        "values": [
            {"datetime": "2025-07-01", "open": "208", "high": "212", "low": "207", "close": "210.5", "volume": "1000"},
            {"datetime": "2025-06-30", "open": "206", "high": "209", "low": "205", "close": "208", "volume": "900"},
        ],
        "status": "ok", "credits": 2,
    }


def adapter(http=None, *, configured=True, entitlement_resolver=None, guard=None):
    from data_sources.providers.twelve_data import TwelveDataAdapter
    return TwelveDataAdapter(http=http or FakeHttp(), credentials=credentials(configured), budget_guard=guard, entitlement_resolver=entitlement_resolver, fetched_at=lambda: NOW)


def parse(payload, req=None, *, cached=False):
    active_resolver = resolver(); active = adapter(entitlement_resolver=active_resolver)
    reason, snapshot = active._trusted_entitlement("stock_history", NOW)
    assert reason is None and snapshot is not None
    return active._parse(payload, req or request(), now=NOW, entitlement=snapshot, cached=cached)


def test_twelve_data_credentials_and_query_auth_precede_request_budget_transport():
    http, guard = FakeHttp(), FakeBudget()
    missing = adapter(http, configured=False, entitlement_resolver=resolver(), guard=guard)
    assert missing.probe("stock_history", parameters={"symbol": object()})["status"] == "unconfigured"
    with pytest.raises(ProviderUnavailable, match="unconfigured"): missing.fetch(ProviderRequest("stock_history", {"symbol": object()}))
    active = adapter(http, entitlement_resolver=resolver(), guard=guard)
    assert active.probe("stock_history")["status"] == "unsupported_credential_transport"
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"): active.fetch(request())
    assert guard.calls == [] and http.calls == []


def test_twelve_data_parser_preserves_symbol_interval_timezone_currency_and_credits():
    rows = parse(fixture()); row = rows[0]
    assert len(rows) == 2
    assert row.value == {"open": Decimal("208"), "high": Decimal("212"), "low": Decimal("207"), "close": Decimal("210.5"), "volume": Decimal("1000")}
    assert row.as_of_date.isoformat() == "2025-07-01" and row.unit == "USD" and row.frequency == "1day"
    assert row.source_metadata == {"symbol": "AAPL", "interval": "1day", "timezone": "America/New_York", "credits_used": "2", "plan_name": "fixture-basic", "quota_remaining": "7", "source_reference": "https://api.twelvedata.com/"}
    assert SECRET not in repr(rows)


@pytest.mark.parametrize("payload,error_type,code", [
    ({"status": "ok", "meta": fixture()["meta"], "values": [], "credits": 0}, ProviderUnavailable, "empty_result"),
    ({"status": "error", "code": 401, "message": "invalid key"}, ProviderUnavailable, "authentication"),
    ({"status": "error", "code": 403, "message": "plan does not include endpoint"}, ProviderUnavailable, "plan_unavailable"),
    ({"status": "error", "code": 429, "message": "rate limit", "retry_after": 11}, ProviderRateLimited, "rate_limited"),
    ({"status": "ok", "values": "bad"}, ProviderSchemaChanged, "schema_changed"),
])
def test_twelve_data_parser_distinguishes_empty_auth_plan_rate_and_schema(payload, error_type, code):
    with pytest.raises(error_type) as captured: parse(payload)
    assert captured.value.code == code
    if code == "rate_limited": assert captured.value.retry_after_seconds == 11


@pytest.mark.parametrize("payload", [
    {**fixture(), "values": [{**fixture()["values"][0], "datetime": "2099-01-01"}]},
    {**fixture(), "credits": -1},
    {**fixture(), "credits": True},
    {**fixture(), "values": [{**fixture()["values"][0], "close": float("inf")}]},
    {**fixture(), "values": [{**fixture()["values"][0], "close": Decimal("1e999999")}]},
    {**fixture(), "values": [{**fixture()["values"][0], "close": {"nested": 1}}]},
    {**fixture(), "values": fixture()["values"] * 501},
])
def test_twelve_data_parser_rejects_future_invalid_credit_nonfinite_unbounded_nested(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"): parse(payload)


def test_twelve_data_parser_marks_cached_stale_and_unknown_currency_explicitly():
    assert parse(fixture(), cached=True)[0].source_metadata["cache_status"] == "fallback"
    assert parse(fixture(), request(max_age_days=0))[0].data_status == "stale"
    payload = fixture(); payload["meta"] = {**payload["meta"], "currency": ""}
    assert parse(payload)[0].unit == "unknown"


def test_twelve_data_request_shape_and_prepared_url_are_secret_free():
    active = adapter(); params = active._request(request())
    prepared = requests.Request("GET", "https://api.twelvedata.com/time_series", params=params).prepare()
    assert prepared.url is not None and SECRET not in prepared.url and "apikey" not in prepared.url.lower()
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"): active._request(request(interval="bad"))


def test_twelve_data_catalog_is_stable_default_disabled_plan_dependent():
    row = build_catalog({"sources": []}).adapter("twelve-data")
    assert row.source_family_id == "twelve_data" and row.capability_ids == ("stock_history",)
    assert row.credential_env_names == ("TWELVE_DATA_API_KEY",) and row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert "实际套餐" in row.quota_policy and "未知" in row.cost_policy
