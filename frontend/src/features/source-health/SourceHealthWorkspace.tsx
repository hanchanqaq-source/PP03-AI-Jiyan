import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Loader2, Plus, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { SourceHealthRating, SourceHealthSource } from "./types";
import { SourceHealthSummary } from "./SourceHealthSummary";
import { SourceCatalogWorkspace } from "@/features/source-catalog/SourceCatalogWorkspace";

type GroupFilter = "all" | "fund" | "news" | "official" | "candidate";
type RatingFilter = "all" | SourceHealthRating;
type SortKey = "status" | "last_success" | "latency";
type LifecycleState = "候选" | "检测中" | "待验证" | "可启用" | "已启用" | "降级" | "已停用";

interface ProviderAggregate {
  key: string;
  name: string;
  rating: SourceHealthRating;
  healthy: number;
  failed: number;
  lastSuccessAt: string | null;
  averageLatencyMs: number;
  sources: SourceHealthSource[];
}

interface CandidateSource {
  id: string;
  name: string;
  url: string;
  direction: string;
  language: string;
  region: string;
  note: string;
  lifecycle: LifecycleState;
}

const ratingLabels: Record<SourceHealthRating, string> = {
  healthy: "健康", usable: "基本可用", degraded: "降级", failed: "失败",
};

const ratingSeverity: Record<SourceHealthRating, number> = {
  healthy: 0, usable: 1, degraded: 2, failed: 3,
};

const ratingTone: Record<SourceHealthRating, string> = {
  healthy: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300",
  usable: "border-sky-400/40 bg-sky-400/10 text-sky-300",
  degraded: "border-amber-400/45 bg-amber-400/10 text-amber-300",
  failed: "border-red-400/45 bg-red-400/10 text-red-300",
};

const providerLabels: Record<string, string> = {
  "akshare-eastmoney": "AKShare / 东方财富基金档案",
  "akshare-danjuan": "AKShare / 蛋卷基金档案",
  "eastmoney-direct": "东方财富直连",
  "tencent-quote": "腾讯行情",
  "cninfo-industry": "巨潮资讯行业分类",
};

const capabilityLabels: Record<string, string> = {
  search: "基金搜索",
  profile: "基金档案",
  nav_history: "净值历史",
  holdings: "持仓披露",
  stock_snapshot: "行情快照",
  industry_allocation: "行业配置",
  stock_industry_classification: "行业分类",
  feed: "资讯 Feed",
};

const repairLabels: Record<string, string> = {
  none: "无需修复", immediate_fix: "立即修复", worth_fixing: "值得修复", observe: "继续观察",
  replace_candidate: "建议替换", disable_candidate: "建议停用",
};

const cacheLabels: Record<string, string> = {
  not_used: "未使用缓存", realtime: "实时结果", cache: "完整缓存", partial: "部分缓存", stale: "过期缓存",
};

const errorLabels: Record<string, string> = {
  none: "无", timeout: "超时", dns: "DNS 解析失败", tls: "TLS 失败", connection: "连接失败",
  http: "HTTP 错误", redirect: "重定向异常", rate_limit: "访问频率受限", authentication: "鉴权失败",
  parse: "解析失败", empty_payload: "返回为空", schema_changed: "字段结构变化", stale_data: "数据过期", unknown: "未知错误",
};

const lifecycleStates: LifecycleState[] = ["候选", "检测中", "待验证", "可启用", "已启用", "降级", "已停用"];

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "暂无记录";
}

function formatFreshness(value: number | null): string {
  if (value == null) return "暂无记录";
  if (value < 60) return value + " 秒";
  if (value < 3600) return Math.round(value / 60) + " 分钟";
  if (value < 86400) return Math.round(value / 3600) + " 小时";
  return Math.round(value / 86400) + " 天";
}

function providerDisplayName(value: string): string {
  return providerLabels[value] || value;
}

function capabilityLabel(value: string): string {
  return capabilityLabels[value] || value;
}

function aggregateProviders(rows: SourceHealthSource[]): ProviderAggregate[] {
  const grouped = new Map<string, SourceHealthSource[]>();
  for (const row of rows) grouped.set(row.source_name, [...(grouped.get(row.source_name) || []), row]);
  return [...grouped.entries()].map(([key, providerRows]) => {
    const rating = providerRows.reduce<SourceHealthRating>((worst, row) => ratingSeverity[row.rating] > ratingSeverity[worst] ? row.rating : worst, "healthy");
    const successTimes = providerRows.map((row) => row.last_success_at).filter((value): value is string => Boolean(value)).sort();
    const lastSuccessAt = successTimes[successTimes.length - 1] || null;
    return {
      key,
      name: providerDisplayName(key),
      rating,
      healthy: providerRows.filter((row) => row.rating === "healthy").length,
      failed: providerRows.filter((row) => row.rating === "failed").length,
      lastSuccessAt,
      averageLatencyMs: Math.round(providerRows.reduce((total, row) => total + row.latency_ms, 0) / providerRows.length),
      sources: [...providerRows].sort((left, right) => capabilityLabel(left.capability).localeCompare(capabilityLabel(right.capability), "zh-CN")),
    };
  });
}

function SourceRatingBadge({ rating, prefix = "状态" }: { rating: SourceHealthRating; prefix?: string }) {
  return <span className={cn("rounded-full border px-2 py-1 text-[11px] font-semibold", ratingTone[rating])}>{prefix}：{ratingLabels[rating]}</span>;
}

function SourceDetailDrawer({ source, onClose }: { source: SourceHealthSource | null; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!source) return;
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose, source]);
  if (!source) return null;
  const currentTime = formatTime(source.finished_at);
  const lastSuccess = formatTime(source.last_success_at);
  return <div className="fixed inset-0 z-50 bg-black/65" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <aside role="dialog" aria-modal="true" aria-label="单来源健康详情" className="ml-auto flex h-full w-full max-w-xl flex-col border-l border-primary/25 bg-background/95 shadow-2xl backdrop-blur-xl">
      <header className="flex items-start justify-between gap-3 border-b border-border/60 px-5 py-4">
        <div><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">单来源健康详情</p><h2 className="mt-1 text-xl font-bold">{source.source_name}</h2><p className="mt-1 break-all text-xs text-muted-foreground">{source.source_id}</p></div>
        <button ref={closeRef} onClick={onClose} aria-label="关闭单来源健康详情" className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-5 w-5" /></button>
      </header>
      <div className="flex-1 space-y-5 overflow-y-auto p-5 text-sm">
        <section className="grid gap-3 rounded-xl border border-border/55 bg-muted/10 p-4 sm:grid-cols-2">
          <p>类型与能力：{source.group === "news" ? "资讯来源" : "基金与行情 Provider"} / {capabilityLabel(source.capability)}</p>
          <p>当前状态：{ratingLabels[source.rating]}</p>
          <p>最近成功：{lastSuccess}</p><p>连续失败：{source.consecutive_failures}</p>
          <p>响应时间：{source.latency_ms} ms</p><p>字段完整率：{source.field_completeness_pct == null ? "暂无记录" : source.field_completeness_pct + "%"}</p>
          <p>数据新鲜度：{formatFreshness(source.freshness_seconds)}</p><p>缓存状态：{cacheLabels[source.cache_status] || source.cache_status || "暂无记录"}</p>
          <p>备用来源：{source.fallback_available ? "可用" : "未确认"}</p><p>返回条数：{source.returned_items}</p>
        </section>
        <section><h3 className="font-semibold">公开地址</h3><p className="mt-2 break-all rounded-lg border border-border/50 bg-background/45 p-3 text-xs text-muted-foreground">{source.final_reference || "暂无公开地址"}</p></section>
        <section className="grid gap-3 sm:grid-cols-2"><div><h3 className="font-semibold">脱敏错误原因</h3><p className="mt-2 text-xs text-muted-foreground">{errorLabels[source.error_type] || "未知错误"} · {source.error_message_redacted || "暂无公开错误说明"}</p></div><div><h3 className="font-semibold">修复建议</h3><p className="mt-2 text-xs text-muted-foreground">{repairLabels[source.repair_value] || source.repair_value || "暂无建议"} · {source.repair_reason || "继续观察公开来源"}</p></div></section>
        <section className="rounded-xl border border-primary/20 bg-primary/5 p-4"><h3 className="font-semibold">最近健康历史（当前快照）</h3><p className="mt-1 text-[11px] text-muted-foreground">A1 API 当前只返回本次探测与最近成功时间，不扩写为完整历史。</p><ol className="mt-3 space-y-2 text-xs text-muted-foreground"><li>{currentTime} · 当前探测：{ratingLabels[source.rating]}</li>{source.last_success_at && source.last_success_at !== source.finished_at && <li>{lastSuccess} · 最近成功</li>}</ol></section>
      </div>
    </aside>
  </div>;
}

function AddSourceDrawer({ open, onClose, onDetected }: { open: boolean; onClose: () => void; onDetected: (candidate: CandidateSource) => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const timerRef = useRef<number | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [direction, setDirection] = useState("general");
  const [language, setLanguage] = useState("zh-CN");
  const [region, setRegion] = useState("CN");
  const [note, setNote] = useState("");
  const [stage, setStage] = useState<LifecycleState>("候选");
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => () => { if (timerRef.current != null) window.clearTimeout(timerRef.current); }, []);
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onClose(); return; }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      ) || []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose, open]);
  if (!open) return null;
  const detect = (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !url.trim()) { setMessage("请填写来源名称和网址或 Feed 地址。"); return; }
    setStage("检测中");
    setMessage("正在运行前端演示检测…");
    timerRef.current = window.setTimeout(() => {
      const candidate: CandidateSource = { id: "prototype-candidate", name: name.trim(), url: url.trim(), direction, language, region, note: note.trim(), lifecycle: "待验证" };
      setStage("待验证");
      setMessage("当前为前端原型，A1.1 后续阶段接入正式检测和保存。");
      onDetected(candidate);
    }, 300);
  };
  return <div className="fixed inset-0 z-50 bg-black/65" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <aside ref={dialogRef} role="dialog" aria-modal="true" aria-label="添加数据源原型" className="ml-auto flex h-full w-full max-w-xl flex-col border-l border-primary/25 bg-background/95 shadow-2xl backdrop-blur-xl">
      <header className="flex items-start justify-between border-b border-border/60 px-5 py-4"><div><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">A1.1-W0 · 前端原型</p><h2 className="mt-1 text-xl font-bold">添加数据源</h2><p className="mt-1 text-xs text-muted-foreground">不执行真实检测、保存或正式接入</p></div><button ref={closeRef} onClick={onClose} aria-label="关闭添加数据源" className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-5 w-5" /></button></header>
      <form onSubmit={detect} className="flex-1 space-y-5 overflow-y-auto p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-xs text-muted-foreground sm:col-span-2">来源名称<input aria-label="来源名称" value={name} onChange={(event) => setName(event.target.value)} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground" /></label>
          <label className="text-xs text-muted-foreground sm:col-span-2">网址或 Feed 地址<input aria-label="网址或 Feed 地址" value={url} onChange={(event) => setUrl(event.target.value)} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground" /></label>
          <label className="text-xs text-muted-foreground">所属方向<select aria-label="所属方向" value={direction} onChange={(event) => setDirection(event.target.value)} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"><option value="general">综合资讯</option><option value="semiconductor">半导体</option><option value="storage">存储</option><option value="robotics">机器人</option></select></label>
          <label className="text-xs text-muted-foreground">语言<select aria-label="语言" value={language} onChange={(event) => setLanguage(event.target.value)} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"><option value="zh-CN">简体中文</option><option value="en">English</option><option value="ja">日本語</option></select></label>
          <label className="text-xs text-muted-foreground">地区<select aria-label="地区" value={region} onChange={(event) => setRegion(event.target.value)} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"><option value="CN">中国大陆</option><option value="HK">中国香港</option><option value="GLOBAL">全球</option></select></label>
          <label className="text-xs text-muted-foreground sm:col-span-2">备注<textarea aria-label="备注" value={note} onChange={(event) => setNote(event.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground" /></label>
        </div>
        <section className="rounded-xl border border-border/55 bg-muted/10 p-4"><h3 className="text-sm font-semibold">未来接入流程</h3><p className="mt-2 text-xs text-muted-foreground">添加 → 自动检测 → 候选来源 → 健康检查 → 来源身份验证 → 启用</p><div className="mt-3 flex flex-wrap gap-2">{lifecycleStates.map((state) => <span key={state} className={cn("rounded-full border px-2 py-1 text-[11px]", state === stage ? "border-primary/60 bg-primary/15 font-semibold text-primary" : "border-border text-muted-foreground")}>{state}</span>)}</div></section>
        {message && <p role="status" className={cn("rounded-lg border px-3 py-2 text-xs", stage === "检测中" ? "border-sky-400/35 bg-sky-400/10 text-sky-300" : "border-primary/30 bg-primary/10 text-primary")}>{message}</p>}
        <button type="submit" disabled={stage === "检测中"} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50">{stage === "检测中" && <Loader2 className="h-4 w-4 animate-spin" />}检测来源</button>
      </form>
    </aside>
  </div>;
}

function sortProviders(rows: ProviderAggregate[], sort: SortKey): ProviderAggregate[] {
  return [...rows].sort((left, right) => {
    if (sort === "latency") return right.averageLatencyMs - left.averageLatencyMs;
    if (sort === "last_success") return (right.lastSuccessAt || "").localeCompare(left.lastSuccessAt || "");
    return ratingSeverity[right.rating] - ratingSeverity[left.rating] || left.name.localeCompare(right.name, "zh-CN");
  });
}

function sortSources(rows: SourceHealthSource[], sort: SortKey): SourceHealthSource[] {
  return [...rows].sort((left, right) => {
    if (sort === "latency") return right.latency_ms - left.latency_ms;
    if (sort === "last_success") return (right.last_success_at || "").localeCompare(left.last_success_at || "");
    return ratingSeverity[right.rating] - ratingSeverity[left.rating] || left.source_name.localeCompare(right.source_name, "zh-CN");
  });
}

export function SourceHealthWorkspace() {
  const [sources, setSources] = useState<SourceHealthSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [group, setGroup] = useState<GroupFilter>("all");
  const [rating, setRating] = useState<RatingFilter>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("status");
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<SourceHealthSource | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [candidates, setCandidates] = useState<CandidateSource[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [catalogUnavailable, setCatalogUnavailable] = useState(false);
  const detailTriggerRef = useRef<HTMLButtonElement | null>(null);
  const addTriggerRef = useRef<HTMLButtonElement | null>(null);

  const loadSources = useCallback(async () => {
    setLoading(true);
    setError(false);
    try { setSources(await api.sourceHealthSources()); } catch { setError(true); } finally { setLoading(false); }
  }, []);
  // Catalog owns registration and list rendering. Legacy source-health clients remain
  // available for compatibility consumers, but this workspace no longer treats them as a registry.
  useEffect(() => { if (catalogUnavailable) void loadSources(); }, [catalogUnavailable, loadSources, refreshToken]);

  const normalizedSearch = search.trim().toLocaleLowerCase("zh-CN");
  const providers = useMemo(() => sortProviders(aggregateProviders(sources.filter((row) => row.group !== "news")).filter((provider) => {
    const matchesSearch = !normalizedSearch || provider.name.toLocaleLowerCase("zh-CN").includes(normalizedSearch) || provider.key.toLowerCase().includes(normalizedSearch) || provider.sources.some((row) => capabilityLabel(row.capability).includes(search.trim()));
    return matchesSearch && (rating === "all" || provider.rating === rating);
  }), sort), [normalizedSearch, rating, search, sort, sources]);
  const news = useMemo(() => sortSources(sources.filter((row) => row.group === "news" && (!normalizedSearch || row.source_name.toLocaleLowerCase("zh-CN").includes(normalizedSearch)) && (rating === "all" || row.rating === rating)), sort), [normalizedSearch, rating, sort, sources]);
  const visibleCandidates = candidates.filter((candidate) => rating === "all" && (!normalizedSearch || candidate.name.toLocaleLowerCase("zh-CN").includes(normalizedSearch)));
  const showProviders = group === "all" || group === "fund";
  const showNews = group === "all" || group === "news";
  const showCandidates = group === "all" || group === "candidate";

  const openDetail = (source: SourceHealthSource, trigger: HTMLButtonElement) => { detailTriggerRef.current = trigger; setSelected(source); };
  const closeDetail = () => { setSelected(null); detailTriggerRef.current?.focus(); };
  const closeAdd = useCallback(() => { setAddOpen(false); addTriggerRef.current?.focus(); }, []);
  const handleCatalogUnavailable = useCallback(() => setCatalogUnavailable(true), []);
  const toggleProvider = (key: string) => setExpandedProviders((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  const addCandidate = (candidate: CandidateSource) => setCandidates((current) => [...current.filter((row) => row.id !== candidate.id), candidate]);

  if (!catalogUnavailable) return <div className="mt-4 space-y-4">
    <SourceHealthSummary variant="bar" refreshToken={refreshToken} onUpdated={() => setRefreshToken((value) => value + 1)} />
    <SourceCatalogWorkspace onCatalogUnavailable={handleCatalogUnavailable} />
  </div>;
  return <div className="mt-4 space-y-4">
    <SourceHealthSummary variant="bar" refreshToken={refreshToken} onUpdated={() => setRefreshToken((value) => value + 1)} />

    <section className="rounded-xl border border-border/60 bg-background/45" aria-label="完整数据源清单">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border/55 p-4"><div><h2 className="font-semibold">来源库</h2><p className="mt-1 text-xs text-muted-foreground">Provider 聚合展示；资讯来源保持一源一行。所有正式健康数据来自 A1 API。</p></div><button ref={addTriggerRef} onClick={() => setAddOpen(true)} aria-label="+ 添加数据源" className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground"><Plus className="h-4 w-4" />+ 添加数据源</button></header>
      <div className="space-y-3 border-b border-border/55 p-4">
        <div className="flex flex-wrap gap-2" aria-label="来源分组">{([["all", "全部来源"], ["fund", "基金与行情"], ["news", "资讯来源"], ["official", "官方证据"], ["candidate", "候选来源"]] as Array<[GroupFilter, string]>).map(([value, label]) => <button key={value} onClick={() => setGroup(value)} aria-pressed={group === value} className={cn("rounded-lg border px-3 py-1.5 text-xs", group === value ? "border-primary/55 bg-primary/15 font-semibold text-primary" : "border-border text-muted-foreground hover:text-foreground")}>{label}</button>)}</div>
        <div className="flex flex-wrap items-center gap-2" aria-label="健康状态筛选">{([["all", "全部"], ["healthy", "健康"], ["usable", "基本可用"], ["degraded", "降级"], ["failed", "失败"]] as Array<[RatingFilter, string]>).map(([value, label]) => <button key={value} onClick={() => setRating(value)} aria-pressed={rating === value} aria-label={"按状态筛选 " + label} className={cn("rounded-lg px-2.5 py-1 text-xs", rating === value ? "bg-muted font-semibold text-foreground" : "text-muted-foreground hover:bg-muted/50")}>{label}</button>)}</div>
        <div className="flex flex-wrap items-center gap-3"><label className="relative min-w-[240px] flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><input type="search" aria-label="来源名称搜索" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索来源名称" className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground" /></label><label className="text-xs text-muted-foreground">排序<select aria-label="来源排序" value={sort} onChange={(event) => setSort(event.target.value as SortKey)} className="ml-2 rounded-lg border border-border bg-background px-2 py-2 text-foreground"><option value="status">健康状态（异常优先）</option><option value="last_success">最近成功（最近优先）</option><option value="latency">响应时间（最慢优先）</option></select></label></div>
      </div>

      <div className="space-y-6 p-4">
        {notice && <p role="status" className="rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-xs text-primary">{notice}</p>}
        {loading && <p className="inline-flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取完整数据源清单…</p>}
        {error && <p role="alert" className="text-sm text-warning">来源库加载失败，已保留页面结构但不展示虚假来源。</p>}
        {!loading && !error && group === "official" && <p className="rounded-xl border border-warning/25 bg-warning/5 p-4 text-sm text-warning">A1 健康 API 未提供来源身份验证字段，当前不把普通来源标记为官方证据。</p>}

        {!loading && !error && showProviders && <section><h3 className="mb-3 text-sm font-semibold">基金与行情 Provider</h3><div className="space-y-3">{providers.length ? providers.map((provider) => {
          const expanded = expandedProviders.has(provider.key);
          return <article key={provider.key} aria-label={"Provider " + provider.name} className="rounded-xl border border-border/55 bg-muted/10 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h4 className="font-semibold">{provider.name}</h4><div className="mt-2 flex flex-wrap gap-1.5">{provider.sources.map((row) => <span key={row.source_id} className="rounded border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground">{capabilityLabel(row.capability)}</span>)}</div></div><SourceRatingBadge rating={provider.rating} prefix="总体状态" /></div><div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-5"><p>健康能力：{provider.healthy}</p><p>失败能力：{provider.failed}</p><p>最近成功：{formatTime(provider.lastSuccessAt)}</p><p>平均响应：{provider.averageLatencyMs} ms</p><button onClick={() => toggleProvider(provider.key)} aria-expanded={expanded} aria-label={(expanded ? "收起能力 " : "展开能力 ") + provider.name} className="inline-flex items-center justify-end gap-1 font-medium text-primary">{expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}{expanded ? "收起能力" : "展开能力"}</button></div>{expanded && <div className="mt-4 space-y-2 border-t border-border/50 pt-3">{provider.sources.map((source) => <div key={source.source_id} className="grid gap-2 rounded-lg border border-border/45 bg-background/45 p-3 text-xs sm:grid-cols-[minmax(120px,1fr)_auto_auto_auto]"><div><p className="font-medium text-foreground">{capabilityLabel(source.capability)}</p><p className="mt-1 text-muted-foreground">能力状态：{ratingLabels[source.rating]}</p></div><p className="self-center text-muted-foreground">最近成功：{formatTime(source.last_success_at)}</p><p className="self-center text-muted-foreground">{source.latency_ms} ms</p><button onClick={(event) => openDetail(source, event.currentTarget)} aria-label={"查看能力详情 " + capabilityLabel(source.capability) + " " + provider.name} className="self-center rounded-lg border border-primary/40 px-2 py-1 text-primary">查看详情</button></div>)}</div>}</article>;
        }) : <p className="text-xs text-muted-foreground">当前筛选没有基金与行情 Provider。</p>}</div></section>}

        {!loading && !error && showNews && <section><h3 className="mb-3 text-sm font-semibold">资讯来源</h3>{news.length ? <div className="overflow-x-auto"><table className="min-w-[1180px] w-full text-left text-xs"><thead className="text-muted-foreground"><tr><th className="pb-2 pr-3">来源名称</th><th>来源类型</th><th>所属赛道</th><th>当前状态</th><th>最近成功</th><th>响应时间</th><th>数据新鲜度</th><th>返回条数</th><th>修复价值</th><th className="text-right">操作</th></tr></thead><tbody>{news.map((source) => <tr key={source.source_id} className="border-t border-border/45"><td className="py-3 pr-3 font-medium text-foreground">{source.source_name}</td><td>{capabilityLabel(source.capability)}</td><td>未提供</td><td><SourceRatingBadge rating={source.rating} /></td><td>{formatTime(source.last_success_at)}</td><td>{source.latency_ms} ms</td><td>{formatFreshness(source.freshness_seconds)}</td><td>{source.returned_items}</td><td>{repairLabels[source.repair_value] || source.repair_value || "暂无建议"}</td><td><div className="flex justify-end gap-2"><button onClick={(event) => openDetail(source, event.currentTarget)} aria-label={"查看详情 " + source.source_name} className="rounded-lg border border-primary/40 px-2 py-1 text-primary">查看详情</button><button onClick={() => setNotice("单源重试当前为前端原型，未发起网络请求 · " + source.source_name)} aria-label={"单源重试 " + source.source_name} className="rounded-lg border border-border px-2 py-1 text-muted-foreground">单源重试</button></div></td></tr>)}</tbody></table></div> : <p className="text-xs text-muted-foreground">当前筛选没有资讯来源。</p>}</section>}

        {!loading && !error && showCandidates && (group === "candidate" || visibleCandidates.length > 0) && <section><h3 className="mb-3 text-sm font-semibold">候选来源</h3><div className="space-y-3">{visibleCandidates.length ? visibleCandidates.map((candidate) => <article key={candidate.id} aria-label={"候选来源 " + candidate.name} className="rounded-xl border border-dashed border-sky-400/35 bg-sky-400/5 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h4 className="font-semibold">{candidate.name}</h4><p className="mt-1 break-all text-xs text-muted-foreground">{candidate.url}</p><p className="mt-2 text-xs text-warning">前端原型 · 未进入正式资讯流</p></div><span className="rounded-full border border-sky-400/40 bg-sky-400/10 px-2 py-1 text-xs text-sky-300">{candidate.lifecycle}</span></div></article>) : <p className="text-xs text-muted-foreground">尚无前端候选来源。可通过“+ 添加数据源”创建演示候选。</p>}</div></section>}
      </div>
    </section>

    <SourceDetailDrawer source={selected} onClose={closeDetail} />
    <AddSourceDrawer open={addOpen} onClose={closeAdd} onDetected={addCandidate} />
  </div>;
}
