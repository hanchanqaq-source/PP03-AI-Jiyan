from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal

import pytest

from data_sources.budgets import BudgetDecision, BudgetGuard, BudgetPolicy, BudgetValidationError
from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.models import BillingModel
from data_sources.usage_store import UsageStore


NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)
SECRET = "eia-test-credential"


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, {key: "[credential]" if key == "api_key" else value for key, value in (params or {}).items()}))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeBudget:
    def __init__(self, allowed=True, reason="authorized"):
        self.allowed, self.reason, self.calls = allowed, reason, []

    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now))
        return BudgetDecision(self.allowed, self.reason, None, estimated_cost)


class TrapDict(dict):
    def __init__(self, value):
        super().__init__(value)
        self.called = False

    def explode(self, *_args, **_kwargs):
        self.called = True
        raise AssertionError("attacker-controlled dict method executed")

    get = items = keys = values = __iter__ = __len__ = __bool__ = __eq__ = explode


class TrapList(list):
    def __init__(self, value):
        super().__init__(value)
        self.called = False

    def explode(self, *_args, **_kwargs):
        self.called = True
        raise AssertionError("attacker-controlled list method executed")

    __iter__ = __len__ = __bool__ = __getitem__ = __eq__ = explode


class TrapValue:
    def __init__(self):
        self.called = False

    def explode(self, *_args, **_kwargs):
        self.called = True
        raise AssertionError("attacker-controlled budget field executed")

    __eq__ = __bool__ = __str__ = explode


class TrapScalar:
    def __init__(self):
        self.called = False

    def explode(self, *_args, **_kwargs):
        self.called = True
        raise AssertionError("attacker-controlled response scalar executed")

    __eq__ = __ne__ = __hash__ = __str__ = explode


def credentials(configured=True):
    store = MemoryCredentialStore({"eia": ("EIA_API_KEY",)})
    if configured:
        store.set("eia", "EIA_API_KEY", SECRET)
    return store


def request(**overrides):
    parameters = {"route": "electricity/retail-sales", "series_id": "RES-ALL-M", "start": "2025-01", "end": "2025-02"}
    parameters.update(overrides)
    return ProviderRequest("macro_series", parameters)


def fixture():
    return {
        "response": {
            "frequency": "monthly",
            "data": [
                {"series": "RES-ALL-M", "series-name": "Residential sales", "period": "2025-01", "value": "120.5", "units": "million kWh", "type": "actual"},
                {"series": "RES-ALL-M", "series-name": "Residential sales", "period": "2025-02", "value": "121.0", "units": "million kWh", "type": "forecast"},
            ],
        }
    }


def real_guard(tmp_path, *, free_only=True):
    descriptor = build_catalog({"sources": []}).adapter("eia")
    policy = BudgetPolicy("eia", True, True, free_only, Decimal("1.00"), Decimal("5.00"), Decimal("1.00"))
    return BudgetGuard(UsageStore(tmp_path / "eia-usage"), {"eia": policy}, trusted_adapters={"eia": descriptor})


def test_eia_no_key_and_blocked_status_guard_make_zero_transport_calls():
    from data_sources.providers.eia import EiaAdapter

    http, budget = FakeHttp([]), FakeBudget()
    assert EiaAdapter(http=http, credentials=credentials(False), budget_guard=budget).probe("macro_series") == {"status": "unconfigured", "connected": False, "health_failure": False}
    assert budget.calls == [] and http.calls == []

    blocked = FakeBudget(False, "disabled")
    assert EiaAdapter(http=http, credentials=credentials(), budget_guard=blocked, fetched_at=lambda: NOW).probe("macro_series") == {"status": "disabled", "connected": False, "health_failure": False}
    assert len(blocked.calls) == 1 and http.calls == []

    free_only = FakeBudget(False, "free_only")
    assert EiaAdapter(http=http, credentials=credentials(), budget_guard=free_only, fetched_at=lambda: NOW).probe("macro_series") == {"status": "free_only", "connected": False, "health_failure": False}
    assert http.calls == []


def test_eia_rejects_mutated_budget_decision_before_using_hostile_field():
    from data_sources.providers.eia import EiaAdapter

    trap = TrapValue()
    decision = BudgetDecision(True, "authorized", None, Decimal("0"))
    object.__setattr__(decision, "estimated_cost", trap)
    budget = type("HostileBudget", (), {"authorize": lambda *_args, **_kwargs: decision})()
    with pytest.raises(ProviderUnavailable, match="budget_status_invalid"):
        EiaAdapter(http=FakeHttp([]), credentials=credentials(), budget_guard=budget, fetched_at=lambda: NOW).fetch(request())
    assert trap.called is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("estimated_cost", Decimal("0E+13")),
        ("reservation_id", "token-secret-reservation"),
        ("reason", "free_only"),
        ("health_failure", True),
    ],
)
def test_eia_revalidates_every_mutated_budget_decision_field_before_transport(field, value):
    from data_sources.providers.eia import EiaAdapter

    decision = BudgetDecision(True, "authorized", None, Decimal("0"))
    object.__setattr__(decision, field, value)
    budget = type("MutatedBudget", (), {"authorize": lambda *_args, **_kwargs: decision})()
    http = FakeHttp([fixture()])

    with pytest.raises(ProviderUnavailable, match="budget_status_invalid"):
        EiaAdapter(http=http, credentials=credentials(), budget_guard=budget, fetched_at=lambda: NOW).fetch(request())
    assert http.calls == []


def test_eia_normalizes_budget_validation_error_without_leaking_or_transport():
    from data_sources.providers.eia import EiaAdapter

    class InvalidBudget:
        def authorize(self, *_args, **_kwargs):
            raise BudgetValidationError(f"token={SECRET}")

    http = FakeHttp([fixture()])
    with pytest.raises(ProviderUnavailable, match="budget_status_invalid") as captured:
        EiaAdapter(http=http, credentials=credentials(), budget_guard=InvalidBudget(), fetched_at=lambda: NOW).fetch(request())
    assert SECRET not in str(captured.value)
    assert http.calls == []


def test_eia_preserves_series_name_unit_frequency_period_actual_forecast_and_reference():
    from data_sources.providers.eia import EiaAdapter

    http = FakeHttp([fixture()])
    rows = EiaAdapter(http=http, credentials=credentials(), fetched_at=lambda: NOW).fetch(request())

    assert [row.value for row in rows] == [Decimal("120.5"), Decimal("121.0")]
    assert [row.as_of_date.isoformat() for row in rows] == ["2025-01-01", "2025-02-01"]
    assert [row.data_status for row in rows] == ["actual", "forecast"]
    assert {row.unit for row in rows} == {"million kWh"}
    assert {row.frequency for row in rows} == {"monthly"}
    assert {row.source_metadata["series_id"] for row in rows} == {"RES-ALL-M"}
    assert {row.source_metadata["series_name"] for row in rows} == {"Residential sales"}
    assert {row.source_metadata["observation_type"] for row in rows} == {"actual", "forecast"}
    assert {row.source_metadata["source_reference"] for row in rows} == {"https://api.eia.gov/v2/"}
    assert http.calls[0][0] == "https://api.eia.gov/v2/electricity/retail-sales/data/"
    assert SECRET not in repr(rows) and SECRET not in repr(http.calls)


def test_eia_trusted_free_key_catalog_is_zero_cost_under_real_free_only_guard(tmp_path):
    from data_sources.providers.eia import EiaAdapter

    http = FakeHttp([fixture()])
    guard = real_guard(tmp_path, free_only=True)
    rows = EiaAdapter(http=http, credentials=credentials(), budget_guard=guard, fetched_at=lambda: NOW).fetch(request())

    assert len(rows) == 2
    assert len(http.calls) == 1
    assert guard.usage_store.records(now=NOW)[0].estimated_cost == Decimal("0")


def test_eia_missing_guard_allows_only_its_exact_trusted_free_key_descriptor():
    from data_sources.providers.eia import EiaAdapter

    http = FakeHttp([fixture()])
    adapter = EiaAdapter(http=http, credentials=credentials(), fetched_at=lambda: NOW)
    adapter.descriptor = replace(adapter.descriptor, billing_model=BillingModel.FREEMIUM)

    assert adapter.probe("macro_series", parameters=request().parameters) == {
        "status": "budget_guard_unavailable",
        "connected": False,
        "health_failure": False,
    }
    assert http.calls == []


@pytest.mark.parametrize(
    "response,expected",
    [
        (ProviderUnavailable("authentication"), "authentication_failed"),
        (ProviderRateLimited(retry_after_seconds=9), "rate_limited"),
        (ProviderUnavailable("timeout"), "timeout"),
        (ProviderUnavailable("tls"), "tls"),
        ({"error": "API_KEY_INVALID"}, "authentication_failed"),
        ({"response": {"frequency": "monthly", "data": []}}, "empty_result"),
        ({"response": {"frequency": "monthly", "data": "bad"}}, "schema_changed"),
    ],
)
def test_eia_probe_distinguishes_failure_states(response, expected):
    from data_sources.providers.eia import EiaAdapter

    result = EiaAdapter(http=FakeHttp([response]), credentials=credentials(), fetched_at=lambda: NOW).probe("macro_series", parameters=request().parameters)
    assert result["status"] == expected
    assert result["health_failure"] is (expected in {"timeout", "tls", "schema_changed"})


def test_eia_timeout_uses_cache_without_hiding_actual_forecast_or_cache_state():
    from data_sources.providers.eia import EiaAdapter

    rows = EiaAdapter(
        http=FakeHttp([ProviderUnavailable("timeout")]), credentials=credentials(), cache_getter=lambda _request: fixture(), fetched_at=lambda: NOW
    ).fetch(request())
    assert [row.source_metadata["observation_type"] for row in rows] == ["actual", "forecast"]
    assert {row.source_metadata["cache_status"] for row in rows} == {"fallback"}
    assert {row.data_status for row in rows} == {"cached"}


@pytest.mark.parametrize(
    "payload",
    [
        {"response": {"frequency": [], "data": []}},
        {"response": {"frequency": "monthly", "data": [{"series": "RES-ALL-M", "series-name": "x", "period": "2099-01", "value": 1, "units": "kWh", "type": "actual"}]}},
        {"response": {"frequency": "monthly", "data": [{"series": "RES-ALL-M", "series-name": "x", "period": "2025-01", "value": float("nan"), "units": "kWh", "type": "actual"}]}},
        {"response": {"frequency": "monthly", "data": [{"series": "RES-ALL-M", "series-name": "x", "period": "2025-01", "value": "1e999999", "units": "kWh", "type": "actual"}]}},
        {"response": {"frequency": "monthly", "data": [{"series": "RES-ALL-M", "series-name": "x", "period": "2025-01", "value": 1 << 20000, "units": "kWh", "type": "actual"}]}},
        {"response": {"frequency": "monthly", "data": [{"series": "RES-ALL-M", "series-name": "x" * 5000, "period": "2025-01", "value": 1, "units": "kWh", "type": "actual"}]}},
        {"response": {"frequency": "monthly", "data": [{"series": "RES-ALL-M", "series-name": "x", "period": "2025-01", "value": 1, "units": "kWh", "type": "actual"}] * 1001}},
    ],
)
def test_eia_rejects_hostile_scalars_future_dates_and_resource_abuse(payload):
    from data_sources.providers.eia import EiaAdapter

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        EiaAdapter(http=FakeHttp([payload]), credentials=credentials(), fetched_at=lambda: NOW).fetch(request())


@pytest.mark.parametrize("field", ["series", "type"])
def test_eia_rejects_malicious_identity_scalars_before_comparison_hash_or_format(field):
    from data_sources.providers.eia import EiaAdapter

    trap = TrapScalar()
    payload = fixture()
    payload["response"]["data"][0][field] = trap

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        EiaAdapter(http=FakeHttp([payload]), credentials=credentials(), fetched_at=lambda: NOW).fetch(request())
    assert trap.called is False


def test_eia_marks_stale_actual_and_preserves_unknown_unit():
    from data_sources.providers.eia import EiaAdapter

    payload = fixture()
    payload["response"]["data"][0]["units"] = ""
    row = EiaAdapter(http=FakeHttp([payload]), credentials=credentials(), fetched_at=lambda: NOW).fetch(request(max_age_days=30))[0]
    assert row.data_status == "stale"
    assert row.source_metadata["observation_type"] == "actual"
    assert row.unit == "unknown"


def test_eia_rejects_custom_response_containers_without_executing_methods():
    from data_sources.providers.eia import EiaAdapter

    root = TrapDict(fixture())
    response = TrapDict(fixture()["response"])
    items = TrapList(fixture()["response"]["data"])
    row = TrapDict(fixture()["response"]["data"][0])
    variants = [
        (root, [root]),
        ({"response": response}, [response]),
        ({"response": {**fixture()["response"], "data": items}}, [items]),
        ({"response": {**fixture()["response"], "data": [row]}}, [row]),
    ]
    for payload, traps in variants:
        with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
            EiaAdapter(http=FakeHttp([payload]), credentials=credentials(), fetched_at=lambda: NOW).fetch(request())
        assert all(trap.called is False for trap in traps)


def test_eia_rejects_custom_request_mapping_without_executing_methods():
    from data_sources.providers.eia import EiaAdapter

    parameters = TrapDict({"route": "electricity/retail-sales", "series_id": "RES-ALL-M"})
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        EiaAdapter(http=FakeHttp([]), credentials=credentials(), fetched_at=lambda: NOW).fetch(ProviderRequest("macro_series", parameters))
    assert parameters.called is False


def test_eia_catalog_and_registry_metadata_are_exact():
    from data_sources.catalog import build_catalog
    from data_sources.provider_registry import ProviderRegistry

    catalog = build_catalog({"sources": []})
    row = catalog.adapter("eia")
    assert (row.source_family_id, row.billing_model.value, row.auth_type) == ("eia", "free_key", "api_key")
    assert row.credential_env_names == ("EIA_API_KEY",)
    assert row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert "eia" in ProviderRegistry(catalog).available_adapter_ids()
