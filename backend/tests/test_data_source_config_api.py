from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
import pytest

import app as app_module
from data_sources import api as api_module
from data_sources.catalog import build_catalog
from data_sources.config_store import DataSourceConfigStore
from data_sources.credentials import (
    CredentialState,
    CredentialStoreUnavailable,
    CredentialWriteNotSupported,
    MemoryCredentialStore,
)
from data_sources.service import DataSourceService
from data_sources.usage_store import UsageStore


NOW = datetime(2026, 8, 20, 4, 5, 6, tzinfo=timezone.utc)
SECRET = "task6-test-secret-value"


def _test_client(**kwargs):
    return TestClient(
        app_module.app,
        base_url="http://127.0.0.1:8900",
        headers={"X-PP03-Write-Intent": "1"},
        **kwargs,
    )


class _EmptyHealthService:
    def list_sources(self):
        return []

    def start_run(self, scope: str, *, excluded_adapter_ids=()):
        assert scope == "full"
        return {"run_id": "c" * 20}


class _NoCallRegistry:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def adapter(self, adapter_id: str):
        self.calls.append(adapter_id)
        raise AssertionError("provider resolution must not occur without trusted validation metadata")


class _ReadOnlyCredentialStore:
    def get(self, adapter_id: str, env_name: str):
        return "environment-secret"

    def set(self, adapter_id: str, env_name: str, value: str) -> None:
        raise CredentialWriteNotSupported("backend detail must not escape")

    def delete(self, adapter_id: str, env_name: str) -> None:
        raise CredentialWriteNotSupported("backend detail must not escape")

    def state(self, adapter_id: str) -> CredentialState:
        return CredentialState(True, "stored", None, "environment")


class _UnavailableCredentialStore(_ReadOnlyCredentialStore):
    def get(self, adapter_id: str, env_name: str):
        return None

    def set(self, adapter_id: str, env_name: str, value: str) -> None:
        raise CredentialStoreUnavailable("sensitive backend failure")

    def delete(self, adapter_id: str, env_name: str) -> None:
        raise CredentialStoreUnavailable("sensitive backend failure")

    def state(self, adapter_id: str) -> CredentialState:
        return CredentialState(False, "credential_store_unavailable", None, "keyring")


class _MemoryConfigStore:
    def __init__(self, document: dict[str, object], *, fail_save: bool = False) -> None:
        self.document = json.loads(json.dumps(document))
        self.fail_save = fail_save

    def load(self):
        return json.loads(json.dumps(self.document))

    def update_adapter(self, adapter_id: str, updates: dict[str, object]):
        entry = self.document["adapters"].setdefault(adapter_id, {})
        entry.update(updates)
        return self.load()

    def save(self, document: dict[str, object]):
        if self.fail_save:
            raise OSError("rollback storage detail")
        self.document = json.loads(json.dumps(document))
        return self.load()


class _PartiallyFailingConfigStore(_MemoryConfigStore):
    def update_adapter(self, adapter_id: str, updates: dict[str, object]):
        super().update_adapter(adapter_id, updates)
        raise OSError("write completion detail")


class _PartiallyFailingUnreadableConfigStore(_PartiallyFailingConfigStore):
    def __init__(self, document: dict[str, object]) -> None:
        super().__init__(document)
        self.unreadable = False

    def load(self):
        if self.unreadable:
            raise OSError("recovery read detail")
        return super().load()

    def update_adapter(self, adapter_id: str, updates: dict[str, object]):
        try:
            return super().update_adapter(adapter_id, updates)
        finally:
            self.unreadable = True


class _CallbackFailingCredentialStore:
    def __init__(self, *, configured: bool = False, on_set=None, on_delete=None) -> None:
        self.configured = configured
        self.on_set = on_set
        self.on_delete = on_delete
        self.calls: list[str] = []

    def get(self, adapter_id: str, env_name: str):
        raise AssertionError("rollback must not read an old credential")

    def set(self, adapter_id: str, env_name: str, value: str) -> None:
        self.calls.append("set")
        if self.on_set is not None:
            self.on_set()
        raise CredentialStoreUnavailable("secret backend detail")

    def delete(self, adapter_id: str, env_name: str) -> None:
        self.calls.append("delete")
        if self.on_delete is not None:
            self.on_delete()
        raise CredentialStoreUnavailable("secret backend detail")

    def state(self, adapter_id: str) -> CredentialState:
        return CredentialState(
            self.configured,
            "stored" if self.configured else "unconfigured",
            None,
            "test",
        )


class _RecordingCredentialStore(_CallbackFailingCredentialStore):
    def set(self, adapter_id: str, env_name: str, value: str) -> None:
        self.calls.append("set")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    catalog = build_catalog({"sources": []})
    scope = {
        adapter.adapter_id: tuple(adapter.credential_env_names)
        for adapter in catalog.adapters
        if adapter.credential_env_names
    }
    credentials = MemoryCredentialStore(scope)
    config = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    usage = UsageStore(tmp_path / "usage")
    registry = _NoCallRegistry()
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=_EmptyHealthService,
        config_store=config,
        credential_store=credentials,
        usage_store=usage,
        provider_registry=registry,
        now_factory=lambda: NOW,
    )
    monkeypatch.setattr(api_module, "_service", service)
    return _test_client(), config, credentials, usage, registry


def _row(document: dict[str, object], adapter_id: str) -> dict[str, object]:
    return next(row for row in document["adapters"] if row["adapter_id"] == adapter_id)


def test_config_is_redacted_and_defaults_to_free_only(harness):
    client, _config, _credentials, _usage, _registry = harness
    response = client.get("/api/data-sources/config")
    assert response.status_code == 200
    body = response.json()
    assert body["free_only"] is True
    assert _row(body, "fred")["credential"] == {
        "configured": False,
        "status": "unconfigured",
        "last_validated_at": None,
        "credential_source": "memory",
    }
    serialized = json.dumps(body)
    assert SECRET not in serialized
    assert "FRED_API_KEY" not in serialized


def test_credential_put_returns_only_redacted_state_and_clears_validation(harness):
    client, config, credentials, _usage, _registry = harness
    config.update_adapter("fred", {"last_validated_at": "2026-08-19T00:00:00Z"})
    response = client.put("/api/data-sources/fred/credentials", json={"credential": SECRET})
    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "status": "stored",
        "last_validated_at": None,
        "credential_source": "memory",
    }
    assert SECRET not in response.text
    assert credentials.get("fred", "FRED_API_KEY") == SECRET
    assert config.load()["adapters"]["fred"]["last_validated_at"] is None


@pytest.mark.parametrize("payload", [
    {}, {"credential": ""}, {"credential": "   "}, {"credential": "line\nbreak"},
    {"credential": "x" * 4097}, {"credential": SECRET, "unexpected": SECRET},
])
def test_credential_request_validation_is_bounded_exact_and_never_echoes_input(harness, payload):
    client, _config, credentials, _usage, _registry = harness
    response = client.put("/api/data-sources/fred/credentials", json=payload)
    assert response.status_code == 422
    assert SECRET not in response.text
    assert "line\\nbreak" not in response.text
    assert credentials.state("fred").configured is False


def test_credential_request_body_size_is_bounded_without_echo(harness):
    client, _config, _credentials, _usage, _registry = harness
    response = client.put(
        "/api/data-sources/fred/credentials",
        content=b'{"credential":"' + b"z" * 9000 + b'"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert "zzzzzz" not in response.text


@pytest.mark.parametrize(("method", "path", "body"), [
    ("put", "/api/data-sources/not-real/credentials", {"credential": SECRET}),
    ("delete", "/api/data-sources/not-real/credentials", None),
    ("put", "/api/data-sources/not-real/config", {"daily_budget": "1.00"}),
    ("post", "/api/data-sources/not-real/enable", {}),
    ("post", "/api/data-sources/not-real/validate", {}),
])
def test_unknown_adapter_ids_are_404_and_redacted(harness, method, path, body):
    client, _config, _credentials, _usage, _registry = harness
    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
    assert response.status_code == 404
    assert SECRET not in response.text


def test_enterprise_and_catalog_only_sources_cannot_be_enabled(harness):
    client, config, _credentials, _usage, _registry = harness
    enterprise = client.post("/api/data-sources/bloomberg/enable", json={})
    catalog_only = client.post("/api/data-sources/imf/enable", json={})
    assert enterprise.status_code == 409
    assert enterprise.json()["detail"]["code"] == "license_required"
    assert catalog_only.status_code == 409
    assert catalog_only.json()["detail"]["code"] == "catalog_only"
    assert config.load()["adapters"] == {}


def test_missing_credential_validation_is_unconfigured_and_zero_provider_calls(harness):
    client, _config, _credentials, _usage, registry = harness
    response = client.post("/api/data-sources/fred/validate", json={})
    assert response.status_code == 200
    assert response.json() == {
        "adapter_id": "fred", "status": "unconfigured", "connected": False,
        "health_failure": False, "last_validated_at": None,
    }
    assert registry.calls == []


def test_configured_keyed_adapter_without_trusted_validation_transport_fails_closed(harness):
    client, _config, _credentials, _usage, registry = harness
    assert client.put("/api/data-sources/fred/credentials", json={"credential": SECRET}).status_code == 200
    response = client.post("/api/data-sources/fred/validate", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "unsupported_credential_transport"
    assert registry.calls == []
    assert SECRET not in response.text


def test_legacy_validate_unsupported_transport_is_non_health_failure_and_zero_run(
    tmp_path, monkeypatch,
):
    class RecordingHealth(_EmptyHealthService):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def start_run(self, scope: str, *, excluded_adapter_ids=()):
            self.calls.append(scope)
            return super().start_run(scope, excluded_adapter_ids=excluded_adapter_ids)

    catalog = build_catalog({"sources": []})
    scope = {
        adapter.adapter_id: tuple(adapter.credential_env_names)
        for adapter in catalog.adapters if adapter.credential_env_names
    }
    config = DataSourceConfigStore(tmp_path / "legacy-config", catalog=catalog)
    credentials = MemoryCredentialStore(scope)
    credentials.set("fred", "FRED_API_KEY", SECRET)
    health = RecordingHealth()
    registry = _NoCallRegistry()
    monkeypatch.setattr(api_module, "_service", DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=lambda: health,
        config_store=config,
        credential_store=credentials,
        usage_store=UsageStore(tmp_path / "legacy-usage"),
        provider_registry=registry,
        now_factory=lambda: NOW,
    ))

    response = _test_client().post(
        "/api/data-sources/fred/validate", json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "unsupported_credential_transport"
    assert health.calls == []
    assert registry.calls == []
    assert SECRET not in response.text


def test_adapter_config_requires_canonical_decimal_strings_and_exact_fields(harness):
    client, config, _credentials, _usage, _registry = harness
    response = client.put("/api/data-sources/fmp/config", json={
        "usage_mode": "paid_api", "daily_budget": "1.25", "monthly_budget": "12.50",
        "per_request_budget": "0.10", "daily_request_limit": 20, "monthly_request_limit": 200,
    })
    assert response.status_code == 200
    assert config.load()["adapters"]["fmp"] == {
        "usage_mode": "paid_api", "daily_budget": "1.25", "monthly_budget": "12.50",
        "per_request_budget": "0.10", "daily_request_limit": 20, "monthly_request_limit": 200,
    }
    for invalid in (1, 1.0, True, "NaN", "Infinity", "1e2", "-1", ".5"):
        assert client.put("/api/data-sources/fmp/config", json={"daily_budget": invalid}).status_code == 422
    unknown = client.put("/api/data-sources/fmp/config", json={"api_key": SECRET})
    assert unknown.status_code == 422
    assert SECRET not in unknown.text


def test_config_rejects_credential_markers_as_user_input_not_store_failure(harness):
    client, config, _credentials, _usage, _registry = harness
    response = client.put("/api/data-sources/fmp/config", json={"usage_mode": "api_key"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert config.load()["adapters"] == {}


def test_duplicate_secret_json_fields_are_rejected_without_storing_either_value(harness):
    client, _config, credentials, _usage, _registry = harness
    response = client.put(
        "/api/data-sources/fred/credentials",
        content=b'{"credential":"first-secret","credential":"second-secret"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert "first-secret" not in response.text
    assert "second-secret" not in response.text
    assert credentials.state("fred").configured is False


def test_oversized_json_integer_is_a_redacted_422_not_an_unhandled_error(harness):
    _client, _config, _credentials, _usage, _registry = harness
    client = _test_client(raise_server_exceptions=False)
    response = client.put(
        "/api/data-sources/fmp/config",
        content=b'{"daily_request_limit":' + b"9" * 5000 + b'}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert "999999" not in response.text


class _WriteFailingConfigStore:
    def load(self):
        return {"free_only": True, "adapters": {}}

    def update_adapter(self, adapter_id: str, updates: dict[str, object]):
        raise OSError("filesystem detail")


def test_config_write_failure_is_bounded_503_without_backend_detail(harness, monkeypatch):
    _client, _config, credentials, usage, registry = harness
    catalog = build_catalog({"sources": []})
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=_EmptyHealthService,
        config_store=_WriteFailingConfigStore(),
        credential_store=credentials,
        usage_store=usage,
        provider_registry=registry,
        now_factory=lambda: NOW,
    )
    monkeypatch.setattr(api_module, "_service", service)
    client = _test_client(raise_server_exceptions=False)

    response = client.post("/api/data-sources/tencent-quote/enable", json={})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "configuration_store_unavailable"
    assert "filesystem detail" not in response.text


def test_free_only_update_and_free_adapter_toggle_are_persisted(harness):
    client, config, _credentials, _usage, _registry = harness
    assert client.put("/api/data-sources/config", json={"free_only": False}).json()["free_only"] is False
    enabled = client.post("/api/data-sources/tencent-quote/enable", json={})
    disabled = client.post("/api/data-sources/tencent-quote/disable", json={})
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    document = config.load()
    assert document["free_only"] is False
    assert document["adapters"]["tencent-quote"]["enabled"] is False


def test_paid_enable_requires_budgets_confirmation_credential_and_supported_transport(harness):
    client, config, _credentials, _usage, registry = harness
    assert client.put("/api/data-sources/config", json={"free_only": False}).status_code == 200
    assert client.put("/api/data-sources/fmp/credentials", json={"credential": SECRET}).status_code == 200
    missing = client.post("/api/data-sources/fmp/enable", json={"confirm_paid_usage": True})
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "budget_required"
    assert client.put("/api/data-sources/fmp/config", json={
        "daily_budget": "1", "monthly_budget": "5", "per_request_budget": "0.10",
    }).status_code == 200
    unconfirmed = client.post("/api/data-sources/fmp/enable", json={})
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["detail"]["code"] == "explicit_confirmation_required"
    unsupported = client.post("/api/data-sources/fmp/enable", json={"confirm_paid_usage": True})
    assert unsupported.status_code == 409
    assert unsupported.json()["detail"]["code"] == "unsupported_credential_transport"
    assert config.load()["adapters"]["fmp"].get("enabled") is not True
    assert registry.calls == []


def test_environment_mutation_and_keyring_failure_have_bounded_redacted_errors(harness, monkeypatch):
    client, config, _credentials, usage, registry = harness
    catalog = build_catalog({"sources": []})
    for store, expected_status, expected_code in (
        (_ReadOnlyCredentialStore(), 409, "credential_source_read_only"),
        (_UnavailableCredentialStore(), 503, "credential_store_unavailable"),
    ):
        monkeypatch.setattr(api_module, "_service", DataSourceService(
            catalog_builder=lambda: catalog, health_service_factory=_EmptyHealthService,
            config_store=config, credential_store=store, usage_store=usage,
            provider_registry=registry, now_factory=lambda: NOW,
        ))
        response = client.put("/api/data-sources/fred/credentials", json={"credential": SECRET})
        assert response.status_code == expected_status
        assert response.json()["detail"]["code"] == expected_code
        assert SECRET not in response.text
        assert "backend detail" not in response.text
        assert "sensitive backend" not in response.text


def test_double_submit_and_enable_config_race_do_not_lose_adapter_fields(harness):
    client, config, _credentials, _usage, _registry = harness
    def set_budget():
        return client.put("/api/data-sources/tencent-quote/config", json={
            "daily_budget": "1.00", "monthly_budget": "5.00", "per_request_budget": "0.10",
        }).status_code
    def enable():
        return client.post("/api/data-sources/tencent-quote/enable", json={}).status_code
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda fn: fn(), [set_budget, enable, set_budget, enable]))
    assert results == [200, 200, 200, 200]
    stored = config.load()["adapters"]["tencent-quote"]
    assert stored["enabled"] is True
    assert stored["daily_budget"] == "1.00"
    assert stored["monthly_budget"] == "5.00"
    assert stored["per_request_budget"] == "0.10"


def test_credential_delete_is_idempotent_and_returns_state_only(harness):
    client, _config, _credentials, _usage, _registry = harness
    client.put("/api/data-sources/fred/credentials", json={"credential": SECRET})
    first = client.delete("/api/data-sources/fred/credentials")
    second = client.delete("/api/data-sources/fred/credentials")
    expected = {"configured": False, "status": "unconfigured", "last_validated_at": None, "credential_source": "memory"}
    assert first.status_code == 200 and first.json() == expected
    assert second.status_code == 200 and second.json() == expected
    assert SECRET not in first.text + second.text


def test_global_config_update_does_not_overwrite_concurrent_adapter_update(tmp_path):
    catalog = build_catalog({"sources": []})
    scope = {
        adapter.adapter_id: tuple(adapter.credential_env_names)
        for adapter in catalog.adapters
        if adapter.credential_env_names
    }
    root = tmp_path / "shared-config"
    primary = DataSourceConfigStore(root, catalog=catalog)
    peer = DataSourceConfigStore(root, catalog=catalog)
    credentials = MemoryCredentialStore(scope)
    usage = UsageStore(tmp_path / "usage")
    original_load = primary.load
    begin_peer = threading.Event()
    peer_done = threading.Event()

    def coordinated_load():
        document = original_load()
        begin_peer.set()
        assert peer_done.wait(2)
        return document

    primary.load = coordinated_load

    def peer_update():
        if not begin_peer.wait(0.2):
            begin_peer.set()
        peer.update_adapter("fmp", {"daily_budget": "1.00"})
        peer_done.set()

    worker = threading.Thread(target=peer_update)
    worker.start()
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=_EmptyHealthService,
        config_store=primary,
        credential_store=credentials,
        usage_store=usage,
        provider_registry=_NoCallRegistry(),
        now_factory=lambda: NOW,
    )

    service.update_free_only(False)
    worker.join(2)

    primary.load = original_load
    assert worker.is_alive() is False
    assert primary.load() == {
        "free_only": False,
        "adapters": {"fmp": {"daily_budget": "1.00"}},
    }


def _transaction_client(monkeypatch, tmp_path, config, credentials):
    catalog = build_catalog({"sources": []})
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=_EmptyHealthService,
        config_store=config,
        credential_store=credentials,
        usage_store=UsageStore(tmp_path / "transaction-usage"),
        provider_registry=_NoCallRegistry(),
        now_factory=lambda: NOW,
    )
    monkeypatch.setattr(api_module, "_service", service)
    return _test_client(raise_server_exceptions=False)


def test_failed_credential_put_restores_exact_prior_adapter_config(tmp_path, monkeypatch):
    catalog = build_catalog({"sources": []})
    config = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    before = {
        "free_only": True,
        "adapters": {"fred": {
            "enabled": True,
            "last_validated_at": "2026-08-19T00:00:00Z",
            "daily_budget": "2.00",
        }},
    }
    config.save(before)
    client = _transaction_client(
        monkeypatch, tmp_path, config, _CallbackFailingCredentialStore()
    )

    response = client.put("/api/data-sources/fred/credentials", json={"credential": SECRET})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "credential_store_unavailable"
    assert config.load() == before
    assert SECRET not in response.text


def test_failed_credential_delete_restores_exact_prior_adapter_config(tmp_path, monkeypatch):
    catalog = build_catalog({"sources": []})
    config = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    before = {
        "free_only": False,
        "adapters": {"fred": {
            "enabled": True,
            "last_validated_at": "2026-08-19T00:00:00Z",
            "monthly_request_limit": 25,
        }},
    }
    config.save(before)
    client = _transaction_client(
        monkeypatch, tmp_path, config,
        _CallbackFailingCredentialStore(configured=True),
    )

    response = client.delete("/api/data-sources/fred/credentials")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "credential_store_unavailable"
    assert config.load() == before


def test_credential_rollback_preserves_concurrent_unrelated_adapter_fields(tmp_path, monkeypatch):
    config = _MemoryConfigStore({
        "free_only": True,
        "adapters": {"fred": {
            "enabled": True,
            "last_validated_at": "2026-08-19T00:00:00Z",
            "daily_budget": "1.00",
        }},
    })
    credentials = _CallbackFailingCredentialStore(
        on_set=lambda: config.update_adapter("fred", {"daily_budget": "2.00"}),
    )
    client = _transaction_client(monkeypatch, tmp_path, config, credentials)

    response = client.put("/api/data-sources/fred/credentials", json={"credential": SECRET})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "credential_store_unavailable"
    assert config.load()["adapters"]["fred"] == {
        "enabled": True,
        "last_validated_at": "2026-08-19T00:00:00Z",
        "daily_budget": "2.00",
    }


def test_credential_rollback_does_not_clobber_concurrent_operation_field(tmp_path, monkeypatch):
    config = _MemoryConfigStore({
        "free_only": True,
        "adapters": {"fred": {"enabled": True}},
    })
    credentials = _CallbackFailingCredentialStore(
        on_set=lambda: config.update_adapter("fred", {"enabled": True}),
    )
    client = _transaction_client(monkeypatch, tmp_path, config, credentials)

    response = client.put("/api/data-sources/fred/credentials", json={"credential": SECRET})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "configuration_recovery_required"
    assert config.load()["adapters"]["fred"]["enabled"] is True
    assert SECRET not in response.text


def test_credential_rollback_failure_is_bounded_and_never_false_success(tmp_path, monkeypatch):
    config = _MemoryConfigStore({
        "free_only": True,
        "adapters": {"fred": {
            "enabled": True,
            "last_validated_at": "2026-08-19T00:00:00Z",
        }},
    }, fail_save=True)
    client = _transaction_client(
        monkeypatch, tmp_path, config, _CallbackFailingCredentialStore()
    )

    response = client.put("/api/data-sources/fred/credentials", json={"credential": SECRET})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "configuration_recovery_required"
    assert config.load()["adapters"]["fred"] == {
        "enabled": False,
        "last_validated_at": None,
    }
    assert "rollback storage detail" not in response.text


def test_config_failure_happens_before_credential_mutation(tmp_path, monkeypatch):
    credentials = _RecordingCredentialStore()
    client = _transaction_client(
        monkeypatch, tmp_path, _WriteFailingConfigStore(), credentials,
    )

    response = client.put("/api/data-sources/fred/credentials", json={"credential": SECRET})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "configuration_store_unavailable"
    assert credentials.calls == []
    assert SECRET not in response.text


def test_partial_config_failure_is_compensated_before_any_credential_mutation(
    tmp_path, monkeypatch,
):
    before = {
        "free_only": True,
        "adapters": {"fred": {
            "enabled": True,
            "last_validated_at": "2026-08-19T00:00:00Z",
            "daily_budget": "3.00",
        }},
    }
    config = _PartiallyFailingConfigStore(before)
    credentials = _RecordingCredentialStore()
    client = _transaction_client(monkeypatch, tmp_path, config, credentials)

    response = client.put(
        "/api/data-sources/fred/credentials", json={"credential": SECRET},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "configuration_store_unavailable"
    assert config.load() == before
    assert credentials.calls == []
    assert "write completion detail" not in response.text


def test_partial_config_failure_with_unreadable_recovery_state_requires_recovery(
    tmp_path, monkeypatch,
):
    config = _PartiallyFailingUnreadableConfigStore({
        "free_only": True, "adapters": {"fred": {"enabled": True}},
    })
    credentials = _RecordingCredentialStore()
    client = _transaction_client(monkeypatch, tmp_path, config, credentials)

    response = client.put(
        "/api/data-sources/fred/credentials", json={"credential": SECRET},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "configuration_recovery_required"
    assert credentials.calls == []
    assert "recovery read detail" not in response.text


def test_put_cors_preflight_is_allowed_without_broadening_method_or_origin_policy():
    production = next(
        middleware for middleware in app_module.app.user_middleware
        if middleware.cls is CORSMiddleware
    )
    options = dict(production.kwargs)
    options["allow_origins"] = ["http://127.0.0.1:5899"]
    strict_app = FastAPI()
    strict_app.add_middleware(CORSMiddleware, **options)
    client = TestClient(strict_app)
    headers = {
        "Origin": "http://127.0.0.1:5899",
        "Access-Control-Request-Method": "PUT",
    }
    for path in (
        "/api/data-sources/config",
        "/api/data-sources/fred/credentials",
    ):
        allowed = client.options(path, headers=headers)
        assert allowed.status_code == 200
        assert "PUT" in allowed.headers["access-control-allow-methods"]

    disallowed_method = client.options(
        "/api/data-sources/config",
        headers={**headers, "Access-Control-Request-Method": "PATCH"},
    )
    disallowed_origin = client.options(
        "/api/data-sources/config",
        headers={**headers, "Origin": "https://unapproved.example"},
    )
    assert disallowed_method.status_code == 400
    assert disallowed_origin.status_code == 400
    assert "access-control-allow-origin" not in disallowed_origin.headers
