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
