from __future__ import annotations

from datetime import datetime, timezone

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
            ]},
            {"key": "ai", "name": "AI / 大模型", "items": [
                _source("global", "Global AI chip demand rises", "https://global.example.test/ai", "2026-08-17T08:30:00+08:00", region="US", language="en"),
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
                    "holdings": [{"stock_code": "002371", "stock_name": "北方华创", "weight_pct": 9.8}],
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
                    }],
                }},
            },
        }],
    }


def _service(*, portfolio_loader=None, radar_refresher=None) -> MarketNewsService:
    return MarketNewsService(
        radar_loader=_radar,
        radar_refresher=radar_refresher or _radar,
        portfolio_loader=portfolio_loader or (lambda: _portfolio()),
        now=lambda: NOW,
    )


def test_api_supports_four_modes_and_keeps_filters(monkeypatch):
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service())

    focus = client.get("/api/market-news/events?mode=my_focus&tag_id=storage&category=all&days=7&sort=importance")
    holdings = client.get("/api/market-news/events?mode=my_holdings&category=all&days=7&sort=holding_relevance")
    global_tech = client.get("/api/market-news/events?mode=global_tech&category=all&days=7&sort=latest")
    policy = client.get("/api/market-news/events?mode=domestic_policy&category=policy&days=7&sort=importance")

    assert focus.status_code == holdings.status_code == global_tech.status_code == policy.status_code == 200
    assert [event["title"] for event in focus.json()["data"]["events"]] == ["DRAM 产品报价出现改善"]
    assert {event["relation_level"] for event in holdings.json()["data"]["events"]} == {"direct_holding", "industry_relation"}
    assert "Global AI chip demand rises" in [event["title"] for event in global_tech.json()["data"]["events"]]
    assert [event["title"] for event in policy.json()["data"]["events"]] == ["半导体设备产业政策发布"]
    assert focus.json()["data"]["filters"] == {
        "mode": "my_focus", "tag_ids": ["storage"], "category": "all", "days": 7, "sort": "importance",
    }


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
    assert len(response.json()["data"]["events"]) == 4
    assert response.json()["data"]["portfolio_status"] == "error"
    assert response.json()["data"]["impact_summary"] is None
    assert response.json()["data"]["ai_status"] == "unavailable"
    assert all(event["impact_tendency"] == "unclear" for event in response.json()["data"]["events"])

    holdings = client.get("/api/market-news/events?mode=my_holdings")
    assert holdings.json()["data"]["events"] == []
    assert holdings.json()["data"]["empty_reason"] == "portfolio_error"


def test_refresh_failure_keeps_cached_events(monkeypatch):
    def fail_refresh():
        raise OSError("all sources offline")

    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service(radar_refresher=fail_refresh))

    response = client.post("/api/market-news/refresh?mode=global_tech&days=7&sort=importance")

    assert response.status_code == 200
    assert len(response.json()["data"]["events"]) == 4
    assert response.json()["data"]["source_summary"]["refresh_failed"] is True
    assert response.json()["data"]["data_status"] == "cache"


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


def test_unknown_publication_time_is_excluded_from_time_window_and_today_focus(monkeypatch):
    radar = _radar()
    radar["industries"][0]["items"].append({
        **_source("cn", "发布时间未知的政策资讯", "https://cn.example.test/unknown", "", region="CN"),
        "published_at": None,
    })
    service = MarketNewsService(
        radar_loader=lambda: radar,
        radar_refresher=lambda: radar,
        portfolio_loader=_portfolio,
        now=lambda: NOW,
    )
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: service)

    data = client.get("/api/market-news/events?mode=domestic_policy&days=30").json()["data"]

    assert "发布时间未知的政策资讯" not in [event["title"] for event in data["events"]]
    assert "发布时间未知的政策资讯" not in [event["title"] for event in data["today_focus"]]


def test_invalid_market_news_filters_return_422(monkeypatch):
    monkeypatch.setattr(app_module.market_news_service, "get_service", lambda: _service())

    for query in ["mode=unknown", "category=rumor", "days=2", "sort=ai_opinion"]:
        assert client.get(f"/api/market-news/events?{query}").status_code == 422
