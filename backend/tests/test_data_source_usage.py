from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from data_sources.usage_store import (
    UsageBudgetExceeded,
    UsageConflictError,
    UsageRecord,
    UsageStore,
    UsageStoreError,
    UsageSummary,
    UsageValidationError,
)


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path) -> UsageStore:
    return UsageStore(tmp_path / "data", lock_timeout_seconds=0.05)


def _reserve(
    store: UsageStore,
    *,
    reservation_id: str = "reservation-1",
    estimated_cost: Decimal = Decimal("0.10"),
    now: datetime = NOW,
):
    return store.reserve(
        "paid-test",
        estimated_cost=estimated_cost,
        daily_budget=Decimal("1.00"),
        monthly_budget=Decimal("5.00"),
        now=now,
        reservation_id=reservation_id,
    )


def test_usage_round_trip_serializes_exact_decimal_strings_and_no_request_data(store: UsageStore):
    _reserve(store)
    record = store.reconcile(
        "paid-test",
        reservation_id="reservation-1",
        actual_cost=Decimal("0.0700"),
        request_count=1,
        status="succeeded",
        units=Decimal("3.5000"),
        now=NOW + timedelta(seconds=1),
    )

    assert record.to_dict() == {
        "reservation_id": "reservation-1",
        "adapter_id": "paid-test",
        "authorized_at": "2026-08-19T12:00:00Z",
        "recorded_at": "2026-08-19T12:00:01Z",
        "estimated_cost": "0.10",
        "actual_cost": "0.0700",
        "request_count": 1,
        "status": "succeeded",
        "units": "3.5000",
    }
    raw = store.path.read_text(encoding="utf-8")
    assert "0.0700" in raw
    for forbidden in ("http://", "https://", "token", "cookie", "account", "holding", "secret"):
        assert forbidden not in raw.lower()


@pytest.mark.parametrize("reservation_id", ["secret-value", "api-token", "cookie-id", "bearer-id"])
def test_reservation_identifier_cannot_smuggle_credential_markers(
    store: UsageStore, reservation_id: str
):
    with pytest.raises(UsageValidationError):
        _reserve(store, reservation_id=reservation_id)

    assert not store.path.exists()


def test_restart_loads_reservation_and_reconciliation_exactly(tmp_path):
    first = UsageStore(tmp_path / "data")
    _reserve(first)
    first.reconcile(
        "paid-test",
        reservation_id="reservation-1",
        actual_cost=Decimal("0.11"),
        request_count=1,
        status="succeeded",
        units=Decimal("2"),
        now=NOW + timedelta(seconds=1),
    )

    restarted = UsageStore(tmp_path / "data")
    records = restarted.records(now=NOW + timedelta(seconds=1))

    assert len(records) == 1
    assert records[0].actual_cost == Decimal("0.11")
    assert restarted.summary("paid-test", now=NOW + timedelta(seconds=1)).monthly_cost == Decimal("0.11")


def test_usage_record_constructor_enforces_decimal_and_timestamp_invariants():
    with pytest.raises(UsageValidationError):
        UsageRecord(
            reservation_id="direct-record",
            adapter_id="paid-test",
            authorized_at=NOW,
            recorded_at=NOW,
            estimated_cost=Decimal("0.10"),
            actual_cost=0.10,
            request_count=1,
            status="succeeded",
            units=Decimal("1"),
        )

    with pytest.raises(UsageValidationError):
        UsageRecord(
            reservation_id="direct-record",
            adapter_id="paid-test",
            authorized_at=NOW,
            recorded_at=NOW - timedelta(seconds=1),
            estimated_cost=Decimal("0.10"),
            actual_cost=Decimal("0.10"),
            request_count=1,
            status="succeeded",
            units=Decimal("1"),
        )


def test_duplicate_reservation_retry_is_idempotent_but_conflicting_retry_fails(store: UsageStore):
    first = _reserve(store)
    second = _reserve(store)

    assert first == second
    assert len(store.records(now=NOW)) == 1
    with pytest.raises(UsageConflictError):
        _reserve(store, estimated_cost=Decimal("0.11"))


def test_reservation_retry_later_in_period_preserves_original_timestamp_and_single_charge(store: UsageStore):
    first = _reserve(store, reservation_id="retry-one")
    retried = _reserve(
        store,
        reservation_id="retry-one",
        now=NOW + timedelta(seconds=1),
    )

    assert retried == first
    assert retried.authorized_at == NOW
    assert len(store.records(now=NOW + timedelta(seconds=1))) == 1


def test_reservation_retry_across_utc_day_and_month_returns_original_without_moving_period(store: UsageStore):
    original_time = datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)
    first = _reserve(store, reservation_id="month-boundary", now=original_time)
    retried = _reserve(
        store,
        reservation_id="month-boundary",
        now=datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc),
    )

    assert retried == first
    assert retried.authorized_at == original_time
    september = store.summary(
        "paid-test", now=datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc)
    )
    assert september.monthly_cost == Decimal("0")
    assert len(store.records(now=datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc))) == 1


def test_reservation_retry_with_wrong_adapter_or_estimate_remains_a_conflict(store: UsageStore):
    _reserve(store, reservation_id="immutable-reservation")

    with pytest.raises(UsageConflictError):
        store.reserve(
            "other-adapter",
            estimated_cost=Decimal("0.10"),
            daily_budget=Decimal("1.00"),
            monthly_budget=Decimal("5.00"),
            now=NOW + timedelta(seconds=1),
            reservation_id="immutable-reservation",
        )
    with pytest.raises(UsageConflictError):
        _reserve(
            store,
            reservation_id="immutable-reservation",
            estimated_cost=Decimal("0.11"),
            now=NOW + timedelta(seconds=1),
        )


def test_duplicate_reconciliation_is_idempotent_but_conflicting_retry_fails(store: UsageStore):
    _reserve(store)
    arguments = {
        "reservation_id": "reservation-1",
        "actual_cost": Decimal("0.10"),
        "request_count": 1,
        "status": "failed",
        "units": Decimal("0"),
        "now": NOW + timedelta(seconds=1),
    }
    first = store.reconcile("paid-test", **arguments)
    second = store.reconcile("paid-test", **arguments)

    assert first == second
    assert len(store.records(now=NOW + timedelta(seconds=1))) == 1
    with pytest.raises(UsageConflictError):
        store.reconcile("paid-test", **{**arguments, "actual_cost": Decimal("0.09")})


@pytest.mark.parametrize("value", [True, 1, 1.0, Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01"), Decimal("0.000000001")])
def test_reserve_rejects_non_decimal_nonfinite_negative_or_excess_precision(store: UsageStore, value: object):
    with pytest.raises(UsageValidationError):
        store.reserve(
            "paid-test",
            estimated_cost=value,
            daily_budget=Decimal("1.00"),
            monthly_budget=Decimal("5.00"),
            now=NOW,
        )


@pytest.mark.parametrize("value", [True, -1, 1.0, Decimal("1")])
def test_reconcile_rejects_invalid_request_count(store: UsageStore, value: object):
    _reserve(store)
    with pytest.raises(UsageValidationError):
        store.reconcile(
            "paid-test",
            reservation_id="reservation-1",
            actual_cost=Decimal("0.10"),
            request_count=value,
            status="succeeded",
            units=Decimal("1"),
            now=NOW + timedelta(seconds=1),
        )


@pytest.mark.parametrize("field,value", [("status", "Bearer_secret"), ("status", ""), ("units", 1.0), ("units", Decimal("-1"))])
def test_reconcile_rejects_unsafe_status_or_units(store: UsageStore, field: str, value: object):
    _reserve(store)
    arguments: dict[str, object] = {
        "reservation_id": "reservation-1",
        "actual_cost": Decimal("0.10"),
        "request_count": 1,
        "status": "succeeded",
        "units": Decimal("1"),
        "now": NOW + timedelta(seconds=1),
    }
    arguments[field] = value

    with pytest.raises(UsageValidationError):
        store.reconcile("paid-test", **arguments)


@pytest.mark.parametrize("now", [datetime(2026, 8, 19, 12, 0), "2026-08-19T12:00:00Z", None])
def test_operations_require_timezone_aware_datetime(store: UsageStore, now: object):
    with pytest.raises(UsageValidationError):
        store.records(now=now)


def test_reconciliation_before_authorization_timestamp_fails_closed(store: UsageStore):
    _reserve(store)
    with pytest.raises(UsageValidationError):
        store.reconcile(
            "paid-test",
            reservation_id="reservation-1",
            actual_cost=Decimal("0.10"),
            request_count=1,
            status="succeeded",
            units=Decimal("1"),
            now=NOW - timedelta(seconds=1),
        )


def test_future_ledger_record_relative_to_authorization_time_fails_closed(store: UsageStore):
    _reserve(store, now=NOW + timedelta(minutes=1))

    with pytest.raises(UsageValidationError):
        store.summary("paid-test", now=NOW)


def test_old_period_records_do_not_consume_new_period_budget(store: UsageStore):
    _reserve(store, estimated_cost=Decimal("1.00"), now=NOW)
    later = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    record = store.reserve(
        "paid-test",
        estimated_cost=Decimal("1.00"),
        daily_budget=Decimal("1.00"),
        monthly_budget=Decimal("1.00"),
        now=later,
        reservation_id="new-month",
    )

    assert record.reservation_id == "new-month"


def test_actual_cost_above_estimate_is_recorded_and_blocks_future_authorization(store: UsageStore):
    _reserve(store, estimated_cost=Decimal("0.10"))
    store.reconcile(
        "paid-test",
        reservation_id="reservation-1",
        actual_cost=Decimal("1.10"),
        request_count=1,
        status="succeeded",
        units=Decimal("1"),
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(UsageBudgetExceeded) as raised:
        store.reserve(
            "paid-test",
            estimated_cost=Decimal("0.01"),
            daily_budget=Decimal("1.00"),
            monthly_budget=Decimal("5.00"),
            now=NOW + timedelta(seconds=2),
        )

    assert raised.value.reason == "daily_budget_exhausted"


@pytest.mark.parametrize("payload", [b"{not json", b"[]", b'{"version":999,"records":[]}'])
def test_malformed_or_unknown_version_ledger_fails_closed(store: UsageStore, payload: bytes):
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(payload)

    with pytest.raises(UsageStoreError):
        store.records(now=NOW)


def test_oversized_ledger_fails_closed_before_json_parse(tmp_path):
    store = UsageStore(tmp_path / "data", max_ledger_bytes=64)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(b"{" + b"x" * 64 + b"}")

    with pytest.raises(UsageStoreError, match="too large"):
        store.records(now=NOW)


@pytest.mark.parametrize("max_ledger_bytes", [64 * 1024 * 1024 + 1, 10**1000])
def test_configurable_ledger_size_has_a_hard_upper_bound(tmp_path, max_ledger_bytes: int):
    with pytest.raises(UsageValidationError):
        UsageStore(tmp_path / "data", max_ledger_bytes=max_ledger_bytes)


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1E+1000000"),
        Decimal("0E+1000000"),
        Decimal("-0E+1000000"),
        Decimal("1000000000000.01"),
        Decimal("9" * 29),
    ],
)
def test_usage_decimal_rejects_pathological_or_above_ceiling_magnitude(store: UsageStore, value: Decimal):
    with pytest.raises(UsageValidationError):
        store.reserve(
            "paid-test",
            estimated_cost=value,
            daily_budget=Decimal("1000000000000"),
            monthly_budget=Decimal("1000000000000"),
            now=NOW,
        )


@pytest.mark.parametrize(
    "value",
    [Decimal("0E+12"), Decimal("-0E+12"), Decimal("1E+12"), Decimal("1E-8")],
)
def test_usage_decimal_accepts_explicit_exponent_boundaries(store: UsageStore, value: Decimal):
    record = store.reserve(
        "paid-test",
        estimated_cost=value,
        daily_budget=Decimal("1000000000000"),
        monthly_budget=Decimal("1000000000000"),
        now=NOW,
    )

    assert record.estimated_cost.as_tuple() == value.as_tuple()


def test_direct_usage_record_and_summary_reject_extreme_zero_exponents():
    with pytest.raises(UsageValidationError):
        UsageRecord(
            reservation_id="extreme-record",
            adapter_id="paid-test",
            authorized_at=NOW,
            recorded_at=None,
            estimated_cost=Decimal("0E+1000000"),
            actual_cost=None,
            request_count=0,
            status="reserved",
            units=Decimal("0"),
        )

    with pytest.raises(UsageValidationError):
        UsageSummary(
            adapter_id="paid-test",
            day="2026-08-19",
            month="2026-08",
            daily_cost=Decimal("0E+1000000"),
            monthly_cost=Decimal("0"),
            daily_request_count=0,
            monthly_request_count=0,
            daily_units=Decimal("0"),
            monthly_units=Decimal("0"),
        )


def test_extreme_exponent_ledger_is_rejected_before_fixed_point_formatting(
    store: UsageStore, monkeypatch: pytest.MonkeyPatch
):
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "reservation_id": "extreme-ledger",
                        "adapter_id": "paid-test",
                        "authorized_at": "2026-08-19T12:00:00Z",
                        "recorded_at": None,
                        "estimated_cost": "0E+1000000",
                        "actual_cost": None,
                        "request_count": 0,
                        "status": "reserved",
                        "units": "0",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def forbidden_format(_value):
        raise AssertionError("invalid exponent reached fixed-point formatting")

    monkeypatch.setattr("data_sources.usage_store._decimal_text", forbidden_format)

    with pytest.raises(UsageStoreError):
        store.records(now=NOW)


def test_request_count_has_a_hard_ceiling_before_serialization(store: UsageStore):
    _reserve(store)

    with pytest.raises(UsageValidationError):
        store.reconcile(
            "paid-test",
            reservation_id="reservation-1",
            actual_cost=Decimal("0.10"),
            request_count=10**1000,
            status="succeeded",
            units=Decimal("1"),
            now=NOW + timedelta(seconds=1),
        )


def test_ledger_record_count_has_a_hard_ceiling_before_record_parsing(tmp_path):
    store = UsageStore(tmp_path / "data", max_ledger_bytes=64 * 1024 * 1024)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps({"version": 1, "records": [{}] * 100_001}),
        encoding="utf-8",
    )

    with pytest.raises(UsageStoreError, match="too many records"):
        store.records(now=NOW)


def test_pathological_json_integer_is_wrapped_as_closed_ledger_failure(store: UsageStore):
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        '{"version":1,"records":[{"request_count":' + "9" * 5000 + "}]}",
        encoding="utf-8",
    )

    with pytest.raises(UsageStoreError, match="corrupt"):
        store.records(now=NOW)


def test_unknown_or_secret_bearing_fields_in_ledger_fail_closed(store: UsageStore):
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps({"version": 1, "records": [{"api_token": "secret-value"}]}),
        encoding="utf-8",
    )

    with pytest.raises(UsageStoreError):
        store.records(now=NOW)
    assert "secret-value" not in repr(store)


def test_atomic_replace_failure_preserves_existing_ledger_and_removes_temp(store: UsageStore, monkeypatch: pytest.MonkeyPatch):
    _reserve(store)
    original = store.path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("data_sources.usage_store.os.replace", fail_replace)
    with pytest.raises(UsageStoreError):
        _reserve(store, reservation_id="reservation-2")

    assert store.path.read_bytes() == original
    assert list(store.path.parent.glob(".usage.*.tmp")) == []


@pytest.mark.parametrize("mode,attributes", [(stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)])
def test_reparse_or_dangling_link_ancestor_is_rejected_before_open(
    tmp_path, monkeypatch: pytest.MonkeyPatch, mode: int, attributes: int
):
    root = tmp_path / "data"
    store = UsageStore(root)
    root.mkdir()
    unsafe_ancestor = store.root
    original_lstat = os.lstat
    opened: list[Path] = []

    def fake_lstat(path):
        candidate = Path(path)
        if candidate == unsafe_ancestor:
            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
        return original_lstat(path)

    original_open = Path.open

    def record_open(path, *args, **kwargs):
        opened.append(Path(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("data_sources.usage_store.os.lstat", fake_lstat)
    monkeypatch.setattr("data_sources.usage_store.Path.open", record_open)

    with pytest.raises(UsageStoreError):
        store.records(now=NOW)

    assert opened == []


def test_lock_timeout_fails_closed_before_writing(store: UsageStore, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(store, "_try_lock_file", lambda _handle: False)

    with pytest.raises(UsageStoreError, match="lock"):
        _reserve(store)

    assert not store.path.exists()


def test_process_lock_preserves_preexisting_matching_and_foreign_temp_files(tmp_path):
    store = UsageStore(tmp_path / "data")
    store.root.mkdir(parents=True)
    stale = store.root / ".usage.abcdef12.tmp"
    recent = store.root / ".usage.recent12.tmp"
    foreign = store.root / ".config.abcdef12.tmp"
    for candidate in (stale, recent, foreign):
        candidate.write_text("temporary", encoding="utf-8")
    old = datetime.now(tz=timezone.utc).timestamp() - (25 * 60 * 60)
    os.utime(stale, (old, old))

    store.records(now=NOW)

    assert stale.exists()
    assert recent.exists()
    assert foreign.exists()


def test_stale_temp_cleanup_does_not_unlink_reparse_candidate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    store = UsageStore(tmp_path / "data")
    store.root.mkdir(parents=True)
    candidate = store.root / ".usage.attacker.tmp"
    candidate.write_text("not trusted", encoding="utf-8")
    old = datetime.now(tz=timezone.utc).timestamp() - (25 * 60 * 60)
    os.utime(candidate, (old, old))
    original_lstat = os.lstat

    def fake_lstat(path):
        metadata = original_lstat(path)
        if Path(path) == candidate:
            return SimpleNamespace(
                st_mode=stat.S_IFREG,
                st_file_attributes=0x400,
                st_mtime=metadata.st_mtime,
            )
        return metadata

    monkeypatch.setattr("data_sources.usage_store.os.lstat", fake_lstat)

    store.records(now=NOW)

    assert candidate.exists()


def test_process_lock_does_not_scan_for_preexisting_temp_files(tmp_path, monkeypatch: pytest.MonkeyPatch):
    store = UsageStore(tmp_path / "data")

    def forbidden_scan(_path):
        raise AssertionError("usage lock must not scan unknown temp files")

    monkeypatch.setattr("data_sources.usage_store.Path.iterdir", forbidden_scan)

    store.records(now=NOW)


def test_process_lock_never_unlinks_old_matching_replacement_target(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    store = UsageStore(tmp_path / "data")
    store.root.mkdir(parents=True)
    candidate = store.root / ".usage.replaced.tmp"
    candidate.write_text("must remain", encoding="utf-8")
    old = datetime.now(tz=timezone.utc).timestamp() - (25 * 60 * 60)
    os.utime(candidate, (old, old))
    original_unlink = Path.unlink
    unlinked: list[Path] = []

    def record_unlink(path, *args, **kwargs):
        unlinked.append(Path(path))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr("data_sources.usage_store.Path.unlink", record_unlink)

    store.records(now=NOW)

    assert unlinked == []
    assert candidate.read_text(encoding="utf-8") == "must remain"


@pytest.mark.parametrize("timeout", [True, False, "1", None, 0, -1, 0.0, math.nan, math.inf, -math.inf, 10**1000])
def test_invalid_lock_timeout_is_rejected(tmp_path, timeout: object):
    with pytest.raises(UsageValidationError):
        UsageStore(tmp_path / "data", lock_timeout_seconds=timeout)


def test_two_process_authorizations_cannot_overspend(tmp_path):
    root = tmp_path / "data"
    backend_root = Path(__file__).parents[1]
    worker = r'''
from datetime import datetime, timezone
from decimal import Decimal
from data_sources.budgets import BudgetGuard, BudgetPolicy
from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, SourceRole
from data_sources.usage_store import UsageStore
import sys
policy = BudgetPolicy(
    adapter_id="paid-test", enabled=True, configured=True, free_only=False,
    daily_budget=Decimal("1.00"), monthly_budget=Decimal("1.00"), per_request_budget=Decimal("1.00"),
)
adapter = AdapterDescriptor(
    adapter_id="paid-test", adapter_name="test", source_family_id="test-family",
    provider_type="http_client", source_roles=(SourceRole.MARKET_DATA,),
    capability_ids=("test",), billing_model=BillingModel.PAID_API,
    auth_type="api_key", credential_env_names=("TEST_API_KEY",), default_enabled=False,
    license_note="fixture", usage_note="fixture", data_delay="fixture",
    quota_policy="fixture", cost_policy="fixture", configured_reference="https://example.test/",
    current_provider_priority=10, catalog_status=CatalogStatus.UNCONFIGURED,
)
guard = BudgetGuard(
    UsageStore(sys.argv[1], lock_timeout_seconds=5),
    {"paid-test": policy}, trusted_adapters={"paid-test": adapter},
)
decision = guard.authorize(
    adapter, estimated_cost=Decimal("0.60"),
    now=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc), reservation_id=sys.argv[2],
)
print(decision.reason)
'''
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker, str(root), reservation_id],
            cwd=backend_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for reservation_id in ("process-a", "process-b")
    ]
    results = [process.communicate(timeout=20) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    reasons = sorted(stdout.strip() for stdout, _stderr in results)
    assert reasons == ["authorized", "daily_budget_exhausted"]
    records = UsageStore(root).records(now=NOW)
    assert len(records) == 1
    assert records[0].estimated_cost == Decimal("0.60")
