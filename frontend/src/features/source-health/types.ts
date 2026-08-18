export type SourceHealthGroup = "fund" | "quote" | "industry" | "news";
export type SourceHealthRating = "healthy" | "usable" | "degraded" | "failed";
export type SourceHealthConfidence = "initial" | "growing" | "stable";
export type SourceHealthRunStatus = "queued" | "running" | "completed" | "failed";

export interface SourceHealthCounts {
  healthy: number;
  usable: number;
  degraded: number;
  failed: number;
}

export interface SourceHealthSummaryData {
  rating_confidence: SourceHealthConfidence;
  last_run_at: string | null;
  fund: SourceHealthCounts;
  news: SourceHealthCounts;
  total_sources: number;
  reclaimable_bytes: number;
}

export interface SourceHealthSource {
  source_id: string;
  source_name: string;
  group: SourceHealthGroup;
  capability: string;
  started_at: string;
  finished_at: string;
  latency_ms: number;
  probe_status: "success" | "partial" | "failure";
  error_type: string;
  error_message_redacted: string;
  http_status: number | null;
  returned_items: number;
  data_as_of_date: string | null;
  freshness_seconds: number | null;
  field_completeness_pct: number | null;
  used_cache: boolean;
  cache_status: string;
  fallback_available: boolean;
  redirected: boolean;
  final_reference: string | null;
  rating_score: number;
  rating: SourceHealthRating;
  rating_confidence: SourceHealthConfidence;
  repair_value: string;
  repair_reason: string;
  consecutive_failures: number;
  last_success_at: string | null;
}

export interface SourceHealthRun {
  run_id: string;
  scope: "full";
  status: SourceHealthRunStatus;
  started_at: string;
  finished_at: string | null;
  total: number;
  completed: number;
  success: number;
  partial: number;
  failure: number;
  current_source: string;
}

export interface SourceHealthRunStarted {
  run_id: string;
}
