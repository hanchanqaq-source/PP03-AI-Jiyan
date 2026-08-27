"""Native Information Radar source management.

Built-in sources remain repository-owned. User changes are stored separately in
``%VR_DATA_DIR%/news-sources.custom.json`` and are merged only at runtime.
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import io
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from functools import partial
from typing import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from news_probe_errors import public_source_url, redact_probe_message, redact_url


SCHEMA_VERSION = 1
STORE_NAME = "news-sources.custom.json"
MAX_STORE_BYTES = 2 * 1024 * 1024
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
REQUEST_TIMEOUT_SECONDS = 12
USER_AGENT = "Vibe-Research/0.3 (+local Information Radar source validation)"
HEALTH_ERROR_TYPES = {
    "authentication", "connection", "dns", "empty_payload", "http", "http_status",
    "parse", "rate_limit", "response_size", "rss_parse", "schema_changed", "security",
    "stale_data", "timeout", "tls", "unknown", "validation",
}

# API sources are intentionally closed-world. A source type of ``api`` is legal
# only after a named adapter with an explicit response contract is registered.
API_ADAPTERS: dict[str, Callable[[dict], dict]] = {}

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


class SourceManagerError(ValueError):
    pass


class SourceValidationError(SourceManagerError):
    pass


class SourceStoreCorruptError(SourceManagerError):
    pass


class DuplicateSourceError(SourceManagerError):
    pass


class SourceNotFoundError(SourceManagerError):
    pass


class BuiltinSourceDeletionError(SourceManagerError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _sanitize_error(value: object) -> str:
    return redact_probe_message(value)[:240]


def source_identifier(source: Mapping[str, object]) -> str:
    explicit = source.get("id")
    if type(explicit) is str and re.fullmatch(r"[0-9a-f]{16}", explicit):
        return explicit
    raw_url = str(source.get("url") or "")
    try:
        identity_url = canonical_source_url(raw_url)
    except (TypeError, ValueError):
        identity_url = raw_url
    identity = "\0".join((
        str(source.get("hint") or ""),
        str(source.get("name") or ""),
        identity_url,
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def canonical_source_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = parsed.port
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (parsed.scheme.lower() == "http" and port == 80)
    authority = hostname if port is None or default_port else f"{hostname}:{port}"
    return urlunsplit((parsed.scheme.lower(), authority, parsed.path or "/", parsed.query, ""))


_source_id = source_identifier
_canonical_url = canonical_source_url


def _valid_iso_timestamp(value: object) -> bool:
    if value is None:
        return True
    if type(value) is not str or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _normalize_health_record(value: Mapping[str, object], *, strict: bool) -> dict:
    status = value.get("status", "unknown")
    if type(status) is not str or status not in {"ok", "failed", "unknown"}:
        raise SourceStoreCorruptError("自定义资讯来源健康状态无效")
    checked_at = value.get("checked_at")
    last_success_at = value.get("last_success_at")
    if not _valid_iso_timestamp(checked_at) or not _valid_iso_timestamp(last_success_at):
        raise SourceStoreCorruptError("自定义资讯来源健康时间无效")
    http_status = value.get("http_status")
    if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
        raise SourceStoreCorruptError("自定义资讯来源 HTTP 状态无效")
    error_type = value.get("error_type")
    if error_type is not None and (type(error_type) is not str or error_type not in HEALTH_ERROR_TYPES):
        if strict:
            raise SourceStoreCorruptError("自定义资讯来源错误类型无效")
        error_type = "unknown"
    error_message = value.get("error_message")
    if error_message is not None and type(error_message) is not str:
        raise SourceStoreCorruptError("自定义资讯来源错误信息无效")
    if status == "ok" and (error_type is not None or error_message is not None):
        if strict:
            raise SourceStoreCorruptError("自定义资讯来源成功状态含错误信息")
        error_type = None
        error_message = None
    if status == "failed" and error_type is None:
        if strict:
            raise SourceStoreCorruptError("自定义资讯来源失败状态缺少错误类型")
        error_type = "unknown"
    return {
        "status": status,
        "checked_at": checked_at,
        "http_status": http_status,
        "error_type": error_type,
        "error_message": _sanitize_error(error_message) if error_message is not None else None,
        "last_success_at": last_success_at,
    }


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(address.is_global)


def validate_url_shape(url: str):
    """Reject unsafe URL syntax and literal/local targets without doing DNS I/O."""
    if type(url) is not str or not url or len(url) > 2048 or url != url.strip():
        raise SourceValidationError("URL 不能为空、含首尾空格或超过 2048 字符")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (UnicodeError, ValueError) as error:
        raise SourceValidationError("URL 格式无效") from error
    if parsed.scheme.lower() not in {"http", "https"}:
        raise SourceValidationError("仅支持 HTTP/HTTPS 公网来源")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise SourceValidationError("URL 必须包含主机且不得嵌入凭据")
    if parsed.fragment:
        raise SourceValidationError("URL 不得包含片段")
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise SourceValidationError("URL 主机名无效") from error
    if (
        hostname in {"localhost", "localhost.localdomain"}
        or hostname.endswith((".localhost", ".local", ".internal", ".lan", ".home"))
    ):
        raise SourceValidationError("拒绝本机或内网来源")
    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise SourceValidationError("拒绝本机、内网、链路本地或保留地址")
    if port is not None and not 1 <= port <= 65535:
        raise SourceValidationError("URL 端口无效")
    return parsed


def validate_public_url(url: str, *, resolver=None):
    """Validate scheme, credentials and every currently resolved address."""
    parsed = validate_url_shape(url)
    port = parsed.port
    hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    resolve = resolver or socket.getaddrinfo
    try:
        answers = resolve(hostname, port or (443 if parsed.scheme.lower() == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as error:
        raise SourceValidationError(f"域名解析失败：{_sanitize_error(error)}") from error
    addresses = {str(answer[4][0]) for answer in answers if len(answer) >= 5 and answer[4]}
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise SourceValidationError("域名必须只解析到公网地址")
    return parsed


def _create_public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *, resolver=None):
    """Resolve, validate, then connect the socket to that exact validated address."""
    host, port = address
    resolve = resolver or socket.getaddrinfo
    try:
        answers = resolve(host.rstrip("."), port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise SourceValidationError(f"域名解析失败：{_sanitize_error(error)}") from error
    usable = [answer for answer in answers if len(answer) >= 5 and answer[4]]
    if not usable or any(not _public_ip(str(answer[4][0])) for answer in usable):
        raise SourceValidationError("域名必须只解析到公网地址")
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in usable:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as error:
            last_error = error
            if sock is not None:
                sock.close()
    if last_error is not None:
        raise last_error
    raise SourceValidationError("域名没有可用的公网地址")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, resolver=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolver = resolver

    def connect(self):
        self.sock = _create_public_connection(
            (self.host, self.port), self.timeout, self.source_address, resolver=self._resolver,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, resolver=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolver = resolver

    def connect(self):
        self.sock = _create_public_connection(
            (self.host, self.port), self.timeout, self.source_address, resolver=self._resolver,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host.rstrip("."))


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, resolver=None):
        super().__init__()
        self.resolver = resolver

    def http_open(self, request):
        return self.do_open(partial(_PinnedHTTPConnection, resolver=self.resolver), request)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, *, context, resolver=None):
        super().__init__(context=context)
        self.resolver = resolver

    def https_open(self, request):
        factory = partial(_PinnedHTTPSConnection, resolver=self.resolver)
        return self.do_open(factory, request, context=self._context)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed_payload(payload: bytes) -> dict:
    if not payload:
        raise SourceValidationError("RSS/Atom 响应为空")
    if len(payload) > MAX_FEED_BYTES:
        raise SourceValidationError("RSS/Atom 响应超过大小上限")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise SourceValidationError("响应不是可解析的 RSS/Atom XML") from error
    root_name = _local_name(root.tag).lower()
    if root_name not in {"rss", "rdf", "feed"}:
        raise SourceValidationError("响应根元素不是 RSS/Atom")
    feed_format = "atom" if root_name == "feed" else "rss"
    entry_names = {"entry"} if feed_format == "atom" else {"item"}
    valid: list[tuple[str, str]] = []
    for entry in (node for node in root.iter() if _local_name(node.tag).lower() in entry_names):
        title = ""
        link = ""
        for child in entry.iter():
            name = _local_name(child.tag).lower()
            if name == "title" and not title:
                title = " ".join("".join(child.itertext()).split())
            elif name == "link" and not link:
                link = str(child.get("href") or child.text or "").strip()
        if title and link:
            valid.append((title[:300], link[:2048]))
    if not valid:
        raise SourceValidationError("RSS/Atom 至少需要一个同时包含 title 和 link 的条目")
    return {
        "feed_format": feed_format,
        "item_count": len(valid),
        "sample_title": valid[0][0],
        "sample_link": valid[0][1],
    }


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, resolver=None):
        super().__init__()
        self.resolver = resolver
        self.count = 0
        self.statuses: list[int] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        self.statuses.append(int(code))
        if self.count > MAX_REDIRECTS:
            raise SourceValidationError("来源重定向次数超过上限")
        validate_public_url(newurl, resolver=self.resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public_request(request: urllib.request.Request, *, timeout: float, resolver=None):
    """Open one public request with pinned DNS, verified TLS, no proxy and bounded redirects."""
    validate_public_url(request.full_url, resolver=resolver)
    redirects = _SafeRedirectHandler(resolver=resolver)
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        redirects,
        _PinnedHTTPHandler(resolver=resolver),
        _PinnedHTTPSHandler(context=context, resolver=resolver),
    )
    response = opener.open(request, timeout=timeout)
    final_url = response.geturl() if hasattr(response, "geturl") else request.full_url
    validate_public_url(final_url, resolver=resolver)
    response.redirect_statuses = tuple(redirects.statuses)
    return response


def read_bounded_response(response) -> bytes:
    content_length = str(getattr(response, "headers", {}).get("Content-Length", "") or "")
    if content_length.isdigit() and int(content_length) > MAX_FEED_BYTES:
        raise SourceValidationError("RSS/Atom 响应超过大小上限")
    raw = response.read(MAX_FEED_BYTES + 1)
    if len(raw) > MAX_FEED_BYTES:
        raise SourceValidationError("RSS/Atom 响应超过大小上限")
    encoding = str(getattr(response, "headers", {}).get("Content-Encoding", "") or "").lower()
    if "gzip" in encoding or raw.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(MAX_FEED_BYTES + 1)
        except (OSError, EOFError) as error:
            raise SourceValidationError("RSS/Atom gzip 解码失败") from error
        if len(raw) > MAX_FEED_BYTES:
            raise SourceValidationError("RSS/Atom 解压后超过大小上限")
    return raw


_read_bounded = read_bounded_response


def probe_rss_source(definition: dict, *, resolver=None) -> dict:
    started = time.perf_counter()
    request = urllib.request.Request(
        str(definition["url"]),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*;q=0.1",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with open_public_request(request, timeout=REQUEST_TIMEOUT_SECONDS, resolver=resolver) as response:
            status = int(getattr(response, "status", None) or response.getcode() or 200)
            final_url = response.geturl() if hasattr(response, "geturl") else str(definition["url"])
            if not 200 <= status < 300:
                raise SourceValidationError(f"来源返回 HTTP {status}")
            parsed = parse_feed_payload(read_bounded_response(response))
    except SourceValidationError:
        raise
    except urllib.error.HTTPError as error:
        return {
            "ok": False, "http_status": int(error.code), "error_type": "http_status",
            "error_message": f"来源返回 HTTP {int(error.code)}", "checked_at": _now_iso(),
        }
    except ssl.SSLError as error:
        return {"ok": False, "http_status": None, "error_type": "tls", "error_message": _sanitize_error(error), "checked_at": _now_iso()}
    except (TimeoutError, socket.timeout) as error:
        return {"ok": False, "http_status": None, "error_type": "timeout", "error_message": _sanitize_error(error), "checked_at": _now_iso()}
    except OSError as error:
        return {"ok": False, "http_status": None, "error_type": "connection", "error_message": _sanitize_error(error), "checked_at": _now_iso()}
    return {
        "ok": True,
        "http_status": status,
        "final_url": redact_url(final_url),
        "redirect_count": len(getattr(response, "redirect_statuses", ())),
        "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
        "checked_at": _now_iso(),
        **parsed,
    }


def probe_source(definition: dict) -> dict:
    if definition.get("source_type") == "api":
        adapter_name = str(definition.get("api_adapter") or "")
        adapter = API_ADAPTERS.get(adapter_name)
        if adapter is None:
            raise SourceValidationError("API 来源必须选择已实现的显式适配器，禁止通用 JSON 猜测")
        return adapter(definition)
    return probe_rss_source(definition)


class SourceManager:
    def __init__(
        self,
        *,
        builtins_path: str | os.PathLike[str] | None = None,
        data_dir: str | os.PathLike[str] | None = None,
        probe: Callable[[dict], dict] | None = None,
    ) -> None:
        here = Path(__file__).resolve().parent
        self.builtins_path = Path(builtins_path or here / "news_sources.json")
        configured_data_dir = os.environ.get("VR_DATA_DIR")
        root = Path(data_dir) if data_dir is not None else Path(configured_data_dir) if configured_data_dir else Path.home() / ".vibe-research"
        self.store_path = root / STORE_NAME
        self._probe = probe or probe_source
        self._lock = _lock_for(self.store_path)

    @staticmethod
    def _empty_store() -> dict:
        return {"schema_version": SCHEMA_VERSION, "disabled_builtin_ids": [], "custom_sources": [], "health": {}}

    def _builtins(self) -> dict:
        try:
            document = json.loads(self.builtins_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SourceManagerError("内置资讯来源配置不可读") from error
        if type(document) is not dict or type(document.get("sources")) is not list or type(document.get("industries")) is not list:
            raise SourceManagerError("内置资讯来源配置结构无效")
        default_region = str(document.get("default_region") or "unknown")
        document["sources"] = [
            {**row, "region": row.get("region") or default_region}
            for row in document["sources"] if type(row) is dict
        ]
        return document

    def _load_store(self, *, for_write: bool = False) -> tuple[dict, str]:
        try:
            if self.store_path.stat().st_size > MAX_STORE_BYTES:
                raise SourceStoreCorruptError("自定义资讯来源配置超过大小上限")
            document = json.loads(self.store_path.read_text(encoding="utf-8"))
            if (
                type(document) is not dict
                or document.get("schema_version") != SCHEMA_VERSION
                or type(document.get("disabled_builtin_ids")) is not list
                or type(document.get("custom_sources")) is not list
                or type(document.get("health")) is not dict
            ):
                raise SourceStoreCorruptError("自定义资讯来源配置结构无效")
            if any(type(value) is not str for value in document["disabled_builtin_ids"]):
                raise SourceStoreCorruptError("自定义资讯来源停用清单无效")
            normalized_custom = []
            for row in document["custom_sources"]:
                if type(row) is not dict or type(row.get("enabled", True)) is not bool:
                    raise SourceStoreCorruptError("自定义资讯来源条目无效")
                required_strings = ("id", "source_type", "name", "url", "hint", "region", "created_at")
                if any(type(row.get(key)) is not str for key in required_strings):
                    raise SourceStoreCorruptError("自定义资讯来源字段类型无效")
                if "api_adapter" in row and type(row.get("api_adapter")) is not str:
                    raise SourceStoreCorruptError("自定义资讯来源 API 适配器无效")
                try:
                    normalized = self._normalize_definition(row)
                    validate_url_shape(normalized["url"])
                except SourceManagerError as error:
                    raise SourceStoreCorruptError("自定义资讯来源条目无效") from error
                identifier = row.get("id")
                if type(identifier) is not str or not re.fullmatch(r"[0-9a-f]{16}", identifier) or identifier != _source_id(normalized):
                    raise SourceStoreCorruptError("自定义资讯来源标识无效")
                normalized_custom.append({
                    **normalized,
                    "id": identifier,
                    "enabled": row.get("enabled", True),
                    "created_at": str(row.get("created_at") or ""),
                })
            normalized_health = {}
            for key, value in document["health"].items():
                if type(key) is not str or not re.fullmatch(r"[0-9a-f]{16}", key) or type(value) is not dict:
                    raise SourceStoreCorruptError("自定义资讯来源健康状态无效")
                normalized_health[key] = _normalize_health_record(value, strict=True)
            document["custom_sources"] = normalized_custom
            document["health"] = normalized_health
            return document, "ok"
        except FileNotFoundError:
            return self._empty_store(), "missing"
        except (OSError, UnicodeError, json.JSONDecodeError, SourceStoreCorruptError) as error:
            if for_write:
                if isinstance(error, SourceStoreCorruptError):
                    raise
                raise SourceStoreCorruptError("自定义资讯来源配置已损坏，拒绝覆盖") from error
            return self._empty_store(), "corrupt"

    def _atomic_write(self, document: dict) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.store_path.parent,
                prefix=".news-sources.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.store_path)
            temporary = None
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def _normalize_definition(self, value: Mapping[str, object]) -> dict:
        source_type = str(value.get("source_type") or "rss").strip().lower()
        name = str(value.get("name") or "").strip()
        url = str(value.get("url") or "").strip()
        hint = str(value.get("hint") or "").strip()
        region = str(value.get("region") or self._builtins().get("default_region") or "unknown").strip()
        if source_type not in {"rss", "api"}:
            raise SourceValidationError("来源类型仅支持 rss 或 api")
        if not name or len(name) > 120:
            raise SourceValidationError("来源名称不能为空且不得超过 120 字符")
        if not url or len(url) > 2048:
            raise SourceValidationError("来源 URL 不能为空且不得超过 2048 字符")
        validate_url_shape(url)
        allowed_hints = {str(row.get("key")) for row in self._builtins()["industries"]}
        if hint not in allowed_hints:
            raise SourceValidationError("来源赛道不存在")
        normalized = {
            "source_type": source_type, "name": name, "url": url,
            "hint": hint, "region": region or "unknown",
        }
        if source_type == "api":
            adapter = str(value.get("api_adapter") or "").strip()
            if adapter not in API_ADAPTERS:
                raise SourceValidationError("API 来源必须选择已实现的显式适配器，禁止通用 JSON 猜测")
            normalized["api_adapter"] = adapter
        return normalized

    @staticmethod
    def _health_from_probe(result: Mapping[str, object]) -> dict:
        checked_at = result.get("checked_at") if _valid_iso_timestamp(result.get("checked_at")) else _now_iso()
        http_status = result.get("http_status")
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            http_status = None
        candidate_error_type = result.get("error_type")
        health = {
            "status": "ok" if result.get("ok") is True else "failed",
            "checked_at": checked_at,
            "http_status": http_status,
            "error_type": None if result.get("ok") is True else (
                candidate_error_type
                if type(candidate_error_type) is str and candidate_error_type in HEALTH_ERROR_TYPES
                else "unknown"
            ),
            "error_message": None if result.get("ok") is True else _sanitize_error(result.get("error_message")),
            "last_success_at": checked_at if result.get("ok") is True else None,
        }
        return _normalize_health_record(health, strict=False)

    def test_definition(self, definition: Mapping[str, object]) -> dict:
        normalized = self._normalize_definition(definition)
        result = dict(self._probe(normalized))
        result.setdefault("ok", False)
        result.setdefault("checked_at", _now_iso())
        return self._project_probe(result)

    @staticmethod
    def _project_probe(result: Mapping[str, object]) -> dict:
        projected = dict(result)
        for key in ("final_url", "source_url"):
            if key in projected:
                projected[key] = public_source_url(projected[key])
        if "sample_link" in projected:
            projected["sample_link"] = redact_url(projected["sample_link"])
        if "error_message" in projected:
            projected["error_message"] = _sanitize_error(projected["error_message"])
        return projected

    @staticmethod
    def _project_source(source: Mapping[str, object]) -> dict:
        return {
            **{key: value for key, value in source.items() if key != "url"},
            "display_url": public_source_url(source.get("url")),
        }

    def _rows(self, builtins: dict, store: dict) -> list[dict]:
        disabled = {str(value) for value in store["disabled_builtin_ids"]}
        health = store["health"]
        rows = []
        for source in builtins["sources"]:
            identifier = _source_id(source)
            rows.append(self._project_source({
                "id": identifier, "source_type": "rss", "built_in": True,
                "enabled": identifier not in disabled,
                "name": str(source.get("name") or ""), "url": str(source.get("url") or ""),
                "hint": str(source.get("hint") or ""), "region": str(source.get("region") or "unknown"),
                "health": health.get(identifier) or {"status": "unknown", "checked_at": None},
            }))
        for source in store["custom_sources"]:
            if type(source) is not dict:
                continue
            identifier = str(source.get("id") or _source_id(source))
            rows.append(self._project_source({
                **source, "id": identifier, "built_in": False,
                "enabled": source.get("enabled") is not False,
                "health": health.get(identifier) or {"status": "unknown", "checked_at": None},
            }))
        return rows

    def list_sources(self) -> dict:
        with self._lock:
            builtins = self._builtins()
            store, status = self._load_store()
            rows = self._rows(builtins, store)
        return {
            "store_status": status,
            "store_error": "自定义配置已损坏；内置来源继续可用，写入已禁用" if status == "corrupt" else None,
            "api_adapters": sorted(API_ADAPTERS),
            "industries": builtins["industries"],
            "summary": {
                "total": len(rows),
                "built_in": sum(row["built_in"] for row in rows),
                "custom": sum(not row["built_in"] for row in rows),
                "enabled": sum(row["enabled"] for row in rows),
            },
            "sources": rows,
        }

    def add_source(self, definition: Mapping[str, object]) -> dict:
        normalized = self._normalize_definition(definition)
        result = dict(self._probe(normalized))
        if result.get("ok") is not True:
            raise SourceValidationError(_sanitize_error(result.get("error_message")) or "来源连接测试未通过")
        with self._lock:
            builtins = self._builtins()
            store, _ = self._load_store(for_write=True)
            target_url = _canonical_url(normalized["url"])
            existing = [*builtins["sources"], *store["custom_sources"]]
            if any(_canonical_url(str(row.get("url") or "")) == target_url for row in existing if row.get("url")):
                raise DuplicateSourceError("该来源 URL 已存在")
            identifier = _source_id(normalized)
            custom = {**normalized, "id": identifier, "enabled": True, "created_at": _now_iso()}
            store["custom_sources"].append(custom)
            store["health"][identifier] = self._health_from_probe(result)
            self._atomic_write(store)
        return self._project_source({**custom, "built_in": False, "health": store["health"][identifier]})

    def _find(self, source_id: str, builtins: dict, store: dict) -> tuple[dict, bool]:
        for source in builtins["sources"]:
            if _source_id(source) == source_id:
                return source, True
        for source in store["custom_sources"]:
            if str(source.get("id") or _source_id(source)) == source_id:
                return source, False
        raise SourceNotFoundError("资讯来源不存在")

    def test_source(self, source_id: str) -> dict:
        with self._lock:
            builtins = self._builtins()
            store, _ = self._load_store(for_write=True)
            source, built_in = self._find(source_id, builtins, store)
            definition = {
                "source_type": source.get("source_type") or "rss",
                "name": source.get("name"), "url": source.get("url"), "hint": source.get("hint"),
                "region": source.get("region") or builtins.get("default_region"),
                "api_adapter": source.get("api_adapter"),
            }
            result = self.test_definition(definition)
            store["health"][source_id] = self._health_from_probe(result)
            self._atomic_write(store)
        return {"source_id": source_id, "built_in": built_in, **result}

    def set_enabled(self, source_id: str, enabled: bool) -> dict:
        with self._lock:
            builtins = self._builtins()
            store, _ = self._load_store(for_write=True)
            source, built_in = self._find(source_id, builtins, store)
            disabled = {str(value) for value in store["disabled_builtin_ids"]}
            if built_in:
                if enabled:
                    disabled.discard(source_id)
                else:
                    disabled.add(source_id)
                store["disabled_builtin_ids"] = sorted(disabled)
            else:
                source["enabled"] = bool(enabled)
            self._atomic_write(store)
        return {"id": source_id, "built_in": built_in, "enabled": bool(enabled)}

    def delete_source(self, source_id: str) -> dict:
        with self._lock:
            builtins = self._builtins()
            store, _ = self._load_store(for_write=True)
            _, built_in = self._find(source_id, builtins, store)
            if built_in:
                raise BuiltinSourceDeletionError("内置来源不可删除，只能停用")
            store["custom_sources"] = [
                row for row in store["custom_sources"]
                if str(row.get("id") or _source_id(row)) != source_id
            ]
            store["health"].pop(source_id, None)
            self._atomic_write(store)
        return {"id": source_id, "deleted": True}

    def runtime_config(self) -> dict:
        with self._lock:
            builtins = self._builtins()
            store, status = self._load_store()
            disabled = {str(value) for value in store["disabled_builtin_ids"]}
            sources = [
                {**row, "id": _source_id(row)}
                for row in builtins["sources"] if _source_id(row) not in disabled
            ]
            seen = {_canonical_url(str(row["url"])) for row in sources}
            for row in store["custom_sources"]:
                if type(row) is not dict or row.get("enabled") is False:
                    continue
                if row.get("source_type", "rss") == "api" and row.get("api_adapter") not in API_ADAPTERS:
                    continue
                canonical = _canonical_url(str(row.get("url") or ""))
                if canonical in seen:
                    continue
                seen.add(canonical)
                sources.append({
                    "id": str(row.get("id") or _source_id(row)),
                    "name": row.get("name"), "url": row.get("url"), "hint": row.get("hint"),
                    "region": row.get("region") or builtins.get("default_region") or "unknown",
                    "source_type": row.get("source_type") or "rss", "api_adapter": row.get("api_adapter"),
                })
            return {**builtins, "sources": sources, "custom_store_status": status}

    def record_statuses(self, statuses: list[dict]) -> bool:
        with self._lock:
            try:
                store, _ = self._load_store(for_write=True)
            except SourceStoreCorruptError:
                return False
            changed = False
            for row in statuses:
                identifier = str(row.get("source_id") or "")
                if not identifier:
                    continue
                previous = store["health"].get(identifier) or {}
                success = row.get("status") in {"ok", "success"}
                checked_at = row.get("fetched_at") if _valid_iso_timestamp(row.get("fetched_at")) else _now_iso()
                last_success_at = row.get("last_success_at") or previous.get("last_success_at")
                if not _valid_iso_timestamp(last_success_at):
                    last_success_at = None
                http_status = row.get("http_status")
                if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
                    http_status = None
                candidate_error_type = row.get("error_type")
                health = {
                    "status": "ok" if success else "failed",
                    "checked_at": checked_at,
                    "http_status": http_status,
                    "error_type": None if success else (
                        candidate_error_type
                        if type(candidate_error_type) is str and candidate_error_type in HEALTH_ERROR_TYPES
                        else "unknown"
                    ),
                    "error_message": None if success else _sanitize_error(row.get("error_reason") or row.get("error_message")),
                    "last_success_at": last_success_at,
                }
                store["health"][identifier] = _normalize_health_record(health, strict=False)
                changed = True
            if changed:
                self._atomic_write(store)
            return changed


def runtime_source_config(builtins_path: str | os.PathLike[str] | None = None) -> dict:
    return SourceManager(builtins_path=builtins_path).runtime_config()


def record_runtime_statuses(statuses: list[dict], builtins_path: str | os.PathLike[str] | None = None) -> bool:
    return SourceManager(builtins_path=builtins_path).record_statuses(statuses)
