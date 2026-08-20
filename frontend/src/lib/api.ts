// Vibe-Research 后端 API 客户端。/api → vite 代理到本地 FastAPI（默认 8900）。
// 后端未启动或数据源异常时抛 ApiError，页面据此优雅降级。

import type {
  DataSection,
  FundAnalysis,
  FundHoldingInput,
  FundPortfolioAnalysisData,
  FundPortfolioData,
  FundSearchResult,
} from "@/features/fund-portfolio/types";
import type {
  CacheCleanupResult,
  CacheStatus,
  MarketNewsEvent,
  MarketNewsQuery,
  MarketNewsResponse,
  MarketNewsSourceRetryResult,
  MarketNewsTranslationResponse,
  NewsPipelineStarted,
  NewsPipelineStatusData,
  NewsPipelineCounts,
  NewsPipelineErrorCode,
  NewsPipelinePhase,
} from "@/features/market-news/types";
import type { LlmConfig } from "@/lib/llm";
import type {
  SourceHealthRun,
  SourceHealthRunStarted,
  SourceHealthSource,
  SourceHealthSummaryData,
} from "@/features/source-health/types";
import type {
  AdapterActionResponse,
  AdapterConfigMutationResponse,
  AdapterConfigUpdate,
  AdapterConfigurationView,
  AdapterCostView,
  AdapterUsageView,
  CredentialState,
  DataSourceCatalogResponse,
  DataSourceConfigurationResponse,
  DataSourceCostResponse,
  DataSourceUsageResponse,
  SourceFamilyView,
} from "@/features/source-catalog/types";
import type {
  EvidenceEventDetail,
  EvidenceEventList,
  EvidenceEventQuery,
  EvidenceSummaryData,
} from "@/features/evidence-center/types";

export type {
  DataMeta,
  DataSection,
  FundAnalysis,
  FundHolding,
  FundHoldingInput,
  FundPortfolioAnalysisData,
  FundPortfolioData,
  FundSearchResult,
  PositionMetrics,
} from "@/features/fund-portfolio/types";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

// 后端访问密钥（对应后端部署时的 VR_API_KEY，公网部署防蹭用）。只存本地浏览器。
const ACCESS_KEY = "vr-access-key";

export function loadAccessKey(): string {
  try {
    return localStorage.getItem(ACCESS_KEY) || "";
  } catch {
    return "";
  }
}

export function saveAccessKey(key: string) {
  try {
    if (key) localStorage.setItem(ACCESS_KEY, key);
    else localStorage.removeItem(ACCESS_KEY);
  } catch {
    /* 隐私模式等场景 localStorage 不可用 */
  }
}

export function authHeaders(): Record<string, string> {
  const k = loadAccessKey();
  return k ? { Authorization: `Bearer ${k}` } : {};
}

export interface MyReport {
  id: string; name: string; industry: string; size: number; ext: string; ts: number;
}

// 下载/预览研报：带鉴权头 fetch → blob → 触发浏览器下载（<a download> 无法带 Authorization，故走 blob）。
export async function downloadReport(id: string, name: string): Promise<void> {
  const resp = await fetch(`/api/myreports/file/${id}`, { headers: authHeaders() });
  if (!resp.ok) throw new ApiError(`下载失败 HTTP ${resp.status}`, resp.status);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function isAbortError(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && "name" in error && error.name === "AbortError");
}

async function request<T>(
  path: string,
  method: "GET" | "POST" | "PUT" | "DELETE" = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let resp: Response;
  const headers: Record<string, string> = { ...authHeaders() };
  const opts: RequestInit = { method, signal };
  if (method !== "GET" && (
    path.startsWith("/data-sources/")
    || path.startsWith("/market-news/refresh")
    || path === "/evidence/refresh"
    || path === "/radar/refresh"
  )) {
    headers["X-PP03-Write-Intent"] = "1";
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (Object.keys(headers).length > 0) opts.headers = headers;
  try {
    resp = await fetch(`/api${path}`, opts);
  } catch (error) {
    if (signal?.aborted || isAbortError(error)) throw error;
    throw new ApiError("连接不到后端，请先启动 backend（uvicorn app:app --port 8900）", 0);
  }
  let payload: any = null;
  try {
    payload = await resp.json();
  } catch {
    /* 非 JSON 响应 */
  }
  if (!resp.ok) {
    if (resp.status === 401) {
      throw new ApiError("后端开启了访问鉴权（VR_API_KEY）：请在「接入 AI」页底部填写后端访问密钥", 401);
    }
    throw new ApiError(payload?.detail || `HTTP ${resp.status}`, resp.status);
  }
  return (payload?.data ?? payload) as T;
}

const get = <T>(path: string, signal?: AbortSignal) => request<T>(path, "GET", undefined, signal);

const PIPELINE_PHASES = new Set<NewsPipelinePhase>([
  "queued", "fetching", "raw_saved", "verifying", "evidence_saved",
  "trusted_published", "failed", "interrupted",
]);
const PIPELINE_ERROR_CODES = new Set<NewsPipelineErrorCode>([
  "collection_failed", "evidence_compatibility_failed", "evidence_persistence_failed",
  "pipeline_error", "pipeline_interrupted", "publication_failed",
  "radar_compatibility_failed", "storage_error", "verification_failed",
]);
const PIPELINE_COUNT_KEYS: Array<keyof NewsPipelineCounts> = [
  "raw_event_count", "verified_count", "corroborated_count", "pending_count",
  "conflicting_count", "corrected_count", "disproved_count", "failed_source_count",
];
const PIPELINE_STATUS_KEYS = new Set([
  "loaded", "run_id", "raw_snapshot_id", "evidence_snapshot_id", "trusted_snapshot_id",
  "phase", "counts", "admitted_count", "has_pending_evidence_message", "created_at",
  "updated_at", "redacted_error", "recovery_status", "recovery_error",
  "compatibility_error", "displayed_trusted_snapshot_id", "displayed_trusted",
  ...PIPELINE_COUNT_KEYS,
]);
const MAX_PIPELINE_COUNT = 1_000_000_000;

function pipelineError(): never {
  throw new ApiError("资讯流水线响应无效", 502);
}

function pipelineRecord(value: unknown, allowed: Set<string>): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) pipelineError();
  const record = value as Record<string, unknown>;
  if (Object.keys(record).some((key) => !allowed.has(key))) pipelineError();
  return record;
}

function pipelineString(value: unknown, maxLength: number): string {
  if (typeof value !== "string" || value.length < 1 || value.length > maxLength || value !== value.trim()) pipelineError();
  return value;
}

function pipelineId(value: unknown): string {
  const id = pipelineString(value, 128);
  if (!/^[A-Za-z0-9_-]+$/.test(id)) pipelineError();
  return id;
}

function nullablePipelineId(value: unknown): string | null {
  return value === null ? null : pipelineId(value);
}

function pipelineTimestamp(value: unknown): string {
  return pipelineString(value, 64);
}

function nullablePipelineTimestamp(value: unknown): string | null {
  return value === null ? null : pipelineTimestamp(value);
}

function pipelineCount(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > MAX_PIPELINE_COUNT) pipelineError();
  return value;
}

function pipelineCounts(value: unknown): NewsPipelineCounts {
  const row = pipelineRecord(value, new Set(PIPELINE_COUNT_KEYS));
  if (Object.keys(row).length !== PIPELINE_COUNT_KEYS.length) pipelineError();
  return Object.fromEntries(PIPELINE_COUNT_KEYS.map((key) => [key, pipelineCount(row[key])])) as unknown as NewsPipelineCounts;
}

function pipelinePhase(value: unknown): NewsPipelinePhase {
  if (typeof value !== "string" || !PIPELINE_PHASES.has(value as NewsPipelinePhase)) pipelineError();
  return value as NewsPipelinePhase;
}

function nullablePipelineError(value: unknown): NewsPipelineErrorCode | null {
  if (value === null) return null;
  if (typeof value !== "string" || !PIPELINE_ERROR_CODES.has(value as NewsPipelineErrorCode)) pipelineError();
  return value as NewsPipelineErrorCode;
}

function evidenceCountTotal(counts: NewsPipelineCounts): number {
  return counts.verified_count + counts.corroborated_count + counts.pending_count
    + counts.conflicting_count + counts.corrected_count + counts.disproved_count;
}

function hasOnlyRawCounts(counts: NewsPipelineCounts): boolean {
  return evidenceCountTotal(counts) === 0;
}

export function validateNewsPipelineStatus(status: NewsPipelineStatusData): NewsPipelineStatusData {
  const displayedId = status.displayed_trusted_snapshot_id;
  const displayed = status.displayed_trusted;
  if ((displayedId === null) !== (displayed === null) || (displayed && displayed.snapshot_id !== displayedId)) pipelineError();

  if (!status.loaded) {
    if (status.has_pending_evidence_message || status.compatibility_error !== null) pipelineError();
    return status;
  }
  const phase = status.phase;
  const counts = status.counts;
  if (phase === null || counts === null || status.raw_snapshot_id === null || status.run_id === null) pipelineError();
  const hasEvidence = status.evidence_snapshot_id !== null;
  const hasTrusted = status.trusted_snapshot_id !== null;
  const evidenceTotal = evidenceCountTotal(counts);
  const compatibilityFromError = status.redacted_error === "evidence_compatibility_failed"
    || status.redacted_error === "radar_compatibility_failed"
    ? status.redacted_error
    : null;
  if (status.compatibility_error !== compatibilityFromError) pipelineError();

  if (phase === "queued" || phase === "fetching") {
    if (hasEvidence || hasTrusted || Object.values(counts).some((value) => value !== 0)
      || status.redacted_error !== null || status.has_pending_evidence_message) pipelineError();
  } else if (phase === "raw_saved" || phase === "verifying") {
    if (hasEvidence || hasTrusted || !hasOnlyRawCounts(counts)
      || status.redacted_error !== null || status.has_pending_evidence_message) pipelineError();
  } else if (phase === "evidence_saved") {
    if (!hasEvidence || hasTrusted || evidenceTotal !== counts.raw_event_count
      || (status.redacted_error !== null && status.redacted_error !== "radar_compatibility_failed")
      || status.has_pending_evidence_message) pipelineError();
  } else if (phase === "trusted_published") {
    const pendingExpected = counts.raw_event_count > 0 && status.admitted_count === 0;
    if (!hasEvidence || !hasTrusted || status.trusted_snapshot_id !== status.raw_snapshot_id
      || evidenceTotal !== counts.raw_event_count || displayedId === null
      || (status.redacted_error !== null && status.redacted_error !== "radar_compatibility_failed")
      || status.has_pending_evidence_message !== pendingExpected) pipelineError();
  } else if (phase === "interrupted") {
    if (hasTrusted || status.redacted_error !== "pipeline_interrupted" || status.has_pending_evidence_message
      || (hasEvidence ? evidenceTotal !== counts.raw_event_count : evidenceTotal !== 0)) pipelineError();
  } else if (phase === "failed") {
    if (hasTrusted || status.redacted_error === null || status.redacted_error === "pipeline_interrupted"
      || status.redacted_error === "radar_compatibility_failed" || status.has_pending_evidence_message) pipelineError();
    if (status.redacted_error === "collection_failed" || status.redacted_error === "storage_error"
      || status.redacted_error === "pipeline_error") {
      if (hasEvidence || counts.raw_event_count !== 0 || evidenceTotal !== 0) pipelineError();
    } else if (status.redacted_error === "verification_failed" || status.redacted_error === "evidence_persistence_failed") {
      if (hasEvidence || evidenceTotal !== 0) pipelineError();
    } else if (status.redacted_error === "evidence_compatibility_failed" || status.redacted_error === "publication_failed") {
      if (!hasEvidence || evidenceTotal !== counts.raw_event_count) pipelineError();
    } else pipelineError();
  }
  return status;
}

function shapeNewsPipelineStarted(value: unknown): NewsPipelineStarted {
  const row = pipelineRecord(value, new Set(["run_id", "raw_snapshot_id", "phase"]));
  if (Object.keys(row).length !== 3 || row.phase !== "queued") pipelineError();
  return { run_id: pipelineId(row.run_id), raw_snapshot_id: pipelineId(row.raw_snapshot_id), phase: "queued" };
}

function shapeNewsPipelineStatus(value: unknown): NewsPipelineStatusData {
  const row = pipelineRecord(value, PIPELINE_STATUS_KEYS);
  const required = [
    "loaded", "run_id", "raw_snapshot_id", "evidence_snapshot_id", "trusted_snapshot_id",
    "phase", "counts", "admitted_count", "has_pending_evidence_message", "created_at",
    "updated_at", "redacted_error", "recovery_status", "recovery_error",
    "compatibility_error", "displayed_trusted_snapshot_id", "displayed_trusted",
  ];
  if (required.some((key) => !(key in row)) || typeof row.loaded !== "boolean" || typeof row.has_pending_evidence_message !== "boolean") pipelineError();
  if (!new Set(["ready", "pending", "failed"]).has(String(row.recovery_status))) pipelineError();
  if (row.recovery_error !== null && row.recovery_error !== "storage_corrupt") pipelineError();
  if (row.compatibility_error !== null && row.compatibility_error !== "evidence_compatibility_failed" && row.compatibility_error !== "radar_compatibility_failed") pipelineError();

  const counts = row.counts === null ? null : pipelineCounts(row.counts);
  const admitted = row.admitted_count === null ? null : pipelineCount(row.admitted_count);
  if (counts && admitted !== counts.verified_count + counts.corroborated_count) pipelineError();
  for (const key of PIPELINE_COUNT_KEYS) {
    if (key in row && (!counts || pipelineCount(row[key]) !== counts[key])) pipelineError();
  }
  if (!row.loaded && (
    row.run_id !== null || row.raw_snapshot_id !== null || row.evidence_snapshot_id !== null
    || row.trusted_snapshot_id !== null || row.phase !== null || counts !== null || admitted !== null
    || row.created_at !== null || row.updated_at !== null || row.redacted_error !== null
  )) pipelineError();
  if (row.loaded && (
    row.run_id === null || row.raw_snapshot_id === null || row.phase === null || counts === null
    || admitted === null || row.created_at === null || row.updated_at === null
  )) pipelineError();
  if (row.loaded && PIPELINE_COUNT_KEYS.some((key) => !(key in row))) pipelineError();
  if (!row.loaded && PIPELINE_COUNT_KEYS.some((key) => key in row)) pipelineError();

  let displayedTrusted: NewsPipelineStatusData["displayed_trusted"] = null;
  if (row.displayed_trusted !== null) {
    const displayed = pipelineRecord(row.displayed_trusted, new Set(["snapshot_id", "published_at", "event_count"]));
    if (Object.keys(displayed).length !== 3) pipelineError();
    displayedTrusted = {
      snapshot_id: pipelineId(displayed.snapshot_id),
      published_at: pipelineTimestamp(displayed.published_at),
      event_count: pipelineCount(displayed.event_count),
    };
  }

  return validateNewsPipelineStatus({
    loaded: row.loaded,
    run_id: nullablePipelineId(row.run_id),
    raw_snapshot_id: nullablePipelineId(row.raw_snapshot_id),
    evidence_snapshot_id: nullablePipelineId(row.evidence_snapshot_id),
    trusted_snapshot_id: nullablePipelineId(row.trusted_snapshot_id),
    phase: row.phase === null ? null : pipelinePhase(row.phase),
    counts,
    admitted_count: admitted,
    has_pending_evidence_message: row.has_pending_evidence_message,
    created_at: nullablePipelineTimestamp(row.created_at),
    updated_at: nullablePipelineTimestamp(row.updated_at),
    redacted_error: nullablePipelineError(row.redacted_error),
    recovery_status: row.recovery_status as NewsPipelineStatusData["recovery_status"],
    recovery_error: row.recovery_error as NewsPipelineStatusData["recovery_error"],
    compatibility_error: row.compatibility_error as NewsPipelineStatusData["compatibility_error"],
    displayed_trusted_snapshot_id: nullablePipelineId(row.displayed_trusted_snapshot_id),
    displayed_trusted: displayedTrusted,
  });
}

const MARKET_NEWS_RESPONSE_KEYS = new Set([
  "events", "focus_events", "impact_summary", "snapshot_id", "raw_snapshot_id",
  "trusted_snapshot_id", "evidence_snapshot_id", "generated_at", "data_status",
  "source_summary", "portfolio_status", "ai_status", "empty_reason", "empty_message",
  "filters", "filter_options",
]);
const MARKET_NEWS_QUERY_KEYS = new Set(["mode", "tag_ids", "category", "days", "sort"]);
const MARKET_NEWS_SOURCE_SUMMARY_KEYS = new Set([
  "total_sources", "failed_sources", "cache_status", "source_state", "refresh_failed",
  "source_statuses",
]);
const MARKET_NEWS_FILTER_OPTION_KEYS = new Set(["modes", "categories", "days", "sorts"]);
const MARKET_NEWS_MODES = new Set(["my_focus", "my_holdings", "global_tech", "domestic_policy"]);
const MARKET_NEWS_CATEGORIES = new Set(["all", "policy", "industry", "company", "fund_notice", "deep_content"]);
const MARKET_NEWS_SORTS = new Set(["importance", "latest", "holding_relevance"]);
const MARKET_NEWS_SOURCE_STATES = new Set(["all_success", "partial_failure", "cached", "stale_cache", "all_failed", "empty", "pipeline_pending", "trusted"]);
const MARKET_NEWS_PORTFOLIO_STATES = new Set(["ready", "empty", "error", "public_relationships", "unavailable"]);
const MARKET_NEWS_EMPTY_REASONS = new Set(["no_tags", "no_holdings", "portfolio_error", "portfolio_unavailable", "no_events", "no_trusted_snapshot"]);

function marketNewsError(): never {
  throw new ApiError("市场资讯响应无效", 502);
}

function marketNewsRecord(value: unknown, allowed: Set<string>): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) marketNewsError();
  const record = value as Record<string, unknown>;
  if (Object.keys(record).some((key) => !allowed.has(key))) marketNewsError();
  return record;
}

function marketNewsString(value: unknown, maxLength = 512): string {
  if (typeof value !== "string" || value.length < 1 || value.length > maxLength || value !== value.trim()) marketNewsError();
  return value;
}

function marketNewsNullableString(value: unknown, maxLength = 512): string | null {
  return value === null ? null : marketNewsString(value, maxLength);
}

function marketNewsEnum(value: unknown, values: Set<string>): string {
  const selected = marketNewsString(value, 64);
  if (!values.has(selected)) marketNewsError();
  return selected;
}

function shapeMarketNewsQuery(value: unknown): MarketNewsQuery {
  const row = marketNewsRecord(value, MARKET_NEWS_QUERY_KEYS);
  if (Object.keys(row).length !== MARKET_NEWS_QUERY_KEYS.size || !Array.isArray(row.tag_ids)) marketNewsError();
  const tagIds = row.tag_ids.map((item) => marketNewsString(item, 128));
  if (tagIds.length > 128 || new Set(tagIds).size !== tagIds.length) marketNewsError();
  if (row.days !== 1 && row.days !== 3 && row.days !== 7 && row.days !== 30) marketNewsError();
  return {
    mode: marketNewsEnum(row.mode, MARKET_NEWS_MODES) as MarketNewsQuery["mode"],
    tag_ids: tagIds,
    category: marketNewsEnum(row.category, MARKET_NEWS_CATEGORIES) as MarketNewsQuery["category"],
    days: row.days,
    sort: marketNewsEnum(row.sort, MARKET_NEWS_SORTS) as MarketNewsQuery["sort"],
  };
}

function shapeMarketNewsResponse(value: unknown): MarketNewsResponse {
  const row = marketNewsRecord(value, MARKET_NEWS_RESPONSE_KEYS);
  if (Object.keys(row).length !== MARKET_NEWS_RESPONSE_KEYS.size
    || !Array.isArray(row.events) || !Array.isArray(row.focus_events)) marketNewsError();
  const source = marketNewsRecord(row.source_summary, MARKET_NEWS_SOURCE_SUMMARY_KEYS);
  if (Object.keys(source).length !== MARKET_NEWS_SOURCE_SUMMARY_KEYS.size
    || !Array.isArray(source.source_statuses) || typeof source.refresh_failed !== "boolean") marketNewsError();
  const totalSources = pipelineCount(source.total_sources);
  const failedSources = pipelineCount(source.failed_sources);
  if (failedSources > totalSources) marketNewsError();
  const filters = shapeMarketNewsQuery(row.filters);
  const options = marketNewsRecord(row.filter_options, MARKET_NEWS_FILTER_OPTION_KEYS);
  if (Object.keys(options).length !== MARKET_NEWS_FILTER_OPTION_KEYS.size
    || !Array.isArray(options.modes) || !Array.isArray(options.categories)
    || !Array.isArray(options.days) || !Array.isArray(options.sorts)) marketNewsError();
  const modes = options.modes.map((item) => marketNewsEnum(item, MARKET_NEWS_MODES)) as MarketNewsResponse["filter_options"]["modes"];
  const categories = options.categories.map((item) => marketNewsEnum(item, MARKET_NEWS_CATEGORIES)) as MarketNewsResponse["filter_options"]["categories"];
  const sorts = options.sorts.map((item) => marketNewsEnum(item, MARKET_NEWS_SORTS)) as MarketNewsResponse["filter_options"]["sorts"];
  const days = options.days.map((item) => {
    if (item !== 1 && item !== 3 && item !== 7 && item !== 30) marketNewsError();
    return item;
  });
  const snapshotId = row.snapshot_id === null ? null : pipelineId(row.snapshot_id);
  const rawSnapshotId = nullablePipelineId(row.raw_snapshot_id);
  const trustedSnapshotId = nullablePipelineId(row.trusted_snapshot_id);
  const evidenceSnapshotId = nullablePipelineId(row.evidence_snapshot_id);
  const dataStatus = marketNewsString(row.data_status, 64);
  if ((rawSnapshotId === null) !== (trustedSnapshotId === null)
    || (rawSnapshotId !== null && rawSnapshotId !== trustedSnapshotId)) marketNewsError();
  if (dataStatus === "pipeline_pending") {
    if (snapshotId !== null || rawSnapshotId !== null || evidenceSnapshotId !== null
      || row.events.length !== 0 || row.focus_events.length !== 0) marketNewsError();
  } else if (snapshotId === null) marketNewsError();
  const emptyReason = row.empty_reason === null
    ? null
    : marketNewsEnum(row.empty_reason, MARKET_NEWS_EMPTY_REASONS) as MarketNewsResponse["empty_reason"];
  if (row.impact_summary !== null && (typeof row.impact_summary !== "object" || Array.isArray(row.impact_summary))) marketNewsError();
  if (row.ai_status !== "available" && row.ai_status !== "unavailable") marketNewsError();

  return {
    events: row.events as MarketNewsResponse["events"],
    focus_events: row.focus_events as MarketNewsResponse["focus_events"],
    impact_summary: row.impact_summary as MarketNewsResponse["impact_summary"],
    snapshot_id: snapshotId,
    raw_snapshot_id: rawSnapshotId,
    trusted_snapshot_id: trustedSnapshotId,
    evidence_snapshot_id: evidenceSnapshotId,
    generated_at: marketNewsNullableString(row.generated_at, 64),
    data_status: dataStatus,
    source_summary: {
      total_sources: totalSources,
      failed_sources: failedSources,
      cache_status: marketNewsString(source.cache_status, 64),
      source_state: marketNewsEnum(source.source_state, MARKET_NEWS_SOURCE_STATES) as MarketNewsResponse["source_summary"]["source_state"],
      refresh_failed: source.refresh_failed,
      source_statuses: source.source_statuses as MarketNewsResponse["source_summary"]["source_statuses"],
    },
    portfolio_status: marketNewsEnum(row.portfolio_status, MARKET_NEWS_PORTFOLIO_STATES) as MarketNewsResponse["portfolio_status"],
    ai_status: row.ai_status,
    empty_reason: emptyReason,
    empty_message: marketNewsNullableString(row.empty_message, 2048),
    filters,
    filter_options: { modes, categories, days, sorts },
  };
}

function dataSourceRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ApiError("数据源配置响应无效", 502);
  }
  return value as Record<string, unknown>;
}

function dataSourceString(value: unknown): string {
  if (typeof value !== "string") throw new ApiError("数据源配置响应无效", 502);
  return value;
}

function dataSourceNullableString(value: unknown): string | null {
  if (value === null) return null;
  return dataSourceString(value);
}

function dataSourceTimezone(value: unknown): "UTC" | null {
  return value === "UTC" ? "UTC" : null;
}

function dataSourceBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") throw new ApiError("数据源配置响应无效", 502);
  return value;
}

function dataSourceBillingModel(value: unknown): AdapterConfigurationView["billing_model"] {
  if (!["free_no_key", "free_key", "freemium", "paid_api", "enterprise_license", "internal_only"].includes(String(value))) {
    throw new ApiError("数据源配置响应无效", 502);
  }
  return value as AdapterConfigurationView["billing_model"];
}

function dataSourceCatalogStatus(value: unknown): AdapterConfigurationView["catalog_status"] {
  if (!["connected", "configured", "unconfigured", "catalog_only", "license_required", "disabled"].includes(String(value))) {
    throw new ApiError("数据源配置响应无效", 502);
  }
  return value as AdapterConfigurationView["catalog_status"];
}

function dataSourceNullableInteger(value: unknown): number | null {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new ApiError("数据源配置响应无效", 502);
  }
  return value;
}

function credentialState(value: unknown): CredentialState {
  const row = dataSourceRecord(value);
  return {
    configured: dataSourceBoolean(row.configured),
    status: dataSourceString(row.status),
    last_validated_at: dataSourceNullableString(row.last_validated_at),
    credential_source: dataSourceString(row.credential_source),
  };
}

function adapterConfiguration(value: unknown): AdapterConfigurationView {
  const row = dataSourceRecord(value);
  return {
    adapter_id: dataSourceString(row.adapter_id),
    billing_model: dataSourceBillingModel(row.billing_model),
    catalog_status: dataSourceCatalogStatus(row.catalog_status),
    enabled: dataSourceBoolean(row.enabled),
    usage_mode: dataSourceNullableString(row.usage_mode),
    daily_budget: dataSourceNullableString(row.daily_budget),
    monthly_budget: dataSourceNullableString(row.monthly_budget),
    per_request_budget: dataSourceNullableString(row.per_request_budget),
    daily_request_limit: dataSourceNullableInteger(row.daily_request_limit),
    monthly_request_limit: dataSourceNullableInteger(row.monthly_request_limit),
    credential: credentialState(row.credential),
  };
}

function dataSourceConfiguration(value: unknown): DataSourceConfigurationResponse {
  const row = dataSourceRecord(value);
  if (!Array.isArray(row.adapters)) throw new ApiError("数据源配置响应无效", 502);
  return { free_only: dataSourceBoolean(row.free_only), adapters: row.adapters.map(adapterConfiguration) };
}

function usageStatus(value: unknown): "observed" | "unobserved" {
  if (value !== "observed" && value !== "unobserved") throw new ApiError("数据源用量响应无效", 502);
  return value;
}

function statusCounts(value: unknown): Record<string, number> {
  const row = dataSourceRecord(value);
  const result: Record<string, number> = {};
  Object.entries(row).forEach(([key, count]) => {
    if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) {
      throw new ApiError("数据源用量响应无效", 502);
    }
    result[key] = count;
  });
  return result;
}

function adapterUsage(value: unknown): AdapterUsageView {
  const row = dataSourceRecord(value);
  return {
    adapter_id: dataSourceString(row.adapter_id),
    day: dataSourceString(row.day),
    month: dataSourceString(row.month),
    usage_status: usageStatus(row.usage_status),
    daily_cost: dataSourceNullableString(row.daily_cost),
    monthly_cost: dataSourceNullableString(row.monthly_cost),
    daily_request_count: dataSourceNullableInteger(row.daily_request_count),
    monthly_request_count: dataSourceNullableInteger(row.monthly_request_count),
    daily_units: dataSourceNullableString(row.daily_units),
    monthly_units: dataSourceNullableString(row.monthly_units),
    status_counts: statusCounts(row.status_counts),
    open_reservations: dataSourceNullableInteger(row.open_reservations),
  };
}

function dataSourceUsage(value: unknown): DataSourceUsageResponse {
  const row = dataSourceRecord(value);
  if (!Array.isArray(row.adapters)) throw new ApiError("数据源用量响应无效", 502);
  return {
    as_of: dataSourceString(row.as_of),
    timezone: dataSourceTimezone(row.timezone),
    usage_status: usageStatus(row.usage_status),
    adapters: row.adapters.map(adapterUsage),
  };
}

function adapterCost(value: unknown): AdapterCostView {
  const row = dataSourceRecord(value);
  return {
    adapter_id: dataSourceString(row.adapter_id),
    billing_model: dataSourceBillingModel(row.billing_model),
    enabled: dataSourceBoolean(row.enabled),
    credential_configured: dataSourceBoolean(row.credential_configured),
    status: dataSourceString(row.status),
    usage_status: usageStatus(row.usage_status),
    day: dataSourceString(row.day),
    month: dataSourceString(row.month),
    daily_budget: dataSourceNullableString(row.daily_budget),
    monthly_budget: dataSourceNullableString(row.monthly_budget),
    per_request_budget: dataSourceNullableString(row.per_request_budget),
    daily_cost: dataSourceNullableString(row.daily_cost),
    monthly_cost: dataSourceNullableString(row.monthly_cost),
    daily_remaining: dataSourceNullableString(row.daily_remaining),
    monthly_remaining: dataSourceNullableString(row.monthly_remaining),
    open_reservations: dataSourceNullableInteger(row.open_reservations),
  };
}

function dataSourceCost(value: unknown): DataSourceCostResponse {
  const row = dataSourceRecord(value);
  if (!Array.isArray(row.adapters)) throw new ApiError("数据源费用响应无效", 502);
  return {
    as_of: dataSourceString(row.as_of),
    timezone: dataSourceTimezone(row.timezone),
    free_only: dataSourceBoolean(row.free_only),
    usage_status: usageStatus(row.usage_status),
    adapters: row.adapters.map(adapterCost),
  };
}

function adapterConfigMutation(value: unknown): AdapterConfigMutationResponse {
  const row = dataSourceRecord(value);
  const rawConfig = dataSourceRecord(row.config);
  const config: Record<string, string | number | boolean | null> = {};
  for (const key of ["enabled", "usage_mode", "daily_budget", "monthly_budget", "per_request_budget", "daily_request_limit", "monthly_request_limit", "last_validated_at"]) {
    const item = rawConfig[key];
    if (item === null || typeof item === "string" || typeof item === "boolean" || (typeof item === "number" && Number.isSafeInteger(item))) config[key] = item;
  }
  return { adapter_id: dataSourceString(row.adapter_id), config };
}

function adapterAction(value: unknown): AdapterActionResponse {
  const row = dataSourceRecord(value);
  const result: AdapterActionResponse = {
    adapter_id: dataSourceString(row.adapter_id),
    status: dataSourceString(row.status),
    connected: dataSourceBoolean(row.connected),
  };
  if (row.action === "enable" || row.action === "disable") result.action = row.action;
  if (typeof row.enabled === "boolean") result.enabled = row.enabled;
  if (typeof row.health_failure === "boolean") result.health_failure = row.health_failure;
  if (row.last_validated_at === null || typeof row.last_validated_at === "string") result.last_validated_at = row.last_validated_at;
  return result;
}

function marketNewsPath(path: string, query: MarketNewsQuery): string {
  const params = new URLSearchParams({
    mode: query.mode,
    category: query.category,
    days: String(query.days),
    sort: query.sort,
  });
  query.tag_ids.forEach((tagId) => params.append("tag_id", tagId));
  return `${path}?${params.toString()}`;
}

function evidenceEventsPath(query: EvidenceEventQuery): string {
  const params = new URLSearchParams();
  if (query.verification_status) params.set("verification_status", query.verification_status);
  if (query.tag_id) params.set("tag_id", query.tag_id);
  if (query.category) params.set("category", query.category);
  params.set("days", String(query.days ?? 7));
  if (query.holding_relevance) params.set("holding_relevance", query.holding_relevance);
  return `/evidence/events?${params.toString()}`;
}

export interface Quote {
  name: string; price: number; last_close: number; change_pct: number;
  pe_ttm: number; pb: number; mcap_yi: number; turnover_pct: number;
  limit_up: number; limit_down: number;
}

export interface Valuation {
  name: string; code: string; price: number; mcap_yi: number;
  pe_ttm: number; pb: number;
  eps_26e: number | null; eps_27e: number | null; pe_26e: number | null;
  cagr_pct: number | null; peg: number | null; digest_years: number | null;
  analyst_count: number; forecast_note?: string;
}

export interface Report {
  title: string; publishDate: string; orgSName: string;
  emRatingName?: string; indvInduName?: string; pdfUrl?: string | null;
}

export interface ValMetric {
  current: number; percentile: number; min: number; max: number;
  p20: number; p50: number; p80: number; n: number;
}
export interface ValPercentile {
  period: string; metrics: { pe_ttm?: ValMetric; pb?: ValMetric };
}

export interface Announcement {
  date: string; title: string; type: string; url: string;
}

export interface Financials {
  period: string | null;
  revenue: string | null; revenue_yoy: string | null;
  net_profit: string | null; net_profit_yoy: string | null;
  eps: string | null; bvps: string | null; roe: string | null;
  gross_margin: string | null; net_margin: string | null; op_cf_ps: string | null;
}

export interface NewsItem {
  新闻标题?: string; 发布时间?: string; 文章来源?: string; 新闻链接?: string;
}

export interface IndexQuote {
  name: string; price: number; change_pct: number; change_amt: number;
}

export interface MarketSentiment {
  up: number; down: number; flat: number; zt: number; zt_real: number; dt: number; dt_real: number;
  active: string; breadth: string; speculation: string; date: string;
}
export interface SectorFlow {
  name: string; pct: number; net: number; inflow: number; outflow: number; firms: number;
}
export interface MarketOverview {
  sentiment: MarketSentiment; sectors: SectorFlow[]; updated: string;
}

// 短线情绪：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率 / 涨跌停家数 + 连板股清单（客观公开榜单）
export interface EmotionTier { boards: number; count: number; plus: boolean }
export interface LianbanStock {
  code: string; name: string; boards: number;
  price: number; pct: number; amount: number | null; float_cap: number | null; industry: string;
}
export interface ShortTermEmotion {
  date: string;
  zt_count: number; dt_count: number; zb_count: number;
  max_boards: number; lianban_count: number;
  ladder: EmotionTier[];
  lianban_stocks: LianbanStock[];
  seal_rate: number | null; break_rate: number | null; promotion_rate: number | null;
  yzt_count: number;
}

// 全市场成交额榜（客观公开榜单）
export interface TurnoverStock {
  code: string; name: string;
  price: number | null; pct: number | null;
  amount: number | null; mcap: number | null; float_cap: number | null; industry: string;
}
export interface TurnoverTop { stocks: TurnoverStock[]; updated: string }

export interface RadarItem {
  title: string; url: string; time: string; ts?: number; source: string; summary?: string; zh?: string;
}
export interface Industry {
  key: string; name: string; accent: string; total: number; items: RadarItem[];
}
export interface RadarData {
  generated_at: string | null; recent_days: number; industries: Industry[];
  stats: { industries: number; total_sources: number; failed_sources?: number };
}

export interface Holding {
  code: string; name: string; price: number; shares: number; cost: number;
  market_value: number; pnl: number; pnl_pct: number;
}
export interface ClosedPosition {
  code: string; name: string; date: string; price: number; shares: number; cost: number;
  pnl: number; pnl_pct: number;
}
export interface PortfolioData {
  holdings: Holding[];
  totals: { market_value: number; cost: number; pnl: number; pnl_pct: number };
  closed: ClosedPosition[];
  realized_pnl: number;
  updated: string; last_refresh: string | null;
}

// 资金面 / 筹码 / 信号（v3.3 并入，均为「用户查的那只股」的公开数据）
export interface MarginRow { date: string; rzye: number; rzmre: number; rzche: number; rqye: number; rqmcl: number; rzrqye: number }
export interface BlockTradeRow { date: string; price: number; close: number; premium_pct: number; vol: number; amount: number; buyer: string; seller: string }
export interface HolderRow { date: string; holder_num: number; change_ratio: number; avg_shares: number }
export interface DividendRow { date: string; bonus_rmb: number; transfer_ratio: number; bonus_ratio: number | null; plan: string }
export interface FundFlowRow { date: string; main_net: number; small_net: number; mid_net: number; large_net: number; super_net: number }
export interface DtSeat { name: string; buy_amt: number; sell_amt: number; net: number }
export interface DragonTiger {
  records: { date: string; reason: string; net_buy: number; turnover: number }[];
  seats: { buy: DtSeat[]; sell: DtSeat[] };
  institution: { buy_amt: number; sell_amt: number; net_amt: number };
}
export interface LockupRow { date: string; type: string; shares: number; able_shares: number; ratio: number }
export interface Lockup { history: LockupRow[]; upcoming: LockupRow[] }
export interface Board { name: string; code: string; change_pct: number | string; lead_stock: string }
export interface Blocks { total: number; boards: Board[]; concept_tags: string[] }
export interface HotConcept { concept: string; bk: string; hit: number }
export interface QaRow { company: string; question: string; answer: string | null; answerer: string; ask_time: string }
export interface IndustryRow { rank: number; name: string; change_pct: number; code: string; up_count: number; down_count: number }
export interface IndustryData { top: IndustryRow[]; bottom: IndustryRow[]; total: number }

// 全球市场（美股 / 港股，移植自 global-stock-data · 东财域内源）
export interface GlobalIndex {
  key: string; name: string; region: string;
  price: number | null; change_pct: number | null;
}
export interface GlobalQuote {
  code: string; name: string;
  price: number | null; open: number | null; high: number | null; low: number | null;
  prev_close: number | null; amount: number | null; mcap: number | null; change_pct: number | null;
}
export interface GlobalMetrics {
  report_date: string;
  revenue: number | null; revenue_yoy: number | null; net_profit: number | null;
  eps: number | null; roe: number | null; gross_margin: number | null;
  net_margin: number | null; debt_ratio: number | null;
}
export interface GlobalStock {
  code: string; name: string; market: string;
  quote: GlobalQuote; metrics: GlobalMetrics | null;
}
export interface HkCashflowItem { amount: number | null; yoy: number | null }
export interface HkCashflowPeriod {
  report_date: string; report: string | null;
  currency: string | null; account_standard: string | null;
  items: Record<string, HkCashflowItem>;
}
export interface HkCashflow {
  code: string; name: string; market: string;
  currency: string | null; item_order: string[]; periods: HkCashflowPeriod[];
}

export const api = {
  health: () => get<{ ok: boolean }>("/health"),
  indices: () => get<IndexQuote[]>("/indices"),
  marketOverview: () => get<MarketOverview>("/market/overview"),
  emotion: () => get<ShortTermEmotion>("/market/emotion"),
  turnoverTop: () => get<TurnoverTop>("/market/turnover-top"),
  globalIndices: () => get<GlobalIndex[]>("/global/indices"),
  globalStock: (symbol: string) => get<GlobalStock>(`/global/stock?symbol=${encodeURIComponent(symbol)}`),
  hkCashflow: (symbol: string) => get<HkCashflow>(`/global/hk/cashflow?symbol=${encodeURIComponent(symbol)}`),
  radar: (signal?: AbortSignal) => get<RadarData>("/radar", signal),
  radarRefresh: (signal?: AbortSignal) => request<unknown>("/radar/refresh", "POST", undefined, signal).then(shapeNewsPipelineStarted),
  marketNewsEvents: (query: MarketNewsQuery, signal?: AbortSignal) => get<unknown>(marketNewsPath("/market-news/events", query), signal).then(shapeMarketNewsResponse),
  marketNewsRefresh: (query?: MarketNewsQuery, signal?: AbortSignal) => request<unknown>(
    query ? marketNewsPath("/market-news/refresh", query) : "/market-news/refresh",
    "POST",
    undefined,
    signal,
  ).then(shapeNewsPipelineStarted),
  newsPipelineStatus: (runId?: string, signal?: AbortSignal) => get<unknown>(
    `/news/pipeline-status${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`,
    signal,
  ).then(shapeNewsPipelineStatus),
  marketNewsRetrySource: (sourceId: string, query: MarketNewsQuery) => request<MarketNewsSourceRetryResult>(
    marketNewsPath(`/market-news/sources/${encodeURIComponent(sourceId)}/retry`, query),
    "POST",
  ),
  marketNewsTranslations: (payload: {
    items: Array<{ event_id: string; title: string; summary: string; source_language: string }>;
    llm: LlmConfig | null;
  }) => request<MarketNewsTranslationResponse>("/market-news/translations", "POST", payload),
  marketNewsEvent: (eventId: string, snapshotId?: string) => get<MarketNewsEvent>(
    `/market-news/events/${encodeURIComponent(eventId)}${snapshotId ? `?snapshot_id=${encodeURIComponent(snapshotId)}` : ""}`,
  ),
  evidenceSummary: (signal?: AbortSignal) => get<EvidenceSummaryData>("/evidence/summary", signal),
  evidenceEvents: (query: EvidenceEventQuery = {}, signal?: AbortSignal) => get<EvidenceEventList>(evidenceEventsPath(query), signal),
  evidenceEvent: (eventId: string) => get<EvidenceEventDetail>(`/evidence/events/${encodeURIComponent(eventId)}`),
  evidenceRefresh: (signal?: AbortSignal) => request<unknown>("/evidence/refresh", "POST", undefined, signal).then(shapeNewsPipelineStarted),
  cacheStatus: () => get<CacheStatus>("/cache/status"),
  cacheCleanupExpired: () => request<CacheCleanupResult>("/cache/cleanup-expired", "POST"),
  sourceHealthSummary: () => get<SourceHealthSummaryData>("/source-health/summary"),
  sourceHealthSources: () => get<SourceHealthSource[]>("/source-health/sources"),
  sourceHealthStartFullRun: () => request<SourceHealthRunStarted>("/source-health/runs", "POST", { scope: "full" }),
  sourceHealthRun: (runId: string) => get<SourceHealthRun>(`/source-health/runs/${encodeURIComponent(runId)}`),
  dataSourceCatalog: () => get<DataSourceCatalogResponse>("/data-sources/catalog"),
  dataSourceFamilies: () => get<SourceFamilyView[]>("/data-sources/families"),
  dataSourceFamily: (familyId: string) => get<SourceFamilyView>(`/data-sources/families/${encodeURIComponent(familyId)}`),
  dataSourceRefresh: () => request<SourceHealthRunStarted>("/data-sources/refresh", "POST"),
  dataSourceConfig: () => request<unknown>("/data-sources/config").then(dataSourceConfiguration),
  dataSourceUpdateFreeOnly: (freeOnly: boolean) => request<unknown>("/data-sources/config", "PUT", { free_only: freeOnly }).then(dataSourceConfiguration),
  dataSourceUpdateAdapterConfig: (adapterId: string, updates: AdapterConfigUpdate) => request<unknown>(`/data-sources/${encodeURIComponent(adapterId)}/config`, "PUT", updates).then(adapterConfigMutation),
  dataSourcePutCredential: (adapterId: string, credential: string) => request<unknown>(`/data-sources/${encodeURIComponent(adapterId)}/credentials`, "PUT", { credential }).then(credentialState),
  dataSourceDeleteCredential: (adapterId: string) => request<unknown>(`/data-sources/${encodeURIComponent(adapterId)}/credentials`, "DELETE").then(credentialState),
  dataSourceUsage: (adapterId?: string) => request<unknown>(`/data-sources/usage${adapterId ? `?adapter_id=${encodeURIComponent(adapterId)}` : ""}`).then(dataSourceUsage),
  dataSourceCost: (adapterId?: string) => request<unknown>(`/data-sources/cost${adapterId ? `?adapter_id=${encodeURIComponent(adapterId)}` : ""}`).then(dataSourceCost),
  dataSourceEnable: (adapterId: string, confirmPaidUsage: boolean) => request<unknown>(`/data-sources/${encodeURIComponent(adapterId)}/enable`, "POST", { confirm_paid_usage: confirmPaidUsage }).then(adapterAction),
  dataSourceDisable: (adapterId: string) => request<unknown>(`/data-sources/${encodeURIComponent(adapterId)}/disable`, "POST", {}).then(adapterAction),
  dataSourceValidate: (adapterId: string) => request<unknown>(`/data-sources/${encodeURIComponent(adapterId)}/validate`, "POST", {}).then(adapterAction),
  portfolio: () => get<PortfolioData>("/portfolio"),
  addHolding: (code: string, shares: number, cost: number) => request<PortfolioData>("/portfolio/holding", "POST", { code, shares, cost }),
  removeHolding: (code: string) => request<PortfolioData>(`/portfolio/holding?code=${code}`, "DELETE"),
  refreshPortfolio: () => request<PortfolioData>("/portfolio/refresh", "POST"),
  closePosition: (code: string, date: string, price: number, shares: number, cost: number) =>
    request<PortfolioData>("/portfolio/close", "POST", { code, date, price, shares, cost }),
  removeClosed: (index: number) => request<PortfolioData>(`/portfolio/close?index=${index}`, "DELETE"),
  fundPortfolio: () => get<FundPortfolioData>("/fund-portfolio"),
  upsertFundHolding: (holding: FundHoldingInput) => request<FundPortfolioData>("/fund-portfolio/holding", "POST", holding),
  deleteFundHolding: (code: string) => request<FundPortfolioData>(`/fund-portfolio/holding?code=${encodeURIComponent(code)}`, "DELETE"),
  fundPortfolioAnalysis: () => get<FundPortfolioAnalysisData>("/fund-portfolio/analysis"),
  searchFunds: (query: string) => get<DataSection<FundSearchResult[]>>(`/funds/search?q=${encodeURIComponent(query)}`),
  fundAnalysis: (code: string) => get<FundAnalysis>(`/funds/${encodeURIComponent(code)}/analysis`),
  refreshFund: (code: string) => request<FundAnalysis>(`/funds/${encodeURIComponent(code)}/refresh`, "POST"),
  valuation: (code: string) => get<Valuation>(`/valuation?code=${code}`),
  percentile: (code: string) => get<ValPercentile>(`/valuation/percentile?code=${code}`),
  financials: (code: string) => get<Financials>(`/financials?code=${code}`),
  announcements: (code: string) => get<Announcement[]>(`/announcements?code=${code}`),
  quote: (codes: string) => get<Record<string, Quote>>(`/quote?codes=${codes}`),
  reports: (code: string) => get<Report[]>(`/reports?code=${code}`),
  news: (code: string) => get<NewsItem[]>(`/news?code=${code}`),
  margin: (code: string) => get<MarginRow[]>(`/margin?code=${code}`),
  blockTrade: (code: string) => get<BlockTradeRow[]>(`/block-trade?code=${code}`),
  holders: (code: string) => get<HolderRow[]>(`/holders?code=${code}`),
  dividend: (code: string) => get<DividendRow[]>(`/dividend?code=${code}`),
  fundFlow: (code: string) => get<FundFlowRow[]>(`/fund-flow?code=${code}`),
  dragonTiger: (code: string) => get<DragonTiger>(`/dragon-tiger?code=${code}`),
  lockup: (code: string) => get<Lockup>(`/lockup?code=${code}`),
  blocks: (code: string) => get<Blocks>(`/blocks?code=${code}`),
  hotConcepts: (code: string) => get<HotConcept[]>(`/hot-concepts?code=${code}`),
  investorQa: (code: string) => get<QaRow[]>(`/investor-qa?code=${code}`),
  industry: (top = 20) => get<IndustryData>(`/industry?top=${top}`),
  myReports: () => get<MyReport[]>("/myreports"),
  uploadReport: (name: string, contentB64: string) =>
    request<MyReport>("/myreports", "POST", { name, content_b64: contentB64 }),
  deleteReport: (id: string) => request<{ ok: boolean }>(`/myreports/${id}`, "DELETE"),
};
