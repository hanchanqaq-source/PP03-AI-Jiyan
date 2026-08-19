from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from data_sources.credentials import MemoryCredentialStore
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable


NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)
SECRET = "tushare-test-credential"


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post_json(self, url, *, headers=None, json_body=None):
        self.calls.append((url, headers, {key: "[credential]" if key == "token" else value for key, value in (json_body or {}).items()}))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeBudget:
    def __init__(self, allowed=True, reason="authorized"):
        self.allowed, self.reason, self.calls = allowed, reason, []

    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now))
        return type("Decision", (), {"allowed": self.allowed, "reason": self.reason})()


def credentials(configured=True):
    store = MemoryCredentialStore({"tushare": ("TUSHARE_TOKEN",)})
    if configured:
        store.set("tushare", "TUSHARE_TOKEN", SECRET)
    return store


def request(capability="fund_holdings", **overrides):
    parameters = {"ts_code": "510300.SH", "period": "20250630"}
    parameters.update(overrides)
    return ProviderRequest(capability, parameters)


def fixture():
    return {
        "request_id": "safe-id",
        "code": 0,
        "msg": "",
        "data": {
            "fields": ["ts_code", "symbol", "end_date", "mkv", "stk_mkv_ratio"],
            "items": [["510300.SH", "600000.SH", "20250630", 123.4, 1.25]],
        },
    }


def permission_fixture():
    return {"request_id": "safe-id", "code": -2001, "msg": "抱歉，您没有访问该接口的权限"}


def test_tushare_no_key_short_circuits_budget_and_transport():
    from data_sources.providers.tushare import TushareAdapter

    http, budget = FakeHttp([]), FakeBudget()
    result = TushareAdapter(http=http, credentials=credentials(False), budget_guard=budget).probe("fund_holdings")
    assert result == {"status": "unconfigured", "connected": False, "health_failure": False}
    assert budget.calls == [] and http.calls == []

    free_only = FakeBudget(False, "free_only")
    assert TushareAdapter(http=http, credentials=credentials(), budget_guard=free_only, fetched_at=lambda: NOW).probe("fund_holdings") == {"status": "free_only", "connected": False, "health_failure": False, "capability_id": "fund_holdings"}
    assert http.calls == []


def test_tushare_configured_state_refuses_get_only_safe_client_without_network():
    from data_sources.providers.tushare import TushareAdapter

    class GetOnlySafeClient:
        def __init__(self):
            self.calls = []

        def get_json(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    http = GetOnlySafeClient()
    result = TushareAdapter(http=http, credentials=credentials(), fetched_at=lambda: NOW).probe("fund_holdings", parameters=request().parameters)

    assert result == {"status": "unsupported_transport", "connected": False, "health_failure": False, "capability_id": "fund_holdings"}
    assert http.calls == []


def test_tushare_capability_denial_is_account_permission_not_failure_and_stays_scoped():
    from data_sources.providers.tushare import TushareAdapter

    adapter = TushareAdapter(http=FakeHttp([permission_fixture()]), credentials=credentials(), fetched_at=lambda: NOW)
    result = adapter.probe("fund_holdings", parameters=request().parameters)

    assert result == {"status": "plan_unavailable", "connected": False, "health_failure": False, "capability_id": "fund_holdings"}
    assert adapter.capability_status("fund_holdings") == "plan_unavailable"
    assert adapter.capability_status("stock_history") == "unprobed"


def test_tushare_fetch_records_only_the_denied_capability_before_raising():
    from data_sources.providers.tushare import TushareAdapter

    adapter = TushareAdapter(http=FakeHttp([permission_fixture()]), credentials=credentials(), fetched_at=lambda: NOW)
    with pytest.raises(ProviderUnavailable, match="plan_unavailable"):
        adapter.fetch(request())

    assert adapter.capability_status("fund_holdings") == "plan_unavailable"
    assert adapter.capability_status("stock_history") == "unprobed"


def test_tushare_parses_capability_payload_and_preserves_public_period_unit_source():
    from data_sources.providers.tushare import TushareAdapter

    http = FakeHttp([fixture()])
    rows = TushareAdapter(http=http, credentials=credentials(), fetched_at=lambda: NOW).fetch(request())
    row = rows[0]
    assert row.value == {"ts_code": "510300.SH", "symbol": "600000.SH", "end_date": "20250630", "mkv": Decimal("123.4"), "stk_mkv_ratio": Decimal("1.25")}
    assert row.as_of_date.isoformat() == "2025-06-30"
    assert row.source_family_id == "tushare"
    assert row.adapter_id == "tushare"
    assert row.capability_id == "fund_holdings"
    assert row.unit == "provider_native"
    assert row.frequency == "quarterly"
    assert row.source_metadata == {"api_name": "fund_portfolio", "period": "20250630", "source_reference": "https://api.tushare.pro/", "plan_status": "available"}
    assert SECRET not in repr(row) and SECRET not in repr(http.calls)


def test_tushare_capability_availability_does_not_promote_other_capabilities():
    from data_sources.providers.tushare import TushareAdapter

    adapter = TushareAdapter(http=FakeHttp([fixture()]), credentials=credentials(), fetched_at=lambda: NOW)
    result = adapter.probe("fund_holdings", parameters=request().parameters)
    assert result["status"] == "available"
    assert adapter.capability_status("fund_holdings") == "available"
    assert adapter.capability_status("stock_financials") == "unprobed"


@pytest.mark.parametrize(
    "response,status,health_failure",
    [
        (ProviderUnavailable("authentication"), "authentication_failed", False),
        (ProviderRateLimited(retry_after_seconds=12), "rate_limited", False),
        (ProviderUnavailable("timeout"), "timeout", True),
        (ProviderUnavailable("tls"), "tls", True),
        ({"code": -2002, "msg": "token无效"}, "authentication_failed", False),
        ({"code": 0, "msg": "", "data": {"fields": ["ts_code"], "items": []}}, "empty_result", False),
        ({"code": 0, "msg": "", "data": {"fields": "bad", "items": []}}, "schema_changed", True),
    ],
)
def test_tushare_probe_distinguishes_auth_rate_timeout_empty_and_schema(response, status, health_failure):
    from data_sources.providers.tushare import TushareAdapter

    result = TushareAdapter(http=FakeHttp([response]), credentials=credentials(), fetched_at=lambda: NOW).probe("fund_holdings", parameters=request().parameters)
    assert result["status"] == status
    assert result["health_failure"] is health_failure


def test_tushare_timeout_cache_fallback_is_explicit():
    from data_sources.providers.tushare import TushareAdapter

    rows = TushareAdapter(
        http=FakeHttp([ProviderUnavailable("timeout")]), credentials=credentials(), cache_getter=lambda _request: fixture(), fetched_at=lambda: NOW
    ).fetch(request())
    assert rows[0].data_status == "cached"
    assert rows[0].source_metadata["cache_status"] == "fallback"


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "msg": "", "data": {"fields": ["x"] * 65, "items": []}},
        {"code": 0, "msg": "", "data": {"fields": ["x"], "items": [["y"]] * 1001}},
        {"code": 0, "msg": "", "data": {"fields": ["x"], "items": [[float("nan")]]}},
        {"code": 0, "msg": "", "data": {"fields": ["x"], "items": [[Decimal("1e999999")]]}},
        {"code": 0, "msg": "", "data": {"fields": ["x"], "items": [["y" * 5000]]}},
        {"code": 0, "msg": "", "data": {"fields": ["end_date"], "items": [["20990101"]]}},
        {"code": 0, "msg": "", "data": {"fields": ["x"], "items": [[{"nested": "hostile"}]]}},
    ],
)
def test_tushare_rejects_unbounded_hostile_nested_and_future_payloads(payload):
    from data_sources.providers.tushare import TushareAdapter

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        TushareAdapter(http=FakeHttp([payload]), credentials=credentials(), fetched_at=lambda: NOW).fetch(request())


def test_tushare_marks_stale_period_without_fabricating_permission_for_others():
    from data_sources.providers.tushare import TushareAdapter

    adapter = TushareAdapter(http=FakeHttp([fixture()]), credentials=credentials(), fetched_at=lambda: NOW)
    row = adapter.fetch(request(max_age_days=30))[0]
    assert row.data_status == "stale"
    assert adapter.capability_status("fund_holdings") == "available"
    assert adapter.capability_status("stock_history") == "unprobed"


def test_tushare_catalog_metadata_and_capabilities_are_exact():
    from data_sources.catalog import build_catalog
    from data_sources.models import SourceRole
    from data_sources.provider_registry import ProviderRegistry

    catalog = build_catalog({"sources": []})
    row = catalog.adapter("tushare")
    assert row.source_family_id == "tushare"
    assert row.billing_model.value == "freemium"
    assert row.auth_type == "api_token"
    assert row.credential_env_names == ("TUSHARE_TOKEN",)
    assert row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert row.source_roles == (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK)
    assert row.capability_ids == ("fund_holdings", "stock_history", "stock_financials", "index_calendar")
    assert "tushare" in ProviderRegistry(catalog).available_adapter_ids()
