from __future__ import annotations

import json
import socket
import ssl
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import newsradar
import pytest


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._payload


def _write_sources(
    path: Path,
    urls: list[str],
    *,
    default_region: str | None = None,
    source_region: str | None = "CN",
) -> None:
    config = {
        "fetch": {"recent_days": 7, "per_source": 6},
        "redline_keywords": [],
        "industries": [{"key": "semi", "name": "半导体", "accent": "#f59e0b"}],
        "sources": [
            {
                "name": f"公开源 {index}",
                "url": url,
                "hint": "semi",
                "language": "zh-CN",
                **({"region": source_region} if source_region else {}),
            }
            for index, url in enumerate(urls, start=1)
        ],
    }
    if default_region:
        config["default_region"] = default_region
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")


def _rss(title: str, link: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><item>
      <title>{title}</title><link>{link}</link>
      <pubDate>Sun, 16 Aug 2026 02:35:00 GMT</pubDate>
      <description>公开摘要内容</description>
    </item></channel></rss>""".encode()


def test_fetch_radar_preserves_complete_source_provenance(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    _write_sources(sources, ["https://feed.example.test/rss"])
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))
    monkeypatch.setattr(
        newsradar.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(_rss("北方华创发布公开公告", "https://news.example.test/a")),
    )

    data = newsradar.fetch_radar()

    item = data["industries"][0]["items"][0]
    assert item["source_name"] == "公开源 1"
    assert item["source_url"] == "https://feed.example.test/rss"
    assert item["original_url"] == "https://news.example.test/a"
    assert item["published_at"] == "2026-08-16T10:35:00+08:00"
    assert item["fetched_at"].endswith("+08:00")
    assert item["title"] == "北方华创发布公开公告"
    assert item["summary_or_excerpt"] == "公开摘要内容"
    assert item["language"] == "zh-CN"
    assert item["region"] == "CN"
    assert data["cache_status"] == "realtime"
    status = data["source_statuses"][0]
    assert status == {
        "source_id": newsradar.source_id({"url": "https://feed.example.test/rss", "hint": "semi", "name": "公开源 1"}),
        "source_name": "公开源 1",
        "source_url": "https://feed.example.test/rss",
        "status": "ok",
        "error_type": None,
        "error_reason": None,
        "last_success_at": item["fetched_at"],
        "used_cached_items": False,
        "item_count": 1,
    }
    assert data["source_state"] == "all_success"


def test_fetch_radar_applies_audited_config_default_region(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    _write_sources(
        sources,
        ["https://global.example.test/rss"],
        default_region="GLOBAL",
        source_region=None,
    )
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))
    monkeypatch.setattr(
        newsradar.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(_rss("Global chip policy update", "https://global.example.test/a")),
    )

    data = newsradar.fetch_radar()

    assert data["industries"][0]["items"][0]["region"] == "GLOBAL"


def test_all_source_failure_keeps_last_valid_cache_bytes(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    _write_sources(
        sources,
        ["https://failed.example.test/rss"],
        default_region="GLOBAL",
        source_region=None,
    )
    old = {
        "generated_at": "2026-08-15T09:00:00+08:00",
        "recent_days": 7,
        "industries": [{
            "key": "semi", "name": "半导体", "accent": "#f59e0b", "total": 1,
            "items": [{"title": "最后一次有效资讯", "source_url": "https://failed.example.test/rss", "region": "unknown", "data_status": "realtime"}],
        }],
        "stats": {"industries": 1, "total_sources": 1, "failed_sources": 0},
        "cache_status": "cache",
    }
    cache.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    original_bytes = cache.read_bytes()
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))
    monkeypatch.setattr(newsradar.urllib.request, "urlopen", lambda request, timeout: (_ for _ in ()).throw(OSError("offline")))

    data = newsradar.fetch_radar()

    assert data["industries"][0]["items"][0]["title"] == "最后一次有效资讯"
    assert data["cache_status"] == "stale"
    assert data["industries"][0]["items"][0]["data_status"] == "stale"
    assert data["industries"][0]["items"][0]["region"] == "GLOBAL"
    assert data["stats"]["failed_sources"] == 1
    assert data["source_statuses"][0]["status"] == "failed"
    assert cache.read_bytes() == original_bytes


def test_single_source_failure_returns_partial_success(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    _write_sources(sources, ["https://good.example.test/rss", "https://failed.example.test/rss"])
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))

    def fake_open(request, timeout):
        if request.full_url == "https://good.example.test/rss":
            return _Response(_rss("存储行业公开进展", "https://news.example.test/storage"))
        raise OSError("offline")

    monkeypatch.setattr(newsradar.urllib.request, "urlopen", fake_open)

    data = newsradar.fetch_radar()

    assert [item["title"] for item in data["industries"][0]["items"]] == ["存储行业公开进展"]
    assert data["cache_status"] == "partial"
    assert data["stats"]["failed_sources"] == 1
    assert [status["status"] for status in data["source_statuses"]] == ["ok", "failed"]
    failed = data["source_statuses"][1]
    assert failed["error_type"] == "connection"
    assert failed["error_reason"] == "来源连接失败"
    assert failed["used_cached_items"] is False
    assert data["source_state"] == "partial_failure"
    assert json.loads(cache.read_text(encoding="utf-8"))["cache_status"] == "partial"


def test_partial_failure_reuses_legacy_item_with_configured_region(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    urls = ["https://good.example.test/rss", "https://failed.example.test/rss"]
    _write_sources(sources, urls, default_region="GLOBAL", source_region=None)
    cache.write_text(json.dumps({
        "generated_at": "2026-08-17T09:00:00+00:00",
        "recent_days": 30,
        "cache_status": "realtime",
        "industries": [{
            "key": "semi", "name": "半导体", "accent": "#f59e0b", "total": 2,
            "items": [{
                "title": "失败源旧资讯",
                "source_name": "公开源 2",
                "source_url": "https://failed.example.test/rss",
                "region": "unknown",
                "data_status": "realtime",
            }],
        }],
        "stats": {"industries": 1, "total_sources": 2, "failed_sources": 0},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))

    def fake_open(request, timeout):
        if request.full_url == "https://good.example.test/rss":
            return _Response(_rss("新抓取资讯", "https://news.example.test/new"))
        raise OSError("offline")

    monkeypatch.setattr(newsradar.urllib.request, "urlopen", fake_open)

    data = newsradar.fetch_radar()
    reused = next(item for item in data["industries"][0]["items"] if item["title"] == "失败源旧资讯")

    assert data["cache_status"] == "partial"
    assert reused["data_status"] == "stale"
    assert reused["region"] == "GLOBAL"
    failed = next(status for status in data["source_statuses"] if status["status"] == "failed")
    assert failed["used_cached_items"] is True
    assert failed["item_count"] == 1
    assert failed["last_success_at"] == "2026-08-17T09:00:00+00:00"


def test_load_cache_downgrades_item_status_to_current_container_state(tmp_path, monkeypatch):
    cache = tmp_path / "radar.json"
    cache.write_text(json.dumps({
        "generated_at": "2026-08-17T09:00:00+00:00",
        "recent_days": 30,
        "cache_status": "realtime",
        "industries": [{"key": "semi", "items": [{"title": "缓存资讯", "data_status": "realtime"}]}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))

    data = newsradar.load_cache()

    assert data["cache_status"] == "cache"
    assert data["industries"][0]["items"][0]["data_status"] == "cache"


def test_load_cache_enriches_legacy_region_in_memory_without_rewriting_bytes(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    _write_sources(
        sources,
        ["https://global.example.test/rss"],
        default_region="GLOBAL",
        source_region=None,
    )
    cache.write_text(json.dumps({
        "generated_at": "2026-08-17T09:00:00+00:00",
        "recent_days": 30,
        "cache_status": "realtime",
        "industries": [{"key": "semi", "items": [{
            "title": "旧缓存全球资讯",
            "source_url": "https://global.example.test/rss",
            "region": "unknown",
            "data_status": "realtime",
        }]}],
    }, ensure_ascii=False), encoding="utf-8")
    original_bytes = cache.read_bytes()
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))

    data = newsradar.load_cache()

    assert data["industries"][0]["items"][0]["region"] == "GLOBAL"
    assert cache.read_bytes() == original_bytes


def test_load_cache_enriches_legacy_source_failures_without_inventing_error_detail(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    _write_sources(sources, ["https://feed.example.test/rss"])
    cache.write_text(json.dumps({
        "generated_at": "2026-08-17T09:00:00+00:00",
        "recent_days": 30,
        "industries": [{"key": "semi", "items": []}],
        "stats": {"industries": 1, "total_sources": 1, "failed_sources": 1},
        "source_statuses": [{
            "source_name": "公开源 1",
            "source_url": "https://feed.example.test/rss",
            "status": "failed",
            "item_count": 0,
        }],
    }, ensure_ascii=False), encoding="utf-8")
    original_bytes = cache.read_bytes()
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))

    data = newsradar.load_cache()

    assert data["source_statuses"] == [{
        "source_id": newsradar.source_id({"url": "https://feed.example.test/rss", "hint": "semi", "name": "公开源 1"}),
        "source_name": "公开源 1",
        "source_url": "https://feed.example.test/rss",
        "status": "failed",
        "error_type": "unknown",
        "error_reason": "旧缓存未记录具体失败原因",
        "last_success_at": None,
        "used_cached_items": False,
        "item_count": 0,
    }]
    assert cache.read_bytes() == original_bytes


def test_unique_atomic_cache_writes_remain_valid_under_concurrency(tmp_path, monkeypatch):
    cache = tmp_path / "radar.json"
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))
    payloads = [{"writer": index, "industries": []} for index in range(12)]

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(newsradar._write_cache, payloads))

    assert json.loads(cache.read_text(encoding="utf-8")) in payloads
    assert list(tmp_path.glob("radar.*.tmp")) == []


def test_production_sources_include_auditable_mainland_region_metadata():
    config = json.loads(Path(newsradar.SOURCES_FILE).read_text(encoding="utf-8"))
    by_name = {source["name"]: source for source in config["sources"]}

    for name in ("量子位", "智东西", "华尔街见闻", "东方财富资讯", "经济观察网"):
        assert by_name[name]["region"] == "CN"
        assert by_name[name]["language"] == "zh-CN"


def test_source_error_classification_is_bounded_and_sanitized():
    cases = [
        (TimeoutError("token=secret"), "timeout", "来源请求超时"),
        (urllib.error.HTTPError("https://feed.test/?token=secret", 503, "Bearer secret", {}, None), "http_status", "来源返回 HTTP 503"),
        (ssl.SSLError("C:\\Users\\private\\cert.pem token=secret"), "tls", "来源 TLS 连接失败"),
        (socket.gaierror("dns token=secret"), "dns", "来源域名解析失败"),
        (ConnectionError("Authorization: Bearer secret"), "connection", "来源连接失败"),
    ]

    for error, expected_type, expected_reason in cases:
        error_type, reason = newsradar.classify_source_error(error)
        assert (error_type, reason) == (expected_type, expected_reason)
        assert len(reason) <= 200
        assert "secret" not in reason
        assert "Users" not in reason

    sanitized = newsradar.sanitize_source_error(
        "Authorization: Bearer abc token=xyz C:\\Users\\private\\cache https://x.test/?api_key=value"
    )
    assert len(sanitized) <= 200
    assert "abc" not in sanitized and "xyz" not in sanitized and "private" not in sanitized and "value" not in sanitized


def test_load_cache_sanitizes_and_normalizes_legacy_failure_diagnostics(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    _write_sources(sources, ["https://feed.example.test/rss"])
    cache.write_text(json.dumps({
        "generated_at": "2026-08-17T09:00:00+00:00",
        "recent_days": 30,
        "industries": [{"key": "semi", "items": []}],
        "stats": {"industries": 1, "total_sources": 1, "failed_sources": 1},
        "source_statuses": [{
            "source_name": "公开源 1",
            "source_url": "https://feed.example.test/rss",
            "status": "failed",
            "error_type": "private_exception",
            "error_reason": "Authorization: Bearer secret C:\\Users\\private\\cache https://x.test/?token=value",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))

    status = newsradar.load_cache()["source_statuses"][0]

    assert status["error_type"] == "unknown"
    assert "Authorization=[redacted]" in status["error_reason"]
    assert "[local-path]" in status["error_reason"]
    assert "https://x.test/?[redacted]" in status["error_reason"]
    assert "secret" not in status["error_reason"]
    assert "private" not in status["error_reason"]
    assert "value" not in status["error_reason"]
    assert len(status["error_reason"]) <= 200


def test_duplicate_url_sources_have_track_scoped_ids_and_retry_the_selected_track(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    shared_url = "https://shared.example.test/rss"
    configured = [
        {"name": "共享公开源", "url": shared_url, "hint": "tech", "language": "en", "region": "GLOBAL"},
        {"name": "共享公开源", "url": shared_url, "hint": "consumer", "language": "en", "region": "GLOBAL"},
    ]
    sources.write_text(json.dumps({
        "fetch": {"recent_days": 7, "per_source": 6},
        "redline_keywords": [],
        "industries": [
            {"key": "tech", "name": "科技", "accent": "#111111"},
            {"key": "consumer", "name": "消费", "accent": "#222222"},
        ],
        "sources": configured,
    }, ensure_ascii=False), encoding="utf-8")
    cache.write_text(json.dumps({
        "generated_at": "2026-08-16T10:35:00+08:00",
        "recent_days": 7,
        "industries": [
            {"key": "tech", "items": [{"title": "科技旧资讯", "source_name": "共享公开源", "source_url": shared_url}]},
            {"key": "consumer", "items": [{"title": "消费旧资讯", "source_name": "共享公开源", "source_url": shared_url}]},
        ],
        "stats": {"industries": 2, "total_sources": 2, "failed_sources": 2},
        "source_statuses": [],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))
    monkeypatch.setattr(
        newsradar.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(_rss("消费重试资讯", "https://news.example.test/consumer")),
    )

    tech_id = newsradar.source_id(configured[0])
    consumer_id = newsradar.source_id(configured[1])
    result = newsradar.retry_source(consumer_id)

    assert tech_id != consumer_id
    assert result["source_status"]["source_id"] == consumer_id
    stored = json.loads(cache.read_text(encoding="utf-8"))
    by_key = {row["key"]: row["items"] for row in stored["industries"]}
    assert [item["title"] for item in by_key["tech"]] == ["科技旧资讯"]
    assert [item["title"] for item in by_key["consumer"]] == ["消费重试资讯"]
    assert len({row["source_id"] for row in stored["source_statuses"]}) == 2


def test_retry_source_is_whitelisted_and_atomically_replaces_only_that_source(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    _write_sources(sources, ["https://feed.example.test/rss"])
    cache.write_text(json.dumps({
        "generated_at": "2026-08-16T10:35:00+08:00",
        "recent_days": 7,
        "industries": [{
            "key": "semi", "name": "半导体", "accent": "#f59e0b", "total": 1,
            "items": [{
                "title": "旧资讯", "source_name": "公开源 1", "source_url": "https://feed.example.test/rss",
                "fetched_at": "2026-08-16T10:35:00+08:00", "ts": 1,
            }],
        }],
        "stats": {"industries": 1, "total_sources": 1, "failed_sources": 1},
        "cache_status": "partial",
        "source_statuses": [],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))
    monkeypatch.setattr(
        newsradar.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(_rss("重试后的新资讯", "https://news.example.test/retry")),
    )

    result = newsradar.retry_source(newsradar.source_id({"url": "https://feed.example.test/rss", "hint": "semi", "name": "公开源 1"}))

    assert result["ok"] is True
    stored = json.loads(cache.read_text(encoding="utf-8"))
    assert [item["title"] for item in stored["industries"][0]["items"]] == ["重试后的新资讯"]
    assert stored["source_statuses"][0]["status"] == "ok"
    assert stored["source_state"] == "all_success"


def test_successful_retry_does_not_refresh_untouched_stale_cache(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    urls = ["https://retry.example.test/rss", "https://untouched.example.test/rss"]
    _write_sources(sources, urls)
    original_generated_at = "2026-07-01T10:35:00+08:00"
    cache.write_text(json.dumps({
        "generated_at": original_generated_at,
        "recent_days": 7,
        "industries": [{
            "key": "semi", "name": "半导体", "accent": "#f59e0b", "total": 2,
            "items": [
                {"title": "待重试旧资讯", "source_name": "公开源 1", "source_url": urls[0], "ts": 2},
                {"title": "未重试旧资讯", "source_name": "公开源 2", "source_url": urls[1], "ts": 1},
            ],
        }],
        "stats": {"industries": 1, "total_sources": 2, "failed_sources": 1},
        "source_statuses": [],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))
    monkeypatch.setattr(
        newsradar.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(_rss("重试后的新资讯", "https://news.example.test/retry")),
    )

    newsradar.retry_source(newsradar.source_id({"url": urls[0], "hint": "semi", "name": "公开源 1"}))
    stored = json.loads(cache.read_text(encoding="utf-8"))
    loaded = newsradar.load_cache()

    assert stored["generated_at"] == original_generated_at
    assert loaded["cache_status"] == "stale"
    untouched = next(item for item in loaded["industries"][0]["items"] if item["title"] == "未重试旧资讯")
    assert untouched["data_status"] == "stale"


def test_retry_without_cache_does_not_invent_success_for_unfetched_sources(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    urls = ["https://retry.example.test/rss", "https://not-retried.example.test/rss"]
    _write_sources(sources, urls)
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))
    monkeypatch.setattr(
        newsradar.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(_rss("单源恢复资讯", "https://news.example.test/retry")),
    )

    result = newsradar.retry_source(newsradar.source_id({
        "url": urls[0], "hint": "semi", "name": "公开源 1",
    }))

    assert result["ok"] is True
    stored = json.loads(cache.read_text(encoding="utf-8"))
    assert stored["stats"]["failed_sources"] == 1
    assert stored["source_state"] == "partial_failure"
    statuses = {row["source_url"]: row for row in stored["source_statuses"]}
    assert statuses[urls[0]]["status"] == "ok"
    assert statuses[urls[1]]["status"] == "failed"
    assert statuses[urls[1]]["error_type"] == "unknown"
    assert statuses[urls[1]]["error_reason"] == "暂无成功缓存，本次单源重试未抓取该来源"


def test_retry_source_rejects_unknown_id_and_failed_retry_preserves_cache_bytes(tmp_path, monkeypatch):
    sources = tmp_path / "sources.json"
    cache = tmp_path / "radar.json"
    _write_sources(sources, ["https://feed.example.test/rss"])
    cache.write_text(json.dumps({
        "generated_at": "2026-08-16T10:35:00+08:00", "recent_days": 7,
        "industries": [{"key": "semi", "name": "半导体", "accent": "#f59e0b", "total": 1, "items": []}],
        "stats": {"industries": 1, "total_sources": 1, "failed_sources": 1},
        "cache_status": "partial", "source_statuses": [],
    }), encoding="utf-8")
    original = cache.read_bytes()
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(sources))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache))
    monkeypatch.setattr(
        newsradar.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(TimeoutError("token=private")),
    )

    with pytest.raises(ValueError, match="未配置"):
        newsradar.retry_source("0" * 16)
    result = newsradar.retry_source(newsradar.source_id({"url": "https://feed.example.test/rss", "hint": "semi", "name": "公开源 1"}))

    assert result["ok"] is False
    assert result["source_status"]["error_type"] == "timeout"
    assert result["source_status"]["last_success_at"] is None
    assert "private" not in json.dumps(result, ensure_ascii=False)
    assert cache.read_bytes() == original
