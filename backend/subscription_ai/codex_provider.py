"""Official Codex CLI subscription provider."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
import os
import re
import signal
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import cli_runtime

from .base import SubscriptionProvider
from .models import ConnectionTestResult, SubscriptionProviderStatus


class LoginSwitchConfirmationRequired(RuntimeError):
    """The official login flow may replace an existing non-ChatGPT login."""


_CREDENTIAL_ENV_NAMES = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "CODEX_API_KEY",
    "CODEX_ACCESS_TOKEN",
    "AZURE_OPENAI_API_KEY",
}
_TEST_PROMPT = "只回复：PP03_CODEX_CONNECTED"
_CONNECTED_MARKER = "PP03_CODEX_CONNECTED"


def sanitize_diagnostic(value: str, limit: int = 500) -> str:
    """Return a small diagnostic with credential and user-path material removed."""
    text = value or ""
    text = re.sub(r"(?i)(?:[A-Za-z]:\\|/)[^\r\n]*?auth\.json", "[credential path redacted]", text)
    text = re.sub(r"(?i)[A-Za-z]:\\Users\\[^\\\s]+", r"C:\\Users\\[redacted]", text)
    text = re.sub(r"(?i)/(?:Users|home)/[^/\s]+", "/home/[redacted]", text)
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9._-]+", "[redacted]", text)
    text = re.sub(r"(?i)\b(Bearer)\s+\S+", r"\1 [redacted]", text)
    text = re.sub(
        r"(?i)\b(api[_ -]?key|token|cookie)\s*[:=]\s*\S+",
        lambda match: f"{match.group(1)}=[redacted]",
        text,
    )
    return text.strip()[-limit:]


def public_runtime_message(error: Exception | str) -> str:
    """Classify a normal Codex run failure without exposing raw process output."""
    lowered = str(error).lower()
    if any(
        marker in lowered
        for marker in (
            "rate limit",
            "rate_limit",
            "quota",
            "usage limit",
            "limit reached",
            "too many requests",
            "insufficient_quota",
            "http 429",
        )
    ):
        return "Codex 官方额度或速率限制暂不可用，请稍后重试。系统不会自动切换到付费 API。"
    if any(
        marker in lowered
        for marker in ("not logged in", "login required", "authentication expired", "unauthorized", "http 401")
    ):
        return "Codex 登录已过期，请回到「接入 AI」重新检测并完成官方登录。"
    if "timeout" in lowered or "超时" in lowered:
        return "Codex 生成超时，相关进程已停止，请稍后重试。"
    return "Codex CLI 运行异常，请回到「接入 AI」重新检测或测试连接。"


class CodexSubscriptionProvider(SubscriptionProvider):
    provider_id = "codex"
    display_name = "Codex"

    def __init__(
        self,
        *,
        find_binary: Callable[[str], str | None] = cli_runtime.detect_cli,
        run_command: Callable[..., Any] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        test_timeout_seconds: float = 90.0,
        login_timeout_seconds: float = 600.0,
        codex_home: str | os.PathLike[str] | None = None,
        base_env: Mapping[str, str] | None = None,
    ):
        self._find_binary = find_binary
        self._run_command = run_command
        self._popen = popen
        self._now = now
        self._test_timeout_seconds = test_timeout_seconds
        self._login_timeout_seconds = login_timeout_seconds
        self._base_env = dict(base_env) if base_env is not None else None
        self._codex_home = (
            Path(codex_home).resolve()
            if codex_home is not None
            else cli_runtime.product_codex_home(self._base_env)
        )
        self._cached_status: SubscriptionProviderStatus | None = None
        self._last_test_status = "not_tested"
        self._last_test_at: str | None = None
        self._login_process: Any | None = None
        self._login_timer: threading.Timer | None = None
        self._process_lock = threading.Lock()
        self._test_process: Any | None = None
        self._cancel_requested = False

    def _expire_login(self, process: Any) -> None:
        with self._process_lock:
            if self._login_process is not process or process.poll() is not None:
                return
            self._login_process = None
            self._login_timer = None
        self._terminate_tree(process)

    @staticmethod
    def _command_text(result: Any) -> str:
        return "\n".join(part for part in (result.stdout or "", result.stderr or "") if part).strip()

    def _run_probe(self, args: list[str]) -> Any:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 10,
            "env": self._codex_environment(),
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return self._run_command(args, **kwargs)

    def _codex_environment(self) -> dict[str, str]:
        return cli_runtime.codex_subprocess_env(self._base_env, self._codex_home)

    @staticmethod
    def _parse_version(result: Any) -> str | None:
        if result.returncode != 0:
            return None
        match = re.search(
            r"\bcodex(?:-cli)?\s+([0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?)\b",
            CodexSubscriptionProvider._command_text(result),
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else None

    @staticmethod
    def _parse_auth(result: Any) -> tuple[str, str | None, str]:
        lines = [line.strip() for line in CodexSubscriptionProvider._command_text(result).splitlines() if line.strip()]
        if result.returncode == 0:
            if "Logged in using ChatGPT" in lines:
                return "logged_in_chatgpt", None, "已使用 ChatGPT 登录。"
            if any(line.startswith("Logged in using an API key") for line in lines):
                return "logged_in_api_key", "wrong_auth_mode", "当前 Codex 使用 API Key，不属于会员额度接入。"
            return "status_failed", "auth_unknown", "Codex 已返回登录状态，但认证方式无法安全识别。"
        if "Not logged in" in lines:
            return "installed_not_logged_in", "not_logged_in", "Codex 已安装，但尚未登录。"
        lowered = "\n".join(lines).lower()
        if "unrecognized subcommand" in lowered or "unexpected argument 'status'" in lowered:
            return "unsupported_version", "status_unsupported", "当前 Codex 版本过旧或不支持登录状态检测。"
        return "status_failed", "status_failed", "Codex 登录状态检测异常，请重新检测或更新 Codex。"

    def get_status(self, force: bool = False) -> SubscriptionProviderStatus:
        if not force and self._cached_status is not None:
            return self._cached_status

        binary = self._find_binary("codex")
        if binary is None:
            status = SubscriptionProviderStatus(
                provider_id=self.provider_id,
                display_name=self.display_name,
                installed=False,
                version=None,
                auth_status="not_installed",
                available=False,
                supports_streaming=True,
                supports_project_tools=False,
                last_test_status=self._last_test_status,
                last_test_at=self._last_test_at,
                error_code="not_installed",
                message="未检测到 Codex CLI，请先安装官方 Codex。",
            )
            self._cached_status = status
            return status

        try:
            version_result = self._run_probe([binary, "--version"])
            version = self._parse_version(version_result)
        except (OSError, subprocess.SubprocessError):
            version = None
        try:
            auth_result = self._run_probe([binary, "login", "status"])
            auth_status, auth_error, message = self._parse_auth(auth_result)
        except (OSError, subprocess.SubprocessError):
            auth_status, auth_error, message = (
                "status_failed",
                "status_failed",
                "Codex 登录状态检测异常，请重新检测或更新 Codex。",
            )

        error_code = auth_error
        if version is None:
            auth_status = "unsupported_version"
            error_code = "version_failed"
            message = "Codex 版本无法确认，当前版本不支持本次会员接入验证。"
        status = SubscriptionProviderStatus(
            provider_id=self.provider_id,
            display_name=self.display_name,
            installed=True,
            version=version,
            auth_status=auth_status,
            available=auth_status == "logged_in_chatgpt",
            supports_streaming=True,
            supports_project_tools=False,
            last_test_status=self._last_test_status,
            last_test_at=self._last_test_at,
            error_code=error_code,
            message=message,
        )
        self._cached_status = status
        return status

    def start_login(self, confirm_switch: bool = False) -> dict[str, str]:
        status = self.get_status(force=True)
        if not status.installed:
            return {"status": "not_installed", "message": status.message}
        if status.auth_status == "logged_in_chatgpt":
            return {"status": "already_authenticated", "message": "Codex 已使用 ChatGPT 登录，无需重复登录。"}
        if status.auth_status == "logged_in_api_key" and not confirm_switch:
            raise LoginSwitchConfirmationRequired(
                "当前 Codex 使用 API Key；继续官方 ChatGPT 登录流程可能替换当前认证方式。"
            )

        binary = self._find_binary("codex")
        if binary is None:
            return {"status": "not_installed", "message": "未检测到 Codex CLI，请先安装官方 Codex。"}
        kwargs: dict[str, Any] = {
            "close_fds": True,
            "env": self._codex_environment(),
        }
        if hasattr(subprocess, "CREATE_NEW_CONSOLE"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        with self._process_lock:
            if self._login_process is not None and self._login_process.poll() is None:
                return {"status": "login_started", "message": "Codex 官方登录流程已在运行。"}
            if self._login_timer is not None:
                self._login_timer.cancel()
            try:
                process = self._popen([binary, "login"], **kwargs)
            except OSError:
                return {"status": "login_failed", "message": "无法启动 Codex 官方登录流程。"}
            self._login_process = process
            timer = threading.Timer(self._login_timeout_seconds, self._expire_login, args=(process,))
            timer.daemon = True
            self._login_timer = timer
        timer.start()
        self._cached_status = None
        return {
            "status": "login_started",
            "message": "已打开 Codex 官方登录，请在官方浏览器流程中完成登录。",
        }

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def _result(self, status: str, message: str, diagnostic: str | None = None) -> ConnectionTestResult:
        tested_at = self._timestamp()
        self._last_test_status = status
        self._last_test_at = tested_at
        if self._cached_status is not None:
            self._cached_status = replace(
                self._cached_status,
                last_test_status=status,
                last_test_at=tested_at,
                message=message,
            )
        return ConnectionTestResult(
            status=status,
            message=message,
            tested_at=tested_at,
            diagnostic=sanitize_diagnostic(diagnostic or "") or None,
        )

    def _test_environment(self) -> dict[str, str]:
        return self._codex_environment()

    @staticmethod
    def _default_terminate_tree(process: Any) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                kwargs: dict[str, Any] = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                    "timeout": 10,
                    "check": False,
                }
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], **kwargs)
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def _terminate_tree(self, process: Any) -> None:
        self._default_terminate_tree(process)

    @staticmethod
    def _process_status(returncode: int, stdout: str, stderr: str) -> tuple[str, str]:
        combined = f"{stdout}\n{stderr}".lower()
        if returncode == 0:
            if _CONNECTED_MARKER in stdout:
                return "success", "Codex 连接成功。"
            return "unexpected_output", "Codex 已响应，但未返回预期连接标识。"
        if any(
            marker in combined
            for marker in ("rate limit", "rate_limit", "quota", "usage limit", "too many requests", "http 429")
        ):
            return "quota_or_rate_limited", "Codex 官方额度或速率限制暂不可用，请稍后重试。"
        return "process_failed", "Codex 连接测试失败，请重新检测登录状态后再试。"

    def test_connection(self) -> ConnectionTestResult:
        status = self.get_status(force=True)
        if not status.installed:
            return self._result("not_installed", "未检测到 Codex CLI，请先安装官方 Codex。")
        if status.auth_status == "installed_not_logged_in":
            return self._result("not_logged_in", "Codex 尚未登录，请先完成官方 ChatGPT 登录。")
        if status.auth_status == "logged_in_api_key":
            return self._result("wrong_auth_mode", "当前 Codex 使用 API Key，不属于会员额度接入。")
        if status.auth_status != "logged_in_chatgpt":
            return self._result("process_failed", "Codex 登录状态无法安全确认，请先重新检测。")

        binary = self._find_binary("codex")
        if binary is None:
            return self._result("not_installed", "未检测到 Codex CLI，请先安装官方 Codex。")
        args = [
            binary,
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
        with tempfile.TemporaryDirectory(prefix="pp03-codex-test-") as tmpdir:
            kwargs: dict[str, Any] = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "cwd": tmpdir,
                "env": self._test_environment(),
            }
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            try:
                with self._process_lock:
                    if self._test_process is not None and self._test_process.poll() is None:
                        return self._result("process_failed", "Codex 连接测试已经在运行。")
                    self._cancel_requested = False
                    process = self._popen(args, **kwargs)
                    self._test_process = process
            except OSError as error:
                return self._result("process_failed", "无法启动 Codex 连接测试。", str(error))
            try:
                stdout, stderr = process.communicate(input=_TEST_PROMPT, timeout=self._test_timeout_seconds)
                if self._cancel_requested:
                    return self._result("cancelled", "Codex 连接测试已取消。")
                result_status, message = self._process_status(process.returncode, stdout or "", stderr or "")
                diagnostic = None if result_status == "success" else (stderr or stdout or "")
                return self._result(result_status, message, diagnostic)
            except subprocess.TimeoutExpired:
                self._terminate_tree(process)
                return self._result("timeout", "Codex 连接测试超时，相关进程已停止。")
            except (OSError, subprocess.SubprocessError) as error:
                self._terminate_tree(process)
                return self._result("process_failed", "Codex 连接测试异常，相关进程已停止。", str(error))
            finally:
                with self._process_lock:
                    if self._test_process is process:
                        self._test_process = None

    def cancel_test(self) -> ConnectionTestResult:
        with self._process_lock:
            process = self._test_process
            self._cancel_requested = True
        if process is not None and process.poll() is None:
            self._terminate_tree(process)
        return self._result("cancelled", "Codex 连接测试已取消。")
