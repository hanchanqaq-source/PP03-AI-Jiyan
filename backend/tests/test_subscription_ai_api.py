"""FastAPI contracts for the local-only Codex subscription actions."""

import pytest
from fastapi.testclient import TestClient

import app as app_module
from subscription_ai.codex_provider import LoginSwitchConfirmationRequired


PROVIDER_ROW = {
    "provider_id": "codex",
    "installed": True,
    "version": "0.145.0",
    "auth_status": "logged_in_chatgpt",
    "available": True,
    "test_status": "not_tested",
    "last_test_at": None,
    "message": "已使用 ChatGPT 登录。",
}


class FakeService:
    def __init__(self):
        self.calls = []
        self.login_error = None

    def list_providers(self, force=False):
        self.calls.append(("list", force))
        return [dict(PROVIDER_ROW)]

    def start_codex_login(self, confirm_switch=False):
        self.calls.append(("login", confirm_switch))
        if self.login_error:
            raise self.login_error
        return {
            **PROVIDER_ROW,
            "auth_status": "installed_not_logged_in",
            "available": False,
            "message": "等待官方登录完成。",
        }

    def test_codex(self):
        self.calls.append(("test",))
        return {
            **PROVIDER_ROW,
            "test_status": "success",
            "message": "Codex 连接成功。",
            "last_test_at": "2026-08-29T00:00:00+00:00",
        }

    def cancel_codex_test(self):
        self.calls.append(("cancel",))
        return {
            **PROVIDER_ROW,
            "test_status": "cancelled",
            "message": "Codex 连接测试已取消。",
            "last_test_at": "2026-08-29T00:00:00+00:00",
        }


@pytest.fixture
def fake_service(monkeypatch):
    service = FakeService()
    monkeypatch.setattr(app_module, "get_subscription_ai_service", lambda: service, raising=False)
    return service


def local_client():
    return TestClient(
        app_module.app,
        base_url="http://127.0.0.1:8900",
        client=("127.0.0.1", 51000),
    )


def test_provider_list_returns_only_public_status_rows(fake_service):
    response = local_client().get("/api/subscription-ai/providers?refresh=true")

    assert response.status_code == 200
    assert response.json() == [PROVIDER_ROW]
    assert fake_service.calls == [("list", True)]
    body = response.text.lower()
    for forbidden in ("executable_path", "raw_stderr", "auth.json", "codex_home", "c:\\users\\"):
        assert forbidden not in body


@pytest.mark.parametrize(
    ("path", "payload", "expected_call"),
    [
        ("/api/subscription-ai/codex/login", {"confirm_switch": True}, ("login", True)),
        ("/api/subscription-ai/codex/test", None, ("test",)),
        ("/api/subscription-ai/codex/cancel", None, ("cancel",)),
    ],
)
def test_local_codex_actions_are_available(path, payload, expected_call, fake_service):
    kwargs = {"headers": {"Origin": "http://127.0.0.1:5899"}}
    if payload is not None:
        kwargs["json"] = payload

    response = local_client().post(path, **kwargs)

    assert response.status_code == 200
    assert fake_service.calls[-1] == expected_call
    assert set(response.json()) == set(PROVIDER_ROW)


@pytest.mark.parametrize("path", [
    "/api/subscription-ai/codex/login",
    "/api/subscription-ai/codex/test",
    "/api/subscription-ai/codex/cancel",
])
def test_remote_socket_cannot_be_spoofed_as_loopback(path, fake_service):
    client = TestClient(
        app_module.app,
        base_url="http://127.0.0.1:8900",
        client=("203.0.113.8", 51000),
    )

    response = client.post(
        path,
        json={"confirm_switch": True} if path.endswith("login") else None,
        headers={
            "Origin": "http://127.0.0.1:5899",
            "X-Forwarded-For": "127.0.0.1",
        },
    )

    assert response.status_code == 403
    assert fake_service.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "example.com", "Origin": "http://127.0.0.1:5899"},
        {"Host": "127.0.0.1:8900", "Origin": "https://example.com"},
    ],
)
def test_non_loopback_host_or_browser_origin_is_rejected(headers, fake_service):
    response = local_client().post("/api/subscription-ai/codex/test", headers=headers)

    assert response.status_code == 403
    assert fake_service.calls == []


def test_api_key_switch_confirmation_is_a_structured_conflict(fake_service):
    fake_service.login_error = LoginSwitchConfirmationRequired("继续登录可能替换当前认证方式")

    response = local_client().post(
        "/api/subscription-ai/codex/login",
        json={"confirm_switch": False},
        headers={"Origin": "http://127.0.0.1:5899"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "继续登录可能替换当前认证方式"}
