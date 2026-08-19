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


class PreparedUrlCredentialTrap:
    def __init__(self):
        self.calls = 0
        self.credential_in_url = False

    def get_json(self, url, *, headers=None, params=None):
        from requests import Request

        self.calls += 1
        prepared = Request("GET", url, headers=headers, params=params).prepare()
        self.credential_in_url = SECRET in (prepared.url or "")
        raise AssertionError("credential reached a prepared URL query")


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


def parse_fixture(payload, *, provider_request=None, cached=False):
    from data_sources.providers.eia import EiaAdapter

    provider_request = provider_request or request()
    adapter = EiaAdapter(http=FakeHttp([]), credentials=credentials(False), fetched_at=lambda: NOW)
    route, params, max_age_days = adapter._request(provider_request)
    assert route == provider_request.parameters["route"]
    assert "api_key" not in params
    assert SECRET not in repr(params)
    return adapter._parse(payload, provider_request, now=NOW, max_age_days=max_age_days, cached=cached)


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
    assert EiaAdapter(http=http, credentials=credentials(), budget_guard=blocked, fetched_at=lambda: NOW).probe("macro_series") == {
        "status": "unsupported_credential_transport",
        "connected": False,
        "health_failure": False,
        "capability_id": "macro_series",
    }
    assert blocked.calls == [] and http.calls == []

    free_only = FakeBudget(False, "free_only")
    assert EiaAdapter(http=http, credentials=credentials(), budget_guard=free_only, fetched_at=lambda: NOW).probe("macro_series")["status"] == "unsupported_credential_transport"
    assert free_only.calls == [] and http.calls == []


def test_eia_configured_key_is_blocked_before_it_can_enter_a_prepared_url():
    from data_sources.providers.eia import EiaAdapter

    http = PreparedUrlCredentialTrap()
    budget = FakeBudget()
    cache_calls = []
    adapter = EiaAdapter(
        http=http,
        credentials=credentials(),
        budget_guard=budget,
        cache_getter=lambda provider_request: cache_calls.append(provider_request),
        fetched_at=lambda: NOW,
    )

    provider_request = request()
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport") as captured:
        adapter.fetch(provider_request)

    assert http.calls == 0
    assert http.credential_in_url is False
    assert budget.calls == []
    assert cache_calls == []
    evidence = repr((provider_request, captured.value, captured.value.public_reference, adapter, http.calls, budget.calls, cache_calls))
    assert SECRET not in evidence
    assert "api_key" not in captured.value.public_reference


def test_eia_rejects_mutated_budget_decision_before_using_hostile_field():
    from data_sources.providers.eia import EiaAdapter

    trap = TrapValue()
    decision = BudgetDecision(True, "authorized", None, Decimal("0"))
    object.__setattr__(decision, "estimated_cost", trap)
    budget = type("HostileBudget", (), {"authorize": lambda *_args, **_kwargs: decision})()
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"):
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

    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"):
        EiaAdapter(http=http, credentials=credentials(), budget_guard=budget, fetched_at=lambda: NOW).fetch(request())
    assert http.calls == []


def test_eia_normalizes_budget_validation_error_without_leaking_or_transport():
    from data_sources.providers.eia import EiaAdapter

    class InvalidBudget:
        def authorize(self, *_args, **_kwargs):
            raise BudgetValidationError(f"token={SECRET}")

    http = FakeHttp([fixture()])
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport") as captured:
        EiaAdapter(http=http, credentials=credentials(), budget_guard=InvalidBudget(), fetched_at=lambda: NOW).fetch(request())
    assert SECRET not in str(captured.value)
    assert http.calls == []


def test_eia_preserves_series_name_unit_frequency_period_actual_forecast_and_reference():
    rows = parse_fixture(fixture())

    assert [row.value for row in rows] == [Decimal("120.5"), Decimal("121.0")]
    assert [row.as_of_date.isoformat() for row in rows] == ["2025-01-01", "2025-02-01"]
    assert [row.data_status for row in rows] == ["actual", "forecast"]
    assert {row.unit for row in rows} == {"million kWh"}
    assert {row.frequency for row in rows} == {"monthly"}
    assert {row.source_metadata["series_id"] for row in rows} == {"RES-ALL-M"}
    assert {row.source_metadata["series_name"] for row in rows} == {"Residential sales"}
    assert {row.source_metadata["observation_type"] for row in rows} == {"actual", "forecast"}
    assert {row.source_metadata["source_reference"] for row in rows} == {"https://api.eia.gov/v2/"}
    assert SECRET not in repr(rows)


def test_eia_trusted_free_key_catalog_is_zero_cost_under_real_free_only_guard(tmp_path):
    from data_sources.providers.eia import EiaAdapter

    http = FakeHttp([])
    guard = real_guard(tmp_path, free_only=True)
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"):
        EiaAdapter(http=http, credentials=credentials(), budget_guard=guard, fetched_at=lambda: NOW).fetch(request())

    assert http.calls == []
    assert guard.usage_store.records(now=NOW) == ()


def test_eia_missing_guard_allows_only_its_exact_trusted_free_key_descriptor():
    from data_sources.providers.eia import EiaAdapter

    http = FakeHttp([fixture()])
    adapter = EiaAdapter(http=http, credentials=credentials(), fetched_at=lambda: NOW)
    adapter.descriptor = replace(adapter.descriptor, billing_model=BillingModel.FREEMIUM)

    assert adapter.probe("macro_series", parameters=request().parameters) == {
        "status": "unsupported_credential_transport",
        "connected": False,
        "health_failure": False,
        "capability_id": "macro_series",
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

    http = FakeHttp([response])
    result = EiaAdapter(http=http, credentials=credentials(), fetched_at=lambda: NOW).probe("macro_series", parameters=request().parameters)
    assert result == {"status": "unsupported_credential_transport", "connected": False, "health_failure": False, "capability_id": "macro_series"}
    assert http.calls == []


def test_eia_timeout_uses_cache_without_hiding_actual_forecast_or_cache_state():
    rows = parse_fixture(fixture(), cached=True)
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
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        parse_fixture(payload)


@pytest.mark.parametrize("field", ["series", "type"])
def test_eia_rejects_malicious_identity_scalars_before_comparison_hash_or_format(field):
    trap = TrapScalar()
    payload = fixture()
    payload["response"]["data"][0][field] = trap

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        parse_fixture(payload)
    assert trap.called is False


def test_eia_marks_stale_actual_and_preserves_unknown_unit():
    payload = fixture()
    payload["response"]["data"][0]["units"] = ""
    row = parse_fixture(payload, provider_request=request(max_age_days=30))[0]
    assert row.data_status == "stale"
    assert row.source_metadata["observation_type"] == "actual"
    assert row.unit == "unknown"


def test_eia_rejects_custom_response_containers_without_executing_methods():
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
            parse_fixture(payload)
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
    http = FakeHttp([])
    registry = ProviderRegistry(
        catalog,
        http_factory=lambda: http,
        credential_factory=lambda _scope: credentials(False),
    )
    assert "eia" in registry.available_adapter_ids()
    assert registry.adapter("eia").probe("macro_series") == {"status": "unconfigured", "connected": False, "health_failure": False}
    assert http.calls == []
