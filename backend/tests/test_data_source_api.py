from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

import app as app_module
import source_health


class EmptyHealthService:
    def list_sources(self):
        return []

    def start_run(self, scope: str):
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
        def start_run(self, scope: str):
            raise source_health.FullRunConflict("credential=secret-token")

    monkeypatch.setattr(app_module.source_health, "get_service", lambda: ConflictingHealthService())
    response = client.post("/api/data-sources/refresh")

    assert response.status_code == 409
    assert "secret-token" not in response.text
