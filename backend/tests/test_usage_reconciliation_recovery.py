from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from data_sources.catalog import DataSourceCatalog, build_catalog
from data_sources.config_store import DataSourceConfigStore
from data_sources.credentials import MemoryCredentialStore
from data_sources.service import DataSourceService, DataSourceUnavailable
from data_sources.usage_store import UsageStore, UsageStoreError, UsageValidationError


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


def _leave_durable_intent(root, monkeypatch, *, returned_items: int = 3, daily_limit=None):
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


def test_prewrite_failure_persists_exact_intent_and_restart_recovers_without_probe(
    tmp_path, monkeypatch,
):
    _first, _config, usage, provider, _registry = _leave_durable_intent(
        tmp_path, monkeypatch, returned_items=3,
    )
    intent = json.loads(usage.reconciliation_path.read_text(encoding="utf-8"))

    assert set(intent) == {"version", "service", "intents"}
    assert len(intent["intents"]) == 1
    assert set(intent["intents"][0]) == _INTENT_FIELDS
    assert intent["intents"][0]["request_count"] == 1
    assert intent["intents"][0]["status"] == "validation_success"
    assert intent["intents"][0]["units"] == "3"
    assert intent["intents"][0]["recorded_at"] == "2026-08-20T12:00:00Z"
    assert not any(
        marker in usage.reconciliation_path.read_text(encoding="utf-8").lower()
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
    assert json.loads(restarted_usage.reconciliation_path.read_text(encoding="utf-8"))[
        "intents"
    ] == []


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
    assert usage.reconciliation_path.exists()

    restarted, _config, restarted_usage, restarted_provider, _registry = _service(
        tmp_path, [AssertionError("recovery must not probe")],
    )
    diagnostic = restarted.recover_usage_reconciliation()
    after = restarted_usage.records(now=NOW)

    assert diagnostic["status"] == "closed"
    assert diagnostic["attempted"] == 1
    assert after == before
    assert len(after) == 1
    assert provider.calls == ["macro_indicator"]
    assert restarted_provider.calls == []


def test_intent_persistence_failure_never_reconciles_or_reports_probe_success(
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

    monkeypatch.setattr(usage, "stage_reconciliation", fail_stage, raising=False)
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
    first, config, _usage, first_provider, _registry = _leave_durable_intent(
        tmp_path, monkeypatch, daily_limit=1,
    )

    with pytest.raises(DataSourceUnavailable, match="usage_store_unavailable"):
        first.validate_adapter("world-bank")
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
    assert restarted.recover_usage_reconciliation()["status"] == "closed"
    same_day = restarted.validate_adapter("world-bank")
    assert same_day["status"] == "daily_request_limit_exhausted"
    assert provider.calls == []

    clock[0] = NEXT_DAY
    assert restarted.validate_adapter("world-bank")["status"] == "success"
    records = restarted_usage.records(now=NEXT_DAY)
    assert provider.calls == ["macro_indicator"]
    assert len(records) == 2
    assert [record.request_count for record in records] == [1, 1]


def test_permanent_recovery_failure_retains_exact_intent_and_open_reservation(
    tmp_path, monkeypatch,
):
    _first, config, usage, _provider, _registry = _leave_durable_intent(
        tmp_path, monkeypatch,
    )
    before = usage.reconciliation_path.read_bytes()
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
    assert usage.reconciliation_path.read_bytes() == before
    assert len(records) == 1
    assert records[0].actual_cost is None


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


def test_stage_rejects_custom_identity_before_hooks_or_disk_mutation(tmp_path):
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
        usage.stage_reconciliation(
            hostile,
            reservation_id=record.reservation_id,
            actual_cost=Decimal("0"),
            request_count=1,
            status="validation_success",
            units=Decimal("1"),
            recorded_at=NOW,
        )

    assert hostile.called is False
    assert not usage.reconciliation_path.exists()


def test_two_services_recover_one_logical_usage_record_concurrently(tmp_path, monkeypatch):
    _first, config, _usage, _provider, _registry = _leave_durable_intent(
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
    _first, _config, _usage, first_provider, _registry = _leave_durable_intent(
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
