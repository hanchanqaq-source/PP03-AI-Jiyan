from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import requests

from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable


NOW = datetime(2025, 7, 2, 12, tzinfo=timezone.utc)
SECRET = "nasdaq-secret-value"


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
    store = MemoryCredentialStore({"nasdaq-data-link": ("NASDAQ_DATA_LINK_API_KEY",)})
    if configured: store.set("nasdaq-data-link", "NASDAQ_DATA_LINK_API_KEY", SECRET)
    return store


def request(database_code="FRED", dataset_code="DFF", **overrides):
    values = {"database_code": database_code, "dataset_code": dataset_code, "limit": 2}; values.update(overrides)
    return ProviderRequest("macro_series", values)


def fixture(*, premium=False, database="FRED", dataset="DFF"):
    return {"dataset": {
        "id": 1, "dataset_code": dataset, "database_code": database, "name": "Federal Funds Rate", "description": "Public description",
        "refreshed_at": "2025-07-01T20:00:00.000Z", "newest_available_date": "2025-07-01", "oldest_available_date": "2025-06-30",
        "column_names": ["Date", "Value"], "frequency": "daily", "type": "Time Series", "premium": premium,
        "data": [["2025-07-01", "4.33"], ["2025-06-30", "4.34"]], "database_name": "Federal Reserve Economic Data",
    }}


def adapter(http=None, *, configured=True, guard=None):
    from data_sources.providers.nasdaq_data_link import NasdaqDataLinkAdapter
    return NasdaqDataLinkAdapter(http=http or FakeHttp(), credentials=credentials(configured), budget_guard=guard, fetched_at=lambda: NOW)


def parse(payload, req=None, *, cached=False):
    return adapter()._parse(payload, req or request(), now=NOW, cached=cached)


def test_nasdaq_no_key_and_query_auth_never_build_url_authorize_or_transport():
    http, guard = FakeHttp(), FakeBudget()
    missing = adapter(http, configured=False, guard=guard)
    assert missing.probe("macro_series", parameters={"database_code": object()})["status"] == "unconfigured"
    with pytest.raises(ProviderUnavailable, match="unconfigured"): missing.fetch(ProviderRequest("macro_series", {"database_code": object()}))
    active = adapter(http, guard=guard)
    assert active.probe("macro_series")["status"] == "unsupported_credential_transport"
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"): active.fetch(request())
    assert guard.calls == [] and http.calls == []


def test_nasdaq_parser_reports_free_or_premium_dataset_observation_without_authorizing_access():
    assert parse(fixture())[0].source_metadata["dataset_access_observation"] == "known_free_dataset"
    assert parse(fixture(premium=True))[0].source_metadata["dataset_access_observation"] == "premium_dataset"


def test_nasdaq_parser_preserves_database_dataset_date_frequency_unknown_unit():
    row = parse(fixture())[0]
    assert row.value == Decimal("4.33") and row.as_of_date.isoformat() == "2025-07-01"
    assert row.unit == "unknown" and row.frequency == "daily"
    assert row.source_metadata == {"database_code": "FRED", "dataset_code": "DFF", "database_name": "Federal Reserve Economic Data", "dataset_name": "Federal Funds Rate", "newest_available_date": "2025-07-01", "dataset_access_observation": "known_free_dataset", "source_reference": "https://data.nasdaq.com/"}
    assert SECRET not in repr(row)


@pytest.mark.parametrize("payload,error_type,code", [
    ({"dataset": {**fixture()["dataset"], "data": []}}, ProviderUnavailable, "empty_result"),
    ({"quandl_error": {"code": "QEAx01", "message": "Invalid API key"}}, ProviderUnavailable, "authentication"),
    ({"quandl_error": {"code": "QEPx04", "message": "Subscription required"}}, ProviderUnavailable, "plan_unavailable"),
    ({"quandl_error": {"code": "QELx04", "message": "Limit exceeded", "retry_after": 8}}, ProviderRateLimited, "rate_limited"),
    ({"dataset": "bad"}, ProviderSchemaChanged, "schema_changed"),
])
def test_nasdaq_parser_distinguishes_empty_auth_plan_rate_schema(payload, error_type, code):
    with pytest.raises(error_type) as captured: parse(payload)
    assert captured.value.code == code
    if code == "rate_limited": assert captured.value.retry_after_seconds == 8


@pytest.mark.parametrize("payload", [
    {"dataset": {**fixture()["dataset"], "newest_available_date": "2099-01-01", "data": [["2099-01-01", "1"]]}},
    {"dataset": {**fixture()["dataset"], "data": [["2025-07-01", float("nan")]]}},
    {"dataset": {**fixture()["dataset"], "data": [["2025-07-01", Decimal("1e999999")]]}},
    {"dataset": {**fixture()["dataset"], "data": [["2025-07-01", {"nested": 1}]]}},
    {"dataset": {**fixture()["dataset"], "data": [["2025-07-01", 1]] * 1001}},
    {"dataset": {**fixture()["dataset"], "column_names": ["Date", "x" * 5000]}},
])
def test_nasdaq_parser_rejects_future_nonfinite_unbounded_nested_long(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"): parse(payload)


def test_nasdaq_parser_marks_cache_and_stale_explicitly():
    assert parse(fixture(), cached=True)[0].source_metadata["cache_status"] == "fallback"
    assert parse(fixture(), request(max_age_days=0))[0].data_status == "stale"


def test_nasdaq_resource_builder_blocks_ssrf_and_prepared_url_has_no_key():
    active = adapter(); database, dataset, limit = active._request(request())
    prepared = requests.Request("GET", f"https://data.nasdaq.com/api/v3/datasets/{database}/{dataset}.json", params={"limit": limit}).prepare()
    assert prepared.url is not None and SECRET not in prepared.url and "api_key" not in prepared.url.lower()
    for bad in ("../FRED", "https://evil.test", "A/B", "x" * 129):
        with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"): active._request(request(bad, "DFF"))


def test_nasdaq_catalog_is_stable_default_disabled_plan_dependent():
    row = build_catalog({"sources": []}).adapter("nasdaq-data-link")
    assert row.source_family_id == "nasdaq_data_link" and row.capability_ids == ("macro_series",)
    assert row.credential_env_names == ("NASDAQ_DATA_LINK_API_KEY",) and row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert "Premium" in row.usage_note and "未知" in row.cost_policy
