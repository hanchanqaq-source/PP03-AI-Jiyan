"""Codex subscription provider contracts (offline; no real CLI or credentials)."""

import pytest
import os
import subprocess
import threading
from types import SimpleNamespace


def _provider_class():
    try:
        from subscription_ai.codex_provider import CodexSubscriptionProvider
    except ModuleNotFoundError:
        pytest.fail("CodexSubscriptionProvider is not implemented yet", pytrace=False)
    return CodexSubscriptionProvider


def test_missing_codex_is_not_available():
    provider = _provider_class()(find_binary=lambda _kind: None)

    status = provider.get_status(force=True)

    assert status.installed is False
    assert status.available is False
    assert status.auth_status == "not_installed"


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _installed_provider(results, **provider_kwargs):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return results.pop(0)

    provider = _provider_class()(
        find_binary=lambda _kind: "C:/tools/codex.exe",
        run_command=run,
        **provider_kwargs,
    )
    return provider, calls


def test_every_probe_overrides_inherited_global_codex_home_and_credentials(tmp_path):
    product_home = tmp_path / "profile" / "codex-home"
    inherited = {
        "PATH": os.environ.get("PATH", ""),
        "CODEX_HOME": r"C:\Users\sentinel\.codex",
        "CODEX_API_KEY": "must-not-forward",
        "CODEX_ACCESS_TOKEN": "must-not-forward",
        "OPENAI_API_KEY": "must-not-forward",
    }
    provider, calls = _installed_provider(
        [
            _completed(stdout="codex-cli 0.145.0"),
            _completed(stderr="Logged in using ChatGPT"),
        ],
        codex_home=product_home,
        base_env=inherited,
    )

    provider.get_status(force=True)

    assert product_home.is_dir()
    assert len(calls) == 2
    for _args, kwargs in calls:
        assert kwargs["env"]["CODEX_HOME"] == str(product_home.resolve())
        assert "CODEX_API_KEY" not in kwargs["env"]
        assert "CODEX_ACCESS_TOKEN" not in kwargs["env"]
        assert "OPENAI_API_KEY" not in kwargs["env"]


def test_public_provider_contract_is_the_exact_response_whitelist(tmp_path):
    provider, _calls = _installed_provider(
        [
            _completed(stdout="codex-cli 0.145.0"),
            _completed(stderr="Logged in using ChatGPT"),
        ],
        codex_home=tmp_path / "data" / "codex-home",
    )

    row = provider.get_status(force=True).to_public_dict()

    assert set(row) == {
        "provider_id",
        "installed",
        "version",
        "auth_status",
        "available",
        "test_status",
        "message",
        "last_test_at",
    }
    assert "CODEX_HOME" not in str(row)
    assert "auth.json" not in str(row)


def test_chatgpt_login_is_the_only_available_membership_mode():
    provider, calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0\n"),
        _completed(stderr="Logged in using ChatGPT\n"),
    ])

    status = provider.get_status(force=True)

    assert status.version == "0.145.0"
    assert status.auth_status == "logged_in_chatgpt"
    assert status.available is True
    assert [call[0][1:] for call in calls] == [["--version"], ["login", "status"]]


def test_api_key_login_is_not_membership_and_never_exposes_key_fragment():
    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using an API key - sk-sensitive-fragment"),
    ])

    status = provider.get_status(force=True)

    assert status.auth_status == "logged_in_api_key"
    assert status.available is False
    assert "API Key" in status.message
    assert "sk-sensitive" not in status.message


def test_not_logged_in_is_distinguished_from_status_error():
    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(returncode=1, stderr="Not logged in"),
    ])

    status = provider.get_status(force=True)

    assert status.auth_status == "installed_not_logged_in"
    assert status.available is False


@pytest.mark.parametrize(
    ("status_result", "expected_status"),
    [
        (_completed(stderr="Logged in using access token"), "status_failed"),
        (_completed(returncode=2, stderr="error: unrecognized subcommand 'status'"), "unsupported_version"),
        (_completed(returncode=2, stderr="Error checking login status: store unavailable"), "status_failed"),
    ],
)
def test_unknown_or_failed_status_never_infers_chatgpt(status_result, expected_status):
    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        status_result,
    ])

    status = provider.get_status(force=True)

    assert status.auth_status == expected_status
    assert status.available is False


def test_version_failure_is_unsupported_even_if_auth_output_claims_chatgpt():
    provider, _calls = _installed_provider([
        _completed(returncode=1, stderr="version command failed"),
        _completed(stderr="Logged in using ChatGPT"),
    ])

    status = provider.get_status(force=True)

    assert status.version is None
    assert status.auth_status == "unsupported_version"
    assert status.available is False


def test_non_forced_status_reuses_verified_state_until_explicit_refresh():
    provider, calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using ChatGPT"),
    ])

    first = provider.get_status(force=True)
    provider._monotonic = lambda: 999999.0
    second = provider.get_status()

    assert second == first
    assert len(calls) == 2


def test_chatgpt_login_does_not_start_login_again():
    spawned = []
    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using ChatGPT"),
    ])
    provider._popen = lambda *args, **kwargs: spawned.append((args, kwargs))

    result = provider.start_login()

    assert result["status"] == "already_authenticated"
    assert spawned == []


def test_api_key_login_requires_explicit_switch_confirmation():
    from subscription_ai.codex_provider import LoginSwitchConfirmationRequired

    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using an API key - sk-redacted"),
    ])

    with pytest.raises(LoginSwitchConfirmationRequired, match="可能替换"):
        provider.start_login(confirm_switch=False)


def test_confirmed_switch_starts_visible_official_login_without_capturing_output(tmp_path):

    spawned = []
    product_home = tmp_path / "profile" / "codex-home"

    class LoginProcess:
        def poll(self):
            return None

    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using an API key - sk-redacted"),
    ], codex_home=product_home, base_env={
        "PATH": os.environ.get("PATH", ""),
        "CODEX_HOME": r"C:\Users\sentinel\.codex",
        "CODEX_API_KEY": "must-not-forward",
        "OPENAI_API_KEY": "must-not-forward",
    })

    def popen(args, **kwargs):
        spawned.append((args, kwargs))
        return LoginProcess()

    provider._popen = popen

    result = provider.start_login(confirm_switch=True)

    assert result["status"] == "login_started"
    args, kwargs = spawned[0]
    assert args == ["C:/tools/codex.exe", "login"]
    assert "stdout" not in kwargs
    assert "stderr" not in kwargs
    assert "stdin" not in kwargs
    assert kwargs["env"]["CODEX_HOME"] == str(product_home.resolve())
    assert "CODEX_API_KEY" not in kwargs["env"]
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert kwargs["creationflags"] & __import__("subprocess").CREATE_NEW_CONSOLE


def test_login_rechecks_official_auth_before_deciding_switch_confirmation():
    from subscription_ai.codex_provider import LoginSwitchConfirmationRequired

    spawned = []
    provider, calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(returncode=1, stderr="Not logged in"),
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using an API key - sk-redacted"),
    ], login_timeout_seconds=60)
    provider._popen = lambda *args, **kwargs: spawned.append((args, kwargs))

    assert provider.get_status(force=True).auth_status == "installed_not_logged_in"

    with pytest.raises(LoginSwitchConfirmationRequired, match="可能替换"):
        provider.start_login(confirm_switch=False)

    assert spawned == []
    assert len(calls) == 4

def test_login_timeout_terminates_the_official_login_process_tree():
    terminated = threading.Event()

    class LoginProcess:
        pid = 3131
        returncode = None

        def poll(self):
            return self.returncode

    process = LoginProcess()
    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using an API key - sk-redacted"),
    ], login_timeout_seconds=0.01)
    provider._popen = lambda *_args, **_kwargs: process

    def terminate_tree(proc):
        proc.returncode = -9
        terminated.set()

    provider._terminate_tree = terminate_tree

    assert provider.start_login(confirm_switch=True)["status"] == "login_started"
    assert terminated.wait(1)
    assert process.returncode == -9


def test_concurrent_login_requests_spawn_only_one_official_process():
    spawn_entered = threading.Event()
    allow_spawn_return = threading.Event()
    spawned = []

    class LoginProcess:
        def poll(self):
            return None

    process = LoginProcess()
    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using an API key - sk-redacted"),
    ], login_timeout_seconds=60)
    assert provider.get_status(force=True).auth_status == "logged_in_api_key"

    def run_probe(args, **_kwargs):
        if args[-1] == "--version":
            return _completed(stdout="codex-cli 0.145.0")
        return _completed(stderr="Logged in using an API key - sk-redacted")

    provider._run_command = run_probe

    def popen(args, **kwargs):
        spawned.append((args, kwargs))
        spawn_entered.set()
        assert allow_spawn_return.wait(5)
        return process

    provider._popen = popen
    results = []
    first = threading.Thread(target=lambda: results.append(provider.start_login(confirm_switch=True)))
    second = threading.Thread(target=lambda: results.append(provider.start_login(confirm_switch=True)))

    first.start()
    assert spawn_entered.wait(5)
    second.start()
    allow_spawn_return.set()
    first.join(5)
    second.join(5)

    assert len(spawned) == 1
    assert [result["status"] for result in results] == ["login_started", "login_started"]



class FakeExecProcess:
    pid = 4242

    def __init__(self, *, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout_value = stdout
        self.stderr_value = stderr
        self.input_value = None
        self.timeout_value = None

    def communicate(self, input=None, timeout=None):
        self.input_value = input
        self.timeout_value = timeout
        return self.stdout_value, self.stderr_value

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _chatgpt_provider_with_process(process, spawned, **provider_kwargs):
    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using ChatGPT"),
    ], **provider_kwargs)

    def popen(args, **kwargs):
        spawned.append((args, kwargs, os.listdir(kwargs["cwd"])))
        return process

    provider._popen = popen
    return provider


def test_connection_uses_exact_isolated_ephemeral_read_only_command_and_cleans_tempdir(tmp_path):
    spawned = []
    process = FakeExecProcess(stdout="PP03_CODEX_CONNECTED\n")
    product_home = tmp_path / "profile" / "codex-home"
    provider = _chatgpt_provider_with_process(
        process,
        spawned,
        codex_home=product_home,
        base_env={
            "PATH": os.environ.get("PATH", ""),
            "CODEX_HOME": r"C:\Users\sentinel\.codex",
            "CODEX_API_KEY": "must-not-forward",
            "CODEX_ACCESS_TOKEN": "must-not-forward",
            "OPENAI_API_KEY": "must-not-forward",
        },
    )

    result = provider.test_connection()

    assert result.status == "success"
    args, kwargs, initial_files = spawned[0]
    assert args == [
        "C:/tools/codex.exe", "--ask-for-approval", "never", "exec",
        "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--disable", "shell_tool", "--sandbox", "read-only",
        "--skip-git-repo-check", "-",
    ]
    assert process.input_value == "只回复：PP03_CODEX_CONNECTED"
    assert initial_files == []
    assert not os.path.exists(kwargs["cwd"])
    assert kwargs["env"]["CODEX_HOME"] == str(product_home.resolve())
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "CODEX_ACCESS_TOKEN" not in kwargs["env"]


@pytest.mark.parametrize(
    ("auth_line", "auth_rc", "expected"),
    [
        ("Not logged in", 1, "not_logged_in"),
        ("Logged in using an API key - sk-redacted", 0, "wrong_auth_mode"),
    ],
)
def test_connection_requires_chatgpt_auth_before_spawning(auth_line, auth_rc, expected):
    spawned = []
    provider, _calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(returncode=auth_rc, stderr=auth_line),
    ])
    provider._popen = lambda *args, **kwargs: spawned.append((args, kwargs))

    result = provider.test_connection()

    assert result.status == expected
    assert spawned == []
def test_connection_rechecks_official_auth_instead_of_trusting_cached_chatgpt_state():
    spawned = []
    provider, calls = _installed_provider([
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using ChatGPT"),
        _completed(stdout="codex-cli 0.145.0"),
        _completed(stderr="Logged in using an API key - sk-redacted"),
    ])
    provider._popen = lambda *args, **kwargs: spawned.append((args, kwargs))

    assert provider.get_status(force=True).auth_status == "logged_in_chatgpt"
    result = provider.test_connection()

    assert result.status == "wrong_auth_mode"
    assert spawned == []
    assert len(calls) == 4



def test_connection_reports_not_installed_without_spawning():
    spawned = []
    provider = _provider_class()(find_binary=lambda _kind: None, popen=lambda *args, **kwargs: spawned.append((args, kwargs)))

    result = provider.test_connection()

    assert result.status == "not_installed"
    assert spawned == []


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected"),
    [
        (0, "different answer", "", "unexpected_output"),
        (1, "", "You have exceeded the rate limit", "quota_or_rate_limited"),
        (3, "", "generic process error", "process_failed"),
    ],
)
def test_connection_classifies_process_results(returncode, stdout, stderr, expected):
    process = FakeExecProcess(returncode=returncode, stdout=stdout, stderr=stderr)
    provider = _chatgpt_provider_with_process(process, [])

    result = provider.test_connection()

    assert result.status == expected


def test_connection_timeout_terminates_process_tree_and_cleans_tempdir():
    spawned = []
    terminated = []

    class TimeoutProcess(FakeExecProcess):
        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired("codex", timeout)

    process = TimeoutProcess()
    provider = _chatgpt_provider_with_process(process, spawned)
    provider._terminate_tree = lambda proc: terminated.append(proc.pid)

    result = provider.test_connection()

    assert result.status == "timeout"
    assert terminated == [4242]
    assert not os.path.exists(spawned[0][1]["cwd"])


def test_cancel_stops_the_running_process_tree_and_both_calls_report_cancelled():
    started = threading.Event()
    terminated = threading.Event()

    class BlockingProcess(FakeExecProcess):
        returncode = None

        def communicate(self, input=None, timeout=None):
            started.set()
            assert terminated.wait(5)
            self.returncode = -9
            return "", ""

        def poll(self):
            return self.returncode

    process = BlockingProcess(returncode=None)
    provider = _chatgpt_provider_with_process(process, [])

    def terminate_tree(_proc):
        terminated.set()

    provider._terminate_tree = terminate_tree
    holder = {}
    worker = threading.Thread(target=lambda: holder.setdefault("result", provider.test_connection()))
    worker.start()
    assert started.wait(5)

    cancel_result = provider.cancel_test()
    worker.join(5)

    assert cancel_result.status == "cancelled"
    assert holder["result"].status == "cancelled"
def test_cancel_cannot_miss_a_process_between_spawn_and_registration():
    spawn_entered = threading.Event()
    allow_spawn_return = threading.Event()
    communicate_started = threading.Event()
    terminated = threading.Event()

    class BlockingProcess(FakeExecProcess):
        def __init__(self):
            super().__init__(returncode=None)

        def communicate(self, input=None, timeout=None):
            communicate_started.set()
            assert terminated.wait(5)
            self.returncode = -9
            return "", ""

    process = BlockingProcess()
    provider = _chatgpt_provider_with_process(process, [])

    def popen(_args, **_kwargs):
        spawn_entered.set()
        assert allow_spawn_return.wait(5)
        return process

    def terminate_tree(_process):
        terminated.set()

    provider._popen = popen
    provider._terminate_tree = terminate_tree
    test_holder = {}
    cancel_holder = {}
    test_worker = threading.Thread(target=lambda: test_holder.setdefault("result", provider.test_connection()))
    cancel_worker = threading.Thread(target=lambda: cancel_holder.setdefault("result", provider.cancel_test()))

    test_worker.start()
    assert spawn_entered.wait(5)
    cancel_worker.start()
    allow_spawn_return.set()
    assert communicate_started.wait(5)
    test_worker.join(5)
    cancel_worker.join(5)

    assert terminated.is_set()
    assert cancel_holder["result"].status == "cancelled"
    assert test_holder["result"].status == "cancelled"


def test_process_diagnostic_is_redacted_before_returning_to_the_page():
    process = FakeExecProcess(
        returncode=1,
        stderr=r"C:\Users\Alice\.codex\auth.json api_key=sk-secret Bearer token-value Cookie=session-secret",
    )
    provider = _chatgpt_provider_with_process(process, [])

    result = provider.test_connection()

    assert result.status == "process_failed"
    diagnostic = result.diagnostic or ""
    for secret in ("Alice", "auth.json", "sk-secret", "token-value", "session-secret"):
        assert secret not in diagnostic


def test_successful_test_preserves_short_status_cache_for_the_next_page_chat():
    calls = []
    process = FakeExecProcess(stdout="PP03_CODEX_CONNECTED")
    provider = _chatgpt_provider_with_process(process, calls)

    assert provider.test_connection().status == "success"
    status = provider.get_status()

    assert status.auth_status == "logged_in_chatgpt"
    assert status.available is True
    assert status.last_test_status == "success"


def test_service_exposes_only_the_public_codex_provider_contract():
    try:
        from subscription_ai.service import SubscriptionAIService
    except ModuleNotFoundError:
        pytest.fail("SubscriptionAIService is not implemented yet", pytrace=False)

    provider = _provider_class()(find_binary=lambda _kind: None)
    service = SubscriptionAIService(codex_provider=provider)

    rows = service.list_providers(force=True)

    assert [row["provider_id"] for row in rows] == ["codex"]
    assert set(rows[0]) == {
        "provider_id",
        "installed",
        "version",
        "auth_status",
        "available",
        "test_status",
        "message",
        "last_test_at",
    }


def test_normal_codex_page_answer_uses_product_home_and_restricted_exec(tmp_path, monkeypatch):
    import cli_runtime

    product_data = tmp_path / "profile"
    captured = {}

    class FakeInput:
        def write(self, value):
            captured["input"] = value

        def close(self):
            pass

    class FakePageProcess:
        pid = 5252
        returncode = 0

        def __init__(self):
            import io
            self.stdin = FakeInput()
            self.stdout = io.StringIO("页面回答\n")
            self.stderr = io.StringIO("")

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakePageProcess()

    monkeypatch.setenv("VR_DATA_DIR", str(product_data))
    monkeypatch.setenv("CODEX_HOME", r"C:\Users\sentinel\.codex")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-forward")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "must-not-forward")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-forward")
    monkeypatch.setattr(cli_runtime, "detect_cli", lambda _kind: "C:/tools/codex.exe")
    monkeypatch.setattr(cli_runtime.subprocess, "Popen", fake_popen)

    assert cli_runtime.run_cli("codex", "系统", "页面上下文") == "页面回答"

    assert captured["args"] == [
        "C:/tools/codex.exe",
        "--ask-for-approval",
        "never",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--disable",
        "shell_tool",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "-",
    ]
    env = captured["kwargs"]["env"]
    assert env["CODEX_HOME"] == str((product_data / "codex-home").resolve())
    assert "CODEX_API_KEY" not in env
    assert "CODEX_ACCESS_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["HOME"] == captured["kwargs"]["cwd"]
    assert env["USERPROFILE"] == captured["kwargs"]["cwd"]
    assert "USERNAME" not in env
    assert captured["input"] == "系统\n\n页面上下文"
    assert not os.path.exists(captured["kwargs"]["cwd"])


def test_reflection_masks_raw_codex_stderr(monkeypatch):
    import reflection

    monkeypatch.setattr(
        reflection.cli_runtime,
        "run_cli_stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(r"C:\Users\alice\.codex\auth.json token=super-secret")
        ),
    )

    events = list(
        reflection.run_reflection_stream(
            {"provider": "cli-codex"},
            "一段待审计的分析",
        )
    )
    rendered = str(events)

    assert "Codex CLI 运行异常" in rendered
    assert "alice" not in rendered
    assert "auth.json" not in rendered
    assert "super-secret" not in rendered


def test_debate_masks_raw_codex_stderr(monkeypatch):
    import debate

    def fake_dossier(_code):
        if False:
            yield None
        return {
            "code": _code,
            "sections": [{"title": "实时行情", "tool": "query_quote", "data": {"price": 1}}],
            "missing": [],
        }

    monkeypatch.setattr(debate, "collect_dossier", fake_dossier)
    monkeypatch.setattr(debate, "_stage_plan", lambda _rounds: ["bull"])
    monkeypatch.setattr(
        debate.cli_runtime,
        "run_cli_stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(r"C:\Users\alice\.codex\auth.json token=super-secret")
        ),
    )

    events = list(debate.run_debate_stream({"provider": "cli-codex"}, "600519", 1))
    rendered = str(events)

    assert "Codex CLI 运行异常" in rendered
    assert "alice" not in rendered
    assert "auth.json" not in rendered
    assert "super-secret" not in rendered
