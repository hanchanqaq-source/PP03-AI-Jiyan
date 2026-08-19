from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app as app_module
import data_sources.api as api_module
import data_sources.service as service_module
from data_sources.budgets import BudgetGuard, BudgetPolicy
from data_sources.catalog import build_catalog
from data_sources.config_store import DataSourceConfigStore
from data_sources.credentials import (
    EnvironmentCredentialStore,
    MemoryCredentialStore,
)
from data_sources.models import BillingModel, CatalogStatus
from data_sources.provider_registry import ProviderRegistry
from data_sources.routing import CapabilityRouter
from data_sources.service import DataSourceService, _DefaultCredentialStore
from data_sources.usage_store import UsageStore


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)
WRITE_HEADER = {"X-PP03-Write-Intent": "1"}
LOCAL_ORIGIN = "http://127.0.0.1:5899"


class RecordingWriteService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def update_free_only(self, value: bool):
        self.calls.append(f"free_only:{value}")
        return {"free_only": value, "adapters": []}

    def refresh(self):
        self.calls.append("refresh")
        return {"run_id": "a" * 20}

    def delete_credential(self, adapter_id: str):
        self.calls.append(f"delete:{adapter_id}")
        return {
            "configured": False,
            "status": "unconfigured",
            "last_validated_at": None,
            "credential_source": "memory",
        }


@pytest.fixture
def write_client(monkeypatch):
    service = RecordingWriteService()
    monkeypatch.setattr(api_module, "_service", service)
    return TestClient(app_module.app, base_url="http://127.0.0.1:8900"), service


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/data-sources/refresh", None),
        ("PUT", "/api/data-sources/config", {"free_only": False}),
        ("DELETE", "/api/data-sources/fred/credentials", None),
    ],
)
def test_default_app_rejects_foreign_origin_for_every_data_source_write_method(
    write_client, method, path, body
):
    client, service = write_client
    response = client.request(
        method,
        path,
        json=body,
        headers={**WRITE_HEADER, "Origin": "https://foreign.example"},
    )

    assert response.status_code == 403
    assert service.calls == []


@pytest.mark.parametrize("host", ["foreign.example", "localhost.foreign.example", "127.0.0.1.foreign.example"])
def test_default_app_rejects_non_loopback_and_dns_rebinding_hosts(write_client, host):
    client, service = write_client
    response = client.put(
        "/api/data-sources/config",
        json={"free_only": False},
        headers={**WRITE_HEADER, "Origin": LOCAL_ORIGIN, "Host": host},
    )

    assert response.status_code == 403
    assert service.calls == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/data-sources/refresh", None),
        ("PUT", "/api/data-sources/config", {"free_only": False}),
        ("DELETE", "/api/data-sources/fred/credentials", None),
    ],
)
def test_default_app_rejects_simple_write_without_custom_header(write_client, method, path, body):
    client, service = write_client
    response = client.request(method, path, json=body, headers={"Origin": LOCAL_ORIGIN})

    assert response.status_code == 403
    assert service.calls == []


def test_default_app_allows_approved_local_frontend_and_controlled_originless_cli(write_client):
    client, service = write_client

    browser = client.put(
        "/api/data-sources/config",
        json={"free_only": False},
        headers={**WRITE_HEADER, "Origin": LOCAL_ORIGIN},
    )
    cli = client.put(
        "/api/data-sources/config",
        json={"free_only": True},
        headers=WRITE_HEADER,
    )

    assert browser.status_code == 200
    assert cli.status_code == 200
    assert service.calls == ["free_only:False", "free_only:True"]


def test_default_app_preflight_requires_local_origin_and_write_intent_header(write_client):
    client, service = write_client
    headers = {
        "Origin": LOCAL_ORIGIN,
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type, x-pp03-write-intent",
    }

    approved = client.options("/api/data-sources/config", headers=headers)
    foreign = client.options(
        "/api/data-sources/config",
        headers={**headers, "Origin": "https://foreign.example"},
    )
    simple = client.options(
        "/api/data-sources/config",
        headers={**headers, "Access-Control-Request-Headers": "content-type"},
    )

    assert approved.status_code == 200
    assert approved.headers.get("access-control-allow-origin") == LOCAL_ORIGIN
    assert approved.headers.get("access-control-allow-origin") != "*"
    assert foreign.status_code == 403
    assert simple.status_code == 403
    assert service.calls == []


def _credential_scope(catalog):
    return {
        row.adapter_id: tuple(row.credential_env_names)
        for row in catalog.adapters
        if row.credential_env_names
    }


class NoRefreshHealth:
    def __init__(self) -> None:
        self.start_calls = 0

    def list_sources(self):
        return []

    def start_run(self, *_args, **_kwargs):
        self.start_calls += 1
        raise AssertionError("validation must not start the full/news refresh")


class SuccessfulProbe:
    def __init__(self, descriptor) -> None:
        self.descriptor = descriptor
        self.calls: list[str] = []

    def probe(self, capability_id: str):
        self.calls.append(capability_id)
        return {
            "status": "success",
            "connected": True,
            "health_failure": False,
            "returned_items": 1,
            "data_as_of_date": "2026-08-19",
            "final_reference": self.descriptor.configured_reference,
        }


class SingleProviderRegistry:
    def __init__(self, adapter) -> None:
        self._adapter = adapter

    def available_adapter_ids(self):
        return (self._adapter.descriptor.adapter_id,)

    def adapter(self, adapter_id: str):
        if adapter_id != self._adapter.descriptor.adapter_id:
            raise KeyError(adapter_id)
        return self._adapter


def test_http_validate_uses_one_bounded_free_no_key_probe_without_starting_news_refresh(
    tmp_path, monkeypatch
):
    catalog = build_catalog({"sources": []})
    descriptor = catalog.adapter("world-bank")
    provider = SuccessfulProbe(descriptor)
    health = NoRefreshHealth()
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: health,
        config_store=DataSourceConfigStore(tmp_path, catalog=catalog),
        credential_store=MemoryCredentialStore(_credential_scope(catalog)),
        usage_store=UsageStore(tmp_path / "usage"),
        provider_registry=SingleProviderRegistry(provider),
        now_factory=lambda: NOW,
    )
    monkeypatch.setattr(api_module, "_service", service)
    client = TestClient(app_module.app, base_url="http://127.0.0.1:8900")

    response = client.post(
        "/api/data-sources/world-bank/validate",
        json={},
        headers={**WRITE_HEADER, "Origin": LOCAL_ORIGIN},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["connected"] is True
    assert provider.calls == ["macro_indicator"]
    assert health.start_calls == 0


def _service_with_store(catalog, store, credentials, usage, health):
    return DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: health,
        config_store=store,
        credential_store=credentials,
        usage_store=usage,
        now_factory=lambda: NOW,
    )


def _catalog_adapter(document, adapter_id: str):
    return next(
        adapter
        for family in document["families"]
        for adapter in family["adapters"]
        if adapter["adapter_id"] == adapter_id
    )


def test_persisted_enabled_state_survives_service_restart_and_controls_refresh(tmp_path):
    catalog = build_catalog({"sources": []})
    store = DataSourceConfigStore(tmp_path, catalog=catalog)
    credentials = MemoryCredentialStore(_credential_scope(catalog))
    usage = UsageStore(tmp_path / "usage")

    class RecordingHealth:
        def __init__(self):
            self.excluded = None

        def list_sources(self):
            return []

        def start_run(self, _scope, *, excluded_adapter_ids=()):
            self.excluded = set(excluded_adapter_ids)
            return {"run_id": "b" * 20}

    health = RecordingHealth()
    first = _service_with_store(catalog, store, credentials, usage, health)
    first.disable_adapter("world-bank")
    restarted = _service_with_store(catalog, store, credentials, usage, health)

    assert _catalog_adapter(restarted.catalog_document(), "world-bank")["enabled"] is False
    restarted.refresh()
    assert "world-bank" in health.excluded


def test_concurrent_services_preserve_effective_enabled_state_for_every_consumer(tmp_path):
    catalog = build_catalog({"sources": []})
    store = DataSourceConfigStore(tmp_path, catalog=catalog)
    credentials = MemoryCredentialStore(_credential_scope(catalog))
    usage = UsageStore(tmp_path / "usage")
    health = NoRefreshHealth()
    services = [_service_with_store(catalog, store, credentials, usage, health) for _ in range(2)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: pair[0].disable_adapter(pair[1]), zip(services, ("world-bank", "oecd"))))

    observer = _service_with_store(catalog, store, credentials, usage, health)
    family_rows = {
        adapter["adapter_id"]: adapter["enabled"]
        for family in observer.families_document()
        for adapter in family["adapters"]
        if adapter["adapter_id"] in {"world-bank", "oecd"}
    }
    assert family_rows == {"world-bank": False, "oecd": False}


def test_capability_routing_uses_the_same_persisted_effective_enabled_state(tmp_path):
    catalog = build_catalog({"sources": []})
    store = DataSourceConfigStore(tmp_path, catalog=catalog)
    store.update_adapter("world-bank", {"enabled": False})

    route = CapabilityRouter(catalog, configuration=store.load()).route("macro_indicator")

    assert "world-bank" not in (
        *route.primary_adapter_ids,
        *route.fallback_adapter_ids,
        *route.cross_check_adapter_ids,
    )


class FakePostTransport:
    def __init__(self) -> None:
        self.calls = 0

    def post_json(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("authorization must run before transport")


def test_service_registry_reuses_put_credential_usage_and_server_guard_before_transport(
    tmp_path, monkeypatch
):
    catalog = build_catalog({"sources": []})
    config = DataSourceConfigStore(tmp_path, catalog=catalog)
    credentials = MemoryCredentialStore(_credential_scope(catalog))
    usage = UsageStore(tmp_path / "usage")
    transport = FakePostTransport()
    captured = {}

    def registry_factory(*, catalog, credential_store, budget_guard):
        captured.update(
            catalog=catalog,
            credential_store=credential_store,
            budget_guard=budget_guard,
        )
        return ProviderRegistry(
            catalog,
            http_factory=lambda: transport,
            credential_store=credential_store,
            budget_guard=budget_guard,
        )

    monkeypatch.setattr(service_module, "ProviderRegistry", registry_factory)
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        config_store=config,
        credential_store=credentials,
        usage_store=usage,
        now_factory=lambda: NOW,
    )

    state = service.put_credential("tushare", "bounded-test-value")
    config.update_adapter("tushare", {"enabled": True})
    result = service._provider_registry.adapter("tushare").probe("fund_holdings")

    assert state == {
        "configured": True,
        "status": "stored",
        "last_validated_at": None,
        "credential_source": "memory",
    }
    assert captured["credential_store"] is credentials
    assert captured["budget_guard"].usage_store is usage
    assert result["status"] == "credential_not_validated"
    assert transport.calls == 0
    assert "bounded-test-value" not in repr((state, result, captured["budget_guard"]))


def _policy(adapter_id: str, **overrides):
    values = {
        "adapter_id": adapter_id,
        "enabled": True,
        "configured": True,
        "free_only": False,
        "daily_budget": Decimal("10"),
        "monthly_budget": Decimal("100"),
        "per_request_budget": Decimal("10"),
        "credential_validated": True,
        "trusted_entitlement": True,
        "transport_supported": True,
        "live_authorized": True,
        "daily_request_limit": None,
        "monthly_request_limit": None,
    }
    values.update(overrides)
    return BudgetPolicy(**values)


def test_budget_guard_never_authorizes_enterprise_catalog_shell_even_if_policy_is_spoofed(tmp_path):
    descriptor = build_catalog({"sources": []}).adapter("bloomberg")
    store = UsageStore(tmp_path / "usage")
    guard = BudgetGuard(store, {"bloomberg": _policy("bloomberg")}, trusted_adapters={"bloomberg": descriptor})

    decision = guard.authorize(descriptor, estimated_cost=Decimal("1"), now=NOW)

    assert decision.allowed is False
    assert decision.reason == "license_required"
    assert store.records(now=NOW) == ()


def test_budget_guard_blocks_unverified_key_before_transport_and_reservation(tmp_path):
    descriptor = build_catalog({"sources": []}).adapter("fred")
    store = UsageStore(tmp_path / "usage")
    guard = BudgetGuard(
        store,
        {"fred": _policy("fred", credential_validated=False, transport_supported=False)},
        trusted_adapters={"fred": descriptor},
    )

    decision = guard.authorize(descriptor, estimated_cost=Decimal("0"), now=NOW)

    assert decision.allowed is False
    assert decision.reason == "credential_not_validated"
    assert store.records(now=NOW) == ()


def test_budget_guard_consumes_persisted_daily_request_limit_atomically(tmp_path):
    descriptor = build_catalog({"sources": []}).adapter("baostock")
    store = UsageStore(tmp_path / "usage")
    guard = BudgetGuard(
        store,
        {"baostock": _policy("baostock", daily_request_limit=1, monthly_request_limit=5)},
        trusted_adapters={"baostock": descriptor},
    )
    first = guard.authorize(descriptor, estimated_cost=Decimal("0"), now=NOW)
    guard.record(
        "baostock",
        reservation_id=first.reservation_id,
        actual_cost=Decimal("0"),
        request_count=1,
        status="success",
        units=Decimal("1"),
        now=NOW,
    )

    second = guard.authorize(descriptor, estimated_cost=Decimal("0"), now=NOW)

    assert second.allowed is False
    assert second.reason == "daily_request_limit_exhausted"
    assert len(store.records(now=NOW)) == 1


def test_budget_guard_checks_usage_then_blocks_unsupported_transport_without_reserving(tmp_path):
    descriptor = build_catalog({"sources": []}).adapter("baostock")
    store = UsageStore(tmp_path / "usage")
    guard = BudgetGuard(
        store,
        {"baostock": _policy("baostock", transport_supported=False)},
        trusted_adapters={"baostock": descriptor},
    )

    decision = guard.authorize(descriptor, estimated_cost=Decimal("0"), now=NOW)

    assert decision.allowed is False
    assert decision.reason == "unsupported_transport"
    assert store.records(now=NOW) == ()


def test_keyring_delete_reports_environment_fallback_as_still_effective(tmp_path, monkeypatch):
    catalog = build_catalog({"sources": []})
    scope = _credential_scope(catalog)
    keyring = MemoryCredentialStore(scope)
    keyring.set("fred", "FRED_API_KEY", "keyring-test-value")
    monkeypatch.setenv("FRED_API_KEY", "environment-test-value")
    layered = _DefaultCredentialStore(scope)
    layered._keyring = keyring
    layered._environment = EnvironmentCredentialStore(scope)
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        config_store=DataSourceConfigStore(tmp_path, catalog=catalog),
        credential_store=layered,
        usage_store=UsageStore(tmp_path / "usage"),
        now_factory=lambda: NOW,
    )

    state = service.delete_credential("fred")

    assert state == {
        "configured": True,
        "status": "environment_fallback_active",
        "last_validated_at": None,
        "credential_source": "environment",
    }
    assert "keyring-test-value" not in repr(state)
    assert "environment-test-value" not in repr(state)


def test_http_delete_returns_environment_fallback_without_rendering_either_secret(
    tmp_path, monkeypatch,
):
    catalog = build_catalog({"sources": []})
    scope = _credential_scope(catalog)
    keyring = MemoryCredentialStore(scope)
    keyring.set("fred", "FRED_API_KEY", "keyring-api-test-value")
    monkeypatch.setenv("FRED_API_KEY", "environment-api-test-value")
    layered = _DefaultCredentialStore(scope)
    layered._keyring = keyring
    layered._environment = EnvironmentCredentialStore(scope)
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        config_store=DataSourceConfigStore(tmp_path, catalog=catalog),
        credential_store=layered,
        usage_store=UsageStore(tmp_path / "usage"),
        now_factory=lambda: NOW,
    )
    monkeypatch.setattr(api_module, "_service", service)
    client = TestClient(app_module.app, base_url="http://127.0.0.1:8900")

    response = client.delete(
        "/api/data-sources/fred/credentials", headers=WRITE_HEADER,
    )

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "status": "environment_fallback_active",
        "last_validated_at": None,
        "credential_source": "environment",
    }
    assert "keyring-api-test-value" not in response.text
    assert "environment-api-test-value" not in response.text
