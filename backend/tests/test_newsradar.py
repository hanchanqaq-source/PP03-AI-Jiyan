from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import newsradar


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
    assert data["source_statuses"] == [{
        "source_name": "公开源 1",
        "source_url": "https://feed.example.test/rss",
        "status": "ok",
        "item_count": 1,
    }]


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
