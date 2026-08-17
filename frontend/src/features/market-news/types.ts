export type MarketNewsRelationLevel = "direct_holding" | "industry_relation" | "watch_tag" | "none";
export type MarketNewsImpact = "positive" | "negative" | "neutral" | "unclear";
export type MarketNewsConfidence = "high" | "medium" | "low" | "unavailable";
export type MarketNewsCategory = "policy" | "industry" | "company" | "fund_notice" | "deep_content";
export type MarketNewsMode = "my_focus" | "my_holdings" | "global_tech" | "domestic_policy";
export type MarketNewsSort = "importance" | "latest" | "holding_relevance";
export type MarketNewsCategoryFilter = "all" | MarketNewsCategory;

export interface MarketNewsSource {
  source_name: string;
  source_url: string;
  original_url: string;
  published_at: string | null;
  fetched_at: string;
  title: string;
  summary_or_excerpt: string;
  language: string;
  region: string;
  data_status: string;
}

export interface MarketNewsEvidence {
  fund_code?: string;
  fund_name?: string;
  holding_disclosure_date?: string | null;
  stock_code?: string;
  stock_name?: string;
  industry_classification?: string;
  classification_standard?: string;
  matched_kind: string;
  matched_value: string;
  source_name?: string;
  source_reference?: string;
  tag_id?: string;
}

export interface MarketNewsEvent {
  event_id: string;
  title: string;
  summary: string;
  summary_status: "source_excerpt" | "ai_unavailable";
  category: MarketNewsCategory;
  published_at_first: string | null;
  published_at_latest: string | null;
  sources: MarketNewsSource[];
  source_count: number;
  related_tags: Array<{ id: string; name: string }>;
  tag_evidence: Array<{ id: string; name: string; provenance: "article_text" | "feed_track" }>;
  related_companies: Array<{ stock_code: string; stock_name: string }>;
  related_funds: Array<{ fund_code: string; fund_name: string }>;
  relation_level: MarketNewsRelationLevel;
  relation_evidence: MarketNewsEvidence[];
  impact_tendency: MarketNewsImpact;
  impact_basis: string[];
  confidence: MarketNewsConfidence;
  original_links: string[];
  data_status: string;
  missing_information: string[];
  importance_score: number;
  translated_title_zh?: string | null;
  translated_summary_zh?: string | null;
  translation_status?: "translated" | "unavailable" | "not_required";
  translation_provider?: string | null;
  translated_at?: string | null;
}

export interface MarketNewsTranslation {
  event_id: string;
  translated_title_zh: string | null;
  translated_summary_zh: string | null;
  translation_status: "translated" | "unavailable" | "not_required";
  translation_provider: string | null;
  translated_at: string | null;
}

export interface MarketNewsTranslationResponse {
  translations: MarketNewsTranslation[];
  limit: number;
}

export interface MarketNewsQuery {
  mode: MarketNewsMode;
  tag_ids: string[];
  category: MarketNewsCategoryFilter;
  days: 1 | 3 | 7 | 30;
  sort: MarketNewsSort;
}

export interface MarketNewsImpactSummary {
  holding_related_count: number;
  direct_count: number;
  industry_count: number;
  watch_count: number;
  funds: Array<{ fund_code: string; fund_name: string; event_count: number }>;
}

export interface MarketNewsResponse {
  events: MarketNewsEvent[];
  focus_events: MarketNewsEvent[];
  impact_summary: MarketNewsImpactSummary | null;
  snapshot_id: string;
  generated_at: string | null;
  data_status: string;
  source_summary: {
    total_sources: number;
    failed_sources: number;
    cache_status: string;
    source_state: "all_success" | "partial_failure" | "cached" | "stale_cache" | "all_failed" | "empty";
    refresh_failed: boolean;
    source_statuses: Array<{ source_name: string; source_url: string; status: string; item_count: number }>;
  };
  portfolio_status: "ready" | "empty" | "error";
  ai_status: "available" | "unavailable";
  empty_reason: "no_tags" | "no_holdings" | "portfolio_error" | "no_events" | null;
  filters: MarketNewsQuery;
  filter_options: { modes: MarketNewsMode[]; categories: MarketNewsCategoryFilter[]; days: number[]; sorts: MarketNewsSort[] };
}
