from __future__ import annotations

from datetime import datetime, timezone

from news_intelligence.clustering import cluster_items
from news_intelligence.normalizer import normalize_radar


NOW = datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc)


def _radar(*items: dict) -> dict:
    return {
        "generated_at": "2026-08-17T12:00:00+08:00",
        "cache_status": "realtime",
        "industries": [{
            "key": "semi",
            "name": "半导体 / 芯片",
            "items": list(items),
        }],
        "stats": {"failed_sources": 0},
    }


def _item(title: str, url: str, published_at: str, source: str, summary: str = "") -> dict:
    source_domain = url.split("/", 3)[2]
    return {
        "source_name": source,
        "source_url": f"https://{source_domain}/rss",
        "original_url": url,
        "published_at": published_at,
        "fetched_at": "2026-08-17T12:00:00+08:00",
        "title": title,
        "summary_or_excerpt": summary,
        "language": "zh-CN",
        "region": "CN",
        "data_status": "realtime",
    }


def test_normalize_radar_builds_auditable_source_without_ai_facts():
    sources = normalize_radar(_radar(_item(
        "北方华创发布公开公告",
        "https://news.example.test/a",
        "2026-08-17T10:35:00+08:00",
        "公开媒体",
        "公告原始摘要",
    )), now=NOW)

    assert len(sources) == 1
    source = sources[0]
    assert source.title == "北方华创发布公开公告"
    assert source.summary == "公告原始摘要"
    assert source.category == "company"
    assert source.track_key == "semi"
    assert source.related_tags == (("semiconductor", "半导体"),)
    assert source.source_domain == "news.example.test"
    assert source.normalized_title == "北方华创发布公开公告"
    assert "北方华创" in source.anchors
    assert source.published_at.isoformat() == "2026-08-17T10:35:00+08:00"


def test_normalize_radar_classifies_policy_and_specific_storage_tag():
    sources = normalize_radar(_radar(_item(
        "监管部门发布 DRAM 产业规范",
        "https://policy.example.test/a",
        "2026-08-17T09:00:00+08:00",
        "政策公开源",
    )), now=NOW)

    assert sources[0].category == "policy"
    assert sources[0].related_tags == (("semiconductor", "半导体"), ("storage", "存储"))


def test_article_tags_cover_enabled_ai_healthcare_and_semiconductor_aliases():
    sources = normalize_radar(_radar(
        _item("芯片产业需求升温", "https://news.example.test/chip", "2026-08-17T09:00:00+08:00", "公开媒体"),
        _item("AI 医疗器械创新加速", "https://news.example.test/health-ai", "2026-08-17T09:05:00+08:00", "公开媒体"),
    ), now=NOW)

    chip_tags = set(sources[0].text_related_tags)
    health_ai_tags = set(sources[1].text_related_tags)
    assert ("semiconductor", "半导体") in chip_tags
    assert ("artificial-intelligence", "人工智能") in health_ai_tags
    assert ("healthcare", "医疗") in health_ai_tags
    assert ("medical-device", "医疗器械") in health_ai_tags


def test_robotics_article_tag_covers_chinese_and_english_automation_aliases():
    sources = normalize_radar(_radar(
        _item("工业自动化产线升级", "https://news.example.test/automation-cn", "2026-08-17T09:10:00+08:00", "公开媒体"),
        _item("Factory automation investment rises", "https://news.example.test/automation-en", "2026-08-17T09:15:00+08:00", "公开媒体"),
    ), now=NOW)

    assert all(("robotics", "机器人") in set(source.text_related_tags) for source in sources)


def test_normalize_radar_keeps_unknown_publication_time_explicit_and_out_of_recency():
    item = _item(
        "没有可靠发布时间的公开资讯",
        "https://news.example.test/unknown-date",
        None,
        "公开媒体",
    )
    item["ts"] = 0

    source = normalize_radar(_radar(item), now=NOW)[0]
    event = cluster_items([source])[0]

    assert source.published_at is None
    assert source.fetched_at.isoformat() == "2026-08-17T12:00:00+08:00"
    assert event.published_at_first is None
    assert event.published_at_latest is None
    assert event.to_dict()["published_at_latest"] is None


def test_container_cache_status_downgrades_historical_realtime_items():
    radar = _radar(_item(
        "缓存中的公开资讯",
        "https://news.example.test/cached",
        "2026-08-17T10:35:00+08:00",
        "公开媒体",
    ))
    radar["cache_status"] = "cache"

    assert normalize_radar(radar, now=NOW)[0].data_status == "cache"

    radar["cache_status"] = "stale"
    assert normalize_radar(radar, now=NOW)[0].data_status == "stale"


def test_fund_notice_requires_fund_context_instead_of_generic_market_share_wording():
    sources = normalize_radar(_radar(
        _item(
            "Temu靠性价比挑战亚马逊",
            "https://commerce.example.test/a",
            "2026-08-17T09:00:00+08:00",
            "商业公开源",
            "Temu在美国市场份额持续攀升，中小跨境电商企业如何应对？",
        ),
        _item(
            "东方人工智能主题混合型基金2026年第二季度报告",
            "https://fund.example.test/notice",
            "2026-08-17T08:00:00+08:00",
            "基金公告公开源",
        ),
    ), now=NOW)

    assert sources[0].category == "industry"
    assert sources[1].category == "fund_notice"


def test_cluster_merges_equivalent_titles_across_domains_and_keeps_all_sources():
    sources = normalize_radar(_radar(
        _item("北方华创发布2026年半年度报告", "https://one.example.test/a", "2026-08-17T09:00:00+08:00", "来源一", "原始摘要一"),
        _item("北方华创发布 2026 年半年度报告！", "https://two.example.test/b", "2026-08-17T10:00:00+08:00", "来源二", "原始摘要二"),
    ), now=NOW)

    events = cluster_items(sources)

    assert len(events) == 1
    event = events[0]
    assert event.source_count == 2
    assert [source.source_name for source in event.sources] == ["来源一", "来源二"]
    assert event.original_links == ["https://one.example.test/a", "https://two.example.test/b"]
    assert event.published_at_first.isoformat() == "2026-08-17T09:00:00+08:00"
    assert event.published_at_latest.isoformat() == "2026-08-17T10:00:00+08:00"


def test_cluster_counts_the_same_original_article_only_once_across_duplicate_feeds():
    shared_url = "https://publisher.example.test/article"
    sources = normalize_radar(_radar(
        _item("同一篇公开文章", shared_url, "2026-08-17T09:00:00+08:00", "A 媒体主源"),
        _item("同一篇公开文章", shared_url, "2026-08-17T09:00:00+08:00", "B 媒体专题源"),
    ), now=NOW)

    event = cluster_items(sources)[0]

    assert event.source_count == 1
    assert event.original_links == [shared_url]
    assert event.sources[0].source_name == "A 媒体主源"


def test_cluster_merges_shared_entity_and_event_within_48_hours():
    sources = normalize_radar(_radar(
        _item("北方华创发布半年度报告", "https://one.example.test/a", "2026-08-16T09:00:00+08:00", "来源一"),
        _item("北方华创半年报披露", "https://two.example.test/b", "2026-08-17T08:00:00+08:00", "来源二"),
    ), now=NOW)

    assert len(cluster_items(sources)) == 1


def test_cluster_keeps_generic_or_distant_reports_separate():
    sources = normalize_radar(_radar(
        _item("行业景气度出现改善", "https://one.example.test/a", "2026-08-17T09:00:00+08:00", "来源一"),
        _item("产业景气度继续改善", "https://two.example.test/b", "2026-08-17T10:00:00+08:00", "来源二"),
        _item("北方华创发布半年度报告", "https://three.example.test/c", "2026-08-12T09:00:00+08:00", "来源三"),
    ), now=NOW)

    assert len(cluster_items(sources)) == 3


def test_cluster_keeps_distinct_daily_digest_items_separate():
    boilerplate = "APOD Science APOD Today’s APOD Archive Submissions Index Search Calendar RSS Education About Discuss APOD Astronomy Picture of the Day"
    sources = normalize_radar(_radar(
        _item("APOD: 2026 August 15 – Bright Perseids from Sweden", "https://science.example.test/a", "2026-08-15T08:00:00+08:00", "来源一", boilerplate),
        _item("APOD: 2026 August 16 – Milky Way over Yellowstone", "https://science.example.test/b", "2026-08-16T08:00:00+08:00", "来源二", boilerplate),
    ), now=NOW)

    assert len(cluster_items(sources)) == 2


def test_event_ids_and_order_are_stable_across_input_order():
    raw = [
        _item("北方华创发布公开公告", "https://one.example.test/a", "2026-08-17T09:00:00+08:00", "来源一"),
        _item("存储产品报价改善", "https://two.example.test/b", "2026-08-17T10:00:00+08:00", "来源二"),
    ]

    first = cluster_items(normalize_radar(_radar(*raw), now=NOW))
    second = cluster_items(normalize_radar(_radar(*reversed(raw)), now=NOW))

    assert [event.event_id for event in first] == [event.event_id for event in second]
    assert [event.title for event in first] == [event.title for event in second]


def test_trusted_projector_uses_same_raw_identity_and_excludes_pending_events():
    from evidence_verification.models import EvidenceEvent, EvidenceSnapshot, VerificationStatus
    from news_intelligence.service import project_trusted_snapshot
    from news_pipeline.models import RawSnapshot

    raw_events = cluster_items(normalize_radar(_radar(
        _item("星河科技建设存储算力中心", "https://one.example.test/a", "2026-08-17T09:00:00+08:00", "来源一"),
        _item("另一公司拟建设数据中心", "https://two.example.test/b", "2026-08-17T10:00:00+08:00", "来源二"),
    ), now=NOW))
    raw = RawSnapshot("raw-project", NOW, tuple(event.to_dict() for event in raw_events))
    verified, pending = raw_events
    evidence = EvidenceSnapshot(
        snapshot_id="evidence-project",
        raw_snapshot_id=raw.raw_snapshot_id,
        generated_at=NOW,
        events=(
            EvidenceEvent(
                event_id=verified.event_id,
                title="确定性证据确认的可信标题",
                summary="确定性证据确认的可信摘要。",
                category=verified.category,
                related_tags=tuple((tag["id"], tag["name"]) for tag in verified.related_tags),
                published_at=verified.published_at_latest,
                core_claim="可信核心主张",
                verification_status=VerificationStatus.VERIFIED,
                verification_reason="官方证据支持",
                verified_at=NOW,
                evidence_as_of=NOW,
            ),
            EvidenceEvent(
                event_id=pending.event_id,
                title=pending.title,
                summary=pending.summary,
                category=pending.category,
                related_tags=tuple((tag["id"], tag["name"]) for tag in pending.related_tags),
                published_at=pending.published_at_latest,
                core_claim=pending.title,
                verification_status=VerificationStatus.UNVERIFIED,
                verification_reason="证据不足",
                verified_at=NOW,
                evidence_as_of=NOW,
            ),
        ),
    )

    trusted = project_trusted_snapshot(evidence, raw, now=lambda: NOW)

    assert trusted.raw_snapshot_id == raw.raw_snapshot_id
    assert [row["event_id"] for row in trusted.events] == [verified.event_id]
    assert trusted.events[0]["title"] == "确定性证据确认的可信标题"
    assert trusted.events[0]["summary"] == "确定性证据确认的可信摘要。"
    assert trusted.events[0]["verification_status"] == "verified"


def test_market_news_can_read_current_trusted_without_reloading_radar():
    from evidence_verification.models import EvidenceEvent, EvidenceSnapshot, VerificationStatus
    from news_intelligence.service import MarketNewsService, project_trusted_snapshot
    from news_pipeline.models import RawSnapshot

    event = cluster_items(normalize_radar(_radar(
        _item("存储产品报价改善", "https://one.example.test/a", "2026-08-17T09:00:00+08:00", "来源一"),
    ), now=NOW))[0]
    raw = RawSnapshot("raw-current", NOW, (event.to_dict(),))
    evidence = EvidenceSnapshot(
        snapshot_id="evidence-current",
        raw_snapshot_id=raw.raw_snapshot_id,
        generated_at=NOW,
        events=(EvidenceEvent(
            event_id=event.event_id,
            title=event.title,
            summary=event.summary,
            category=event.category,
            related_tags=tuple((tag["id"], tag["name"]) for tag in event.related_tags),
            published_at=event.published_at_latest,
            core_claim=event.title,
            verification_status=VerificationStatus.VERIFIED,
            verification_reason="官方证据支持",
            verified_at=NOW,
            evidence_as_of=NOW,
        ),),
    )
    trusted = project_trusted_snapshot(evidence, raw, now=lambda: NOW)
    service = MarketNewsService(
        radar_loader=lambda: (_ for _ in ()).throw(AssertionError("trusted read reloaded radar")),
        portfolio_loader=lambda: {"overview": {"fund_count": 0}, "holdings": []},
        evidence_admitter=lambda _events: (_ for _ in ()).throw(AssertionError("trusted read re-admitted evidence")),
        evidence_version=lambda: "unavailable",
        trusted_loader=lambda: trusted,
        now=lambda: NOW,
    )

    payload = service.get_events(mode="my_focus", tag_ids=["storage"])

    assert [row["event_id"] for row in payload["events"]] == [event.event_id]
    assert payload["evidence_snapshot_id"] == trusted.raw_snapshot_id
    assert payload["source_summary"]["refresh_failed"] is False
