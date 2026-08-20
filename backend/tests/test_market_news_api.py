from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app as app_module
from news_intelligence.service import MarketNewsService


client = TestClient(app_module.app)
NOW = datetime(2026, 8, 17, 4, tzinfo=timezone.utc)


def _source(name: str, title: str, url: str, published: str, *, region: str, language: str = "zh-CN") -> dict:
    return {
        "source_name": name,
        "source_url": f"https://{name}.example.test/rss",
        "original_url": url,
        "published_at": published,
        "fetched_at": "2026-08-17T12:00:00+08:00",
        "title": title,
        "summary_or_excerpt": "公开来源摘要",
        "language": language,
        "region": region,
        "data_status": "cache",
    }


def _radar() -> dict:
    return {
        "generated_at": "2026-08-17T12:00:00+08:00",
        "cache_status": "cache",
        "source_statuses": [
            {"source_name": "cn", "source_url": "https://cn.example.test/rss", "status": "ok", "item_count": 3},
            {"source_name": "global", "source_url": "https://global.example.test/rss", "status": "ok", "item_count": 1},
        ],
        "stats": {"industries": 2, "total_sources": 2, "failed_sources": 0},
        "industries": [
            {"key": "semi", "name": "半导体 / 芯片", "items": [
                _source("cn", "北方华创发布公开公告", "https://cn.example.test/direct", "2026-08-17T11:30:00+08:00", region="CN"),
                _source("cn", "半导体设备产业政策发布", "https://cn.example.test/policy", "2026-08-17T10:30:00+08:00", region="CN"),
                _source("cn", "DRAM 产品报价出现改善", "https://cn.example.test/storage", "2026-08-17T09:30:00+08:00", region="CN"),
                _source("cn", "存储产业支持政策发布", "https://cn.example.test/storage-policy", "2026-08-17T09:00:00+08:00", region="CN"),
            ]},
            {"key": "ai", "name": "AI / 大模型", "items": [
                _source("global", "Global AI chip demand rises", "https://global.example.test/ai", "2026-08-17T08:30:00+08:00", region="US", language="en"),
            ]},
            {"key": "tech", "name": "Global technology", "items": [
                _source("global", "Global HBM storage demand rises", "https://global.example.test/storage", "2026-08-17T08:00:00+08:00", region="US", language="en"),
                _source("unknown", "Memory prices rise amid supply constraints", "https://unknown.example.test/storage", "2026-08-17T07:45:00+08:00", region="unknown", language="en"),
            ]},
            {"key": "robot", "name": "机器人", "items": [
                _source("cn", "机器人公司发布量产公告", "https://cn.example.test/robot", "2026-08-17T07:30:00+08:00", region="CN"),
            ]},
        ],
    }


def _portfolio() -> dict:
    return {
        "overview": {"fund_count": 1, "updated_at": "2026-08-17T11:00:00+08:00"},
        "holdings": [{
            "code": "017811", "name": "东方人工智能主题混合C",
            "analysis": {
                "holdings": {"data": {
                    "disclosure_date": "2026-06-30",
                    "holdings": [
                        {"stock_code": "002371", "stock_name": "北方华创", "weight_pct": 9.8},
                        {"stock_code": "300999", "stock_name": "机器人公司", "weight_pct": 4.2},
                    ],
                }},
                "industry_exposure": {"data": {
                    "holding_industry_evidence": [{
                        "stock_code": "002371", "stock_name": "北方华创", "weight_pct": 9.8,
                        "primary_industry": "电子", "secondary_industry": "半导体",
                        "detail_industry": "半导体设备", "fine_industry": "半导体设备",
                        "classification_standard": "申银万国行业分类标准",
                        "source_name": "巨潮资讯上市公司行业归属",
                        "source_reference": "https://webapi.cninfo.com.cn/api/stock/p_stock2110",
                        "holding_disclosure_date": "2026-06-30",
                    }, {
                        "stock_code": "300999", "stock_name": "机器人公司", "weight_pct": 4.2,
                        "primary_industry": "机械设备", "secondary_industry": "自动化设备",
                        "detail_industry": "机器人", "fine_industry": "机器人",
                        "classification_standard": "申银万国行业分类标准",
                        "source_name": "巨潮资讯上市公司行业归属",
                        "source_reference": "https://webapi.cninfo.com.cn/api/stock/p_stock2110",
                        "holding_disclosure_date": "2026-06-30",
                    }],
                }},
            },
        }],
    }


def _admit_all(events):
    for event in events:
        event.verification_status = "verified"
        event.verification_reason = "测试中的既有市场资讯行为"
        event.verified_at = NOW.isoformat()
        event.verified_key_fields = []
    return "e" * 20, events


def _service(*, portfolio_loader=None, radar_refresher=None) -> MarketNewsService:
    return MarketNewsService(
        radar_loader=_radar,
        radar_refresher=radar_refresher or _radar,
        portfolio_loader=portfolio_loader or (lambda: _portfolio()),
        evidence_admitter=_admit_all,
        evidence_version=lambda: "e" * 20,
        now=lambda: NOW,
    )


def test_api_supports_four_modes_and_keeps_filters(monkeypatch):
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service())

    focus = client.get("/api/market-news/events?mode=my_focus&tag_id=storage&category=all&days=7&sort=importance")
    holdings = client.get("/api/market-news/events?mode=my_holdings&category=all&days=7&sort=holding_relevance")
    global_tech = client.get("/api/market-news/events?mode=global_tech&category=all&days=7&sort=latest")
    policy = client.get("/api/market-news/events?mode=domestic_policy&tag_id=semiconductor&category=policy&days=7&sort=importance")

    assert focus.status_code == holdings.status_code == global_tech.status_code == policy.status_code == 200
    assert [event["title"] for event in focus.json()["data"]["events"]] == [
        "存储产业支持政策发布",
        "DRAM 产品报价出现改善",
        "Global HBM storage demand rises",
        "Memory prices rise amid supply constraints",
    ]
    assert {event["relation_level"] for event in holdings.json()["data"]["events"]} == {"direct_holding", "industry_relation"}
    assert "Global AI chip demand rises" in [event["title"] for event in global_tech.json()["data"]["events"]]
    assert [event["title"] for event in policy.json()["data"]["events"]] == ["半导体设备产业政策发布"]
    assert focus.json()["data"]["filters"] == {
        "mode": "my_focus", "tag_ids": ["storage"], "category": "all", "days": 7, "sort": "importance",
    }


def test_all_modes_apply_selected_article_text_tags_after_mode_filter(monkeypatch):
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service())

    policy_semiconductor = client.get(
        "/api/market-news/events?mode=domestic_policy&tag_id=semiconductor&category=all&days=7&sort=importance"
    ).json()["data"]
    policy_storage = client.get(
        "/api/market-news/events?mode=domestic_policy&tag_id=storage&category=all&days=7&sort=importance"
    ).json()["data"]
    holdings_robotics = client.get(
        "/api/market-news/events?mode=my_holdings&tag_id=robotics&category=all&days=7&sort=importance"
    ).json()["data"]
    global_storage = client.get(
        "/api/market-news/events?mode=global_tech&tag_id=storage&category=all&days=7&sort=importance"
    ).json()["data"]

    assert [event["title"] for event in policy_semiconductor["events"]] == ["半导体设备产业政策发布"]
    assert [event["title"] for event in policy_storage["events"]] == ["存储产业支持政策发布"]
    assert [event["title"] for event in holdings_robotics["events"]] == ["机器人公司发布量产公告"]
    assert [event["title"] for event in global_storage["events"]] == ["Global HBM storage demand rises"]
    assert all(
        any(tag["id"] == selected and tag["provenance"] == "article_text" for tag in event["tag_evidence"])
        for data, selected in (
            (policy_semiconductor, "semiconductor"),
            (policy_storage, "storage"),
            (holdings_robotics, "robotics"),
            (global_storage, "storage"),
        )
        for event in data["events"]
    )


def test_focus_impact_and_detail_snapshot_use_only_current_filtered_range():
    service = _service()
    all_holdings = service.get_events(mode="my_holdings", tag_ids=[])
    excluded = next(event for event in all_holdings["events"] if event["title"] == "北方华创发布公开公告")

    robotics = service.get_events(
        mode="my_holdings", tag_ids=["robotics"], category="all", days=7, sort="importance",
    )
    robotics_latest = service.get_events(
        mode="my_holdings", tag_ids=["robotics"], category="all", days=7, sort="latest",
    )

    assert [event["title"] for event in robotics["events"]] == ["机器人公司发布量产公告"]
    assert [event["event_id"] for event in robotics["focus_events"]] == [
        event["event_id"] for event in robotics["events"]
    ]
    assert robotics["impact_summary"] == {
        "holding_related_count": 1,
        "direct_count": 1,
        "industry_count": 0,
        "watch_count": 0,
        "funds": [{"fund_code": "017811", "fund_name": "东方人工智能主题混合C", "event_count": 1}],
    }
    assert service.get_event(excluded["event_id"], robotics["snapshot_id"]) is None
    assert robotics["snapshot_id"] != robotics_latest["snapshot_id"]


def test_snapshot_identity_changes_when_time_filter_ages_events_out():
    clock = [NOW]
    service = MarketNewsService(
        radar_loader=_radar,
        radar_refresher=_radar,
        portfolio_loader=_portfolio,
        evidence_admitter=_admit_all,
        evidence_version=lambda: "e" * 20,
        now=lambda: clock[0],
    )

    first = service.get_events(mode="global_tech", tag_ids=["storage"], days=7)
    first_event_id = first["events"][0]["event_id"]
    clock[0] = NOW + timedelta(days=8)
    second = service.get_events(mode="global_tech", tag_ids=["storage"], days=7)

    assert second["events"] == []
    assert first["snapshot_id"] != second["snapshot_id"]
    assert service.get_event(first_event_id, first["snapshot_id"]) is not None


def test_feed_track_only_tag_cannot_pass_current_article_tag_filter(monkeypatch):
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service())

    data = client.get(
        "/api/market-news/events?mode=my_holdings&tag_id=semiconductor&category=all&days=7&sort=importance"
    ).json()["data"]

    assert "北方华创发布公开公告" not in [event["title"] for event in data["events"]]
    assert all(
        any(tag == {"id": "semiconductor", "name": "半导体", "provenance": "article_text"} for tag in event["tag_evidence"])
        for event in data["events"]
    )


def test_api_returns_explicit_no_tags_and_no_holdings_states(monkeypatch):
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service())
    no_tags = client.get("/api/market-news/events?mode=my_focus")

    empty_service = _service(portfolio_loader=lambda: {"overview": {"fund_count": 0}, "holdings": []})
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: empty_service)
    no_holdings = client.get("/api/market-news/events?mode=my_holdings")

    assert no_tags.json()["data"]["events"] == []
    assert no_tags.json()["data"]["empty_reason"] == "no_tags"
    assert no_holdings.json()["data"]["events"] == []
    assert no_holdings.json()["data"]["empty_reason"] == "no_holdings"
    assert no_holdings.json()["data"]["portfolio_status"] == "empty"


def test_portfolio_failure_is_partial_success_and_ai_is_optional(monkeypatch):
    def fail_portfolio():
        raise RuntimeError("fund provider unavailable")

    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service(portfolio_loader=fail_portfolio))

    response = client.get("/api/market-news/events?mode=global_tech")

    assert response.status_code == 200
    assert len(response.json()["data"]["events"]) == 2
    assert response.json()["data"]["portfolio_status"] == "error"
    assert response.json()["data"]["impact_summary"] is None
    assert response.json()["data"]["ai_status"] == "unavailable"
    assert all(event["impact_tendency"] == "unclear" for event in response.json()["data"]["events"])

    holdings = client.get("/api/market-news/events?mode=my_holdings")
    assert holdings.json()["data"]["events"] == []
    assert holdings.json()["data"]["empty_reason"] == "portfolio_error"


def test_async_refresh_kickoff_keeps_cached_events_visible_until_pipeline_publishes(monkeypatch):
    calls = 0

    def fail_refresh():
        nonlocal calls
        calls += 1
        raise OSError("all sources offline")

    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service(radar_refresher=fail_refresh))
    monkeypatch.setattr(
        "news_pipeline.api.get_service",
        lambda: SimpleNamespace(start=lambda: SimpleNamespace(
            run_id="run-compat", raw_snapshot_id="raw-compat", phase=SimpleNamespace(value="queued"),
        )),
    )

    response = client.post(
        "/api/market-news/refresh?mode=global_tech&days=7&sort=importance",
        headers={"X-PP03-Write-Intent": "1", "Origin": "http://127.0.0.1:5899", "Host": "127.0.0.1:8900"},
    )
    cached = client.get("/api/market-news/events?mode=global_tech&days=7&sort=importance")

    assert response.status_code == 202
    assert response.json()["data"] == {
        "run_id": "run-compat", "raw_snapshot_id": "raw-compat", "phase": "queued",
    }
    assert len(cached.json()["data"]["events"]) == 2
    assert cached.json()["data"]["source_summary"]["refresh_failed"] is False
    assert cached.json()["data"]["data_status"] == "cache"
    assert calls == 0


def test_event_detail_and_missing_event(monkeypatch):
    service = _service()
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: service)
    listing = client.get("/api/market-news/events?mode=global_tech").json()["data"]
    event_id = listing["events"][0]["event_id"]

    found = client.get(f"/api/market-news/events/{event_id}")
    missing = client.get("/api/market-news/events/00000000000000000000")

    assert found.status_code == 200
    assert found.json()["data"]["event_id"] == event_id
    assert found.json()["data"]["sources"]
    assert missing.status_code == 404


def test_detail_snapshots_remain_query_safe_across_different_selected_tags(monkeypatch):
    service = _service()
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: service)

    first = client.get("/api/market-news/events?mode=my_focus&tag_id=storage").json()["data"]
    watched = next(event for event in first["events"] if event["title"] == "DRAM 产品报价出现改善")
    client.get("/api/market-news/events?mode=global_tech&tag_id=robotics")

    detail = client.get(
        f"/api/market-news/events/{watched['event_id']}?snapshot_id={first['snapshot_id']}"
    )

    assert detail.status_code == 200
    assert detail.json()["data"]["relation_level"] == "watch_tag"


def test_base_snapshot_key_ignores_runtime_portfolio_timestamps(monkeypatch):
    import news_intelligence.service as service_module

    calls = 0
    original_normalize = service_module.normalize_radar

    def counting_normalize(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_normalize(*args, **kwargs)

    timestamps = iter(("2026-08-17T11:00:01+08:00", "2026-08-17T11:00:02+08:00"))

    def changing_portfolio():
        portfolio = _portfolio()
        portfolio["overview"]["updated_at"] = next(timestamps)
        portfolio["holdings"][0]["analysis"]["holdings"].setdefault("meta", {})["fetched_at"] = portfolio["overview"]["updated_at"]
        return portfolio

    monkeypatch.setattr(service_module, "normalize_radar", counting_normalize)
    service = _service(portfolio_loader=changing_portfolio)

    service.get_events(mode="global_tech", tag_ids=["storage"])
    service.get_events(mode="global_tech", tag_ids=["robotics"])

    assert calls == 1
    assert len(service._base_snapshots) == 1


def test_unknown_publication_time_is_excluded_from_time_window_and_filter_focus(monkeypatch):
    radar = _radar()
    radar["industries"][0]["items"].append({
        **_source("cn", "发布时间未知的政策资讯", "https://cn.example.test/unknown", "", region="CN"),
        "published_at": None,
    })
    service = MarketNewsService(
        radar_loader=lambda: radar,
        radar_refresher=lambda: radar,
        portfolio_loader=_portfolio,
        evidence_admitter=_admit_all,
        evidence_version=lambda: "e" * 20,
        now=lambda: NOW,
    )
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: service)

    data = client.get("/api/market-news/events?mode=domestic_policy&days=30").json()["data"]

    assert "发布时间未知的政策资讯" not in [event["title"] for event in data["events"]]
    assert "发布时间未知的政策资讯" not in [event["title"] for event in data["focus_events"]]


def test_invalid_market_news_filters_return_422(monkeypatch):
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service())

    for query in ["mode=unknown", "category=rumor", "days=2", "sort=ai_opinion"]:
        assert client.get(f"/api/market-news/events?{query}").status_code == 422


def test_market_news_detail_normalizes_pipeline_runtime_failure(monkeypatch):
    class BrokenDetail:
        def get_event(self, event_id, snapshot_id=None):
            raise OSError("C:\\Users\\private\\trusted.json?token=secret")

    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: BrokenDetail())

    response = client.get(f"/api/market-news/events/{'a' * 20}")

    assert response.status_code == 503
    assert response.json() == {"detail": "可信资讯暂时不可用"}
    assert "private" not in response.text
    assert "secret" not in response.text


def test_market_news_translation_endpoint_passes_ephemeral_model_config(monkeypatch):
    captured = {}

    class TranslationService:
        def translate_batch(self, items, llm):
            captured["items"] = items
            captured["llm"] = llm
            return {"translations": [{
                "event_id": items[0]["event_id"],
                "translated_title_zh": "美光发布 HBM3E",
                "translated_summary_zh": "本季度开始出货。",
                "translation_status": "translated",
                "translation_provider": llm["provider"],
                "translated_at": "2026-08-17T04:00:00+00:00",
            }], "limit": 20}

    monkeypatch.setattr(app_module.news_translation, "get_service", lambda: TranslationService())
    payload = {
        "items": [{
            "event_id": "a" * 20,
            "title": "Micron launches HBM3E",
            "summary": "Shipments begin this quarter.",
            "source_language": "en",
        }],
        "llm": {
            "provider": "openai",
            "baseURL": "https://model.example.test/v1",
            "apiKey": "request-only-secret",
            "model": "test-model",
        },
    }

    response = client.post("/api/market-news/translations", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["translations"][0]["translated_title_zh"] == "美光发布 HBM3E"
    assert captured == {"items": payload["items"], "llm": payload["llm"]}
    assert "request-only-secret" not in response.text


def test_market_news_translation_endpoint_allows_missing_model_and_rejects_more_than_twenty(monkeypatch):
    class TranslationService:
        def translate_batch(self, items, llm):
            assert llm is None
            return {"translations": [], "limit": 20}

    monkeypatch.setattr(app_module.news_translation, "get_service", lambda: TranslationService())

    missing = client.post("/api/market-news/translations", json={"items": []})
    too_many = client.post("/api/market-news/translations", json={
        "items": [{
            "event_id": f"{index:020x}",
            "title": "English title",
            "summary": "Summary",
            "source_language": "en",
        } for index in range(21)],
    })

    assert missing.status_code == 200
    assert too_many.status_code == 422


def test_single_source_retry_returns_one_complete_current_query_snapshot(monkeypatch):
    service = _service()
    called = []
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: service)
    monkeypatch.setattr(
        app_module.newsradar,
        "retry_source",
        lambda source_id: called.append(source_id) or {"ok": True, "source_status": {"source_id": source_id}},
    )

    response = client.post(
        "/api/market-news/sources/0123456789abcdef/retry"
        "?mode=global_tech&tag_id=storage&category=all&days=7&sort=latest"
    )

    assert response.status_code == 200
    assert called == ["0123456789abcdef"]
    data = response.json()["data"]
    assert data["filters"] == {
        "mode": "global_tech", "tag_ids": ["storage"], "category": "all", "days": 7, "sort": "latest",
    }
    assert [event["event_id"] for event in data["focus_events"]] == [event["event_id"] for event in data["events"]]
    assert data["source_summary"]["source_state"] == "cached"


def test_single_source_retry_failure_is_inspectable_and_unknown_source_is_rejected(monkeypatch):
    failure = {
        "source_id": "0123456789abcdef",
        "source_name": "公开测试源",
        "source_url": "https://feed.example.test/rss",
        "status": "failed",
        "error_type": "timeout",
        "error_reason": "来源请求超时",
        "last_success_at": "2026-08-16T10:35:00+08:00",
        "used_cached_items": True,
        "item_count": 1,
    }

    def retry(source_id):
        if source_id == "f" * 16:
            raise ValueError("该资讯来源未配置，不能重试")
        return {"ok": False, "source_status": failure}

    monkeypatch.setattr(app_module.newsradar, "retry_source", retry)

    failed = client.post("/api/market-news/sources/0123456789abcdef/retry")
    unknown = client.post(f"/api/market-news/sources/{'f' * 16}/retry")

    assert failed.status_code == 200
    assert failed.json()["data"] == {"retry_succeeded": False, "source_status": failure}
    assert unknown.status_code == 404


def test_cache_status_cleanup_and_startup_use_the_bounded_manager(monkeypatch):
    calls = []
    status = {
        "total_bytes": 2048,
        "file_count": 3,
        "expired_count": 1,
        "reclaimable_bytes": 1024,
        "categories": {"temporary": {"bytes": 1024, "file_count": 1, "expired_count": 1, "reclaimable_bytes": 1024, "pinned_count": 0}},
        "last_auto_cleanup_at": "2026-08-17T04:00:00+00:00",
        "limit_bytes": 524288000,
        "over_limit_bytes": 0,
    }

    class Manager:
        def status(self):
            calls.append("status")
            return status

        def cleanup_expired(self, manual=False):
            calls.append(("cleanup", manual))
            return {"manual": manual, "released_bytes": 1024, "deleted_categories": ["temporary"], "status": status}

        def maybe_auto_cleanup(self):
            calls.append("startup")
            return {"ran": True, "released_bytes": 0, "status": status}

    monkeypatch.setattr(app_module.cache_management, "get_manager", lambda: Manager())

    cache_status = client.get("/api/cache/status")
    cleanup = client.post("/api/cache/cleanup-expired")
    app_module._run_startup_cache_cleanup()

    assert cache_status.status_code == 200 and cache_status.json()["data"] == status
    assert cleanup.status_code == 200 and cleanup.json()["data"]["released_bytes"] == 1024
    assert calls == ["status", ("cleanup", True), "startup"]
    assert "C:\\" not in cache_status.text
