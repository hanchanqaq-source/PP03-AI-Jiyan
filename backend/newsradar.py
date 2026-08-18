"""资讯雷达数据层 —— 移植自 investment-news。

抓 12 赛道 108 个公开 RSS 源 → 合规过滤（赌/预测市场/加密/色情）+ 最近 N 天
+ 按赛道分组、时间倒序。纯标准库 + 线程池，零 key、零个股字段。

AI「今日要点」不在此模块——复用 Vibe-Research 的可插拔 AI 层（前端调 /api/chat，
把某赛道资讯打包给用户自己的模型提炼）。本模块只出客观资讯。
"""

from __future__ import annotations

import json
import gzip
import hashlib
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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

from source_health.probe_errors import (
    ProbeEmptyPayloadError,
    ProbeParseError,
    classify_probe_error,
    redact_probe_message,
    redact_url,
    retry_delay_seconds,
    retry_after_present,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(HERE, "news_sources.json")
CACHE_DIR = os.environ.get("VR_NEWS_CACHE_DIR") or os.path.join(HERE, ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "radar.json")
CACHE_WRITE_LOCK = threading.Lock()

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BEIJING = timezone(timedelta(hours=8))
SOURCE_ERROR_TYPES = {"timeout", "http_status", "tls", "dns", "connection", "rss_parse", "unknown"}
_ORIGINAL_URLOPEN = urllib.request.urlopen


class _RecordingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[int] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.statuses.append(int(code))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_public_url(request: urllib.request.Request, timeout: float):
    # Preserve test and embedding injection of urlopen; real requests record redirect status
    # without changing headers, TLS verification, retry count, or response parsing.
    if urllib.request.urlopen is not _ORIGINAL_URLOPEN:
        response = urllib.request.urlopen(request, timeout=timeout)
        return response, tuple(getattr(response, "redirect_statuses", ()))
    handler = _RecordingRedirectHandler()
    response = urllib.request.build_opener(handler).open(request, timeout=timeout)
    return response, tuple(handler.statuses)


def source_id(source: dict) -> str:
    """Stable non-secret identifier; retry never accepts a caller-provided URL."""
    identity = "\0".join((
        str(source.get("hint") or ""),
        str(source.get("name") or ""),
        str(source.get("url") or ""),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def sanitize_source_error(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)\b(authorization|cookie|token|api[_-]?key)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"(?i)\b[A-Z]:\\Users\\[^\s]+", "[local-path]", text)
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", text)
    return re.sub(r"\s+", " ", text).strip()[:200]


def classify_source_error(error: BaseException) -> tuple[str, str]:
    nested = getattr(error, "reason", None)
    if isinstance(error, urllib.error.HTTPError):
        return "http_status", f"来源返回 HTTP {int(error.code)}"
    if isinstance(error, ET.ParseError):
        return "rss_parse", "来源 RSS / XML 解析失败"
    if isinstance(error, (TimeoutError, socket.timeout)) or isinstance(nested, (TimeoutError, socket.timeout)):
        return "timeout", "来源请求超时"
    if isinstance(error, ssl.SSLError) or isinstance(nested, ssl.SSLError):
        return "tls", "来源 TLS 连接失败"
    if isinstance(error, socket.gaierror) or isinstance(nested, socket.gaierror):
        return "dns", "来源域名解析失败"
    if isinstance(error, (ConnectionError, urllib.error.URLError, OSError)):
        return "connection", "来源连接失败"
    kind = sanitize_source_error(type(error).__name__) or "UnknownError"
    return "unknown", f"来源抓取失败（{kind}）"[:200]


def _load_source_config() -> dict:
    with open(SOURCES_FILE, encoding="utf-8") as source_file:
        config = json.load(source_file)
    default_region = str(config.get("default_region") or "unknown")
    config["sources"] = [
        {**source, "region": source.get("region") or default_region}
        for source in config["sources"]
    ]
    return config


def _apply_configured_regions(data: dict, sources: list[dict]) -> None:
    by_url = {str(source.get("url") or ""): str(source.get("region") or "unknown") for source in sources}
    by_name = {str(source.get("name") or ""): str(source.get("region") or "unknown") for source in sources}
    for industry in data.get("industries") or []:
        for item in industry.get("items") or []:
            if str(item.get("region") or "unknown").lower() != "unknown":
                continue
            region = by_url.get(str(item.get("source_url") or ""))
            if not region:
                region = by_name.get(str(item.get("source_name") or item.get("source") or ""))
            if region:
                item["region"] = region


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _parse_dt(s: str):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _decode_feed(raw: bytes, headers: object) -> str:
    content_encoding = str(getattr(headers, "get", lambda _key, _default=None: _default)("Content-Encoding", "") or "").lower()
    if "gzip" in content_encoding or raw.startswith(b"\x1f\x8b"):
        try:
            raw = gzip.decompress(raw)
        except Exception as error:
            raise ProbeParseError("gzip decode failed") from error
    content_type = str(getattr(headers, "get", lambda _key, _default=None: _default)("Content-Type", "") or "")
    charset = None
    match = re.search(r"(?i)charset\s*=\s*['\"]?([^\s;'\"]+)", content_type)
    if match:
        charset = match.group(1)
    if not charset:
        declaration = re.match(br"\s*<\?xml[^>]*encoding\s*=\s*['\"]([^'\"]+)", raw, flags=re.I)
        if declaration:
            charset = declaration.group(1).decode("ascii", errors="replace")
    try:
        return raw.decode(charset or "utf-8-sig")
    except (LookupError, UnicodeError) as error:
        raise ProbeParseError("character decode failed") from error


def _parse_feed_items(
    src: dict,
    root: ET.Element,
    per: int,
    cutoff,
    redline: list[str],
    fetched_at: str,
) -> tuple[list[dict], int, str | None]:
    out = []
    valid_items_before_cutoff = 0
    latest_valid_published_at = None
    for n in [e for e in root.iter() if _local(e.tag) in ("item", "entry")]:
        if len(out) >= per:
            break
        d = {
            "title": "", "url": "", "time": "", "ts": 0, "summary": "", "source": src["name"],
            "source_name": src["name"], "source_url": src["url"], "original_url": "",
            "published_at": None, "fetched_at": fetched_at, "summary_or_excerpt": "",
            "language": src.get("language") or "unknown", "region": src.get("region") or "unknown",
            "data_status": "realtime",
        }
        rawtime = ""
        for c in n:
            t = _local(c.tag)
            if t == "title" and not d["title"]:
                d["title"] = (c.text or "").strip()
            elif t == "link" and not d["url"]:
                d["url"] = c.get("href") or (c.text or "").strip()
            elif t in ("pubDate", "published", "updated", "date") and not rawtime:
                rawtime = (c.text or "").strip()
            elif t in ("description", "summary", "content") and not d["summary"]:
                d["summary"] = _strip_html(c.text or "")[:160]
        if not d["title"]:
            continue
        blob = (d["title"] + " " + d["summary"]).lower()
        if any(k in blob for k in redline):
            continue
        valid_items_before_cutoff += 1
        dt = _parse_dt(rawtime)
        if dt is not None:
            published_at = dt.astimezone(BEIJING).isoformat(timespec="seconds")
            latest_valid_published_at = max(latest_valid_published_at or published_at, published_at)
            if cutoff and dt < cutoff:
                continue
            d["time"] = dt.astimezone(BEIJING).strftime("%m-%d %H:%M")
            d["ts"] = int(dt.timestamp())
            d["published_at"] = published_at
        else:
            d["time"] = "—"
        d["original_url"] = d["url"]
        d["summary_or_excerpt"] = d["summary"]
        out.append(d)
    return out, valid_items_before_cutoff, latest_valid_published_at


def _request_decode_parse_source(
    src: dict,
    per: int,
    cutoff,
    redline: list[str],
    *,
    timeout: float,
    retry_transient: bool,
) -> dict:
    """Shared production/probe request, decode and RSS/Atom parse path."""
    started = time.perf_counter()
    for attempt in range(2 if retry_transient else 1):
        try:
            fetched_at = datetime.now(BEIJING).isoformat(timespec="seconds")
            req = urllib.request.Request(src["url"], headers={
                "User-Agent": UA,
                "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*",
                "Accept-Encoding": "gzip",
            })
            response, redirect_statuses = _open_public_url(req, timeout)
            with response:
                raw = response.read()
                headers = getattr(response, "headers", {}) or {}
                http_status = getattr(response, "status", None)
                if http_status is None and hasattr(response, "getcode"):
                    http_status = response.getcode()
                http_status = int(http_status or 200)
                final_url = response.geturl() if hasattr(response, "geturl") else src["url"]
                content_type = str(getattr(headers, "get", lambda _key, _default=None: _default)("Content-Type", "") or "")
            decoded = _decode_feed(raw, headers)
            root = ET.fromstring(decoded)
            items, valid_items_before_cutoff, latest_valid_published_at = _parse_feed_items(
                src, root, per, cutoff, redline, fetched_at,
            )
            return {
                "status": "success",
                "items": items,
                "fetched_at": fetched_at,
                "http_status": http_status,
                "error_type": "none",
                "error_message_redacted": "",
                "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
                "redirected": str(final_url) != str(src["url"]),
                "redirect_status": next((code for code in redirect_statuses if code in {301, 308}), None),
                "final_url": redact_url(final_url),
                "content_type": content_type,
                "_valid_items_before_cutoff": valid_items_before_cutoff,
                "_latest_valid_published_at": latest_valid_published_at,
            }
        except Exception as error:
            classified = classify_probe_error(error)
            if retry_transient and attempt == 0 and classified.retryable:
                time.sleep(retry_delay_seconds(error))
                continue
            return {
                "status": "failure",
                "items": [],
                "fetched_at": None,
                "http_status": classified.http_status,
                "error_type": classified.error_type,
                "error_message_redacted": classified.message,
                "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
                "redirected": False,
                "redirect_status": None,
                "final_url": redact_url(src.get("url")),
                "content_type": "",
                "retry_after_present": retry_after_present(error),
            }
    raise AssertionError("unreachable")


def _fetch_source(src: dict, per: int, cutoff, redline: list[str]):
    """抓单个 RSS 源；失败只返回来源级状态，不抛出整页异常。"""
    result = _request_decode_parse_source(src, per, cutoff, redline, timeout=14, retry_transient=False)
    if result["status"] == "success":
        return {
            "status": "ok", "items": result["items"], "fetched_at": result["fetched_at"],
            "error_type": None, "error_reason": None,
        }
    legacy_type = {
        "parse": "rss_parse",
        "authentication": "http_status",
        "rate_limit": "http_status",
        "http": "http_status",
    }.get(result["error_type"], result["error_type"])
    legacy_reason = {
        "parse": "来源 RSS / XML 解析失败",
        "authentication": f"来源返回 HTTP {result['http_status']}",
        "rate_limit": f"来源返回 HTTP {result['http_status']}",
        "http": f"来源返回 HTTP {result['http_status']}",
        "timeout": "来源请求超时",
        "tls": "来源 TLS 连接失败",
        "dns": "来源域名解析失败",
        "connection": "来源连接失败",
    }.get(result["error_type"], f"来源抓取失败（{result['error_message_redacted'] or 'UnknownError'}）")
    return {
        "status": "failed", "items": [], "fetched_at": None,
        "error_type": legacy_type, "error_reason": sanitize_source_error(legacy_reason),
    }


def probe_source_config(
    source: dict,
    *,
    per_source: int = 2,
    recent_days: int = 30,
    timeout: float | None = None,
) -> dict:
    """Use the production request/decode/parser path without cache or user-data access."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
    result = _request_decode_parse_source(
        dict(source),
        max(1, int(per_source)),
        cutoff,
        [],
        timeout=float(timeout if timeout is not None else 14),
        retry_transient=True,
    )
    valid_items_before_cutoff = int(result.pop("_valid_items_before_cutoff", 0))
    latest_valid_published_at = result.pop("_latest_valid_published_at", None)
    if result["status"] == "success" and not result["items"]:
        if valid_items_before_cutoff and latest_valid_published_at:
            result.update({
                "status": "partial",
                "error_type": "stale_data",
                "error_message_redacted": "数据日期超过能力新鲜度阈值",
            })
        else:
            classified = classify_probe_error(ProbeEmptyPayloadError())
            result.update({
                "status": "failure",
                "error_type": classified.error_type,
                "error_message_redacted": classified.message,
            })
    elif result["status"] == "success" and result["redirected"]:
        result.update({
            "status": "partial",
            "error_type": "redirect",
            "error_message_redacted": "来源已重定向到公开最终地址",
        })
    core_fields = ("title", "url", "published_at")
    total_fields = len(result["items"]) * len(core_fields)
    present_fields = sum(
        value is not None and str(value).strip() != ""
        for item in result["items"]
        for value in (item.get(field) for field in core_fields)
    )
    result["field_completeness_pct"] = (
        round(present_fields / total_fields * 100, 2) if total_fields else 0.0
    )
    result["returned_items"] = len(result["items"])
    result["latest_published_at"] = max(
        (str(item["published_at"]) for item in result["items"] if item.get("published_at")),
        default=latest_valid_published_at,
    )
    result["error_message_redacted"] = redact_probe_message(result["error_message_redacted"])
    return result


def _cached_items_for_source(cache: dict | None, industry_key: str, src: dict) -> list[dict]:
    if not cache:
        return []
    industry = next((row for row in cache.get("industries") or [] if row.get("key") == industry_key), None)
    if not industry:
        return []
    out = []
    for item in industry.get("items") or []:
        if item.get("source_url") == src["url"] or item.get("source_name") == src["name"] or item.get("source") == src["name"]:
            copied = dict(item)
            copied["data_status"] = "stale"
            copied["region"] = src.get("region") or copied.get("region") or "unknown"
            out.append(copied)
    return out


def _last_success_at(previous: dict | None, cached_items: list[dict], source: dict | None = None) -> str | None:
    if source:
        configured_id = source_id(source)
        previous_status = next((
            row for row in (previous or {}).get("source_statuses") or []
            if str(row.get("source_id") or "") == configured_id and row.get("last_success_at")
        ), None)
        if previous_status:
            return str(previous_status["last_success_at"])
    timestamps = [
        str(item.get("fetched_at") or item.get("published_at") or "")
        for item in cached_items
        if item.get("fetched_at") or item.get("published_at")
    ]
    if timestamps:
        return max(timestamps)
    if cached_items:
        return str((previous or {}).get("generated_at") or "") or None
    return None


def _source_status(src: dict, result: dict, cached_items: list[dict], previous: dict | None) -> dict:
    succeeded = result.get("status") == "ok"
    return {
        "source_id": source_id(src),
        "source_name": src["name"],
        "source_url": src["url"],
        "status": result.get("status") or "failed",
        "error_type": result.get("error_type"),
        "error_reason": result.get("error_reason"),
        "last_success_at": result.get("fetched_at") if succeeded else _last_success_at(previous, cached_items, src),
        "used_cached_items": bool(cached_items) and not succeeded,
        "item_count": len(result.get("items") or []) if succeeded else len(cached_items),
    }


def _set_all_item_status(data: dict, status: str) -> None:
    for industry in data.get("industries") or []:
        for item in industry.get("items") or []:
            item["data_status"] = status


def _normalize_cached_source_statuses(data: dict, sources: list[dict]) -> None:
    """Expose the new diagnostic schema for legacy cache rows without rewriting cache bytes."""
    configured_by_id = {source_id(source): source for source in sources}
    assigned_ids: set[str] = set()
    normalized = []
    for legacy in data.get("source_statuses") or []:
        row = dict(legacy)
        source_url = str(row.get("source_url") or "")
        configured = configured_by_id.get(str(row.get("source_id") or ""))
        if configured is None:
            candidates = [
                source for source in sources
                if str(source.get("url") or "") == source_url and source_id(source) not in assigned_ids
            ]
            source_name = str(row.get("source_name") or "")
            configured = next((source for source in candidates if str(source.get("name") or "") == source_name), None)
            configured = configured or (candidates[0] if candidates else None)
        configured_id = source_id(configured) if configured else source_id({
            "url": source_url,
            "name": row.get("source_name") or "",
            "hint": row.get("hint") or "",
        })
        assigned_ids.add(configured_id)
        cached_items = _cached_items_for_source(
            data,
            str((configured or {}).get("hint") or ""),
            configured or {"url": source_url, "name": row.get("source_name") or ""},
        ) if source_url else []
        failed = row.get("status") == "failed"
        used_cached_items = bool(row.get("used_cached_items")) if "used_cached_items" in row else failed and bool(cached_items)
        item_count = int(row.get("item_count") or 0)
        if failed and used_cached_items:
            item_count = len(cached_items)
        last_success_at = row.get("last_success_at")
        if not last_success_at:
            last_success_at = _last_success_at(data, cached_items, configured)
            if not failed and not last_success_at:
                last_success_at = data.get("generated_at")
        raw_error_type = str(row.get("error_type") or "unknown")
        error_type = raw_error_type if raw_error_type in SOURCE_ERROR_TYPES else "unknown"
        error_reason = sanitize_source_error(row.get("error_reason")) or "旧缓存未记录具体失败原因"
        normalized.append({
            "source_id": configured_id,
            "source_name": str(row.get("source_name") or (configured or {}).get("name") or "未知来源"),
            "source_url": source_url,
            "status": "failed" if failed else "ok",
            "error_type": error_type if failed else None,
            "error_reason": error_reason if failed else None,
            "last_success_at": last_success_at,
            "used_cached_items": used_cached_items,
            "item_count": item_count,
        })
    data["source_statuses"] = normalized


def _write_cache(data: dict) -> None:
    with CACHE_WRITE_LOCK:
        cache_dir = os.path.dirname(CACHE_FILE)
        os.makedirs(cache_dir, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=cache_dir,
                prefix="radar.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = tmp_file.name
                json.dump(data, tmp_file, ensure_ascii=False)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, CACHE_FILE)
            tmp_path = None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass


def fetch_radar() -> dict:
    """抓全部源，返回 12 赛道数据并落盘缓存。"""
    cfg = _load_source_config()
    previous = load_cache()
    days = cfg.get("fetch", {}).get("recent_days", 7)
    per = cfg.get("fetch", {}).get("per_source", 6)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    redline = [k.lower() for k in cfg.get("redline_keywords", [])]

    byhint: dict[str, list] = {}
    for s in cfg["sources"]:
        byhint.setdefault(s["hint"], []).append(s)

    industries, tasks = [], []
    for i, ind in enumerate(cfg["industries"]):
        pool = byhint.get(ind["key"], [])
        industries.append({"key": ind["key"], "name": ind["name"], "accent": ind["accent"], "total": len(pool), "items": []})
        for s in pool:
            tasks.append((i, s))

    with ThreadPoolExecutor(max_workers=40) as ex:
        results = list(ex.map(lambda t: (t[0], t[1], _fetch_source(t[1], per, cutoff, redline)), tasks))

    failed = 0
    succeeded = 0
    source_statuses = []
    for idx, src, result in results:
        items = result["items"]
        if result["status"] == "failed":
            failed += 1
            cached_items = _cached_items_for_source(previous, industries[idx]["key"], src)
            industries[idx]["items"].extend(cached_items)
            source_statuses.append(_source_status(src, result, cached_items, previous))
            continue
        succeeded += 1
        industries[idx]["items"].extend(items)
        source_statuses.append(_source_status(src, result, [], previous))

    if succeeded == 0:
        if previous:
            fallback = json.loads(json.dumps(previous, ensure_ascii=False))
            fallback["cache_status"] = "stale"
            fallback["source_state"] = "stale_cache"
            _set_all_item_status(fallback, "stale")
            fallback["source_statuses"] = source_statuses
            fallback.setdefault("stats", {})["failed_sources"] = failed
            return fallback
        fallback = skeleton()
        fallback["cache_status"] = "source_failure"
        fallback["source_state"] = "all_failed"
        fallback["source_statuses"] = source_statuses
        fallback["stats"]["failed_sources"] = failed
        return fallback

    for ind in industries:
        ind["items"].sort(key=lambda x: x.get("ts", 0), reverse=True)

    data = {
        "generated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "recent_days": days,
        "industries": industries,
        "stats": {"industries": len(cfg["industries"]), "total_sources": len(cfg["sources"]), "failed_sources": failed},
        "cache_status": "partial" if failed else "realtime",
        "source_state": "partial_failure" if failed else "all_success",
        "source_statuses": source_statuses,
    }
    _write_cache(data)
    return data


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        sources = _load_source_config()["sources"]
        _apply_configured_regions(data, sources)
        _normalize_cached_source_statuses(data, sources)
        generated_at = _parse_dt(str(data.get("generated_at") or ""))
        recent_days = int(data.get("recent_days") or 7)
        is_stale = bool(generated_at and datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc) > timedelta(days=recent_days))
        data["cache_status"] = "stale" if is_stale else "cache"
        failed_sources = int((data.get("stats") or {}).get("failed_sources") or 0)
        data["source_state"] = "stale_cache" if is_stale else ("partial_failure" if failed_sources else "cached")
        _set_all_item_status(data, data["cache_status"])
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def skeleton() -> dict:
    """无缓存时返回赛道骨架（空 items），前端提示点刷新。"""
    cfg = json.load(open(SOURCES_FILE, encoding="utf-8"))
    byhint: dict[str, int] = {}
    for s in cfg["sources"]:
        byhint[s["hint"]] = byhint.get(s["hint"], 0) + 1
    return {
        "generated_at": None,
        "recent_days": cfg.get("fetch", {}).get("recent_days", 7),
        "industries": [{"key": i["key"], "name": i["name"], "accent": i["accent"], "total": byhint.get(i["key"], 0), "items": []} for i in cfg["industries"]],
        "stats": {"industries": len(cfg["industries"]), "total_sources": len(cfg["sources"]), "failed_sources": 0},
        "cache_status": "empty",
        "source_state": "empty",
        "source_statuses": [],
    }


def retry_source(requested_source_id: str) -> dict:
    """Retry one configured source and atomically merge it into the current radar cache."""
    cfg = _load_source_config()
    src = next((source for source in cfg["sources"] if source_id(source) == requested_source_id), None)
    if src is None:
        raise ValueError("该资讯来源未配置，不能重试")

    previous = load_cache()
    days = int(cfg.get("fetch", {}).get("recent_days", 7))
    per = int(cfg.get("fetch", {}).get("per_source", 6))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    redline = [str(key).lower() for key in cfg.get("redline_keywords", [])]
    result = _fetch_source(src, per, cutoff, redline)
    cached_items = _cached_items_for_source(previous, str(src.get("hint") or ""), src)
    status = _source_status(src, result, cached_items, previous)
    if result["status"] != "ok":
        return {"ok": False, "source_status": status}

    data = json.loads(json.dumps(previous or skeleton(), ensure_ascii=False))
    industry = next((row for row in data.get("industries") or [] if row.get("key") == src.get("hint")), None)
    if industry is None:
        raise ValueError("该资讯来源的行业配置不存在")
    industry["items"] = [
        item for item in industry.get("items") or []
        if item.get("source_url") != src["url"]
        and item.get("source_name") != src["name"]
        and item.get("source") != src["name"]
    ]
    industry["items"].extend(result["items"])
    industry["items"].sort(key=lambda item: item.get("ts", 0), reverse=True)

    existing = {
        str(row.get("source_id") or ""): dict(row)
        for row in data.get("source_statuses") or []
        if row.get("source_id")
    }
    existing[requested_source_id] = status
    statuses = []
    for configured in cfg["sources"]:
        configured_id = source_id(configured)
        row = existing.get(configured_id)
        if row is None:
            cached = _cached_items_for_source(data, str(configured.get("hint") or ""), configured)
            if previous is None:
                row = {
                    "source_id": configured_id,
                    "source_name": configured["name"],
                    "source_url": configured["url"],
                    "status": "failed",
                    "error_type": "unknown",
                    "error_reason": "暂无成功缓存，本次单源重试未抓取该来源",
                    "last_success_at": None,
                    "used_cached_items": False,
                    "item_count": 0,
                }
            else:
                row = {
                    "source_id": configured_id,
                    "source_name": configured["name"],
                    "source_url": configured["url"],
                    "status": "ok",
                    "error_type": None,
                    "error_reason": None,
                    "last_success_at": _last_success_at(previous, cached, configured),
                    "used_cached_items": bool(cached),
                    "item_count": len(cached),
                }
        statuses.append(row)

    failed = sum(row.get("status") == "failed" for row in statuses)
    if not data.get("generated_at"):
        data["generated_at"] = datetime.now(BEIJING).isoformat(timespec="seconds")
    data["recent_days"] = days
    data["source_statuses"] = statuses
    generated_at = _parse_dt(str(data.get("generated_at") or ""))
    is_stale = bool(
        generated_at
        and datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc) > timedelta(days=days)
    )
    data["cache_status"] = "stale" if is_stale else ("partial" if failed else "realtime")
    data["source_state"] = "stale_cache" if is_stale else ("partial_failure" if failed else "all_success")
    data.setdefault("stats", {})["industries"] = len(cfg["industries"])
    data["stats"]["total_sources"] = len(cfg["sources"])
    data["stats"]["failed_sources"] = failed
    _write_cache(data)
    return {"ok": True, "source_status": status, "radar": data}


def get_radar(force: bool = False) -> dict:
    if force:
        return fetch_radar()
    return load_cache() or skeleton()
