from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from data_sources.budgets import BudgetDecision, BudgetGuard, BudgetPolicy, BudgetValidationError
from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.models import BillingModel
from data_sources.usage_store import UsageStore


NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)
SECRET = "fred-test-credential"


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, object, object]] = []

    def get_json(self, url, *, headers=None, params=None):
        # Deliberately retain only the public request shape. A transport may see
        # the credential transiently, but test evidence must not persist it.
        safe_params = {key: "[credential]" if key == "api_key" else value for key, value in (params or {}).items()}
        self.calls.append((url, headers, safe_params))
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
        self.allowed = allowed
        self.reason = reason
        self.calls = []

    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now))
        return BudgetDecision(self.allowed, self.reason, None, estimated_cost)


class ExplodingParameters(dict):
    def get(self, *_args, **_kwargs):
        raise AssertionError("request construction happened before credential preflight")


class UnavailableCredentials:
    def get(self, *_args):
        raise RuntimeError("credential backend details must stay local")


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


def credentials(configured: bool = True):
    store = MemoryCredentialStore({"fred": ("FRED_API_KEY",)})
    if configured:
        store.set("fred", "FRED_API_KEY", SECRET)
    return store


def request(**overrides):
    parameters = {
        "series_id": "CPIAUCSL",
        "observation_start": "2025-01-01",
        "observation_end": "2025-02-01",
    }
    parameters.update(overrides)
    return ProviderRequest("macro_series", parameters)


def fixture():
    return {
        "realtime_start": "2026-08-18",
        "realtime_end": "2026-08-18",
        "units": "Index 1982-1984=100",
        "frequency": "Monthly",
        "observations": [
            {"realtime_start": "2026-08-18", "realtime_end": "2026-08-18", "date": "2025-01-01", "value": "317.671"},
            {"realtime_start": "2026-08-18", "realtime_end": "2026-08-18", "date": "2025-02-01", "value": "."},
        ],
    }


def parse_fixture(payload, *, provider_request=None, cached=False):
    from data_sources.providers.fred import FredAdapter

    provider_request = provider_request or request()
    adapter = FredAdapter(http=FakeHttp([]), credentials=credentials(False), fetched_at=lambda: NOW)
    params, max_age_days = adapter._request(provider_request)
    assert "api_key" not in params
    assert SECRET not in repr(params)
    return adapter._parse(payload, provider_request, now=NOW, max_age_days=max_age_days, cached=cached)


def real_guard(tmp_path, *, free_only=True):
    descriptor = build_catalog({"sources": []}).adapter("fred")
    policy = BudgetPolicy("fred", True, True, free_only, Decimal("1.00"), Decimal("5.00"), Decimal("1.00"))
    return BudgetGuard(UsageStore(tmp_path / "fred-usage"), {"fred": policy}, trusted_adapters={"fred": descriptor})


def test_fred_missing_key_short_circuits_before_request_budget_and_transport():
    from data_sources.providers.fred import FredAdapter

    http, budget = FakeHttp([]), FakeBudget()
    adapter = FredAdapter(http=http, credentials=credentials(False), budget_guard=budget)

    result = adapter.probe("macro_series", parameters=ExplodingParameters())

    assert result == {"status": "unconfigured", "connected": False, "health_failure": False}
    assert budget.calls == []
    assert http.calls == []


def test_fred_configured_key_is_blocked_before_it_can_enter_a_prepared_url():
    from data_sources.providers.fred import FredAdapter

    http = PreparedUrlCredentialTrap()
    budget = FakeBudget()
    cache_calls = []
    adapter = FredAdapter(
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


def test_fred_unavailable_credentials_and_transport_barrier_are_zero_call_states():
    from data_sources.providers.fred import FredAdapter

    http = FakeHttp([])
    assert FredAdapter(http=http, credentials=UnavailableCredentials()).probe("macro_series") == {"status": "unconfigured", "connected": False, "health_failure": False}
    budget = FakeBudget(False, "free_only")
    assert FredAdapter(http=http, credentials=credentials(), budget_guard=budget, fetched_at=lambda: NOW).probe("macro_series") == {
        "status": "unsupported_credential_transport",
        "connected": False,
        "health_failure": False,
        "capability_id": "macro_series",
    }
    assert budget.calls == []
    assert http.calls == []


def test_fred_rejects_mutated_budget_decision_before_using_hostile_field():
    from data_sources.providers.fred import FredAdapter

    trap = TrapValue()
    decision = BudgetDecision(True, "authorized", None, Decimal("0"))
    object.__setattr__(decision, "estimated_cost", trap)
    budget = type("HostileBudget", (), {"authorize": lambda *_args, **_kwargs: decision})()
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"):
        FredAdapter(http=FakeHttp([]), credentials=credentials(), budget_guard=budget, fetched_at=lambda: NOW).fetch(request())
    assert trap.called is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("estimated_cost", Decimal("0E+13")),
        ("reservation_id", "api_key-secret-reservation"),
        ("reason", "disabled"),
        ("health_failure", True),
    ],
)
def test_fred_revalidates_every_mutated_budget_decision_field_before_transport(field, value):
    from data_sources.providers.fred import FredAdapter

    decision = BudgetDecision(True, "authorized", None, Decimal("0"))
    object.__setattr__(decision, field, value)
    budget = type("MutatedBudget", (), {"authorize": lambda *_args, **_kwargs: decision})()
    http = FakeHttp([fixture()])

    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"):
        FredAdapter(http=http, credentials=credentials(), budget_guard=budget, fetched_at=lambda: NOW).fetch(request())
    assert http.calls == []


def test_fred_normalizes_budget_validation_error_without_leaking_or_transport():
    from data_sources.providers.fred import FredAdapter

    class InvalidBudget:
        def authorize(self, *_args, **_kwargs):
            raise BudgetValidationError(f"api_key={SECRET}")

    http = FakeHttp([fixture()])
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport") as captured:
        FredAdapter(http=http, credentials=credentials(), budget_guard=InvalidBudget(), fetched_at=lambda: NOW).fetch(request())
    assert SECRET not in str(captured.value)
    assert http.calls == []


def test_fred_preserves_series_dates_unit_frequency_reference_and_missing_value():
    rows = parse_fixture(fixture())

    assert [row.value for row in rows] == [Decimal("317.671"), None]
    assert [row.as_of_date.isoformat() for row in rows] == ["2025-01-01", "2025-02-01"]
    assert [row.data_status for row in rows] == ["upstream_reported", "missing"]
    assert {row.source_family_id for row in rows} == {"fred"}
    assert {row.adapter_id for row in rows} == {"fred"}
    assert {row.capability_id for row in rows} == {"macro_series"}
    assert {row.unit for row in rows} == {"Index 1982-1984=100"}
    assert {row.frequency for row in rows} == {"monthly"}
    assert {row.source_metadata["series_id"] for row in rows} == {"CPIAUCSL"}
    assert {row.source_metadata["realtime_start"] for row in rows} == {"2026-08-18"}
    assert {row.source_metadata["realtime_end"] for row in rows} == {"2026-08-18"}
    assert {row.source_metadata["source_reference"] for row in rows} == {"https://api.stlouisfed.org/fred/"}
    assert SECRET not in repr(rows)


def test_fred_transport_barrier_precedes_real_guard_and_creates_no_reservation(tmp_path):
    from data_sources.providers.fred import FredAdapter

    http = FakeHttp([])
    guard = real_guard(tmp_path, free_only=True)
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"):
        FredAdapter(http=http, credentials=credentials(), budget_guard=guard, fetched_at=lambda: NOW).fetch(request())

    assert http.calls == []
    assert guard.usage_store.records(now=NOW) == ()


def test_fred_missing_guard_allows_only_its_exact_trusted_free_key_descriptor():
    from data_sources.providers.fred import FredAdapter

    http = FakeHttp([fixture()])
    adapter = FredAdapter(http=http, credentials=credentials(), fetched_at=lambda: NOW)
    adapter.descriptor = replace(adapter.descriptor, billing_model=BillingModel.FREEMIUM)

    assert adapter.probe("macro_series", parameters=request().parameters) == {
        "status": "unsupported_credential_transport",
        "connected": False,
        "health_failure": False,
        "capability_id": "macro_series",
    }
    assert http.calls == []


@pytest.mark.parametrize(
    "payload,error",
    [
        ({}, ProviderSchemaChanged),
        ({"error_code": 400, "error_message": "bad credential"}, ProviderUnavailable),
        ({"error_code": 429, "error_message": "rate"}, ProviderRateLimited),
        ({"units": "USD", "frequency": "Annual", "observations": []}, ProviderUnavailable),
        ({"units": "USD", "frequency": "Annual", "observations": "hostile"}, ProviderSchemaChanged),
    ],
)
def test_fred_distinguishes_schema_auth_rate_limit_and_empty(payload, error):
    with pytest.raises(error):
        parse_fixture(payload)


def test_fred_probe_reports_rate_limit_retry_after_without_health_failure():
    from data_sources.providers.fred import FredAdapter

    http = FakeHttp([ProviderRateLimited(retry_after_seconds=17.0)])
    result = FredAdapter(http=http, credentials=credentials(), fetched_at=lambda: NOW).probe("macro_series", parameters={"series_id": "CPIAUCSL"})

    assert result == {"status": "unsupported_credential_transport", "connected": False, "health_failure": False, "capability_id": "macro_series"}
    assert http.calls == []


def test_fred_probe_reports_tls_as_a_transport_health_failure():
    from data_sources.providers.fred import FredAdapter

    http = FakeHttp([ProviderUnavailable("tls")])
    result = FredAdapter(http=http, credentials=credentials(), fetched_at=lambda: NOW).probe("macro_series", parameters={"series_id": "GDP"})
    assert result == {"status": "unsupported_credential_transport", "connected": False, "health_failure": False, "capability_id": "macro_series"}
    assert http.calls == []


def test_fred_uses_bounded_cache_fallback_for_timeout_and_marks_cache_truth():
    rows = parse_fixture(fixture(), cached=True)

    assert {row.data_status for row in rows} == {"cached", "missing"}
    assert {row.source_metadata["cache_status"] for row in rows} == {"fallback"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.update({"units": []}),
        lambda body: body.update({"frequency": 1}),
        lambda body: body["observations"].__setitem__(0, {"date": "2099-01-01", "value": "1", "realtime_start": "2026-08-18", "realtime_end": "2026-08-18"}),
        lambda body: body["observations"].__setitem__(0, {"date": "2025-01-01", "value": "NaN", "realtime_start": "2026-08-18", "realtime_end": "2026-08-18"}),
        lambda body: body["observations"].__setitem__(0, {"date": "2025-01-01", "value": "1e999999", "realtime_start": "2026-08-18", "realtime_end": "2026-08-18"}),
        lambda body: body["observations"].__setitem__(0, {"date": "2025-01-01", "value": 1 << 20000, "realtime_start": "2026-08-18", "realtime_end": "2026-08-18"}),
        lambda body: body.update({"observations": body["observations"] * 501}),
    ],
)
def test_fred_rejects_hostile_schema_future_nonfinite_and_unbounded_rows(mutation):
    payload = fixture()
    mutation(payload)
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        parse_fixture(payload)


def test_fred_marks_explicitly_age_bounded_historical_observation_stale_and_unknown_unit():
    payload = fixture()
    payload["units"] = ""
    row = parse_fixture(payload, provider_request=request(max_age_days=30))[0]

    assert row.data_status == "stale"
    assert row.unit == "unknown"


def test_fred_never_exposes_credential_in_result_error_or_repr():
    from data_sources.providers.fred import FredAdapter

    adapter = FredAdapter(http=FakeHttp([ProviderUnavailable("authentication", reference=f"https://example.test/?api_key={SECRET}")]), credentials=credentials())
    with pytest.raises(ProviderUnavailable) as captured:
        adapter.fetch(request())
    evidence = json.dumps({"adapter": repr(adapter), "error": str(captured.value), "reference": captured.value.public_reference})
    assert SECRET not in evidence
    assert "api_key" not in captured.value.public_reference


def test_fred_rejects_custom_response_containers_without_executing_methods():
    variants = []
    root = TrapDict(fixture())
    variants.append((root, [root]))
    nested_list = TrapList(fixture()["observations"])
    variants.append(({**fixture(), "observations": nested_list}, [nested_list]))
    nested_row = TrapDict(fixture()["observations"][0])
    variants.append(({**fixture(), "observations": [nested_row]}, [nested_row]))

    for payload, traps in variants:
        with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
            parse_fixture(payload)
        assert all(trap.called is False for trap in traps)


def test_fred_rejects_custom_request_mapping_without_executing_methods():
    from data_sources.providers.fred import FredAdapter

    parameters = TrapDict({"series_id": "GDP"})
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        FredAdapter(http=FakeHttp([]), credentials=credentials(), fetched_at=lambda: NOW).fetch(ProviderRequest("macro_series", parameters))
    assert parameters.called is False


def test_fred_rejects_custom_scalar_before_equality_is_evaluated():
    trap = TrapValue()
    payload = fixture()
    payload["observations"][0]["value"] = trap
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        parse_fixture(payload)
    assert trap.called is False


def test_fred_catalog_metadata_is_exact_and_registry_is_stable():
    from data_sources.catalog import build_catalog
    from data_sources.provider_registry import ProviderRegistry

    catalog = build_catalog({"sources": []})
    row = catalog.adapter("fred")
    assert row.source_family_id == "fred"
    assert row.billing_model.value == "free_key"
    assert row.auth_type == "api_key"
    assert row.credential_env_names == ("FRED_API_KEY",)
    assert row.default_enabled is False
    assert row.catalog_status.value == "unconfigured"
    http = FakeHttp([])
    registry = ProviderRegistry(
        catalog,
        http_factory=lambda: http,
        credential_factory=lambda _scope: credentials(False),
    )
    identifiers = registry.available_adapter_ids()
    assert identifiers == tuple(sorted(set(identifiers)))
    assert "fred" in identifiers
    assert registry.adapter("fred").probe("macro_series") == {"status": "unconfigured", "connected": False, "health_failure": False}
    assert http.calls == []
