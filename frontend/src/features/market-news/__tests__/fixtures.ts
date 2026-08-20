import type { MarketNewsEvent, MarketNewsResponse } from "@/features/market-news/types";

export const directEvent: MarketNewsEvent = {
  event_id: "0123456789abcdef0123", title: "北方华创发布半年度报告",
  summary: "公开来源摘要显示公司发布定期报告。", summary_status: "source_excerpt", category: "company",
  published_at_first: "2026-08-17T09:00:00+08:00", published_at_latest: "2026-08-17T10:35:00+08:00",
  sources: [
    { source_name: "来源一", source_url: "https://one.example.test/rss", original_url: "https://one.example.test/a", published_at: "2026-08-17T09:00:00+08:00", fetched_at: "2026-08-17T10:40:00+08:00", title: "北方华创发布报告", summary_or_excerpt: "摘要一", language: "zh-CN", region: "CN", data_status: "cache" },
    { source_name: "来源二", source_url: "https://two.example.test/rss", original_url: "https://two.example.test/b", published_at: "2026-08-17T10:35:00+08:00", fetched_at: "2026-08-17T10:40:00+08:00", title: "北方华创半年报披露", summary_or_excerpt: "摘要二", language: "zh-CN", region: "CN", data_status: "cache" },
  ],
  source_count: 2,
  related_tags: [{ id: "semiconductor", name: "半导体" }, { id: "semiconductor-equipment", name: "半导体设备" }],
  tag_evidence: [
    { id: "semiconductor", name: "半导体", provenance: "feed_track" },
    { id: "semiconductor-equipment", name: "半导体设备", provenance: "article_text" },
  ],
  related_companies: [{ stock_code: "002371", stock_name: "北方华创" }],
  related_funds: [{ fund_code: "017811", fund_name: "东方人工智能主题混合C" }],
  relation_level: "direct_holding",
  relation_evidence: [{
    fund_code: "017811", fund_name: "东方人工智能主题混合C", holding_disclosure_date: "2026-06-30",
    stock_code: "002371", stock_name: "北方华创", industry_classification: "电子 / 半导体 / 半导体设备",
    classification_standard: "申银万国行业分类标准", matched_kind: "company", matched_value: "北方华创",
    source_name: "巨潮资讯上市公司行业归属", source_reference: "https://webapi.cninfo.com.cn/api/stock/p_stock2110",
  }],
  impact_tendency: "unclear", impact_basis: ["新闻直接命中最新公开重仓公司"], confidence: "high",
  original_links: ["https://one.example.test/a", "https://two.example.test/b"], data_status: "cache",
  missing_information: ["AI摘要暂不可用"], importance_score: 502,
  verification_status: "verified",
  verification_reason: "测试夹具：明确官方证据",
  verified_at: "2026-08-17T10:40:00+08:00",
  verified_key_fields: [],
};

export const translatedEnglishEvent: MarketNewsEvent = {
  ...directEvent,
  event_id: "eeeeeeeeeeeeeeeeeeee",
  title: "Micron launches HBM3E",
  summary: "Shipments begin this quarter.",
  sources: directEvent.sources.map((source) => ({
    ...source,
    title: "Micron launches HBM3E",
    summary_or_excerpt: "Shipments begin this quarter.",
    language: "en",
    original_url: "https://news.example.test/micron-hbm3e",
  })),
  original_links: ["https://news.example.test/micron-hbm3e"],
  translated_title_zh: "美光（Micron）发布 HBM3E",
  translated_summary_zh: "本季度开始出货。",
  translation_status: "translated",
  translation_provider: "openai",
  translated_at: "2026-08-17T04:00:00+00:00",
};

export const marketNewsResponse: MarketNewsResponse = {
  events: [directEvent],
  focus_events: [directEvent],
  impact_summary: {
    holding_related_count: 2, direct_count: 1, industry_count: 1, watch_count: 1,
    funds: [{ fund_code: "017811", fund_name: "东方人工智能主题混合C", event_count: 2 }],
  },
  snapshot_id: "aaaaaaaaaaaaaaaaaaaa",
  raw_snapshot_id: null,
  trusted_snapshot_id: null,
  evidence_snapshot_id: null,
  generated_at: "2026-08-17T12:00:00+08:00",
  data_status: "cache",
  source_summary: { total_sources: 108, failed_sources: 0, cache_status: "cache", source_state: "cached", refresh_failed: false, source_statuses: [] },
  portfolio_status: "ready",
  ai_status: "unavailable",
  empty_reason: null,
  empty_message: null,
  filters: { mode: "my_focus", tag_ids: ["storage"], category: "all", days: 7, sort: "importance" },
  filter_options: {
    modes: ["my_focus", "my_holdings", "global_tech", "domestic_policy"],
    categories: ["all", "policy", "industry", "company", "fund_notice", "deep_content"],
    days: [1, 3, 7, 30], sorts: ["importance", "latest", "holding_relevance"],
  },
};
