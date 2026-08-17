from __future__ import annotations

from datetime import datetime, timezone

from news_intelligence.clustering import cluster_items
from news_intelligence.normalizer import normalize_radar
from news_intelligence.ranking import rank_events
from news_intelligence.relationships import relate_events


NOW = datetime(2026, 8, 17, 4, tzinfo=timezone.utc)


def _event(
    title: str,
    published_at: str = "2026-08-17T10:00:00+08:00",
    summary: str = "",
    track_key: str = "semi",
):
    radar = {
        "generated_at": "2026-08-17T12:00:00+08:00",
        "cache_status": "cache",
        "industries": [{"key": track_key, "name": track_key, "items": [{
            "source_name": "公开源",
            "source_url": "https://source.example.test/rss",
            "original_url": f"https://source.example.test/{abs(hash(title))}",
            "published_at": published_at,
            "fetched_at": "2026-08-17T12:00:00+08:00",
            "title": title,
            "summary_or_excerpt": summary,
            "language": "zh-CN",
            "region": "CN",
        }]}],
    }
    return cluster_items(normalize_radar(radar, now=NOW))[0]


def _portfolio() -> dict:
    return {
        "overview": {"fund_count": 1, "updated_at": "2026-08-17T11:00:00+08:00"},
        "holdings": [{
            "code": "017811",
            "name": "东方人工智能主题混合C",
            "analysis": {
                "holdings": {
                    "data": {
                        "disclosure_date": "2026-06-30",
                        "holdings": [{
                            "stock_code": "002371", "stock_name": "北方华创", "weight_pct": 9.8,
                        }],
                    },
                    "meta": {
                        "source_name": "AKShare / 东方财富基金档案",
                        "source_reference": "https://fundf10.eastmoney.com/ccmx_017811.html",
                    },
                },
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
                    "industry_chain_tags": [{
                        "id": "semiconductor-equipment", "name": "半导体设备", "weight_pct": 9.8,
                        "evidence_level": "disclosed_stock_classification",
                        "source_name": "巨潮资讯上市公司行业归属",
                    }],
                }},
            },
        }],
    }


def test_direct_holding_requires_disclosed_company_match_and_emits_full_evidence():
    event = _event("北方华创发布半年度报告")

    related = relate_events([event], _portfolio(), selected_tag_ids=[])[0]

    assert related.relation_level == "direct_holding"
    assert related.related_funds == [{"fund_code": "017811", "fund_name": "东方人工智能主题混合C"}]
    assert related.related_companies == [{"stock_code": "002371", "stock_name": "北方华创"}]
    assert related.relation_evidence == [{
        "fund_code": "017811",
        "fund_name": "东方人工智能主题混合C",
        "holding_disclosure_date": "2026-06-30",
        "stock_code": "002371",
        "stock_name": "北方华创",
        "industry_classification": "电子 / 半导体 / 半导体设备 / 半导体设备",
        "classification_standard": "申银万国行业分类标准",
        "matched_kind": "company",
        "matched_value": "北方华创",
        "source_name": "AKShare / 东方财富基金档案",
        "source_reference": "https://fundf10.eastmoney.com/ccmx_017811.html",
    }]
    assert related.confidence == "high"


def test_fund_marketing_name_cannot_create_direct_holding_relation():
    event = _event("人工智能产业趋势出现变化", track_key="ai")

    related = relate_events([event], _portfolio(), selected_tag_ids=[])[0]

    assert related.relation_level == "none"
    assert related.related_funds == []
    assert related.relation_evidence == []


def test_feed_track_alone_cannot_create_holding_industry_relation():
    event = _event(
        "CEO Interview with Aras about product lifecycle management",
        summary="The engineering software vendor discussed digital-thread tools.",
        track_key="semi",
    )

    related = relate_events([event], _portfolio(), selected_tag_ids=[])[0]

    assert related.related_tags == [
        {"id": "semiconductor", "name": "半导体"},
        {"id": "software", "name": "软件"},
    ]
    assert related.tag_evidence == [
        {"id": "semiconductor", "name": "半导体", "provenance": "feed_track"},
        {"id": "software", "name": "软件", "provenance": "article_text"},
    ]
    assert related.relation_level == "none"
    assert related.relation_evidence == []


def test_numeric_codes_and_ambiguous_names_require_complete_entity_matches():
    numeric = _event("编号 10023710 的公开记录与公司无关")
    numeric = relate_events([numeric], _portfolio(), selected_tag_ids=[])[0]

    portfolio = _portfolio()
    portfolio["holdings"][0]["analysis"]["holdings"]["data"]["holdings"] = [{
        "stock_code": "AAPL", "stock_name": "苹果", "weight_pct": 9.8,
    }]
    common_word = relate_events([_event("苹果派烘焙指南", track_key="consumer")], portfolio, selected_tag_ids=[])[0]
    common_word_action = relate_events([_event("苹果派发布新品配方", track_key="consumer")], portfolio, selected_tag_ids=[])[0]
    prefixed_common_word = relate_events([_event("红苹果发布新品配方", track_key="consumer")], portfolio, selected_tag_ids=[])[0]
    reversed_common_word = relate_events([_event("发布苹果派新品配方", track_key="consumer")], portfolio, selected_tag_ids=[])[0]

    named_portfolio = _portfolio()
    longer_entity = relate_events([_event("北方华创园发布夏季活动")], named_portfolio, selected_tag_ids=[])[0]

    assert numeric.relation_level == "none"
    assert common_word.relation_level == "none"
    assert common_word_action.relation_level == "none"
    assert prefixed_common_word.relation_level == "none"
    assert reversed_common_word.relation_level == "none"
    assert longer_entity.relation_level == "none"


def test_disclosed_alphabetic_ticker_matches_as_a_complete_word_only():
    portfolio = _portfolio()
    holding_section = portfolio["holdings"][0]["analysis"]["holdings"]
    holding_section["data"]["holdings"] = [{
        "stock_code": "00NVDA", "stock_name": "英伟达", "weight_pct": 18.05,
    }]
    event = _event("NVIDIA expands its AI infrastructure", summary="NVDA shares were mentioned.")

    related = relate_events([event], portfolio, selected_tag_ids=[])[0]

    assert related.relation_level == "direct_holding"
    assert related.related_companies == [{"stock_code": "00NVDA", "stock_name": "英伟达"}]
    assert related.relation_evidence[0]["matched_kind"] == "company_ticker"
    assert related.relation_evidence[0]["matched_value"] == "NVDA"
    assert related.relation_evidence[0]["source_reference"].endswith("ccmx_017811.html")


def test_industry_relation_uses_sourced_holding_classification():
    event = _event("半导体设备产业政策发布")

    related = relate_events([event], _portfolio(), selected_tag_ids=[])[0]

    assert related.relation_level == "industry_relation"
    assert related.related_companies == [{"stock_code": "002371", "stock_name": "北方华创"}]
    assert related.relation_evidence[0]["matched_kind"] == "industry"
    assert related.relation_evidence[0]["matched_value"] == "半导体设备"
    assert related.relation_evidence[0]["source_name"] == "巨潮资讯上市公司行业归属"
    assert related.confidence == "medium"


def test_watch_tag_applies_only_after_holding_evidence_is_absent():
    event = _event("DRAM 产品报价出现改善")

    related = relate_events([event], _portfolio(), selected_tag_ids=["storage"])[0]

    assert related.relation_level == "watch_tag"
    assert related.related_funds == []
    assert related.relation_evidence == [{
        "matched_kind": "watch_tag", "matched_value": "存储", "tag_id": "storage",
    }]


def test_watch_tag_uses_selected_article_evidence_not_feed_track():
    event = _event("DRAM 产品报价出现改善", track_key="semi")

    related = relate_events(
        [event],
        {"overview": {"fund_count": 0}, "holdings": []},
        selected_tag_ids=["semiconductor", "storage"],
    )[0]

    assert related.relation_level == "watch_tag"
    assert related.relation_evidence == [{
        "matched_kind": "watch_tag", "matched_value": "存储", "tag_id": "storage",
    }]
    assert related.confidence == "low"


def test_relation_precedence_is_direct_then_industry_then_watch():
    direct = _event("北方华创发布半导体设备公告")
    industry = _event("半导体设备产业政策发布")

    direct, industry = relate_events([direct, industry], _portfolio(), selected_tag_ids=["semiconductor-equipment"])

    assert direct.relation_level == "direct_holding"
    assert industry.relation_level == "industry_relation"


def test_ranking_is_deterministic_for_all_three_sorts():
    direct = _event("北方华创发布公告", "2026-08-16T08:00:00+08:00")
    direct.relation_level = "direct_holding"
    policy = _event("监管部门发布行业规范", "2026-08-17T11:00:00+08:00")
    policy.category = "policy"
    industry = _event("半导体设备供需变化", "2026-08-17T10:00:00+08:00")
    industry.relation_level = "industry_relation"
    watch = _event("存储产品报价改善", "2026-08-17T09:00:00+08:00")
    watch.relation_level = "watch_tag"
    ordinary = _event("普通行业资讯", "2026-08-17T08:00:00+08:00")
    events = [ordinary, watch, industry, policy, direct]

    assert [event.title for event in rank_events(events, "importance")] == [
        "北方华创发布公告", "监管部门发布行业规范", "半导体设备供需变化", "存储产品报价改善", "普通行业资讯",
    ]
    assert [event.title for event in rank_events(events, "latest")] == [
        "监管部门发布行业规范", "半导体设备供需变化", "存储产品报价改善", "普通行业资讯", "北方华创发布公告",
    ]
    assert [event.title for event in rank_events(events, "holding_relevance")] == [
        "北方华创发布公告", "半导体设备供需变化", "存储产品报价改善", "监管部门发布行业规范", "普通行业资讯",
    ]


def test_ranking_uses_event_id_as_final_stable_key():
    left = _event("甲公司发布公告")
    right = _event("乙公司发布公告")
    for event in (left, right):
        event.category = "company"
        event.published_at_latest = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)

    ranked = rank_events([right, left], "importance")

    assert [event.event_id for event in ranked] == sorted([left.event_id, right.event_id])
