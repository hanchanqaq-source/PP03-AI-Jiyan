"""订阅接入：调本机已装、已登录的 AI CLI（Claude Code / Qwen / DeepSeek / Codex），
用用户自己的订阅额度作答、免 API key。移植自 SDesign-opensource 的 cli-runtime（Node），
改为 Python subprocess + 一次性（非流式）取纯文本。

⚠️ 仅当后端跑在用户本机时可用——云端读不到用户本机的 CLI 与登录态。
CLI 不做 function-calling（不像 API 那条能让 AI 自己调数据工具）；因此订阅接入只适合
「数据已在提示词里」的场景（每日复盘 / 今日要点 / 个股页问 AI，页面已把数据塞进 context）。
"""

from __future__ import annotations

import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Mapping
from pathlib import Path

# 提示词投递方式（各 CLI 接口不同）：
#   system-file —— 系统提示词写临时文件用 flag 传，用户提示词走 stdin（Claude）
#   stdin       —— 系统+用户合并走 stdin（Qwen / Codex）
#   arg         —— 系统+用户合并作为最后一个位置参数（DeepSeek）
_CLI_DEFS: dict[str, dict] = {
    "claude": {
        "bins": ["claude", "openclaude"],
        "delivery": "system-file",
        # -p 非交互、纯文本输出、系统提示词走文件；禁掉所有工具（只让它把问题答成文字，不读文件/联网/执行）
        "build_args": lambda sys_file: [
            "-p", "--output-format", "text", "--system-prompt-file", sys_file,
            "--disallowedTools", "Read", "Write", "Edit", "Glob", "Grep", "Bash",
            "NotebookEdit", "WebFetch", "WebSearch", "TodoWrite", "Task",
        ],
        "env": {},
    },
    "qwen": {"bins": ["qwen"], "delivery": "stdin", "build_args": lambda _: ["--yolo"], "env": {}},
    # 注：Gemini CLI 已停止对个人版 Gemini Code Assist 的支持（登录报 "This client is no
    # longer supported for Gemini Code Assist for individuals"），故已从订阅接入中移除。
    "deepseek": {"bins": ["deepseek", "codewhale"], "delivery": "arg",
                 "build_args": lambda _: ["exec", "--auto"], "env": {}},
    # Codex：认证只来自 PP03 产品 CODEX_HOME；忽略其中的用户配置和 rules，
    # 在空临时目录、只读沙箱、无持久会话模式下回答页面已提供的上下文。
    "codex": {"bins": ["codex"], "delivery": "stdin",
              "build_args": lambda _: [
                  "--ask-for-approval", "never", "exec",
                  "--ignore-user-config", "--ignore-rules", "--ephemeral",
                  "--disable", "shell_tool",
                  "--sandbox", "read-only", "--skip-git-repo-check", "-",
              ], "env": {}},
}

_EXTRA_PATH_DIRS = [
    "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
    # Codex 桌面版把 CLI 装在 app bundle 里，不进 PATH（issue #16 / PR #11）
    "/Applications/Codex.app/Contents/Resources",
    str(Path.home() / ".local/bin"), str(Path.home() / ".npm-global/bin"),
    str(Path.home() / ".bun/bin"), str(Path.home() / ".deno/bin"),
    str(Path.home() / ".yarn/bin"),
]

_CLI_TIMEOUT_S = 300  # 子进程兜底超时（秒）
_STREAM_HEARTBEAT_S = 1.0
_STREAM_EOF = object()
_MAX_ARG_BYTES = 110_000  # 位置参数投递的提示词字节上限

_CODEX_ENV_KEYS = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "USERNAME",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "ALL_PROXY", "all_proxy",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CODEX_CA_CERTIFICATE",
}


class CliUnavailable(RuntimeError):
    """本机未检测到对应 CLI（未安装 / 不在 PATH）。"""


def product_codex_home(env: Mapping[str, str] | None = None) -> Path:
    """Return PP03's private Codex state root without consulting ~/.codex."""
    source = os.environ if env is None else env
    configured = str(source.get("VR_DATA_DIR", "") or "").strip()
    data_root = Path(configured) if configured else Path.home() / ".vibe-research"
    return (data_root / "codex-home").resolve()


def codex_subprocess_env(
    env: Mapping[str, str] | None = None,
    codex_home: str | os.PathLike[str] | None = None,
    runtime_home: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Build the minimal membership-mode environment for every Codex process."""
    source = os.environ if env is None else env
    home = Path(codex_home).resolve() if codex_home is not None else product_codex_home(source)
    home.mkdir(parents=True, exist_ok=True)
    clean = {
        key: value
        for key, value in source.items()
        if key.upper() in _CODEX_ENV_KEYS and value is not None
    }
    clean["CODEX_HOME"] = str(home)
    if runtime_home is not None:
        runtime_root = Path(runtime_home).resolve()
        appdata = runtime_root / "appdata"
        local_appdata = runtime_root / "local-appdata"
        temp_root = runtime_root / "temp"
        for path in (runtime_root, appdata, local_appdata, temp_root):
            path.mkdir(parents=True, exist_ok=True)
        clean["HOME"] = str(runtime_root)
        clean["USERPROFILE"] = str(runtime_root)
        clean["APPDATA"] = str(appdata)
        clean["LOCALAPPDATA"] = str(local_appdata)
        clean["TEMP"] = clean["TMP"] = clean["TMPDIR"] = str(temp_root)
        clean.pop("USERNAME", None)
    return clean


def _find_bin(name: str) -> str | None:
    hit = shutil.which(name)
    if hit:
        return hit
    for d in _EXTRA_PATH_DIRS:
        p = Path(d) / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def detect_cli(kind: str) -> str | None:
    """返回某订阅 CLI 的可执行路径，未装则 None。"""
    d = _CLI_DEFS.get(kind)
    if not d:
        return None
    for b in d["bins"]:
        found = _find_bin(b)
        if found:
            return found
    return None


def supported_kinds() -> list[str]:
    return list(_CLI_DEFS.keys())


def _terminate_process_tree(process) -> None:
    """Terminate the spawned CLI and descendants without touching unrelated processes."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            kwargs = {
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


def run_cli(kind: str, system_prompt: str, user_prompt: str) -> str:
    """一次性兼容层；复用流式进程模型，确保取消、超时和进程树回收一致。"""
    return "".join(
        chunk for chunk in run_cli_stream(kind, system_prompt, user_prompt)
        if chunk is not None
    ).strip()


def run_cli_stream(kind: str, system_prompt: str, user_prompt: str):
    """流式版：起 CLI 子进程，stdout 边出边 yield 纯文本块。失败抛异常。"""
    d = _CLI_DEFS.get(kind)
    bin_path = detect_cli(kind)
    if not d or not bin_path:
        raise CliUnavailable(
            f"未检测到「{kind}」对应的本机命令。请先安装并登录该 CLI，或改用「API 接入」。"
        )

    combined = f"{system_prompt}\n\n{user_prompt}"
    tmpdir = tempfile.mkdtemp(prefix="vibe-cli-")
    env = codex_subprocess_env(runtime_home=tmpdir) if kind == "codex" else {**os.environ, **d.get("env", {})}
    proc = None
    try:
        if d["delivery"] == "system-file":
            sys_file = str(Path(tmpdir) / "system.txt")
            Path(sys_file).write_text(system_prompt, encoding="utf-8")
            args = d["build_args"](sys_file)
            stdin_payload = user_prompt
        elif d["delivery"] == "stdin":
            args = d["build_args"](None)
            stdin_payload = combined
        else:  # arg
            if len(combined.encode("utf-8")) > _MAX_ARG_BYTES:
                raise RuntimeError(f"提示词过长，超过 {kind} 的命令行参数上限，请改用 Claude / Qwen 或 API 接入。")
            args = [*d["build_args"](None), combined]
            stdin_payload = None

        process_kwargs = {}
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            process_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            process_kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            [bin_path, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            # stderr 不能丢：CLI 的失败原因只在这里，丢了用户就只看到光秃秃的
            # 「退出码 1」（issue #16）。但必须持续排空，否则管道写满会死锁。
            stderr=subprocess.PIPE, cwd=tmpdir, env=env, text=True, bufsize=1,
            encoding="utf-8", errors="replace",
            **process_kwargs,
        )
        err_tail: deque = deque(maxlen=40)   # 只留尾部若干行，避免长进度日志占内存

        def _drain_err(stderr=proc.stderr):
            try:
                for ln in stderr:
                    err_tail.append(ln)
            except Exception:
                pass

        err_thread = threading.Thread(target=_drain_err, daemon=True)
        err_thread.start()
        if stdin_payload is not None:
            try:
                proc.stdin.write(stdin_payload)
            except BrokenPipeError:
                pass
        if proc.stdin:
            proc.stdin.close()

        # 读线程 + 队列：让 _CLI_TIMEOUT_S 约束整个流式过程。
        # 直接 `for line in proc.stdout` 是无限期阻塞读——CLI 挂起时永远走不到
        # proc.wait(timeout=...)，超时形同虚设，子进程会常驻堆积。
        q: queue.Queue = queue.Queue()

        def _pump(stdout=proc.stdout):
            try:
                for ln in stdout:
                    q.put(ln)
            except Exception:
                pass
            finally:
                q.put(_STREAM_EOF)

        threading.Thread(target=_pump, daemon=True).start()
        deadline = time.monotonic() + _CLI_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"{kind} 生成超时（>{_CLI_TIMEOUT_S}s）")
            try:
                line = q.get(timeout=min(remaining, _STREAM_HEARTBEAT_S))
            except queue.Empty:
                yield None
                continue
            if line is _STREAM_EOF:
                break
            yield line
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"{kind} 输出已结束但进程未退出") from e
        if rc != 0:
            # 必须等排空线程收完再读 err_tail：CLI 常常是「写完错误立刻退出」，
            # proc.wait() 可能先于排空线程读完管道返回，此时 err_tail 还是空的，
            # 报错信息又会退化成「退出码 1」——正是本次要修的症状。
            err_thread.join(timeout=5)
            err = "".join(err_tail).strip()[-500:] or "（子进程未输出错误信息）"
            raise RuntimeError(f"{kind} 退出码 {rc}：{err}")
    finally:
        if proc and proc.poll() is None:
            _terminate_process_tree(proc)
        shutil.rmtree(tmpdir, ignore_errors=True)
