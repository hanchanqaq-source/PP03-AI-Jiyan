from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from data_sources.credentials import MemoryCredentialStore
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable


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


class FakeBudget:
    def __init__(self, allowed=True, reason="authorized"):
        self.allowed = allowed
        self.reason = reason
        self.calls = []

    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now))
        return type("Decision", (), {"allowed": self.allowed, "reason": self.reason})()


class ExplodingParameters(dict):
    def get(self, *_args, **_kwargs):
        raise AssertionError("request construction happened before credential preflight")


class UnavailableCredentials:
    def get(self, *_args):
        raise RuntimeError("credential backend details must stay local")


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


def test_fred_missing_key_short_circuits_before_request_budget_and_transport():
    from data_sources.providers.fred import FredAdapter

    http, budget = FakeHttp([]), FakeBudget()
    adapter = FredAdapter(http=http, credentials=credentials(False), budget_guard=budget)

    result = adapter.probe("macro_series", parameters=ExplodingParameters())

    assert result == {"status": "unconfigured", "connected": False, "health_failure": False}
    assert budget.calls == []
    assert http.calls == []


def test_fred_unavailable_credential_store_and_free_only_block_are_zero_call_states():
    from data_sources.providers.fred import FredAdapter

    http = FakeHttp([])
    assert FredAdapter(http=http, credentials=UnavailableCredentials()).probe("macro_series") == {"status": "unconfigured", "connected": False, "health_failure": False}
    assert FredAdapter(http=http, credentials=credentials(), budget_guard=FakeBudget(False, "free_only"), fetched_at=lambda: NOW).probe("macro_series") == {"status": "free_only", "connected": False, "health_failure": False}
    assert http.calls == []


def test_fred_preserves_series_dates_unit_frequency_reference_and_missing_value():
    from data_sources.providers.fred import FredAdapter

    http, budget = FakeHttp([fixture()]), FakeBudget()
    rows = FredAdapter(http=http, credentials=credentials(), budget_guard=budget, fetched_at=lambda: NOW).fetch(request())

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
    assert budget.calls == [("fred", Decimal("0"), NOW)]
    assert http.calls == [("https://api.stlouisfed.org/fred/series/observations", {"Accept": "application/json"}, {"series_id": "CPIAUCSL", "file_type": "json", "observation_start": "2025-01-01", "observation_end": "2025-02-01", "api_key": "[credential]"})]
    assert SECRET not in repr(rows)
    assert SECRET not in repr(http.calls)


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
    from data_sources.providers.fred import FredAdapter

    with pytest.raises(error):
        FredAdapter(http=FakeHttp([payload]), credentials=credentials(), fetched_at=lambda: NOW).fetch(request())


def test_fred_probe_reports_rate_limit_retry_after_without_health_failure():
    from data_sources.providers.fred import FredAdapter

    result = FredAdapter(
        http=FakeHttp([ProviderRateLimited(retry_after_seconds=17.0)]),
        credentials=credentials(),
        fetched_at=lambda: NOW,
    ).probe("macro_series", parameters={"series_id": "CPIAUCSL"})

    assert result == {"status": "rate_limited", "connected": False, "health_failure": False, "retry_after_seconds": 17.0}


def test_fred_probe_reports_tls_as_a_transport_health_failure():
    from data_sources.providers.fred import FredAdapter

    result = FredAdapter(http=FakeHttp([ProviderUnavailable("tls")]), credentials=credentials(), fetched_at=lambda: NOW).probe("macro_series", parameters={"series_id": "GDP"})
    assert result == {"status": "tls", "connected": False, "health_failure": True}


def test_fred_uses_bounded_cache_fallback_for_timeout_and_marks_cache_truth():
    from data_sources.providers.fred import FredAdapter

    rows = FredAdapter(
        http=FakeHttp([ProviderUnavailable("timeout")]),
        credentials=credentials(),
        cache_getter=lambda _request: fixture(),
        fetched_at=lambda: NOW,
    ).fetch(request())

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
        lambda body: body.update({"observations": body["observations"] * 501}),
    ],
)
def test_fred_rejects_hostile_schema_future_nonfinite_and_unbounded_rows(mutation):
    from data_sources.providers.fred import FredAdapter

    payload = fixture()
    mutation(payload)
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        FredAdapter(http=FakeHttp([payload]), credentials=credentials(), fetched_at=lambda: NOW).fetch(request())


def test_fred_marks_explicitly_age_bounded_historical_observation_stale_and_unknown_unit():
    from data_sources.providers.fred import FredAdapter

    payload = fixture()
    payload["units"] = ""
    row = FredAdapter(http=FakeHttp([payload]), credentials=credentials(), fetched_at=lambda: NOW).fetch(request(max_age_days=30))[0]

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
    identifiers = ProviderRegistry(catalog).available_adapter_ids()
    assert identifiers == tuple(sorted(set(identifiers)))
    assert "fred" in identifiers
