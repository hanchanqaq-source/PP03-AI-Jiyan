from __future__ import annotations

from datetime import datetime, timezone

from news_intelligence.service import MarketNewsService


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def radar():
    return {
        "generated_at": NOW.isoformat(),
        "cache_status": "cache",
        "source_statuses": [],
        "stats": {"total_sources": 1, "failed_sources": 0},
        "industries": [{"key": "semi", "name": "半导体", "items": [{
            "source_name": "公开来源",
            "source_url": "https://publisher.example/feed",
            "original_url": "https://publisher.example/article",
            "published_at": NOW.isoformat(),
            "fetched_at": NOW.isoformat(),
            "title": "半导体公司公告建设项目",
            "summary_or_excerpt": "公开摘要",
            "language": "zh-CN",
            "region": "CN",
            "data_status": "cache",
        }]}],
    }


def test_market_news_applies_evidence_admission_before_relationships_and_exposes_snapshot():
    calls = []

    def admit(events):
        calls.append([row.event_id for row in events])
        events[0].verification_status = "verified"
        events[0].verification_reason = "官方证据支持"
        events[0].verified_at = NOW.isoformat()
        events[0].verified_key_fields = []
        return "e" * 20, events

    service = MarketNewsService(
        radar_loader=radar,
        portfolio_loader=lambda: {"overview": {"fund_count": 0}, "holdings": []},
        evidence_admitter=admit,
        evidence_version=lambda: "e" * 20,
        now=lambda: NOW,
    )

    data = service.get_events(mode="my_focus", tag_ids=["semiconductor"], days=7)

    assert len(calls) == 1
    assert data["evidence_snapshot_id"] == "e" * 20
    assert [row["verification_status"] for row in data["events"]] == ["verified"]


def test_market_news_never_falls_back_when_evidence_admits_no_event():
    service = MarketNewsService(
        radar_loader=radar,
        portfolio_loader=lambda: {"overview": {"fund_count": 0}, "holdings": []},
        evidence_admitter=lambda _events: ("e" * 20, []),
        evidence_version=lambda: "e" * 20,
        now=lambda: NOW,
    )

    data = service.get_events(mode="my_focus", tag_ids=["semiconductor"], days=7)

    assert data["events"] == []
    assert data["empty_reason"] == "no_events"
    assert data["empty_message"] == "当前筛选暂无完成核验的资讯，可前往证据中心查看待核验内容。"
