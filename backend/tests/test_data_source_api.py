from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

import app as app_module
import source_health
from data_sources.catalog import build_catalog


class EmptyHealthService:
    def list_sources(self):
        return []

    def start_run(self, scope: str, *, excluded_adapter_ids=()):
        assert scope == "full"
        return {"run_id": "a" * 20}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module.source_health, "get_service", lambda: EmptyHealthService())
    return TestClient(app_module.app)


def test_catalog_api_is_complete_without_portfolio(client):
    response = client.get("/api/data-sources/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["registration"]["news_sources"] == 108
    assert body["portfolio_relation"]["status"] == "unavailable_no_holdings"
    assert body["observed"]["sources"] == 0


def test_catalog_api_marks_missing_health_overlay_as_unexamined(client):
    body = client.get("/api/data-sources/catalog").json()
    eastmoney = next(row for row in body["families"] if row["source_family_id"] == "eastmoney")

    assert eastmoney["health_status"] == "unexamined"
    assert eastmoney["adapters"][0]["capabilities"][0]["health_status"] == "unexamined"


def test_family_api_aggregates_eastmoney(client):
    response = client.get("/api/data-sources/families/eastmoney")

    assert response.status_code == 200
    body = response.json()
    assert body["source_family_id"] == "eastmoney"
    assert len(body["adapters"]) == 3


def test_unknown_data_source_ids_return_404(client):
    assert client.get("/api/data-sources/families/not-a-family").status_code == 404
    assert client.post("/api/data-sources/not-an-adapter/validate").status_code == 404


def test_refresh_reuses_source_health_run_lifecycle(client):
    response = client.post("/api/data-sources/refresh")

    assert response.status_code == 202
    assert response.json() == {"run_id": "a" * 20}


def test_refresh_conflict_is_409_and_validation_failure_is_redacted(client, monkeypatch):
    class ConflictingHealthService(EmptyHealthService):
        def start_run(self, scope: str, *, excluded_adapter_ids=()):
            raise source_health.FullRunConflict("credential=secret-token")

    monkeypatch.setattr(app_module.source_health, "get_service", lambda: ConflictingHealthService())
    response = client.post("/api/data-sources/refresh")

    assert response.status_code == 409
    assert "secret-token" not in response.text


def test_local_toggle_excludes_disabled_adapter_from_refresh_and_validation():
    class RecordingHealthService(EmptyHealthService):
        def __init__(self):
            self.calls = []

        def start_run(self, scope: str, *, excluded_adapter_ids=()):
            self.calls.append((scope, set(excluded_adapter_ids)))
            return {"run_id": "b" * 20}

    health = RecordingHealthService()
    from data_sources.service import DataSourceService

    service = DataSourceService(health_service_factory=lambda: health)
    assert service.adapter_action("tencent-quote", "disable")["enabled"] is False

    assert service.refresh() == {"run_id": "b" * 20}
    default_disabled = {adapter.adapter_id for adapter in build_catalog(news_config={"sources": []}).adapters if not adapter.default_enabled}
    assert health.calls == [("full", {"tencent-quote", *default_disabled})]
    assert service.adapter_action("tencent-quote", "validate")["status"] == "configuration_barrier"
    assert len(health.calls) == 1

    assert service.adapter_action("tencent-quote", "enable")["enabled"] is True
    assert service.refresh() == {"run_id": "b" * 20}
    assert health.calls[-1] == ("full", default_disabled)


def test_disabled_or_license_review_adapter_keeps_an_honest_barrier():
    from data_sources.service import DataSourceService

    result = DataSourceService(health_service_factory=EmptyHealthService).adapter_action(
        "efinance-eastmoney", "enable"
    )

    assert result == {
        "adapter_id": "efinance-eastmoney",
        "action": "enable",
        "status": "configuration_barrier",
        "catalog_status": "disabled",
        "connected": False,
    }
