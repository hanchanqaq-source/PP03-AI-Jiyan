from __future__ import annotations

from fastapi.testclient import TestClient

import app as app_module


WRITE_HEADERS = {
    "X-PP03-Write-Intent": "1",
    "Origin": "http://127.0.0.1:5899",
    "Host": "127.0.0.1:8900",
}


class FakeManager:
    def __init__(self):
        self.enabled = True

    def list_sources(self):
        return {"store_status": "ok", "summary": {"total": 0, "built_in": 0, "custom": 0, "enabled": 0}, "sources": []}

    def test_definition(self, definition):
        return {"ok": True, "http_status": 200, "feed_format": "rss", "sample_title": "One", "sample_link": "https://x.example/1"}

    def add_source(self, definition):
        return {"id": "custom-1", "built_in": False, "enabled": True, **definition}

    def test_source(self, source_id):
        return {"ok": True, "source_id": source_id, "http_status": 200}

    def set_enabled(self, source_id, enabled):
        self.enabled = enabled
        return {"id": source_id, "enabled": enabled}

    def delete_source(self, source_id):
        return {"id": source_id, "deleted": True}


def _client(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(app_module, "get_news_source_manager", lambda: manager)
    return TestClient(app_module.app, base_url="http://127.0.0.1:8900"), manager


def test_source_list_is_read_only_and_write_routes_require_local_intent(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.get("/api/radar/sources").status_code == 200

    body = {"source_type": "rss", "name": "Custom", "url": "https://feed.example/rss", "hint": "ai"}
    assert client.post("/api/radar/sources/test", json=body).status_code == 403
    assert client.post("/api/radar/sources", json=body).status_code == 403
    assert client.post("/api/radar/sources", json=body, headers=WRITE_HEADERS).status_code == 201


def test_source_test_toggle_and_delete_contract(monkeypatch):
    client, manager = _client(monkeypatch)
    body = {"source_type": "rss", "name": "Custom", "url": "https://feed.example/rss", "hint": "ai"}

    tested = client.post("/api/radar/sources/test", json=body, headers=WRITE_HEADERS)
    existing = client.post("/api/radar/sources/custom-1/test", headers=WRITE_HEADERS)
    disabled = client.put("/api/radar/sources/custom-1/enabled", json={"enabled": False}, headers=WRITE_HEADERS)
    deleted = client.delete("/api/radar/sources/custom-1", headers=WRITE_HEADERS)

    assert tested.status_code == 200 and tested.json()["data"]["feed_format"] == "rss"
    assert existing.status_code == 200 and existing.json()["data"]["source_id"] == "custom-1"
    assert disabled.status_code == 200 and manager.enabled is False
    assert deleted.status_code == 200 and deleted.json()["data"]["deleted"] is True


def test_builtin_source_delete_is_reported_as_conflict(monkeypatch):
    client, manager = _client(monkeypatch)

    def reject_builtin_delete(source_id):
        raise app_module.news_source_manager.BuiltinSourceDeletionError(
            "内置来源不可删除，只能停用"
        )

    manager.delete_source = reject_builtin_delete
    response = client.delete("/api/radar/sources/builtin-1", headers=WRITE_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"] == "内置来源不可删除，只能停用"


def test_duplicate_source_url_is_reported_as_conflict(monkeypatch):
    client, manager = _client(monkeypatch)

    def reject_duplicate_source(definition):
        raise app_module.news_source_manager.DuplicateSourceError(
            "该来源 URL 已存在"
        )

    manager.add_source = reject_duplicate_source
    response = client.post(
        "/api/radar/sources",
        json={
            "source_type": "rss",
            "name": "Duplicate",
            "url": "https://feed.example/rss",
            "hint": "ai",
        },
        headers=WRITE_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "该来源 URL 已存在"


def test_source_writes_reject_missing_or_foreign_browser_origin(monkeypatch):
    client, _ = _client(monkeypatch)
    body = {"source_type": "rss", "name": "Custom", "url": "https://feed.example/rss", "hint": "ai"}
    assert client.post("/api/radar/sources", json=body, headers={
        "X-PP03-Write-Intent": "1", "Host": "127.0.0.1:8900",
    }).status_code == 403
    assert client.post("/api/radar/sources", json=body, headers={
        "X-PP03-Write-Intent": "1", "Host": "127.0.0.1:8900", "Origin": "https://foreign.example",
    }).status_code == 403


def test_source_writes_still_require_global_api_key_when_configured(monkeypatch):
    client, _ = _client(monkeypatch)
    monkeypatch.setattr(app_module, "_API_KEY", "test-key")
    body = {"source_type": "rss", "name": "Custom", "url": "https://feed.example/rss", "hint": "ai"}

    assert client.post("/api/radar/sources", json=body, headers=WRITE_HEADERS).status_code == 401
    approved = client.post("/api/radar/sources", json=body, headers={
        **WRITE_HEADERS, "Authorization": "Bearer test-key",
    })
    assert approved.status_code == 201
