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

async function request<T>(path: string, method: "GET" | "POST" | "PUT" | "DELETE" = "GET", body?: unknown): Promise<T> {
  let resp: Response;
  const headers: Record<string, string> = { ...authHeaders() };
  const opts: RequestInit = { method };
  if (method !== "GET" && path.startsWith("/data-sources/")) {
    headers["X-PP03-Write-Intent"] = "1";
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (Object.keys(headers).length > 0) opts.headers = headers;
  try {
    resp = await fetch(`/api${path}`, opts);
  } catch {
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

const get = <T>(path: string) => request<T>(path, "GET");

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
  radar: () => get<RadarData>("/radar"),
  radarRefresh: () => request<RadarData>("/radar/refresh", "POST"),
  marketNewsEvents: (query: MarketNewsQuery) => get<MarketNewsResponse>(marketNewsPath("/market-news/events", query)),
  marketNewsRefresh: (query: MarketNewsQuery) => request<MarketNewsResponse>(marketNewsPath("/market-news/refresh", query), "POST"),
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
  evidenceSummary: () => get<EvidenceSummaryData>("/evidence/summary"),
  evidenceEvents: (query: EvidenceEventQuery = {}) => get<EvidenceEventList>(evidenceEventsPath(query)),
  evidenceEvent: (eventId: string) => get<EvidenceEventDetail>(`/evidence/events/${encodeURIComponent(eventId)}`),
  evidenceRefresh: () => request<EvidenceSummaryData>("/evidence/refresh", "POST"),
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
