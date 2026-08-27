"""Small, radar-local error classification and redaction helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import socket
import ssl
import urllib.error
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET


_SENSITIVE_QUERY_PARTS = (
    "token", "key", "secret", "password", "passwd", "session", "cookie",
    "authorization", "credential", "signature", "sig", "jwt",
)
_SENSITIVE_QUERY_NAMES = {"code"}


class ProbeSchemaError(ValueError):
    pass


class ProbeEmptyPayloadError(ValueError):
    pass


class ProbeStaleDataError(ValueError):
    pass


class ProbeParseError(ValueError):
    pass


@dataclass(frozen=True)
class ClassifiedProbeError:
    error_type: str
    message: str
    http_status: int | None = None
    retryable: bool = False


def redact_url(value: object) -> str:
    try:
        parts = urlsplit(str(value or ""))
    except ValueError:
        return ""
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        return ""
    try:
        hostname = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        port = parts.port
    except (UnicodeError, ValueError):
        return ""
    if not hostname:
        return ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    authority = hostname if port is None or default_port else f"{hostname}:{port}"
    query = []
    for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _SENSITIVE_QUERY_NAMES or any(part in lowered for part in _SENSITIVE_QUERY_PARTS):
            item_value = "[redacted]"
        query.append((key, item_value))
    return urlunsplit((scheme, authority, parts.path, urlencode(query), ""))


def public_source_url(value: object) -> str:
    """Return a deliberately minimal source label without path/query credentials."""
    try:
        parts = urlsplit(str(value or ""))
        hostname = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        port = parts.port
    except (UnicodeError, ValueError):
        return ""
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not hostname:
        return ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    authority = hostname if port is None or default_port else f"{hostname}:{port}"
    return urlunsplit((scheme, authority, "", "", ""))


def redact_probe_message(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"(?is)Traceback \(most recent call last\):.*", "", text)
    text = re.sub(r"(?im)(?<![\w:/])(?:\\\\\?\\)?[A-Z]:[\\/][^\r\n]*$", "[local-path]", text)
    text = re.sub(r"(?i)\b(?:authorization|proxy-authorization|cookie)\s*[:=]\s*[^\r\n,;]+", "[credential-redacted]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "[credential-redacted]", text)
    text = re.sub(r"(?i)\b(?:token|api[_-]?key|access[_-]?key|secret|password|session|jwt|credential|signature|sig)\s*[:=]\s*[^\s,;]+", "[credential-redacted]", text)

    def _redact_match(match: re.Match[str]) -> str:
        return public_source_url(match.group(0)) or "[url-redacted]"

    text = re.sub(r"https?://[^\s,;]+", _redact_match, text)
    return re.sub(r"\s+", " ", text).strip()[:200]


def _exception_chain(error: BaseException):
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        nested = getattr(current, "reason", None)
        current = nested if isinstance(nested, BaseException) else (current.__cause__ or current.__context__)


def _http_status(error: BaseException) -> int | None:
    if isinstance(error, urllib.error.HTTPError):
        return int(error.code)
    status = getattr(getattr(error, "response", None), "status_code", None)
    return int(status) if isinstance(status, int) else None


def classify_probe_error(error: BaseException) -> ClassifiedProbeError:
    chain = list(_exception_chain(error))
    if any(isinstance(item, ProbeParseError) for item in chain):
        return ClassifiedProbeError("parse", "响应内容解析失败")
    if any(isinstance(item, ProbeEmptyPayloadError) for item in chain):
        return ClassifiedProbeError("empty_payload", "成功连接但未返回有效内容")
    for item in chain:
        if type(item).__name__ in {"SourceValidationError", "SourceStoreCorruptError"}:
            message = str(item)
            if any(marker in message for marker in ("大小上限", "解压后超过")):
                return ClassifiedProbeError("response_size", "来源响应超过大小上限")
            if "gzip" in message.lower():
                return ClassifiedProbeError("parse", "响应内容解析失败")
            return ClassifiedProbeError("security", "来源 URL 或网络目标未通过安全校验")
    for item in chain:
        status = _http_status(item)
        if status is not None:
            if status in {401, 403}:
                return ClassifiedProbeError("authentication", f"HTTP {status}", status, False)
            if status in {408, 429}:
                return ClassifiedProbeError("rate_limit", f"HTTP {status}", status, True)
            return ClassifiedProbeError("http", f"HTTP {status}", status, status in {500, 502, 503, 504})
    if any(isinstance(item, (TimeoutError, socket.timeout)) or type(item).__name__.lower().endswith("timeout") for item in chain):
        return ClassifiedProbeError("timeout", "请求超时", retryable=True)
    if any(isinstance(item, socket.gaierror) for item in chain):
        return ClassifiedProbeError("dns", "域名解析失败")
    for item in chain:
        if isinstance(item, ssl.SSLError) or type(item).__name__ == "SSLError":
            detail = str(item).lower()
            temporary = isinstance(item, (ssl.SSLWantReadError, ssl.SSLWantWriteError)) or any(marker in detail for marker in ("temporary", "temporarily", "timed out", "timeout", "try again"))
            return ClassifiedProbeError("tls", "TLS 连接失败", retryable=temporary)
    if any(isinstance(item, ProbeParseError) for item in chain):
        return ClassifiedProbeError("parse", "响应内容解析失败")
    if any(isinstance(item, (ConnectionError, urllib.error.URLError, OSError)) or type(item).__name__ == "ConnectionError" for item in chain):
        return ClassifiedProbeError("connection", "连接失败", retryable=True)
    if any(isinstance(item, ProbeEmptyPayloadError) for item in chain):
        return ClassifiedProbeError("empty_payload", "成功连接但未返回有效内容")
    if any(isinstance(item, ProbeSchemaError) for item in chain):
        return ClassifiedProbeError("schema_changed", "返回字段不符合现有合同")
    if any(isinstance(item, ProbeStaleDataError) for item in chain):
        return ClassifiedProbeError("stale_data", "数据日期超过能力新鲜度阈值")
    if any(isinstance(item, (ET.ParseError, json.JSONDecodeError, UnicodeError)) for item in chain):
        return ClassifiedProbeError("parse", "响应内容解析失败")
    detail = redact_probe_message(error)
    return ClassifiedProbeError("unknown", detail or type(error).__name__)


def retry_delay_seconds(error: BaseException, *, default: float = 0.5, maximum: float = 5.0) -> float:
    headers = None
    for item in _exception_chain(error):
        headers = getattr(item, "headers", None) or getattr(getattr(item, "response", None), "headers", None)
        if headers:
            break
    raw = headers.get("Retry-After") if headers else None
    if raw is None:
        return default
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        try:
            parsed = datetime.strptime(str(raw), "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)
            delay = max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except ValueError:
            delay = default
    return min(maximum, max(0.0, delay))


def retry_after_present(error: BaseException) -> bool:
    for item in _exception_chain(error):
        headers = getattr(item, "headers", None) or getattr(getattr(item, "response", None), "headers", None)
        if headers and headers.get("Retry-After") is not None:
            return True
    return False
