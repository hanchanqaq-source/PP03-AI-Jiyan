from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import copy
import importlib
import json
import os

import pytest

from data_sources.budgets import BudgetGuard, BudgetPolicy
from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.models import AdapterDescriptor, ProviderValue
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderSchemaChanged, ProviderUnavailable
from data_sources.usage_store import UsageStore


NOW = datetime(2025, 7, 2, 12, tzinfo=timezone.utc)
SECRET = "paid-secret-value"


@dataclass(frozen=True)
class PaidCase:
    adapter_id: str
    module_name: str
    class_name: str
    env_name: str
    capability_id: str
    parameters: dict[str, object]
    payload: object
    expected_value: dict[str, object]
    expected_unit: str
    expected_frequency: str


CASES = (
    PaidCase(
        "fmp",
        "data_sources.providers.fmp",
        "FmpAdapter",
        "FMP_API_KEY",
        "stock_snapshot",
        {"symbol": "AAPL", "start_date": "2025-07-01", "end_date": "2025-07-01"},
        [{"symbol": "AAPL", "price": 123.45, "volume": 1000, "currency": "USD", "date": "2025-07-01"}],
        {"symbol": "AAPL", "price": Decimal("123.45"), "volume": Decimal("1000")},
        "USD",
        "snapshot",
    ),
    PaidCase(
        "massive",
        "data_sources.providers.massive",
        "MassiveAdapter",
        "MASSIVE_API_KEY",
        "stock_history",
        {"symbol": "AAPL", "start_date": "2025-07-01", "end_date": "2025-07-01"},
        {"status": "OK", "results": [{"T": "AAPL", "t": 1751328000000, "c": 123.45, "v": 1000}]},
        {"symbol": "AAPL", "close": Decimal("123.45"), "volume": Decimal("1000")},
        "unknown",
        "daily",
    ),
    PaidCase(
        "tiingo",
        "data_sources.providers.tiingo",
        "TiingoAdapter",
        "TIINGO_API_KEY",
        "stock_history",
        {"symbol": "AAPL", "start_date": "2025-07-01", "end_date": "2025-07-01"},
        [{"ticker": "AAPL", "date": "2025-07-01T00:00:00.000Z", "close": 123.45, "volume": 1000}],
        {"symbol": "AAPL", "close": Decimal("123.45"), "volume": Decimal("1000")},
        "unknown",
        "daily",
    ),
    PaidCase(
        "eodhd",
        "data_sources.providers.eodhd",
        "EodhdAdapter",
        "EODHD_API_KEY",
        "stock_history",
        {"symbol": "AAPL.US", "start_date": "2025-07-01", "end_date": "2025-07-01"},
        [{"code": "AAPL.US", "date": "2025-07-01", "close": 123.45, "volume": 1000, "currency": "USD"}],
        {"symbol": "AAPL.US", "close": Decimal("123.45"), "volume": Decimal("1000")},
        "USD",
        "daily",
    ),
    PaidCase(
        "databento",
        "data_sources.providers.databento",
        "DatabentoAdapter",
        "DATABENTO_API_KEY",
        "stock_history",
        {"symbol": "AAPL", "start_date": "2025-07-01", "end_date": "2025-07-01", "dataset": "XNAS.ITCH", "schema": "ohlcv-1d"},
        {"metadata": {"dataset": "XNAS.ITCH", "schema": "ohlcv-1d"}, "data": [{"symbol": "AAPL", "ts_event": "2025-07-01T00:00:00Z", "close": "123.45", "volume": 1000}]},
        {"symbol": "AAPL", "close": Decimal("123.45"), "volume": Decimal("1000")},
        "unknown",
        "daily",
    ),
)


class FakeHttp:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload
        self.calls: list[tuple[object, ...]] = []

    def get_json(self, url: str, *, headers=None, params=None):
        self.calls.append((url, headers, params))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class HostileDict(dict):
    pass


class HostileString(str):
    pass


class ExplodingBudget:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def authorize(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("budget must not run before the credential transport is safe")


def adapter_type(case: PaidCase):
    return getattr(importlib.import_module(case.module_name), case.class_name)


def credentials(case: PaidCase, *, configured: bool) -> MemoryCredentialStore:
    store = MemoryCredentialStore({case.adapter_id: (case.env_name,)})
    if configured:
        store.set(case.adapter_id, case.env_name, SECRET)
    return store


def make_adapter(case: PaidCase, *, configured: bool = True, http=None, budget_guard=None):
    return adapter_type(case)(
        http=http or FakeHttp(),
        credentials=credentials(case, configured=configured),
        budget_guard=budget_guard,
        fetched_at=lambda: NOW,
    )


def request_context(case: PaidCase, active=None, parameters: dict[str, object] | None = None):
    provider = active or make_adapter(case)
    request = ProviderRequest(case.capability_id, dict(case.parameters if parameters is None else parameters))
    return provider._context(request)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_adapter_mapping_descriptor_and_catalog_are_exact(case: PaidCase):
    active = make_adapter(case, configured=False)
    catalog_row = build_catalog({"sources": []}).adapter(case.adapter_id)

    assert type(active.descriptor) is AdapterDescriptor
    assert active.descriptor == catalog_row
    assert active.descriptor.adapter_id == case.adapter_id
    assert active.descriptor.credential_env_names == (case.env_name,)
    assert active.descriptor.billing_model.value == "paid_api"
    assert active.descriptor.catalog_status.value == "unconfigured"
    assert active.descriptor.default_enabled is False
    assert active.descriptor.capability_ids == (case.capability_id,)
    assert active.descriptor.configured_reference.startswith("https://")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_adapter_no_key_short_circuits_before_request_budget_and_transport(case: PaidCase):
    http, guard = FakeHttp(), ExplodingBudget()
    active = make_adapter(case, configured=False, http=http, budget_guard=guard)
    hostile = ProviderRequest(case.capability_id, HostileDict({"symbol": object()}))

    assert active.probe(case.capability_id) == {
        "status": "unconfigured",
        "connected": False,
        "health_failure": False,
    }
    with pytest.raises(ProviderUnavailable, match="unconfigured"):
        active.fetch(hostile)
    assert guard.calls == []
    assert http.calls == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_adapter_configured_path_fails_closed_before_budget_or_transport(case: PaidCase):
    http, guard = FakeHttp(case.payload), ExplodingBudget()
    active = make_adapter(case, http=http, budget_guard=guard)

    assert active.probe(case.capability_id) == {
        "status": "unsupported_credential_transport",
        "connected": False,
        "health_failure": False,
    }
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport") as captured:
        active.fetch(ProviderRequest(case.capability_id, case.parameters))
    assert SECRET not in str(captured.value)
    assert guard.calls == []
    assert http.calls == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_adapter_pure_request_builder_is_fixed_https_bounded_and_secret_free(case: PaidCase):
    active = make_adapter(case)
    context = request_context(case, active)
    url, params = active._request(context)
    serialized = json.dumps({"url": url, "params": params}, sort_keys=True)

    assert url.startswith("https://")
    assert SECRET not in serialized
    assert case.env_name not in serialized
    assert not any(name in serialized.lower() for name in ("api_key", "apikey", "token", "authorization"))
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        active._context(ProviderRequest(case.capability_id, HostileDict(case.parameters)))
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        active._context(ProviderRequest(case.capability_id, {**case.parameters, "url": "https://attacker.invalid/"}))


def test_paid_provider_request_builders_use_unique_fixed_provider_endpoints():
    urls = {
        case.adapter_id: make_adapter(case)._request(request_context(case))[0]
        for case in CASES
    }
    assert len(set(urls.values())) == len(CASES)
    assert all("attacker" not in url for url in urls.values())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_request_builder_rejects_string_subclass_identity_before_lookup(case: PaidCase):
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        make_adapter(case)._context(ProviderRequest(HostileString(case.capability_id), case.parameters))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_provider_unique_fixture_parser_preserves_date_unit_currency_source_and_coverage(case: PaidCase):
    active = make_adapter(case)
    rows = active._parse(case.payload, request_context(case, active), now=NOW, cached=False)

    assert len(rows) == 1
    row = rows[0]
    assert type(row) is ProviderValue
    assert row.value == case.expected_value
    assert row.adapter_id == case.adapter_id
    assert row.source_family_id == case.adapter_id
    assert row.capability_id == case.capability_id
    assert row.as_of_date is not None and row.as_of_date.isoformat() == "2025-07-01"
    assert row.unit == case.expected_unit
    assert row.frequency == case.expected_frequency
    assert row.data_status == "upstream_reported"
    assert row.source_metadata["source_reference"].startswith("https://")
    assert row.source_metadata["coverage"]
    assert row.source_metadata["plan_observation"] == "fixture_only_not_live_entitlement"
    assert row.source_metadata["requested_symbol"] == case.parameters["symbol"]
    assert row.source_metadata["requested_start_date"] == case.parameters["start_date"]
    assert row.source_metadata["requested_end_date"] == case.parameters["end_date"]
    assert SECRET not in repr(row)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
@pytest.mark.parametrize("payload", [None, {}, [], HostileDict(), {"unexpected": object()}])
def test_paid_provider_parsers_fail_closed_on_empty_schema_and_hostile_container(case: PaidCase, payload: object):
    active = make_adapter(case)
    with pytest.raises((ProviderSchemaChanged, ProviderUnavailable)):
        active._parse(payload, request_context(case, active), now=NOW, cached=False)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_provider_parser_rejects_future_and_unbounded_numeric_data(case: PaidCase):
    active = make_adapter(case)
    if case.adapter_id == "fmp":
        future = [{**case.payload[0], "date": "2099-01-01", "price": Decimal("1e999999")}]
    elif case.adapter_id == "massive":
        future = {"status": "OK", "results": [{**case.payload["results"][0], "t": 4070908800000, "c": Decimal("1e999999")}]}
    elif case.adapter_id == "tiingo":
        future = [{**case.payload[0], "date": "2099-01-01T00:00:00Z", "close": Decimal("1e999999")}]
    elif case.adapter_id == "eodhd":
        future = [{**case.payload[0], "date": "2099-01-01", "close": Decimal("1e999999")}]
    else:
        future = {**case.payload, "data": [{**case.payload["data"][0], "ts_event": "2099-01-01T00:00:00Z", "close": Decimal("1e999999")}]}

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        active._parse(future, request_context(case, active), now=NOW, cached=False)


def test_massive_parser_rejects_non_symbol_response_value():
    case = next(row for row in CASES if row.adapter_id == "massive")
    payload = {"status": "OK", "results": [{**case.payload["results"][0], "T": "not a symbol"}]}
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        make_adapter(case)._parse(payload, request_context(case), now=NOW, cached=False)


@pytest.mark.parametrize("adapter_id", ["tiingo", "databento"])
def test_iso_timestamp_parser_rejects_date_prefix_with_invalid_time(adapter_id: str):
    case = next(row for row in CASES if row.adapter_id == adapter_id)
    if adapter_id == "tiingo":
        payload = [{**case.payload[0], "date": "2025-07-01Tnot-a-time"}]
    else:
        payload = {**case.payload, "data": [{**case.payload["data"][0], "ts_event": "2025-07-01Tnot-a-time"}]}
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        make_adapter(case)._parse(payload, request_context(case), now=NOW, cached=False)


def _payload_rows(case: PaidCase, payload: object) -> list[dict[str, object]]:
    if case.adapter_id in {"fmp", "tiingo", "eodhd"}:
        return payload
    if case.adapter_id == "massive":
        return payload["results"]
    return payload["data"]


def _payload_with_rows(case: PaidCase, rows: list[dict[str, object]]) -> object:
    payload = copy.deepcopy(case.payload)
    if case.adapter_id in {"fmp", "tiingo", "eodhd"}:
        return rows
    if case.adapter_id == "massive":
        payload["results"] = rows
    else:
        payload["data"] = rows
    return payload


def _row_with_symbol(case: PaidCase, row: dict[str, object], symbol: str) -> dict[str, object]:
    key = {"fmp": "symbol", "massive": "T", "tiingo": "ticker", "eodhd": "code", "databento": "symbol"}[case.adapter_id]
    return {**row, key: symbol}


def _row_with_date(case: PaidCase, row: dict[str, object], day: str) -> dict[str, object]:
    if case.adapter_id == "massive":
        epoch = {"2025-06-30": 1751241600000, "2025-07-01": 1751328000000, "2025-07-02": 1751414400000}[day]
        return {**row, "t": epoch}
    if case.adapter_id == "tiingo":
        return {**row, "date": f"{day}T00:00:00.000Z"}
    if case.adapter_id == "databento":
        return {**row, "ts_event": f"{day}T00:00:00Z"}
    return {**row, "date": day}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_parser_rejects_wrong_valid_symbol_and_mixed_symbols(case: PaidCase):
    base = copy.deepcopy(_payload_rows(case, case.payload)[0])
    wrong = _row_with_symbol(case, base, "MSFT")
    payload = _payload_with_rows(case, [base, wrong])
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        make_adapter(case)._parse(payload, request_context(case), now=NOW, cached=False)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
@pytest.mark.parametrize("day", ["2025-06-30", "2025-07-02"])
def test_paid_parser_rejects_rows_outside_inclusive_requested_date_range(case: PaidCase, day: str):
    base = copy.deepcopy(_payload_rows(case, case.payload)[0])
    payload = _payload_with_rows(case, [_row_with_date(case, base, day)])
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        make_adapter(case)._parse(payload, request_context(case), now=NOW, cached=False)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_parser_accepts_requested_boundary_and_rejects_duplicate_observation_key(case: PaidCase):
    active = make_adapter(case)
    context = request_context(case, active)
    accepted = active._parse(case.payload, context, now=NOW, cached=False)
    assert [row.as_of_date.isoformat() for row in accepted] == ["2025-07-01"]

    base = copy.deepcopy(_payload_rows(case, case.payload)[0])
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        active._parse(_payload_with_rows(case, [base, copy.deepcopy(base)]), context, now=NOW, cached=False)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_parser_normalizes_unsorted_observations_to_ascending_chronology(case: PaidCase):
    parameters = {**case.parameters, "start_date": "2025-06-30", "end_date": "2025-07-01"}
    active = make_adapter(case)
    context = request_context(case, active, parameters)
    latest = copy.deepcopy(_payload_rows(case, case.payload)[0])
    earlier = _row_with_date(case, latest, "2025-06-30")
    rows = active._parse(_payload_with_rows(case, [latest, earlier]), context, now=NOW, cached=False)
    assert [row.as_of_date.isoformat() for row in rows] == ["2025-06-30", "2025-07-01"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_parser_requires_sealed_context_and_context_survives_request_mapping_mutation(case: PaidCase):
    active = make_adapter(case)
    parameters = dict(case.parameters)
    context = active._context(ProviderRequest(case.capability_id, parameters))
    parameters["symbol"] = "MSFT"
    parameters["start_date"] = "2025-06-30"

    rows = active._parse(case.payload, context, now=NOW, cached=False)
    expected_symbol = "AAPL.US" if case.adapter_id == "eodhd" else "AAPL"
    assert rows[0].source_metadata["requested_symbol"] == expected_symbol
    with pytest.raises(ProviderUnavailable, match="invalid_request_context"):
        active._parse(case.payload, object(), now=NOW, cached=False)

    object.__setattr__(context, "symbol", "MSFT")
    with pytest.raises(ProviderUnavailable, match="invalid_request_context"):
        active._parse(case.payload, context, now=NOW, cached=False)


@pytest.mark.parametrize(
    "metadata",
    [
        {"dataset": "GLBX.MDP3", "schema": "ohlcv-1d"},
        {"dataset": "XNAS.ITCH", "schema": "trades"},
        {"dataset": "XNAS.ITCH"},
        HostileDict({"dataset": "XNAS.ITCH", "schema": "ohlcv-1d"}),
    ],
)
def test_databento_parser_binds_exact_requested_dataset_and_schema(metadata: object):
    case = next(row for row in CASES if row.adapter_id == "databento")
    payload = {**case.payload, "metadata": metadata}
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        make_adapter(case)._parse(payload, request_context(case), now=NOW, cached=False)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_parser_rejects_naive_datetime_before_utc_comparison(case: PaidCase):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        make_adapter(case)._parse(
            case.payload,
            request_context(case),
            now=datetime(2025, 7, 2, 12),
            cached=False,
        )


@pytest.mark.parametrize(
    "row_fields",
    [
        {"dataset": "GLBX.MDP3"},
        {"schema": "trades"},
        {"dataset": "XNAS.ITCH", "schema": "trades"},
    ],
)
def test_databento_parser_rejects_conflicting_row_dataset_or_schema(row_fields: dict[str, str]):
    case = next(row for row in CASES if row.adapter_id == "databento")
    row = {**case.payload["data"][0], **row_fields}
    payload = {**case.payload, "data": [row]}
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        make_adapter(case)._parse(payload, request_context(case), now=NOW, cached=False)


def test_massive_parser_rejects_conflicting_response_level_ticker():
    case = next(row for row in CASES if row.adapter_id == "massive")
    payload = {**case.payload, "ticker": "MSFT"}
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        make_adapter(case)._parse(payload, request_context(case), now=NOW, cached=False)


def test_databento_parser_rejects_conflicting_metadata_instrument():
    case = next(row for row in CASES if row.adapter_id == "databento")
    payload = {**case.payload, "metadata": {**case.payload["metadata"], "symbol": "MSFT"}}
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        make_adapter(case)._parse(payload, request_context(case), now=NOW, cached=False)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_provider_cached_fixture_is_explicit_and_never_claimed_live(case: PaidCase):
    row = make_adapter(case)._parse(
        case.payload,
        request_context(case, parameters={**case.parameters, "max_age_days": 0}),
        now=NOW,
        cached=True,
    )[0]
    assert row.data_status == "cached"
    assert row.source_metadata["cache_status"] == "fixture_fallback"
    assert row.source_metadata["plan_observation"] == "fixture_only_not_live_entitlement"


def _policy(case: PaidCase, **overrides: object) -> BudgetPolicy:
    values: dict[str, object] = {
        "adapter_id": case.adapter_id,
        "enabled": True,
        "configured": True,
        "free_only": False,
        "daily_budget": Decimal("1.00"),
        "monthly_budget": Decimal("5.00"),
        "per_request_budget": Decimal("0.10"),
    }
    values.update(overrides)
    return BudgetPolicy(**values)


def _isolated_fixture_run(
    case: PaidCase,
    tmp_path,
    monkeypatch,
    *,
    policy: BudgetPolicy,
    entitlement: object = None,
    payload: object = None,
):
    """Test-only paid gate: no production entitlement authority or live transport."""
    descriptor = build_catalog({"sources": []}).adapter(case.adapter_id)
    store = UsageStore(tmp_path / case.adapter_id)
    guard = BudgetGuard(store, {case.adapter_id: policy}, trusted_adapters={case.adapter_id: descriptor})
    fake = FakeHttp(case.payload if payload is None else payload)
    active = make_adapter(case, http=fake)
    decision = guard.authorize(descriptor, estimated_cost=Decimal("0.01"), now=NOW)
    if not decision.allowed:
        return decision.reason, fake, store
    trusted_entitlement = (
        {"adapter_id": case.adapter_id, "plan_verified": True, "license_verified": True, "actual_cost": Decimal("0.01")}
        if entitlement is None
        else entitlement
    )
    if type(trusted_entitlement) is not dict or trusted_entitlement != {
        "adapter_id": case.adapter_id,
        "plan_verified": True,
        "license_verified": True,
        "actual_cost": Decimal("0.01"),
    }:
        guard.record(case.adapter_id, reservation_id=decision.reservation_id, actual_cost=Decimal("0"), request_count=0, status="plan_unavailable", units=Decimal("0"), now=NOW)
        return "plan_unavailable", fake, store
    if os.environ.get("VR_ALLOW_PAID_PROVIDER_TESTS") != "1":
        guard.record(case.adapter_id, reservation_id=decision.reservation_id, actual_cost=Decimal("0"), request_count=0, status="test_gate_blocked", units=Decimal("0"), now=NOW)
        return "paid_test_gate", fake, store
    context = request_context(case, active)
    url, params = active._request(context)
    try:
        response = fake.get_json(url, headers={"Accept": "application/json"}, params=params)
        rows = active._parse(response, context, now=NOW, cached=False)
    except Exception:
        guard.record(case.adapter_id, reservation_id=decision.reservation_id, actual_cost=Decimal("0.01"), request_count=1, status="fixture_failed", units=Decimal("0"), now=NOW)
        raise
    guard.record(case.adapter_id, reservation_id=decision.reservation_id, actual_cost=trusted_entitlement["actual_cost"], request_count=1, status="fixture_succeeded", units=Decimal(len(rows)), now=NOW)
    return rows, fake, store


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_fixture_harness_free_only_and_missing_budget_make_zero_calls(case: PaidCase, tmp_path, monkeypatch):
    monkeypatch.setenv("VR_ALLOW_PAID_PROVIDER_TESTS", "1")
    result, http, store = _isolated_fixture_run(case, tmp_path / "free", monkeypatch, policy=_policy(case, free_only=True))
    assert result == "free_only" and http.calls == [] and store.records(now=NOW) == ()

    result, http, store = _isolated_fixture_run(case, tmp_path / "budget", monkeypatch, policy=_policy(case, daily_budget=Decimal("0")))
    assert result == "daily_budget_not_configured" and http.calls == [] and store.records(now=NOW) == ()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"enabled": False}, "disabled"),
        ({"configured": False}, "unconfigured"),
        ({"monthly_budget": Decimal("0")}, "monthly_budget_not_configured"),
        ({"per_request_budget": Decimal("0")}, "per_request_budget_not_configured"),
    ],
)
def test_paid_fixture_harness_all_config_and_budget_gates_are_bounded_zero_call(case: PaidCase, tmp_path, monkeypatch, overrides, reason):
    monkeypatch.setenv("VR_ALLOW_PAID_PROVIDER_TESTS", "1")
    result, http, store = _isolated_fixture_run(case, tmp_path, monkeypatch, policy=_policy(case, **overrides))
    assert result == reason and http.calls == [] and store.records(now=NOW) == ()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_fixture_harness_untrusted_or_missing_entitlement_is_plan_unavailable_zero_call(case: PaidCase, tmp_path, monkeypatch):
    monkeypatch.setenv("VR_ALLOW_PAID_PROVIDER_TESTS", "1")
    result, http, store = _isolated_fixture_run(case, tmp_path, monkeypatch, policy=_policy(case), entitlement={})
    assert result == "plan_unavailable" and http.calls == []
    assert all(record.recorded_at is not None and record.request_count == 0 for record in store.records(now=NOW))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
def test_paid_fixture_harness_requires_exact_test_gate_and_reconciles_usage_once(case: PaidCase, tmp_path, monkeypatch):
    monkeypatch.setenv("VR_ALLOW_PAID_PROVIDER_TESTS", "true")
    blocked, http, store = _isolated_fixture_run(case, tmp_path / "blocked", monkeypatch, policy=_policy(case))
    assert blocked == "paid_test_gate" and http.calls == []
    blocked_summary = store.summary(case.adapter_id, now=NOW)
    assert blocked_summary.daily_request_count == 0
    assert all(record.recorded_at is not None for record in store.records(now=NOW))

    monkeypatch.setenv("VR_ALLOW_PAID_PROVIDER_TESTS", "1")
    rows, http, store = _isolated_fixture_run(case, tmp_path / "allowed", monkeypatch, policy=_policy(case))
    assert len(rows) == 1 and len(http.calls) == 1
    summary = store.summary(case.adapter_id, now=NOW)
    assert all(record.recorded_at is not None for record in store.records(now=NOW))
    assert summary.daily_request_count == 1
    assert summary.daily_cost == Decimal("0.01")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.adapter_id)
@pytest.mark.parametrize("payload", [TimeoutError("fixture timeout"), {"malformed": True}])
def test_paid_fixture_harness_reconciles_authorized_transport_and_parser_failures(case: PaidCase, tmp_path, monkeypatch, payload):
    monkeypatch.setenv("VR_ALLOW_PAID_PROVIDER_TESTS", "1")
    with pytest.raises(Exception):
        _isolated_fixture_run(case, tmp_path, monkeypatch, policy=_policy(case), payload=payload)
    records = UsageStore(tmp_path / case.adapter_id).records(now=NOW)
    assert len(records) == 1
    assert records[0].recorded_at is not None
    assert records[0].request_count == 1
    assert records[0].actual_cost == Decimal("0.01")
