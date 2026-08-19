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
SECRET = "nasdaq-secret-value"


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
    store = MemoryCredentialStore({"nasdaq-data-link": ("NASDAQ_DATA_LINK_API_KEY",)})
    if configured: store.set("nasdaq-data-link", "NASDAQ_DATA_LINK_API_KEY", SECRET)
    return store


def entitlement(**overrides):
    from data_sources.providers.nasdaq_data_link import NasdaqDataLinkEntitlement
    values = dict(capability_id="macro_series", plan_name="fixture-plan", available=True, estimated_cost=Decimal("0"), quota_remaining=5, premium_access=False)
    values.update(overrides); return NasdaqDataLinkEntitlement(**values)


def request(database_code="FRED", dataset_code="DFF"):
    return ProviderRequest("macro_series", {"database_code": database_code, "dataset_code": dataset_code, "limit": 2})


def fixture(*, premium=False, database="FRED", dataset="DFF"):
    return {"dataset": {
        "id": 1, "dataset_code": dataset, "database_code": database, "name": "Federal Funds Rate",
        "description": "Public description", "refreshed_at": "2025-07-01T20:00:00.000Z",
        "newest_available_date": "2025-07-01", "oldest_available_date": "2025-06-30",
        "column_names": ["Date", "Value"], "frequency": "daily", "type": "Time Series",
        "premium": premium, "data": [["2025-07-01", "4.33"], ["2025-06-30", "4.34"]],
        "database_name": "Federal Reserve Economic Data",
    }}


def adapter(http, *, configured=True, guard=None, entitlements=(), cache_getter=None):
    from data_sources.providers.nasdaq_data_link import NasdaqDataLinkAdapter
    return NasdaqDataLinkAdapter(http=http, credentials=credentials(configured), budget_guard=guard, entitlements=entitlements, cache_getter=cache_getter, fetched_at=lambda: NOW)


def test_nasdaq_data_link_no_key_guard_or_entitlement_never_builds_dataset_url():
    http, guard = FakeHttp([]), FakeBudget()
    assert adapter(http, configured=False, guard=guard).probe("macro_series", parameters={"database_code": object()})["status"] == "unconfigured"
    assert guard.calls == [] and http.calls == []
    assert adapter(http, entitlements=(entitlement(),)).probe("macro_series")["status"] == "budget_guard_unavailable"
    assert adapter(http, guard=guard).probe("macro_series")["status"] == "cost_unknown"
    assert http.calls == []


def test_nasdaq_data_link_distinguishes_known_free_dataset_from_premium_entitlement():
    free = adapter(FakeHttp([fixture()]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())
    assert free[0].source_metadata["entitlement"] == "known_free_dataset"

    http = FakeHttp([])
    result = adapter(http, guard=FakeBudget(), entitlements=(entitlement(premium_access=False),)).probe(
        "macro_series", parameters=request("PREMIUM", "SECRET_SET").parameters
    )
    assert result["status"] == "plan_unavailable" and result["health_failure"] is False
    assert http.calls == []

    premium_entitlement = entitlement(premium_access=True, estimated_cost=Decimal("0.02"))
    free_only = FakeBudget(BudgetDecision(False, "free_only", None, Decimal("0.02")))
    result = adapter(http, guard=free_only, entitlements=(premium_entitlement,)).probe(
        "macro_series", parameters=request("PREMIUM", "SECRET_SET").parameters
    )
    assert result["status"] == "free_only" and http.calls == []


def test_nasdaq_data_link_preserves_database_dataset_source_date_frequency_and_unknown_unit():
    http = FakeHttp([fixture()])
    rows = adapter(http, guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())
    row = rows[0]
    assert row.value == Decimal("4.33") and row.as_of_date.isoformat() == "2025-07-01"
    assert row.unit == "unknown" and row.frequency == "daily"
    assert row.source_metadata == {
        "database_code": "FRED", "dataset_code": "DFF", "database_name": "Federal Reserve Economic Data",
        "dataset_name": "Federal Funds Rate", "newest_available_date": "2025-07-01", "entitlement": "known_free_dataset",
        "plan_name": "fixture-plan", "quota_remaining": "5", "source_reference": "https://data.nasdaq.com/",
    }
    assert http.calls[0][0] == "https://data.nasdaq.com/api/v3/datasets/FRED/DFF.json"
    assert http.calls[0][2]["api_key"] == SECRET and SECRET not in repr(rows)


@pytest.mark.parametrize(
    "payload,error_type,code",
    [
        ({"dataset": {**fixture()["dataset"], "data": []}}, ProviderUnavailable, "empty_result"),
        ({"quandl_error": {"code": "QEAx01", "message": "Invalid API key"}}, ProviderUnavailable, "authentication"),
        ({"quandl_error": {"code": "QEPx04", "message": "Subscription required"}}, ProviderUnavailable, "plan_unavailable"),
        ({"quandl_error": {"code": "QELx04", "message": "Limit exceeded", "retry_after": 8}}, ProviderRateLimited, "rate_limited"),
        ({"dataset": "bad"}, ProviderSchemaChanged, "schema_changed"),
    ],
)
def test_nasdaq_data_link_distinguishes_empty_auth_plan_rate_and_schema(payload, error_type, code):
    with pytest.raises(error_type) as captured:
        adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())
    assert captured.value.code == code
    if code == "rate_limited": assert captured.value.retry_after_seconds == 8


def test_nasdaq_data_link_cache_is_explicit_and_only_for_transient_failure():
    rows = adapter(FakeHttp([ProviderUnavailable("server_error", reference="https://data.nasdaq.com/")]), guard=FakeBudget(), entitlements=(entitlement(),), cache_getter=lambda _: fixture()).fetch(request())
    assert rows[0].data_status == "cached" and rows[0].source_metadata["cache_status"] == "fallback"


def test_nasdaq_data_link_marks_old_observation_stale_by_explicit_max_age():
    active_request = ProviderRequest("macro_series", {"database_code": "FRED", "dataset_code": "DFF", "limit": 2, "max_age_days": 0})
    row = adapter(FakeHttp([fixture()]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(active_request)[0]
    assert row.data_status == "stale"


@pytest.mark.parametrize(
    "payload",
    [
        {"dataset": {**fixture()["dataset"], "newest_available_date": "2099-01-01", "data": [["2099-01-01", "1"]]}},
        {"dataset": {**fixture()["dataset"], "data": [["2025-07-01", float("nan")]]}},
        {"dataset": {**fixture()["dataset"], "data": [["2025-07-01", Decimal("1e999999")]]}},
        {"dataset": {**fixture()["dataset"], "data": [["2025-07-01", {"nested": 1}]]}},
        {"dataset": {**fixture()["dataset"], "data": [["2025-07-01", 1]] * 1001}},
        {"dataset": {**fixture()["dataset"], "column_names": ["Date", "x" * 5000]}},
    ],
)
def test_nasdaq_data_link_rejects_future_nonfinite_unbounded_nested_and_long_payload(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())


def test_nasdaq_data_link_rejects_resource_paths_before_transport():
    http = FakeHttp([])
    for bad in ("../FRED", "https://evil.test", "A/B", "x" * 129):
        with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
            adapter(http, guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request(bad, "DFF"))
    assert http.calls == []


def test_nasdaq_data_link_catalog_is_stable_default_disabled_and_plan_dependent():
    row = build_catalog({"sources": []}).adapter("nasdaq-data-link")
    assert row.source_family_id == "nasdaq_data_link" and row.capability_ids == ("macro_series",)
    assert row.credential_env_names == ("NASDAQ_DATA_LINK_API_KEY",)
    assert row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert "Premium" in row.usage_note and "未知" in row.cost_policy


def test_nasdaq_data_link_copies_trusted_entitlement_before_caller_mutation():
    trusted = entitlement()
    active = adapter(FakeHttp([fixture()]), guard=FakeBudget(), entitlements=(trusted,))
    object.__setattr__(trusted, "available", False)
    assert len(active.fetch(request())) == 2


def test_nasdaq_data_link_does_not_label_upstream_premium_dataset_as_known_free():
    http = FakeHttp([fixture(premium=True)])
    with pytest.raises(ProviderUnavailable, match="plan_unavailable"):
        adapter(http, guard=FakeBudget(), entitlements=(entitlement(premium_access=False),)).fetch(request())
