from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app as app_module
import data_sources.api as api_module
from data_sources.budgets import BudgetValidationError
from data_sources.catalog import DataSourceCatalog, build_catalog
from data_sources.config_store import ConfigValidationError, DataSourceConfigStore
from data_sources.credentials import MemoryCredentialStore
from data_sources.models import (
    AdapterDescriptor,
    BillingModel,
    CapabilityDescriptor,
    CatalogStatus,
    SourceFamily,
    SourceRole,
)
from data_sources.routing import CapabilityRouter
from data_sources.service import DataSourceService
from data_sources.usage_store import UsageStore, UsageStoreError


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


class _NoHealth:
    def list_sources(self):
        return []


def _credential_scope(catalog: DataSourceCatalog) -> dict[str, tuple[str, ...]]:
    return {
        adapter.adapter_id: tuple(adapter.credential_env_names)
        for adapter in catalog.adapters
        if adapter.credential_env_names
    }


def _routing_catalog() -> DataSourceCatalog:
    capability_id = "runtime_route"

    def family(family_id: str) -> SourceFamily:
        return SourceFamily(
            family_id,
            family_id,
            "test",
            "test",
            (SourceRole.PRIMARY_DATA,),
            True,
            "test",
            CatalogStatus.CONFIGURED,
        )

    def adapter(adapter_id: str, family_id: str) -> AdapterDescriptor:
        return AdapterDescriptor(
            adapter_id,
            adapter_id,
            family_id,
            "test",
            (SourceRole.PRIMARY_DATA,),
            (capability_id,),
            BillingModel.FREE_NO_KEY,
            "none",
            (),
            True,
            "test",
            "test",
            "test",
            "test",
            "test",
            "https://public.example.test/",
            1,
            CatalogStatus.CONFIGURED,
        )

    return DataSourceCatalog(
        families=tuple(family(role) for role in ("primary", "fallback", "cross-check")),
        adapters=tuple(
            adapter(f"{role}-adapter", role)
            for role in ("primary", "fallback", "cross-check")
        ),
        capabilities=(
            CapabilityDescriptor(
                capability_id,
                "runtime route",
                "test",
                None,
                True,
                "test",
                "test",
                ("primary",),
                ("fallback",),
                ("cross-check",),
            ),
        ),
    )


def _yahoo_catalog(
    *,
    status: CatalogStatus = CatalogStatus.CONFIGURED,
    default_enabled: bool = False,
) -> DataSourceCatalog:
    capability_id = "overseas_stock_history"
    return DataSourceCatalog(
        families=(SourceFamily(
            "yahoo",
            "Yahoo Finance",
            "reference",
            "test",
            (SourceRole.FALLBACK_DATA,),
            False,
            "personal research only",
            status,
        ),),
        adapters=(AdapterDescriptor(
            "yahoo-finance",
            "Yahoo Finance",
            "yahoo",
            "reference",
            (SourceRole.FALLBACK_DATA,),
            (capability_id,),
            BillingModel.FREE_NO_KEY,
            "none",
            (),
            default_enabled,
            "personal research only",
            "test",
            "test",
            "test",
            "test",
            "https://finance.yahoo.com/",
            1,
            status,
        ),),
        capabilities=(CapabilityDescriptor(
            capability_id,
            "overseas stock history",
            "test",
            None,
            True,
            "test",
            "test",
            (),
            ("yahoo",),
            (),
        ),),
    )


def _service(
    catalog: DataSourceCatalog,
    store: DataSourceConfigStore,
    usage: UsageStore,
    *,
    provider_registry=None,
) -> DataSourceService:
    return DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: _NoHealth(),
        config_store=store,
        credential_store=MemoryCredentialStore(_credential_scope(catalog)),
        usage_store=usage,
        provider_registry=provider_registry,
        now_factory=lambda: NOW,
    )


def test_capability_router_rejects_missing_configuration_instead_of_using_static_defaults():
    with pytest.raises(TypeError, match="configuration"):
        CapabilityRouter(build_catalog({"sources": []}))


@pytest.mark.parametrize(
    ("role", "field"),
    [
        ("primary", "primary_adapter_ids"),
        ("fallback", "fallback_adapter_ids"),
        ("cross-check", "cross_check_adapter_ids"),
    ],
)
def test_service_routing_uses_persisted_disabled_state_after_restart_for_every_role(
    tmp_path, role: str, field: str,
):
    catalog = _routing_catalog()
    store = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    usage = UsageStore(tmp_path / "usage")
    # The synthetic Catalog intentionally has no provider implementations; the
    # routing composition must not need one.
    first = _service(catalog, store, usage, provider_registry=object())
    first.disable_adapter(f"{role}-adapter")

    restarted = _service(
        catalog,
        DataSourceConfigStore(tmp_path / "config", catalog=catalog),
        UsageStore(tmp_path / "usage"),
        provider_registry=object(),
    )
    route = restarted.capability_route("runtime_route")

    assert f"{role}-adapter" not in getattr(route, field)
    assert len(catalog.adapters) == 3


def test_service_routing_restart_keeps_the_registered_provider_catalog_count(tmp_path):
    catalog = build_catalog({"sources": []})
    registered_before = len(catalog.adapters)
    store = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    usage = UsageStore(tmp_path / "usage")
    _service(catalog, store, usage).disable_adapter("world-bank")

    restarted = _service(
        catalog,
        DataSourceConfigStore(tmp_path / "config", catalog=catalog),
        UsageStore(tmp_path / "usage"),
    )
    route = restarted.capability_route("macro_indicator")

    assert "world-bank" not in route.primary_adapter_ids
    assert restarted.catalog_document()["registration"]["adapters"] == registered_before


@pytest.mark.parametrize(
    "configuration",
    [
        {"free_only": True, "adapters": {}},
        {"free_only": True, "adapters": {"yahoo-finance": {"enabled": False}}},
        {"free_only": True, "adapters": {"yahoo-finance": {"enabled": True}}},
    ],
    ids=("absent", "explicit-false", "explicit-true-catalog-disabled"),
)
def test_default_yahoo_personal_research_never_bypasses_enablement_or_catalog_disabled(
    configuration,
):
    route = CapabilityRouter(
        build_catalog({"sources": []}), configuration=configuration,
    ).route("overseas_stock_history", personal_research=True)

    assert "yahoo-finance" not in route.fallback_adapter_ids
    assert "yahoo-finance" not in route.evidence_adapter_ids


@pytest.mark.parametrize(
    ("configuration", "expected"),
    [
        ({"free_only": True, "adapters": {}}, ()),
        ({"free_only": True, "adapters": {"yahoo-finance": {"enabled": False}}}, ()),
        (
            {"free_only": True, "adapters": {"yahoo-finance": {"enabled": True}}},
            ("yahoo-finance",),
        ),
    ],
    ids=("absent", "explicit-false", "explicit-true"),
)
def test_configured_yahoo_requires_explicit_enabled_and_personal_research(
    configuration, expected,
):
    catalog = _yahoo_catalog()
    router = CapabilityRouter(catalog, configuration=configuration)

    assert router.route(
        "overseas_stock_history", personal_research=True,
    ).fallback_adapter_ids == expected
    assert router.route(
        "overseas_stock_history", personal_research=False,
    ).fallback_adapter_ids == ()


def test_yahoo_static_default_cannot_replace_explicit_server_owned_enablement():
    route = CapabilityRouter(
        _yahoo_catalog(default_enabled=True),
        configuration={"free_only": True, "adapters": {}},
    ).route("overseas_stock_history", personal_research=True)

    assert route.fallback_adapter_ids == ()


def test_server_owned_yahoo_enablement_survives_restart_and_disable_removes_route(tmp_path):
    catalog = _yahoo_catalog()
    root = tmp_path / "config"
    first = _service(
        catalog,
        DataSourceConfigStore(root, catalog=catalog),
        UsageStore(tmp_path / "usage"),
        provider_registry=object(),
    )
    first.enable_adapter("yahoo-finance")

    restarted = _service(
        catalog,
        DataSourceConfigStore(root, catalog=catalog),
        UsageStore(tmp_path / "usage"),
        provider_registry=object(),
    )
    enabled = restarted.capability_route(
        "overseas_stock_history", personal_research=True,
    )
    restarted.disable_adapter("yahoo-finance")
    disabled = _service(
        catalog,
        DataSourceConfigStore(root, catalog=catalog),
        UsageStore(tmp_path / "usage"),
        provider_registry=object(),
    ).capability_route("overseas_stock_history", personal_research=True)

    assert enabled.fallback_adapter_ids == ("yahoo-finance",)
    assert enabled.evidence_adapter_ids == ()
    assert disabled.fallback_adapter_ids == ()
    assert len(catalog.adapters) == 1


class _BoundedProbe:
    def __init__(self, descriptor: AdapterDescriptor, outcomes: list[object]) -> None:
        self.descriptor = descriptor
        self._outcomes = list(outcomes)
        self.calls: list[str] = []

    def probe(self, capability_id: str):
        self.calls.append(capability_id)
        outcome = self._outcomes.pop(0)
        if callable(outcome):
            outcome = outcome()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _SingleRegistry:
    def __init__(self, provider: _BoundedProbe) -> None:
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


def _success(returned_items: int = 1) -> dict[str, object]:
    return {
        "status": "success",
        "connected": True,
        "health_failure": False,
        "returned_items": returned_items,
        "data_as_of_date": "2026-08-19",
        "final_reference": "https://api.worldbank.org/v2/",
    }


def _validation_service(tmp_path, outcomes: list[object], *, daily_limit: int | None = None):
    catalog = build_catalog({"sources": []})
    config = DataSourceConfigStore(
        tmp_path / "config", catalog=catalog, lock_timeout_seconds=0.01,
    )
    if daily_limit is not None:
        config.update_adapter("world-bank", {"daily_request_limit": daily_limit})
    usage = UsageStore(tmp_path / "usage")
    provider = _BoundedProbe(catalog.adapter("world-bank"), outcomes)
    registry = _SingleRegistry(provider)
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: _NoHealth(),
        config_store=config,
        credential_store=MemoryCredentialStore(_credential_scope(catalog)),
        usage_store=usage,
        provider_registry=registry,
        now_factory=lambda: NOW,
    )
    return service, config, usage, provider, registry


def test_free_no_key_validation_authorizes_and_records_success_exactly_once(tmp_path):
    service, _config, usage, provider, registry = _validation_service(tmp_path, [_success(3)])

    result = service.validate_adapter("world-bank")
    records = usage.records(now=NOW)

    assert result["status"] == "success"
    assert provider.calls == ["macro_indicator"]
    assert registry.adapter_calls == 1
    assert len(records) == 1
    assert records[0].actual_cost == Decimal("0")
    assert records[0].request_count == 1
    assert records[0].status == "validation_success"
    assert records[0].units == Decimal("3")


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("bounded timeout"),
        ValueError("bounded parser failure"),
        ConnectionError("bounded transport failure"),
    ],
    ids=("timeout", "parser", "transport"),
)
def test_free_no_key_validation_reconciles_failed_probe_once(tmp_path, error: Exception):
    service, _config, usage, provider, _registry = _validation_service(tmp_path, [error])

    result = service.validate_adapter("world-bank")
    records = usage.records(now=NOW)

    assert result["status"] == "failure"
    assert provider.calls == ["macro_indicator"]
    assert len(records) == 1
    assert records[0].request_count == 1
    assert records[0].status == "validation_failure"
    assert records[0].units == Decimal("0")


def test_free_no_key_duplicate_invocations_and_retry_each_account_once(tmp_path):
    service, _config, usage, provider, _registry = _validation_service(
        tmp_path,
        [_success(), TimeoutError("retryable timeout")],
        daily_limit=2,
    )

    assert service.validate_adapter("world-bank")["status"] == "success"
    assert service.validate_adapter("world-bank")["status"] == "failure"
    blocked = service.validate_adapter("world-bank")
    records = usage.records(now=NOW)

    assert blocked["status"] == "daily_request_limit_exhausted"
    assert blocked["connected"] is False
    assert provider.calls == ["macro_indicator", "macro_indicator"]
    assert len(records) == 2
    assert len({record.reservation_id for record in records}) == 2
    assert [record.request_count for record in records] == [1, 1]


def test_free_no_key_zero_request_limit_blocks_before_registry_or_probe(tmp_path):
    service, _config, usage, provider, registry = _validation_service(
        tmp_path, [_success()], daily_limit=0,
    )

    result = service.validate_adapter("world-bank")

    assert result["status"] == "daily_request_limit_exhausted"
    assert result["connected"] is False
    assert provider.calls == []
    assert registry.available_calls == 0
    assert registry.adapter_calls == 0
    assert usage.records(now=NOW) == ()


@pytest.mark.parametrize(
    ("probe_outcome", "expected_status", "usage_status"),
    [
        (None, "success", "validation_success"),
        (TimeoutError("bounded timeout"), "failure", "validation_failure"),
        (ValueError("bounded parser failure"), "failure", "validation_failure"),
        (ConnectionError("bounded transport failure"), "failure", "validation_failure"),
    ],
    ids=("success", "timeout", "parser", "transport"),
)
def test_validation_reconciles_bound_authorization_after_config_corrupts(
    tmp_path, probe_outcome, expected_status: str, usage_status: str,
):
    service, config, usage, provider, _registry = _validation_service(tmp_path, [None])
    corrupt = "{corrupt-after-authorization"

    def mutate_config_after_authorization():
        config.path.write_text(corrupt, encoding="utf-8")
        if probe_outcome is not None:
            raise probe_outcome
        return _success(2)

    provider._outcomes = [mutate_config_after_authorization]

    result = service.validate_adapter("world-bank")
    records = usage.records(now=NOW)

    assert result["status"] == expected_status
    assert provider.calls == ["macro_indicator"]
    assert len(records) == 1
    assert records[0].actual_cost == Decimal("0")
    assert records[0].request_count == 1
    assert records[0].status == usage_status
    assert config.path.read_text(encoding="utf-8") == corrupt


def test_validation_reconciles_bound_authorization_after_config_lock_timeout(tmp_path):
    service, config, usage, provider, _registry = _validation_service(tmp_path, [None])

    def block_config_after_authorization():
        config._try_lock_file = lambda _handle: False
        return _success()

    provider._outcomes = [block_config_after_authorization]

    result = service.validate_adapter("world-bank")
    records = usage.records(now=NOW)

    assert result["status"] == "success"
    assert len(records) == 1
    assert records[0].status == "validation_success"
    assert records[0].actual_cost == Decimal("0")


def test_transient_reconcile_error_retries_idempotently_without_open_reservation(tmp_path):
    service, _config, usage, _provider, _registry = _validation_service(tmp_path, [_success()])
    original_reconcile = usage.reconcile
    calls = 0

    def flaky_reconcile(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UsageStoreError("bounded transient reconcile failure")
        return original_reconcile(*args, **kwargs)

    usage.reconcile = flaky_reconcile

    result = service.validate_adapter("world-bank")
    records = usage.records(now=NOW)

    assert result["status"] == "success"
    assert calls == 2
    assert len(records) == 1
    assert records[0].actual_cost == Decimal("0")
    assert records[0].request_count == 1


def test_permanent_reconcile_error_is_redacted_and_preserves_fail_closed_reservation(
    tmp_path, monkeypatch,
):
    service, _config, usage, provider, _registry = _validation_service(tmp_path, [_success()])
    calls = 0
    internal_error = "bounded-internal-reconcile-marker"
    original_reconcile = usage.reconcile

    def broken_reconcile(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise UsageStoreError(internal_error)

    usage.reconcile = broken_reconcile
    monkeypatch.setattr(api_module, "_service", service)
    client = TestClient(app_module.app, base_url="http://127.0.0.1:8900")

    response = client.post(
        "/api/data-sources/world-bank/validate",
        json={},
        headers={"X-PP03-Write-Intent": "1"},
    )
    records = usage.records(now=NOW)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "usage_store_unavailable"
    assert internal_error not in response.text
    assert calls == 2
    assert provider.calls == ["macro_indicator"]
    assert len(records) == 1
    assert records[0].actual_cost is None
    assert records[0].status == "reserved"

    usage.reconcile = original_reconcile
    recovered = service._budget_guard.record(
        "world-bank",
        reservation_id=records[0].reservation_id,
        actual_cost=Decimal("0"),
        request_count=1,
        status="validation_success",
        units=Decimal("1"),
        now=NOW,
    )
    assert recovered.actual_cost == Decimal("0")
    assert recovered.request_count == 1
    assert len(usage.records(now=NOW)) == 1


def test_double_record_uses_original_context_after_config_corruption(tmp_path):
    service, config, usage, _provider, _registry = _validation_service(tmp_path, [_success(2)])
    service.validate_adapter("world-bank")
    first = usage.records(now=NOW)[0]
    config.path.write_text("{corrupt-after-completion", encoding="utf-8")

    duplicate = service._budget_guard.record(
        "world-bank",
        reservation_id=first.reservation_id,
        actual_cost=Decimal("0"),
        request_count=1,
        status="validation_success",
        units=Decimal("2"),
        now=NOW,
    )

    assert duplicate == first
    assert usage.records(now=NOW) == (first,)


def test_another_service_cannot_reconcile_caller_supplied_reservation_authority(tmp_path):
    catalog = build_catalog({"sources": []})
    config_root = tmp_path / "config"
    usage_root = tmp_path / "usage"
    first = _service(
        catalog,
        DataSourceConfigStore(config_root, catalog=catalog),
        UsageStore(usage_root),
    )
    second = _service(
        catalog,
        DataSourceConfigStore(config_root, catalog=catalog),
        UsageStore(usage_root),
    )
    first._ensure_configuration_dependencies()
    second._ensure_configuration_dependencies()
    decision = first._budget_guard.authorize(
        catalog.adapter("world-bank"), estimated_cost=Decimal("0"), now=NOW,
    )

    with pytest.raises(BudgetValidationError, match="authorization context"):
        second._budget_guard.record(
            "world-bank",
            reservation_id=decision.reservation_id,
            actual_cost=Decimal("0"),
            request_count=1,
            status="validation_success",
            units=Decimal("1"),
            now=NOW,
        )

    records = UsageStore(usage_root).records(now=NOW)
    assert len(records) == 1
    assert records[0].actual_cost is None
    assert records[0].status == "reserved"


class _ExplodingMapping(Mapping):
    called = False

    def _explode(self, *_args, **_kwargs):
        self.called = True
        raise AssertionError("hostile mapping method executed")

    __iter__ = __len__ = __getitem__ = keys = items = values = get = _explode


def test_update_adapter_rejects_mapping_subclass_before_methods_or_disk_access(tmp_path):
    catalog = build_catalog({"sources": []})
    store = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    updates = _ExplodingMapping()

    with pytest.raises(ConfigValidationError, match="adapter updates"):
        store.update_adapter("world-bank", updates)

    assert updates.called is False
    assert not store.root.exists()


def test_save_rejects_mapping_subclass_before_methods_or_disk_access(tmp_path):
    catalog = build_catalog({"sources": []})
    store = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    document = _ExplodingMapping()

    with pytest.raises(ConfigValidationError, match="configuration must be an object"):
        store.save(document)

    assert document.called is False
    assert not store.root.exists()


def test_update_adapter_rejects_string_subclass_id_before_hash_or_disk_access(tmp_path):
    class ExplodingIdentifier(str):
        called = False

        def __hash__(self):
            self.called = True
            raise AssertionError("hostile identifier hash executed")

    catalog = build_catalog({"sources": []})
    store = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    adapter_id = ExplodingIdentifier("world-bank")

    with pytest.raises(ConfigValidationError, match="adapter identifier"):
        store.update_adapter(adapter_id, {"enabled": False})

    assert adapter_id.called is False
    assert not store.root.exists()


@pytest.mark.parametrize("hostile_position", ["key", "value"])
def test_update_adapter_rejects_hostile_key_or_value_before_custom_string_methods(
    tmp_path, hostile_position: str,
):
    class HostileString(str):
        called = False

        def _explode(self, *_args, **_kwargs):
            self.called = True
            raise AssertionError("hostile string method executed")

        __eq__ = __format__ = __str__ = lower = _explode
        __hash__ = str.__hash__

    catalog = build_catalog({"sources": []})
    store = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    hostile = HostileString("daily_budget" if hostile_position == "key" else "0")
    updates = (
        {hostile: "0"}
        if hostile_position == "key"
        else {"daily_budget": hostile}
    )
    hostile.called = False

    with pytest.raises(ConfigValidationError):
        store.update_adapter("world-bank", updates)

    assert hostile.called is False
    assert not store.root.exists()


def test_load_normalizes_deep_json_recursion_without_removing_the_document(tmp_path):
    catalog = build_catalog({"sources": []})
    store = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    store.root.mkdir(parents=True)
    raw = '{"free_only":true,"adapters":' + "[" * 5_000 + "null" + "]" * 5_000 + "}"
    store.path.write_text(raw, encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="configuration is corrupt"):
        store.load()

    assert store.path.read_text(encoding="utf-8") == raw


def test_load_normalizes_duplicate_disk_fields_without_removing_the_document(tmp_path):
    catalog = build_catalog({"sources": []})
    store = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    store.root.mkdir(parents=True)
    raw = '{"free_only":true,"free_only":false,"adapters":{}}'
    store.path.write_text(raw, encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="configuration is corrupt"):
        store.load()

    assert store.path.read_text(encoding="utf-8") == raw
