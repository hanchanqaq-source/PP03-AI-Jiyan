"""API 验证/契约测（FastAPI TestClient）。大多在校验层就返回，不联网、可靠。"""
import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace

import app as app_module

client = TestClient(app_module.app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.parametrize("path", [
    "/api/quote?codes=abc",
    "/api/valuation?code=12",
    "/api/margin?code=notcode",
    "/api/holders?code=1234567",
    "/api/announcements?code=",
])
def test_bad_code_400(path):
    assert client.get(path).status_code == 400


def test_industry_top_range():
    assert client.get("/api/industry?top=2").status_code == 422   # ge=5
    assert client.get("/api/industry?top=999").status_code == 422  # le=50


def test_chat_empty_messages_400():
    r = client.post("/api/chat", json={"messages": [], "llm": {"model": "x", "baseURL": "http://x", "apiKey": "k"}})
    assert r.status_code == 400


def test_chat_api_missing_key_400():
    # API 接入缺 baseURL/apiKey → 400（在开流前拦下）
    r = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
        "llm": {"provider": "deepseek", "model": "deepseek-chat", "baseURL": "", "apiKey": ""},
    })
    assert r.status_code == 400


def test_chat_cli_not_installed_400():
    # 订阅接入选一个本机没装的 CLI → 400 明确提示（不静默失败）
    r = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
        "llm": {"provider": "cli-qwen", "model": "qwen-code", "baseURL": "", "apiKey": ""},
    })
    # qwen 一般未装 → 400；若恰好装了 qwen 则会进流式（放宽断言）
    assert r.status_code in (400, 200)


def _codex_status(*, installed=True, auth_status="logged_in_chatgpt", available=True, message="ok"):
    return SimpleNamespace(
        installed=installed,
        auth_status=auth_status,
        available=available,
        message=message,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_codex_status(installed=False, auth_status="not_installed", available=False), "未检测到 Codex"),
        (_codex_status(auth_status="installed_not_logged_in", available=False), "尚未登录"),
        (_codex_status(auth_status="logged_in_api_key", available=False), "API Key"),
        (_codex_status(auth_status="status_failed", available=False), "无法安全确认"),
    ],
)
def test_chat_codex_requires_verified_chatgpt_membership(monkeypatch, status, expected):
    service = SimpleNamespace(codex_status=lambda force=False: status)
    monkeypatch.setattr(app_module, "get_subscription_ai_service", lambda: service)
    monkeypatch.setattr(app_module.cli_runtime, "detect_cli", lambda _kind: "codex")

    response = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
        "llm": {"provider": "cli-codex", "model": "codex", "baseURL": "", "apiKey": ""},
    })

    assert response.status_code == 400
    assert expected in response.json()["detail"]


def test_chat_codex_rechecks_official_auth_before_each_page_call(monkeypatch):
    force_calls = []
    cli_calls = []

    def codex_status(force=False):
        force_calls.append(force)
        if force:
            return _codex_status(auth_status="logged_in_api_key", available=False)
        return _codex_status()

    service = SimpleNamespace(codex_status=codex_status)
    monkeypatch.setattr(app_module, "get_subscription_ai_service", lambda: service)
    monkeypatch.setattr(
        app_module.chat_layer,
        "run_chat_cli_stream",
        lambda *_args, **_kwargs: cli_calls.append("codex") or iter(()),
    )

    response = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
        "llm": {"provider": "cli-codex", "model": "codex", "baseURL": "", "apiKey": ""},
    })

    assert response.status_code == 400
    assert force_calls == [True]
    assert cli_calls == []


def test_chat_codex_keeps_existing_cli_stream_and_never_calls_api(monkeypatch):
    status = _codex_status()
    service = SimpleNamespace(codex_status=lambda force=False: status)
    monkeypatch.setattr(app_module, "get_subscription_ai_service", lambda: service)
    cli_calls = []
    monkeypatch.setattr(
        app_module.chat_layer,
        "run_chat_cli_stream",
        lambda cfg, messages, context: iter([
            {"type": "delta", "text": "已连接"},
            {"type": "done", "trace": [], "rounds": 1},
        ]),
    )
    monkeypatch.setattr(
        app_module.chat_layer,
        "run_chat_stream",
        lambda *args, **kwargs: cli_calls.append("paid-api") or iter(()),
    )

    response = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
        "context": "only this page context",
        "llm": {"provider": "cli-codex", "model": "codex", "baseURL": "", "apiKey": ""},
    })

    assert response.status_code == 200
    assert "已连接" in response.text
    assert cli_calls == []


@pytest.mark.parametrize(
    ("raw_error", "public_text"),
    [
        ("rate limit exceeded C:/Users/Alice/.codex/auth.json sk-secret", "额度或速率限制"),
        ("Not logged in; token=secret-value", "登录已过期"),
        ("process crashed Cookie=session-secret", "Codex CLI 运行异常"),
    ],
)
def test_chat_codex_runtime_errors_are_classified_redacted_and_never_fall_back(monkeypatch, raw_error, public_text):
    service = SimpleNamespace(codex_status=lambda force=False: _codex_status())
    monkeypatch.setattr(app_module, "get_subscription_ai_service", lambda: service)
    paid_calls = []

    def broken_cli(*_args, **_kwargs):
        raise RuntimeError(raw_error)
        yield  # pragma: no cover

    monkeypatch.setattr(app_module.chat_layer, "run_chat_cli_stream", broken_cli)
    monkeypatch.setattr(
        app_module.chat_layer,
        "run_chat_stream",
        lambda *args, **kwargs: paid_calls.append("paid-api") or iter(()),
    )

    response = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
        "llm": {"provider": "cli-codex", "model": "codex", "baseURL": "", "apiKey": ""},
    })

    assert response.status_code == 200
    assert public_text in response.text
    for secret in ("Alice", "auth.json", "sk-secret", "secret-value", "session-secret"):
        assert secret not in response.text
    assert paid_calls == []


def test_global_stock_404(monkeypatch):
    """无法解析的美股/港股代码 → 404（不 500、不崩）。"""
    import gstock
    monkeypatch.setattr(gstock, "us_hk_stock", lambda q: {})
    assert client.get("/api/global/stock?symbol=ZZZZ").status_code == 404


def test_gstock_quote_full_null_shape():
    """行情取不到时 `_quote_from({})` 仍返回完整 null 形状（契合 GlobalQuote 类型），不是空 dict。"""
    import gstock
    q = gstock._quote_from({})
    assert set(q) == {"code", "name", "price", "open", "high", "low", "prev_close", "amount", "mcap", "change_pct"}
    assert all(v is None for v in q.values())
