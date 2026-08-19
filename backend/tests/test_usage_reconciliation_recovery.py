from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import threading

import pytest
from fastapi.testclient import TestClient

import app as app_module
import data_sources.api as api_module
import data_sources.usage_store as usage_store_module
from data_sources.catalog import DataSourceCatalog, build_catalog
from data_sources.config_store import DataSourceConfigStore
from data_sources.credentials import MemoryCredentialStore
from data_sources.service import DataSourceService, DataSourceUnavailable
from data_sources.usage_store import (
    UsageBudgetExceeded,
    UsageStore,
    UsageStoreError,
    UsageValidationError,
)


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
NEXT_DAY = NOW + timedelta(days=1)
_INTENT_FIELDS = {
    "operation",
    "guard_scope",
    "adapter_id",
    "reservation_id",
    "authorized_at",
    "recorded_at",
    "estimated_cost",
    "actual_cost",
    "request_count",
    "status",
    "units",
}


class _NoHealth:
    def list_sources(self):
        return []


class _Probe:
    def __init__(self, descriptor, outcomes: list[object]) -> None:
        self.descriptor = descriptor
        self._outcomes = list(outcomes)
        self.calls: list[str] = []

    def probe(self, capability_id: str):
        self.calls.append(capability_id)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _BlockingProbe:
    def __init__(self, descriptor, entered: threading.Event, release: threading.Event) -> None:
        self.descriptor = descriptor
        self._entered = entered
        self._release = release
        self.calls: list[str] = []

    def probe(self, capability_id: str):
        self.calls.append(capability_id)
        self._entered.set()
        if not self._release.wait(timeout=5):
            raise AssertionError("blocked probe was not released")
        return _success()


class _Registry:
    def __init__(self, provider: _Probe) -> None:
        self.provider = provider
        self.available_calls = 0
        self.adapter_calls = 0

    def available_adapter_ids(self):
        self.available_calls += 1
        return (self.provider.descriptor.adapter_id,)

    def adapter(self, adapter_id: str):
        self.adapter_calls += 1
        assert adapter_id == self.provider.descriptor.adapter_id
        return self.provider


class _ExplodingDependency:
    def __getattribute__(self, name: str):
        if name.startswith("__"):
            return object.__getattribute__(self, name)
        raise AssertionError(f"recovery touched unrelated dependency: {name}")


class _HostileText(str):
    called = False

    def _explode(self, *_args, **_kwargs):
        self.called = True
        raise AssertionError("hostile text method executed")

    __hash__ = __eq__ = __ne__ = _explode


def _credential_scope(catalog: DataSourceCatalog) -> dict[str, tuple[str, ...]]:
    return {
        adapter.adapter_id: tuple(adapter.credential_env_names)
        for adapter in catalog.adapters
        if adapter.credential_env_names
    }


def _success(returned_items: int = 1) -> dict[str, object]:
    return {
        "status": "success",
        "connected": True,
        "health_failure": False,
        "returned_items": returned_items,
        "data_as_of_date": "2026-08-19",
        "final_reference": "https://api.worldbank.org/v2/",
    }


def _service(
    root,
    outcomes: list[object],
    *,
    daily_limit: int | None = None,
    now_factory=lambda: NOW,
    usage_store: UsageStore | None = None,
    config_store: object | None = None,
    credential_store: object | None = None,
    provider_registry: object | None = None,
):
    catalog = build_catalog({"sources": []})
    config = config_store or DataSourceConfigStore(root / "config", catalog=catalog)
    if daily_limit is not None:
        config.update_adapter("world-bank", {"daily_request_limit": daily_limit})
    usage = usage_store or UsageStore(root / "usage")
    provider = _Probe(catalog.adapter("world-bank"), outcomes)
    registry = provider_registry or _Registry(provider)
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: _NoHealth(),
        config_store=config,
        credential_store=(
            credential_store
            if credential_store is not None
            else MemoryCredentialStore(_credential_scope(catalog))
        ),
        usage_store=usage,
        provider_registry=registry,
        now_factory=now_factory,
    )
    return service, config, usage, provider, registry


def _leave_durable_settlement(root, monkeypatch, *, returned_items: int = 3, daily_limit=None):
    service, config, usage, provider, registry = _service(
        root, [_success(returned_items)], daily_limit=daily_limit,
    )

    def fail_before_write(*_args, **_kwargs):
        raise UsageStoreError("private-pre-write-marker")

    monkeypatch.setattr(usage, "reconcile", fail_before_write)
    with pytest.raises(DataSourceUnavailable, match="usage_store_unavailable"):
        service.validate_adapter("world-bank")
    return service, config, usage, provider, registry


def _intent_document(record, **overrides: object) -> dict[str, object]:
    intent = {
        "operation": "provider_validation",
        "guard_scope": "server_authorization_v1",
        "adapter_id": record.adapter_id,
        "reservation_id": record.reservation_id,
        "authorized_at": record.authorized_at.isoformat().replace("+00:00", "Z"),
        "recorded_at": NOW.isoformat().replace("+00:00", "Z"),
        "estimated_cost": "0",
        "actual_cost": "0",
        "request_count": 1,
        "status": "validation_success",
        "units": "1",
    }
    intent.update(overrides)
    return {"version": 1, "service": "pp03-data-sources", "intents": [intent]}


def test_prewrite_failure_persists_exact_ledger_settlement_and_restart_recovers_without_probe(
    tmp_path, monkeypatch,
):
    _first, _config, usage, provider, _registry = _leave_durable_settlement(
        tmp_path, monkeypatch, returned_items=3,
    )
    staged = usage.records(now=NOW)

    assert len(staged) == 1
    assert staged[0].actual_cost is None
    assert staged[0].request_count == 1
    assert staged[0].status == "validation_success"
    assert staged[0].units == Decimal("3")
    assert staged[0].recorded_at == NOW
    assert not usage.reconciliation_path.exists()
    assert not any(
        marker in usage.path.read_text(encoding="utf-8").lower()
        for marker in ("credential", "url", "query", "private-pre-write-marker")
    )

    restarted, _config, restarted_usage, restarted_provider, _registry = _service(
        tmp_path, [AssertionError("recovery must not probe")],
    )
    diagnostic = restarted.recover_usage_reconciliation()
    records = restarted_usage.records(now=NOW)

    assert diagnostic == {
        "status": "closed", "attempted": 1, "recovered": 1, "retained": 0,
    }
    assert provider.calls == ["macro_indicator"]
    assert restarted_provider.calls == []
    assert len(records) == 1
    assert records[0].actual_cost == Decimal("0")
    assert records[0].request_count == 1
    assert records[0].status == "validation_success"
    assert records[0].units == Decimal("3")
    assert not restarted_usage.reconciliation_path.exists()


def test_postwrite_ambiguous_failure_restart_retry_is_idempotent(tmp_path, monkeypatch):
    service, _config, usage, provider, _registry = _service(tmp_path, [_success(2)])
    original = usage.reconcile

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise UsageStoreError("private-post-write-marker")

    monkeypatch.setattr(usage, "reconcile", write_then_fail)
    with pytest.raises(DataSourceUnavailable, match="usage_store_unavailable"):
        service.validate_adapter("world-bank")

    before = UsageStore(tmp_path / "usage").records(now=NOW)
    assert len(before) == 1
    assert before[0].actual_cost == Decimal("0")
    assert not usage.reconciliation_path.exists()

    restarted, _config, restarted_usage, restarted_provider, _registry = _service(
        tmp_path, [AssertionError("recovery must not probe")],
    )
    diagnostic = restarted.recover_usage_reconciliation()
    after = restarted_usage.records(now=NOW)

    assert diagnostic["status"] == "closed"
    assert diagnostic["attempted"] == 0
    assert after == before
    assert len(after) == 1
    assert provider.calls == ["macro_indicator"]
    assert restarted_provider.calls == []


def test_ledger_settlement_stage_failure_never_reconciles_or_reports_probe_success(
    tmp_path, monkeypatch,
):
    service, _config, usage, provider, _registry = _service(tmp_path, [_success()])
    stage_calls = 0
    reconcile_calls = 0

    def fail_stage(*_args, **_kwargs):
        nonlocal stage_calls
        stage_calls += 1
        raise UsageStoreError("private-intent-write-marker")

    def forbidden_reconcile(*_args, **_kwargs):
        nonlocal reconcile_calls
        reconcile_calls += 1
        raise UsageStoreError("reconcile must follow durable intent")

    monkeypatch.setattr(usage, "_stage_validation_reconciliation", fail_stage)
    monkeypatch.setattr(usage, "reconcile", forbidden_reconcile)

    with pytest.raises(DataSourceUnavailable, match="usage_store_unavailable"):
        service.validate_adapter("world-bank")

    records = usage.records(now=NOW)
    assert provider.calls == ["macro_indicator"]
    assert stage_calls == 1
    assert reconcile_calls == 0
    assert len(records) == 1
    assert records[0].actual_cost is None
    assert records[0].status == "reserved"


def test_limit_one_stays_closed_until_recovery_then_continues_on_next_utc_day(
    tmp_path, monkeypatch,
):
    first, config, _usage, first_provider, _registry = _leave_durable_settlement(
        tmp_path, monkeypatch, daily_limit=1,
    )
    assert first_provider.calls == ["macro_indicator"]

    clock = [NOW]
    restarted_usage = UsageStore(tmp_path / "usage")
    restarted, _config, _usage, provider, _registry = _service(
        tmp_path,
        [_success()],
        daily_limit=None,
        now_factory=lambda: clock[0],
        usage_store=restarted_usage,
        config_store=config,
    )
    original_atomic_write = restarted_usage._atomic_write
    monkeypatch.setattr(
        restarted_usage,
        "_atomic_write",
        lambda _records: (_ for _ in ()).throw(UsageStoreError("recovery unavailable")),
    )
    blocked = restarted.recover_usage_reconciliation()
    assert blocked["status"] == "blocked"
    with pytest.raises(DataSourceUnavailable, match="usage_store_unavailable"):
        restarted.validate_adapter("world-bank")
    assert provider.calls == []

    monkeypatch.setattr(restarted_usage, "_atomic_write", original_atomic_write)
    same_day = restarted.validate_adapter("world-bank")
    assert same_day["status"] == "daily_request_limit_exhausted"
    assert provider.calls == []

    clock[0] = NEXT_DAY
    assert restarted.validate_adapter("world-bank")["status"] == "success"
    records = restarted_usage.records(now=NEXT_DAY)
    assert provider.calls == ["macro_indicator"]
    assert len(records) == 2
    assert [record.request_count for record in records] == [1, 1]


def test_permanent_recovery_failure_retains_exact_staged_ledger_settlement(
    tmp_path, monkeypatch,
):
    _first, config, usage, _provider, _registry = _leave_durable_settlement(
        tmp_path, monkeypatch,
    )
    before = usage.path.read_bytes()
    restarted_usage = UsageStore(tmp_path / "usage")
    restarted, _config, _usage, _provider, _registry = _service(
        tmp_path,
        [],
        usage_store=restarted_usage,
        config_store=config,
        provider_registry=_ExplodingDependency(),
    )
    monkeypatch.setattr(
        restarted_usage,
        "_atomic_write",
        lambda _records: (_ for _ in ()).throw(UsageStoreError("private recovery detail")),
    )

    diagnostic = restarted.recover_usage_reconciliation()
    records = restarted_usage.records(now=NOW)

    assert diagnostic == {
        "status": "blocked", "attempted": 1, "recovered": 0, "retained": 1,
    }
    assert usage.path.read_bytes() == before
    assert len(records) == 1
    assert records[0].actual_cost is None
    assert records[0].request_count == 1
    assert records[0].status == "validation_success"


@pytest.mark.parametrize(
    "variant",
    (
        "corrupt",
        "oversized",
        "deep",
        "future_version",
        "cross_service",
        "unknown_adapter",
        "unknown_reservation",
        "hostile_identity",
        "hostile_settlement",
        "future_timestamp",
    ),
)
def test_recovery_rejects_untrusted_artifact_without_execution_or_rewrite(
    tmp_path, variant: str,
):
    catalog = build_catalog({"sources": []})
    usage = UsageStore(tmp_path / "usage")
    record = usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    document = _intent_document(record)
    if variant == "corrupt":
        raw = b"{not-json"
    elif variant == "oversized":
        raw = b"{" + (b"x" * (4 * 1024 * 1024 + 1))
    elif variant == "deep":
        raw = ("[" * 5_000 + "0" + "]" * 5_000).encode("ascii")
    else:
        if variant == "future_version":
            document["version"] = 999
        elif variant == "cross_service":
            document["service"] = "another-service"
        elif variant == "unknown_adapter":
            document["intents"][0]["adapter_id"] = "unknown-adapter"
        elif variant == "unknown_reservation":
            document["intents"][0]["reservation_id"] = "f" * 32
        elif variant == "hostile_identity":
            document["intents"][0]["adapter_id"] = "../world-bank"
        elif variant == "hostile_settlement":
            document["intents"][0]["request_count"] = 0
            document["intents"][0]["status"] = "validation_success"
            document["intents"][0]["units"] = "1"
        elif variant == "future_timestamp":
            document["intents"][0]["recorded_at"] = "2099-01-01T00:00:00Z"
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    usage.root.mkdir(parents=True, exist_ok=True)
    usage.reconciliation_path.write_bytes(raw)
    adjacent = usage.root / "unknown-recovery-artifact.json"
    adjacent.write_bytes(b"operator-owned")
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        usage_store=usage,
        config_store=_ExplodingDependency(),
        credential_store=_ExplodingDependency(),
        provider_registry=_ExplodingDependency(),
        now_factory=lambda: NOW,
    )

    diagnostic = service.recover_usage_reconciliation()

    assert diagnostic["status"] == "blocked"
    assert diagnostic["attempted"] == 0
    assert usage.reconciliation_path.read_bytes() == raw
    assert adjacent.read_bytes() == b"operator-owned"
    remaining = usage.records(now=NOW)
    assert len(remaining) == 1
    assert remaining[0].actual_cost is None


def test_internal_stage_rejects_custom_identity_before_hooks_or_disk_mutation(tmp_path):
    usage = UsageStore(tmp_path / "usage")
    record = usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    hostile = _HostileText("world-bank")

    with pytest.raises(UsageValidationError, match="adapter_id"):
        usage._stage_validation_reconciliation(
            hostile,
            reservation_id=record.reservation_id,
            status="validation_success",
            units=Decimal("1"),
            recorded_at=NOW,
        )

    assert hostile.called is False
    assert not usage.reconciliation_path.exists()


def test_internal_stage_rejects_cross_reservation_binding_without_mutation(tmp_path):
    usage = UsageStore(tmp_path / "usage")
    record = usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    before = usage.path.read_bytes()

    with pytest.raises(UsageStoreError, match="another adapter"):
        usage._stage_validation_reconciliation(
            "oecd",
            reservation_id=record.reservation_id,
            status="validation_success",
            units=Decimal("1"),
            recorded_at=NOW,
        )

    assert usage.path.read_bytes() == before


def test_public_store_exposes_no_caller_document_staging_api(tmp_path):
    usage = UsageStore(tmp_path / "usage")

    assert getattr(usage, "stage_reconciliation", None) is None


def test_two_services_recover_one_logical_usage_record_concurrently(tmp_path, monkeypatch):
    _first, config, _usage, _provider, _registry = _leave_durable_settlement(
        tmp_path, monkeypatch,
    )
    services = [
        _service(
            tmp_path,
            [],
            usage_store=UsageStore(tmp_path / "usage"),
            config_store=config,
            credential_store=_ExplodingDependency(),
            provider_registry=_ExplodingDependency(),
        )[0]
        for _ in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        diagnostics = list(executor.map(lambda service: service.recover_usage_reconciliation(), services))

    records = UsageStore(tmp_path / "usage").records(now=NOW)
    assert all(row["status"] == "closed" for row in diagnostics)
    assert sum(row["recovered"] for row in diagnostics) == 1
    assert len(records) == 1
    assert records[0].request_count == 1
    assert records[0].actual_cost == Decimal("0")


def test_recovery_never_touches_provider_keyring_or_config_dependencies(tmp_path, monkeypatch):
    _first, _config, _usage, first_provider, _registry = _leave_durable_settlement(
        tmp_path, monkeypatch,
    )
    catalog = build_catalog({"sources": []})
    restarted = DataSourceService(
        catalog_builder=lambda: catalog,
        usage_store=UsageStore(tmp_path / "usage"),
        config_store=_ExplodingDependency(),
        credential_store=_ExplodingDependency(),
        provider_registry=_ExplodingDependency(),
        health_service_factory=lambda: _ExplodingDependency(),
        now_factory=lambda: NOW,
    )

    assert restarted.recover_usage_reconciliation()["status"] == "closed"
    assert first_provider.calls == ["macro_indicator"]


def test_recovery_accepts_no_caller_payload_or_reservation_authority(tmp_path):
    catalog = build_catalog({"sources": []})
    usage = UsageStore(tmp_path / "usage")
    record = usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        usage_store=usage,
        config_store=_ExplodingDependency(),
        credential_store=_ExplodingDependency(),
        provider_registry=_ExplodingDependency(),
        now_factory=lambda: NOW,
    )

    with pytest.raises(TypeError):
        service.recover_usage_reconciliation({
            "adapter_id": "world-bank",
            "reservation_id": record.reservation_id,
            "request_count": 0,
            "status": "forged",
        })

    remaining = usage.records(now=NOW)
    assert len(remaining) == 1
    assert remaining[0].actual_cost is None
    assert not usage.reconciliation_path.exists()


def _write_reconciliation_document(usage: UsageStore, document: dict[str, object]) -> bytes:
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    usage.root.mkdir(parents=True, exist_ok=True)
    usage.reconciliation_path.write_bytes(raw)
    return raw


def _recovery_only_service(usage: UsageStore, *, now: datetime = NOW) -> DataSourceService:
    catalog = build_catalog({"sources": []})
    return DataSourceService(
        catalog_builder=lambda: catalog,
        usage_store=usage,
        config_store=_ExplodingDependency(),
        credential_store=_ExplodingDependency(),
        provider_registry=_ExplodingDependency(),
        now_factory=lambda: now,
    )


def test_forged_zero_request_intent_cannot_clear_or_reopen_limit_one(tmp_path):
    usage = UsageStore(tmp_path / "usage")
    record = usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        daily_request_limit=1,
        now=NOW,
    )
    raw = _write_reconciliation_document(
        usage,
        _intent_document(
            record,
            request_count=0,
            status="validation_not_attempted",
            units="0",
        ),
    )

    diagnostic = _recovery_only_service(usage).recover_usage_reconciliation()

    assert diagnostic["status"] == "blocked"
    assert usage.reconciliation_path.read_bytes() == raw
    remaining = usage.records(now=NOW)
    assert len(remaining) == 1
    assert remaining[0].actual_cost is None
    with pytest.raises(UsageBudgetExceeded, match="daily_request_limit_exhausted"):
        usage.reserve(
            "world-bank",
            estimated_cost=Decimal("0"),
            daily_budget=Decimal("0"),
            monthly_budget=Decimal("0"),
            daily_request_limit=1,
            now=NOW + timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "overrides",
    (
        {},
        {"status": "validation_failure", "units": "1"},
    ),
    ids=("known-public-constants", "inconsistent-status-units"),
)
def test_uncommitted_or_inconsistent_file_intent_cannot_settle_ledger(
    tmp_path, overrides: dict[str, object],
):
    usage = UsageStore(tmp_path / "usage")
    record = usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    raw = _write_reconciliation_document(usage, _intent_document(record, **overrides))

    diagnostic = _recovery_only_service(usage).recover_usage_reconciliation()

    assert diagnostic["status"] == "blocked"
    assert usage.reconciliation_path.read_bytes() == raw
    remaining = usage.records(now=NOW)
    assert len(remaining) == 1
    assert remaining[0].actual_cost is None
    assert remaining[0].request_count == 0


def test_copied_intent_cannot_settle_another_publicly_similar_reservation(tmp_path):
    source = UsageStore(tmp_path / "source")
    source_record = source.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    copied = _intent_document(source_record)

    target = UsageStore(tmp_path / "target")
    target_record = target.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    copied["intents"][0]["reservation_id"] = target_record.reservation_id
    raw = _write_reconciliation_document(target, copied)

    diagnostic = _recovery_only_service(target).recover_usage_reconciliation()

    assert diagnostic["status"] == "blocked"
    assert target.reconciliation_path.read_bytes() == raw
    remaining = target.records(now=NOW)
    assert len(remaining) == 1
    assert remaining[0].actual_cost is None


def test_redundant_external_intent_matching_closed_ledger_is_inert_and_preserved(tmp_path):
    usage = UsageStore(tmp_path / "usage")
    reservation = usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    settled = usage.reconcile(
        "world-bank",
        reservation_id=reservation.reservation_id,
        actual_cost=Decimal("0"),
        request_count=1,
        status="validation_success",
        units=Decimal("1"),
        now=NOW,
    )
    raw = _write_reconciliation_document(usage, _intent_document(settled))
    before = usage.path.read_bytes()

    diagnostic = _recovery_only_service(usage).recover_usage_reconciliation()

    assert diagnostic == {
        "status": "closed", "attempted": 0, "recovered": 0, "retained": 0,
    }
    assert usage.path.read_bytes() == before
    assert usage.reconciliation_path.read_bytes() == raw
    assert usage.records(now=NOW) == (settled,)


def test_prewarmed_service_rechecks_later_fresh_reservation_without_request_limit(
    tmp_path, monkeypatch,
):
    service, _config, usage, provider, _registry = _service(tmp_path, [_success()])
    assert service.recover_usage_reconciliation() == {
        "status": "closed", "attempted": 0, "recovered": 0, "retained": 0,
    }
    owner_usage = UsageStore(tmp_path / "usage")
    owner_usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    before = owner_usage.path.read_bytes()
    recovery_calls = 0
    original_recover = usage.recover_reconciliations

    def counted_recover(*args, **kwargs):
        nonlocal recovery_calls
        recovery_calls += 1
        return original_recover(*args, **kwargs)

    monkeypatch.setattr(usage, "recover_reconciliations", counted_recover)

    with pytest.raises(DataSourceUnavailable, match="usage_store_unavailable"):
        service.validate_adapter("world-bank")

    assert recovery_calls == 1
    assert provider.calls == []
    assert owner_usage.path.read_bytes() == before
    remaining = owner_usage.records(now=NOW)
    assert len(remaining) == 1
    assert remaining[0].actual_cost is None


def test_prewarmed_service_retries_after_owner_completion_without_duplicate_usage(
    tmp_path,
):
    catalog = build_catalog({"sources": []})
    config_root = tmp_path / "config"
    usage_root = tmp_path / "usage"
    entered = threading.Event()
    release = threading.Event()
    provider_a = _BlockingProbe(catalog.adapter("world-bank"), entered, release)
    provider_b = _Probe(catalog.adapter("world-bank"), [_success(2)])
    service_a = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: _NoHealth(),
        config_store=DataSourceConfigStore(config_root, catalog=catalog),
        credential_store=MemoryCredentialStore(_credential_scope(catalog)),
        usage_store=UsageStore(usage_root),
        provider_registry=_Registry(provider_a),
        now_factory=lambda: NOW,
    )
    service_b = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: _NoHealth(),
        config_store=DataSourceConfigStore(config_root, catalog=catalog),
        credential_store=MemoryCredentialStore(_credential_scope(catalog)),
        usage_store=UsageStore(usage_root),
        provider_registry=_Registry(provider_b),
        now_factory=lambda: NOW,
    )
    assert service_b.recover_usage_reconciliation()["status"] == "closed"

    with ThreadPoolExecutor(max_workers=1) as executor:
        owner = executor.submit(service_a.validate_adapter, "world-bank")
        assert entered.wait(timeout=5)
        try:
            with pytest.raises(DataSourceUnavailable, match="usage_store_unavailable"):
                service_b.validate_adapter("world-bank")
            assert provider_b.calls == []
        finally:
            release.set()
        assert owner.result(timeout=5)["status"] == "success"

    assert service_b.validate_adapter("world-bank")["status"] == "success"
    records = UsageStore(usage_root).records(now=NOW)
    assert provider_a.calls == ["macro_indicator"]
    assert provider_b.calls == ["macro_indicator"]
    assert len(records) == 2
    assert len({record.reservation_id for record in records}) == 2
    assert all(record.actual_cost == Decimal("0") for record in records)
    assert all(record.request_count == 1 for record in records)


def test_prewarmed_service_blocks_later_stale_orphan_without_request_limit(
    tmp_path, monkeypatch,
):
    clock = [NOW]
    service, _config, usage, provider, _registry = _service(
        tmp_path,
        [_success()],
        now_factory=lambda: clock[0],
    )
    assert service.recover_usage_reconciliation()["status"] == "closed"
    owner_usage = UsageStore(tmp_path / "usage")
    owner_usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    before = owner_usage.path.read_bytes()
    clock[0] = NOW + timedelta(minutes=5, microseconds=1)
    recovery_calls = 0
    original_recover = usage.recover_reconciliations

    def counted_recover(*args, **kwargs):
        nonlocal recovery_calls
        recovery_calls += 1
        return original_recover(*args, **kwargs)

    monkeypatch.setattr(usage, "recover_reconciliations", counted_recover)

    with pytest.raises(DataSourceUnavailable, match="usage_store_unavailable"):
        service.validate_adapter("world-bank")

    assert recovery_calls == 1
    assert provider.calls == []
    assert owner_usage.path.read_bytes() == before
    remaining = owner_usage.records(now=clock[0])
    assert len(remaining) == 1
    assert remaining[0].actual_cost is None


def test_exact_legacy_zero_intent_matching_settled_ledger_is_inert_and_preserved(
    tmp_path,
):
    usage = UsageStore(tmp_path / "usage")
    reservation = usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    settled = usage.reconcile(
        "world-bank",
        reservation_id=reservation.reservation_id,
        actual_cost=Decimal("0"),
        request_count=0,
        status="validation_not_attempted",
        units=Decimal("0"),
        now=NOW,
    )
    raw = _write_reconciliation_document(
        usage,
        _intent_document(
            settled,
            request_count=0,
            status="validation_not_attempted",
            units="0",
        ),
    )
    before = usage.path.read_bytes()

    diagnostic = _recovery_only_service(usage).recover_usage_reconciliation()

    assert diagnostic == {
        "status": "closed", "attempted": 0, "recovered": 0, "retained": 0,
    }
    assert usage.path.read_bytes() == before
    assert usage.reconciliation_path.read_bytes() == raw
    assert usage.records(now=NOW) == (settled,)


def test_exact_legacy_zero_intent_against_open_reservation_blocks_without_mutation(
    tmp_path,
):
    usage = UsageStore(tmp_path / "usage")
    reservation = usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )
    raw = _write_reconciliation_document(
        usage,
        _intent_document(
            reservation,
            request_count=0,
            status="validation_not_attempted",
            units="0",
        ),
    )
    before = usage.path.read_bytes()

    diagnostic = _recovery_only_service(usage).recover_usage_reconciliation()

    assert diagnostic == {
        "status": "blocked", "attempted": 0, "recovered": 0, "retained": 2,
    }
    assert usage.path.read_bytes() == before
    assert usage.reconciliation_path.read_bytes() == raw
    remaining = usage.records(now=NOW)
    assert remaining == (reservation,)
    assert remaining[0].actual_cost is None


def test_two_services_treat_fresh_reservation_as_pending_then_retry_after_owner_finishes(
    tmp_path,
):
    catalog = build_catalog({"sources": []})
    config_root = tmp_path / "config"
    usage_root = tmp_path / "usage"
    config_a = DataSourceConfigStore(config_root, catalog=catalog)
    config_a.update_adapter("world-bank", {"daily_request_limit": 1})
    entered = threading.Event()
    release = threading.Event()
    provider_a = _BlockingProbe(catalog.adapter("world-bank"), entered, release)
    provider_b = _Probe(
        catalog.adapter("world-bank"),
        [AssertionError("second service must not duplicate the in-flight probe")],
    )
    service_a = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: _NoHealth(),
        config_store=config_a,
        credential_store=MemoryCredentialStore(_credential_scope(catalog)),
        usage_store=UsageStore(usage_root),
        provider_registry=_Registry(provider_a),
        now_factory=lambda: NOW,
    )
    service_b = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: _NoHealth(),
        config_store=DataSourceConfigStore(config_root, catalog=catalog),
        credential_store=MemoryCredentialStore(_credential_scope(catalog)),
        usage_store=UsageStore(usage_root),
        provider_registry=_Registry(provider_b),
        now_factory=lambda: NOW,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        owner = executor.submit(service_a.validate_adapter, "world-bank")
        assert entered.wait(timeout=5)
        try:
            diagnostic = service_b.recover_usage_reconciliation()
            assert diagnostic == {
                "status": "pending", "attempted": 0, "recovered": 0, "retained": 1,
            }
            with pytest.raises(DataSourceUnavailable, match="usage_store_unavailable"):
                service_b.validate_adapter("world-bank")
            assert provider_b.calls == []
        finally:
            release.set()
        assert owner.result(timeout=5)["status"] == "success"

    second = service_b.validate_adapter("world-bank")
    records = UsageStore(usage_root).records(now=NOW)
    assert second["status"] == "daily_request_limit_exhausted"
    assert provider_a.calls == ["macro_indicator"]
    assert provider_b.calls == []
    assert len(records) == 1
    assert records[0].request_count == 1


@pytest.mark.parametrize(
    ("age", "expected_status"),
    (
        (timedelta(seconds=1), "pending"),
        (timedelta(minutes=5), "pending"),
        (timedelta(minutes=5, microseconds=1), "blocked"),
    ),
    ids=("fresh", "window-boundary", "stale-after-boundary"),
)
def test_open_reservation_freshness_window_is_bounded(
    tmp_path, age: timedelta, expected_status: str,
):
    usage = UsageStore(tmp_path / "usage")
    usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW,
    )

    diagnostic = _recovery_only_service(usage, now=NOW + age).recover_usage_reconciliation()

    assert diagnostic["status"] == expected_status
    assert usage.records(now=NOW + age)[0].actual_cost is None


@pytest.mark.parametrize(
    ("future_delta", "expected_status"),
    (
        (timedelta(seconds=5), "pending"),
        (timedelta(seconds=5, microseconds=1), "blocked"),
    ),
    ids=("clock-skew-boundary", "future-beyond-skew"),
)
def test_open_reservation_clock_skew_boundary_is_bounded(
    tmp_path, future_delta: timedelta, expected_status: str,
):
    usage = UsageStore(tmp_path / "usage")
    usage.reserve(
        "world-bank",
        estimated_cost=Decimal("0"),
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        now=NOW + future_delta,
    )

    diagnostic = _recovery_only_service(usage, now=NOW).recover_usage_reconciliation()

    assert diagnostic["status"] == expected_status


def test_validation_recorded_at_is_fresh_after_probe_not_authorization_time(tmp_path):
    finished = NOW + timedelta(seconds=17)
    ticks = iter((NOW, NOW, finished, finished, finished))
    service, _config, usage, _provider, _registry = _service(
        tmp_path,
        [_success(2)],
        now_factory=lambda: next(ticks),
    )

    assert service.validate_adapter("world-bank")["status"] == "success"

    record = usage.records(now=finished)[0]
    assert record.authorized_at == NOW
    assert record.recorded_at == finished
    assert record.recorded_at > record.authorized_at


@pytest.mark.parametrize(
    "failure_point",
    ("mkstemp", "fdopen", "write", "fsync", "replace", "directory_sync"),
)
def test_atomic_usage_write_normalizes_real_os_errors_without_path_disclosure(
    tmp_path, monkeypatch, failure_point: str,
):
    usage = UsageStore(tmp_path / "usage")
    private_marker = str(tmp_path / "private-secret-marker")

    def fail(*_args, **_kwargs):
        raise PermissionError(private_marker)

    if failure_point == "mkstemp":
        monkeypatch.setattr(usage_store_module.tempfile, "mkstemp", fail)
    elif failure_point == "fdopen":
        monkeypatch.setattr(usage_store_module.os, "fdopen", fail)
    elif failure_point == "write":
        original_fdopen = usage_store_module.os.fdopen

        class _WriteFailingHandle:
            def __init__(self, handle) -> None:
                self._handle = handle

            def __enter__(self):
                self._handle.__enter__()
                return self

            def __exit__(self, *args):
                return self._handle.__exit__(*args)

            def write(self, _value):
                fail()

            def __getattr__(self, name):
                return getattr(self._handle, name)

        monkeypatch.setattr(
            usage_store_module.os,
            "fdopen",
            lambda *args, **kwargs: _WriteFailingHandle(
                original_fdopen(*args, **kwargs)
            ),
        )
    elif failure_point == "fsync":
        monkeypatch.setattr(usage_store_module.os, "fsync", fail)
    elif failure_point == "replace":
        monkeypatch.setattr(usage_store_module.os, "replace", fail)
    else:
        monkeypatch.setattr(usage, "_sync_parent_directory", fail, raising=False)

    with pytest.raises(UsageStoreError) as raised:
        usage.reserve(
            "world-bank",
            estimated_cost=Decimal("0"),
            daily_budget=Decimal("0"),
            monthly_budget=Decimal("0"),
            now=NOW,
        )

    assert str(raised.value) == "usage ledger could not be written"
    assert private_marker not in str(raised.value)


def test_service_normalizes_real_reservation_mkstemp_error_before_probe(
    tmp_path, monkeypatch,
):
    service, _config, usage, provider, _registry = _service(tmp_path, [_success()])
    marker = str(tmp_path / "private-reservation-path")
    monkeypatch.setattr(
        usage_store_module.tempfile,
        "mkstemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError(marker)),
    )

    with pytest.raises(DataSourceUnavailable) as raised:
        service.validate_adapter("world-bank")

    assert raised.value.code == "usage_store_unavailable"
    assert marker not in str(raised.value)
    assert provider.calls == []
    assert not usage.path.exists()


def test_real_stage_mkstemp_error_keeps_one_open_reservation_and_redacts_service(
    tmp_path, monkeypatch,
):
    service, _config, usage, provider, _registry = _service(tmp_path, [_success()])
    original_mkstemp = usage_store_module.tempfile.mkstemp
    marker = str(tmp_path / "private-stage-path")
    calls = 0

    def fail_second_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(marker)
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(usage_store_module.tempfile, "mkstemp", fail_second_write)

    with pytest.raises(DataSourceUnavailable) as raised:
        service.validate_adapter("world-bank")

    records = usage.records(now=NOW)
    assert raised.value.code == "usage_store_unavailable"
    assert marker not in str(raised.value)
    assert provider.calls == ["macro_indicator"]
    assert len(records) == 1
    assert records[0].actual_cost is None
    assert records[0].request_count == 0


def test_postwrite_ambiguous_real_replace_is_idempotent_and_counts_once(
    tmp_path, monkeypatch,
):
    service, _config, usage, provider, _registry = _service(tmp_path, [_success(4)])
    original_replace = usage_store_module.os.replace
    marker = str(tmp_path / "private-post-replace-path")
    calls = 0

    def replace_then_fail_on_reconcile(source, destination):
        nonlocal calls
        calls += 1
        original_replace(source, destination)
        if calls == 3:
            raise OSError(marker)

    monkeypatch.setattr(usage_store_module.os, "replace", replace_then_fail_on_reconcile)

    assert service.validate_adapter("world-bank")["status"] == "success"

    records = usage.records(now=NOW)
    assert provider.calls == ["macro_indicator"]
    assert calls == 3
    assert len(records) == 1
    assert records[0].actual_cost == Decimal("0")
    assert records[0].request_count == 1
    assert records[0].units == Decimal("4")


def test_recovery_real_replace_error_retains_staged_ledger_then_retries(
    tmp_path, monkeypatch,
):
    _first, _config, usage, provider, _registry = _leave_durable_settlement(
        tmp_path, monkeypatch,
    )
    before = usage.path.read_bytes()
    restarted_usage = UsageStore(tmp_path / "usage")
    restarted = _recovery_only_service(restarted_usage)
    marker = str(tmp_path / "private-recovery-replace-path")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            usage_store_module.os,
            "replace",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(marker)),
        )
        diagnostic = restarted.recover_usage_reconciliation()

    assert diagnostic == {
        "status": "blocked", "attempted": 1, "recovered": 0, "retained": 1,
    }
    assert marker not in repr(diagnostic)
    assert restarted_usage.path.read_bytes() == before
    assert restarted.recover_usage_reconciliation() == {
        "status": "closed", "attempted": 1, "recovered": 1, "retained": 0,
    }
    records = restarted_usage.records(now=NOW)
    assert provider.calls == ["macro_indicator"]
    assert len(records) == 1
    assert records[0].actual_cost == Decimal("0")
    assert records[0].request_count == 1


def test_http_api_redacts_real_reservation_mkstemp_error(tmp_path, monkeypatch):
    service, _config, _usage, provider, _registry = _service(tmp_path, [_success()])
    marker = str(tmp_path / "private-http-reservation-path")
    monkeypatch.setattr(api_module, "_service", service)
    monkeypatch.setattr(
        usage_store_module.tempfile,
        "mkstemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError(marker)),
    )
    client = TestClient(app_module.app, base_url="http://127.0.0.1:8900")

    response = client.post(
        "/api/data-sources/world-bank/validate",
        json={},
        headers={"X-PP03-Write-Intent": "1"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "usage_store_unavailable"
    assert marker not in response.text
    assert provider.calls == []
