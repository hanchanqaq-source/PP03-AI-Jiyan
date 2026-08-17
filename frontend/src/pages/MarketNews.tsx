import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Database, Loader2, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataInfoDialog } from "@/features/market-news/DataInfoDialog";
import { EventCard } from "@/features/market-news/EventCard";
import { EventDetailDrawer } from "@/features/market-news/EventDetailDrawer";
import { MarketNewsSidebar } from "@/features/market-news/MarketNewsSidebar";
import type {
  MarketNewsCategoryFilter,
  MarketNewsEvent,
  MarketNewsMode,
  MarketNewsQuery,
  MarketNewsResponse,
  MarketNewsSort,
  MarketNewsTranslation,
} from "@/features/market-news/types";
import { SelectedTagBar } from "@/features/tags/SelectedTagBar";
import { TagSelector } from "@/features/tags/TagSelector";
import { usePageTags } from "@/features/tags/usePageTags";
import { api } from "@/lib/api";
import { loadLlm } from "@/lib/llm";
import { cn } from "@/lib/utils";

const MODES: Array<{ value: MarketNewsMode; label: string }> = [
  { value: "my_focus", label: "我的关注" },
  { value: "my_holdings", label: "我的持仓" },
  { value: "global_tech", label: "全球科技" },
  { value: "domestic_policy", label: "国内政策" },
];

const CATEGORIES: Array<{ value: MarketNewsCategoryFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "policy", label: "政策" },
  { value: "industry", label: "产业" },
  { value: "company", label: "公司" },
  { value: "fund_notice", label: "基金公告" },
  { value: "deep_content", label: "深度内容" },
];

const WINDOWS = [
  { value: 1, label: "今天" },
  { value: 3, label: "3天" },
  { value: 7, label: "7天" },
  { value: 30, label: "30天" },
] as const;

const SORTS: Array<{ value: MarketNewsSort; label: string }> = [
  { value: "importance", label: "重要度" },
  { value: "latest", label: "最新时间" },
  { value: "holding_relevance", label: "持仓相关性" },
];

const STATUS_LABELS: Record<string, string> = {
  realtime: "实时抓取",
  cache: "缓存数据",
  stale: "过期缓存",
  partial: "部分来源可用",
  source_failure: "来源失败",
};

const MARKET_NEWS_CACHE_LIMIT = 12;

export function cacheMarketNewsResponse(
  cache: Map<string, MarketNewsResponse>,
  queryKey: string,
  response: MarketNewsResponse,
): void {
  cache.delete(queryKey);
  cache.set(queryKey, response);
  while (cache.size > MARKET_NEWS_CACHE_LIMIT) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey === undefined) break;
    cache.delete(oldestKey);
  }
}

export function readMarketNewsCache(
  cache: Map<string, MarketNewsResponse>,
  queryKey: string,
): MarketNewsResponse | null {
  const response = cache.get(queryKey) || null;
  if (response) {
    cache.delete(queryKey);
    cache.set(queryKey, response);
  }
  return response;
}

function normalizeQuery(query: MarketNewsQuery): MarketNewsQuery {
  return { ...query, tag_ids: [...query.tag_ids].sort() };
}

function marketNewsQueryKey(query: MarketNewsQuery): string {
  const normalized = normalizeQuery(query);
  return [normalized.mode, normalized.tag_ids.join(","), normalized.category, normalized.days, normalized.sort].join("|");
}

function responseMatchesQuery(response: MarketNewsResponse, query: MarketNewsQuery): boolean {
  return marketNewsQueryKey(response.filters) === marketNewsQueryKey(query);
}

function sourceLanguage(event: MarketNewsEvent): string {
  return event.sources.find((source) => source.language && !source.language.toLowerCase().startsWith("zh"))?.language
    || event.sources[0]?.language
    || "unknown";
}

function needsTranslation(event: MarketNewsEvent): boolean {
  const language = sourceLanguage(event).toLowerCase();
  return !language.startsWith("zh") && !/[\u3400-\u9fff]/.test(`${event.title} ${event.summary}`);
}

function mergeTranslations(response: MarketNewsResponse, translations: MarketNewsTranslation[]): MarketNewsResponse {
  const byId = new Map(translations.map((translation) => [translation.event_id, translation]));
  const mergeEvent = (event: MarketNewsEvent): MarketNewsEvent => {
    const translation = byId.get(event.event_id);
    return translation ? { ...event, ...translation } : event;
  };
  return {
    ...response,
    events: response.events.map(mergeEvent),
    focus_events: response.focus_events.map(mergeEvent),
  };
}

function EmptyState({ reason, onAddTag }: { reason: MarketNewsResponse["empty_reason"]; onAddTag: () => void }) {
  if (reason === "no_tags") {
    return <div className="py-16 text-center"><p className="font-semibold">还没有选择关注行业</p><p className="mt-2 text-sm text-muted-foreground">添加半导体、存储、机器人、医疗等标签后开始跟踪资讯</p><button onClick={onAddTag} className="mt-4 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground">添加标签</button></div>;
  }
  if (reason === "no_holdings") {
    return <div className="py-16 text-center"><p className="font-semibold">还没有基金持仓</p><p className="mt-2 text-sm text-muted-foreground">添加持仓后，系统会把基金公开重仓股与资讯关联</p></div>;
  }
  if (reason === "portfolio_error") {
    return <div className="py-16 text-center"><p className="font-semibold text-warning">持仓数据读取失败，暂无法计算关联</p><p className="mt-2 text-sm text-muted-foreground">资讯仍可查看；持仓关联与影响统计暂不可用。</p></div>;
  }
  return <div className="py-16 text-center"><p className="font-semibold">当前筛选暂无可靠资讯</p><p className="mt-2 text-sm text-muted-foreground">可以扩大时间范围、切换标签或刷新公开来源</p><p className="mt-1 text-xs text-muted-foreground">系统不会用 AI 生成新闻。</p></div>;
}

function QueryFailureState() {
  return <div className="py-16 text-center"><p className="font-semibold text-destructive">当前筛选加载失败</p><p className="mt-2 text-sm text-muted-foreground">该筛选还没有可回用的成功结果，请稍后重试。</p></div>;
}

export function MarketNews() {
  const tags = usePageTags("market_news");
  const [mode, setMode] = useState<MarketNewsMode>("my_focus");
  const [category, setCategory] = useState<MarketNewsCategoryFilter>("all");
  const [days, setDays] = useState<1 | 3 | 7 | 30>(7);
  const [sort, setSort] = useState<MarketNewsSort>("importance");
  const [view, setView] = useState<{ queryKey: string; response: MarketNewsResponse } | null>(null);
  const responseCacheRef = useRef<Map<string, MarketNewsResponse>>(new Map());
  const requestIdRef = useRef(0);
  const translationIdRef = useRef(0);
  const translationRequestedRef = useRef<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [queryError, setQueryError] = useState<{ queryKey: string; message: string } | null>(null);
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [detailSelection, setDetailSelection] = useState<{
    queryKey: string;
    snapshotId: string;
    event: MarketNewsEvent;
  } | null>(null);

  const query = useMemo<MarketNewsQuery>(() => normalizeQuery({
    mode,
    tag_ids: tags.activeTag ? [tags.activeTag.id] : [],
    category,
    days,
    sort,
  }), [category, days, mode, sort, tags.activeTag]);
  const queryKey = marketNewsQueryKey(query);
  const data = view?.queryKey === queryKey
    ? view.response
    : responseCacheRef.current.get(queryKey) || null;
  const detailEvent = detailSelection?.queryKey === queryKey && detailSelection.snapshotId === data?.snapshot_id
    ? data?.events.find((event) => event.event_id === detailSelection.event.event_id) || detailSelection.event
    : null;
  const activeQueryKeyRef = useRef(queryKey);
  const activeSnapshotRef = useRef(data?.snapshot_id || "");
  activeQueryKeyRef.current = queryKey;
  activeSnapshotRef.current = data?.snapshot_id || "";

  useEffect(() => {
    setDetailSelection(null);
  }, [queryKey]);

  useEffect(() => {
    const requestId = ++requestIdRef.current;
    const cached = readMarketNewsCache(responseCacheRef.current, queryKey);
    setRefreshing(false);
    if (mode === "my_focus" && query.tag_ids.length === 0) {
      setView(null);
      setLoading(false);
      setQueryError(null);
      return;
    }

    setView(cached ? { queryKey, response: cached } : null);
    setLoading(true);
    setQueryError(null);
    api.marketNewsEvents(query).then((response) => {
      if (requestId !== requestIdRef.current) return;
      if (!responseMatchesQuery(response, query)) throw new Error("market-news response filters mismatch");
      cacheMarketNewsResponse(responseCacheRef.current, queryKey, response);
      setView({ queryKey, response });
    }).catch(() => {
      if (requestId !== requestIdRef.current) return;
      const sameQueryCache = readMarketNewsCache(responseCacheRef.current, queryKey);
      setView(sameQueryCache ? { queryKey, response: sameQueryCache } : null);
      setQueryError({
        queryKey,
        message: sameQueryCache
          ? "当前筛选加载失败，继续显示该筛选上次成功结果。"
          : "当前筛选加载失败，请稍后重试。",
      });
    }).finally(() => {
      if (requestId === requestIdRef.current) setLoading(false);
    });
  }, [mode, query, queryKey]);

  useEffect(() => {
    if (!data) return;
    const candidates = data.events.filter(needsTranslation).slice(0, 20);
    if (!candidates.length) return;
    const translationKey = `${queryKey}|${data.snapshot_id}`;
    if (translationRequestedRef.current.has(translationKey)) return;
    translationRequestedRef.current.add(translationKey);
    if (translationRequestedRef.current.size > 48) {
      const oldest = translationRequestedRef.current.values().next().value;
      if (oldest) translationRequestedRef.current.delete(oldest);
    }
    const translationId = ++translationIdRef.current;
    const apply = (translations: MarketNewsTranslation[]) => {
      if (translationId !== translationIdRef.current) return;
      if (activeQueryKeyRef.current !== queryKey || activeSnapshotRef.current !== data.snapshot_id) return;
      const current = responseCacheRef.current.get(queryKey);
      if (!current || current.snapshot_id !== data.snapshot_id) return;
      const merged = mergeTranslations(current, translations);
      cacheMarketNewsResponse(responseCacheRef.current, queryKey, merged);
      setView({ queryKey, response: merged });
    };
    const unavailable = candidates.map<MarketNewsTranslation>((event) => ({
      event_id: event.event_id,
      translated_title_zh: null,
      translated_summary_zh: null,
      translation_status: "unavailable",
      translation_provider: null,
      translated_at: null,
    }));
    const llm = loadLlm();
    if (!llm) {
      apply(unavailable);
      return;
    }
    api.marketNewsTranslations({
      items: candidates.map((event) => ({
        event_id: event.event_id,
        title: event.title,
        summary: event.summary,
        source_language: sourceLanguage(event),
      })),
      llm,
    }).then((result) => apply(result.translations)).catch(() => apply(unavailable));
  }, [data?.snapshot_id, queryKey]);

  const refresh = async () => {
    if (refreshing || loading || (mode === "my_focus" && query.tag_ids.length === 0)) return;
    const requestId = ++requestIdRef.current;
    setRefreshing(true);
    setQueryError(null);
    try {
      const response = await api.marketNewsRefresh(query);
      if (requestId !== requestIdRef.current) return;
      if (!responseMatchesQuery(response, query)) throw new Error("market-news refresh filters mismatch");
      cacheMarketNewsResponse(responseCacheRef.current, queryKey, response);
      setView({ queryKey, response });
    } catch {
      if (requestId !== requestIdRef.current) return;
      const sameQueryCache = readMarketNewsCache(responseCacheRef.current, queryKey);
      setView(sameQueryCache ? { queryKey, response: sameQueryCache } : null);
      setQueryError({
        queryKey,
        message: sameQueryCache
          ? "当前筛选加载失败，继续显示该筛选上次成功结果。"
          : "当前筛选加载失败，请稍后重试。",
      });
    } finally {
      if (requestId === requestIdRef.current) setRefreshing(false);
    }
  };

  const noTags = mode === "my_focus" && query.tag_ids.length === 0;
  const error = queryError?.queryKey === queryKey ? queryError.message : null;
  const queryFailedWithoutCache = Boolean(error && !data && !noTags);
  const emptyReason = noTags ? "no_tags" : data?.empty_reason || (data && data.events.length === 0 ? "no_events" : null);
  const status = STATUS_LABELS[data?.data_status || ""] || "等待公开数据";

  return (
    <div>
      <PageHeader
        title="市场资讯"
        subtitle="把新闻、政策、公司公告和你的基金持仓关联起来"
        actions={<div className="flex items-center gap-2">
          <button onClick={() => setInfoOpen(true)} aria-label="数据说明" className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:border-primary/45 hover:text-foreground"><Database className="h-4 w-4" />数据说明</button>
          <button onClick={refresh} disabled={refreshing || loading || noTags} aria-label="刷新资讯" className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50">{refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}{refreshing ? "刷新中" : "刷新资讯"}</button>
        </div>}
      />

      <section className="sticky top-0 z-20 mb-4 border-b border-border/50 bg-background/95 pb-3 backdrop-blur">
        <div className="flex flex-wrap gap-1 pb-3" aria-label="资讯模式">
          {MODES.map((item) => <button key={item.value} onClick={() => setMode(item.value)} aria-pressed={mode === item.value} className={cn("rounded-lg px-3 py-1.5 text-sm", mode === item.value ? "bg-primary/15 font-semibold text-primary" : "text-muted-foreground hover:bg-muted/50 hover:text-foreground")}>{item.label}</button>)}
        </div>
        <SelectedTagBar tags={tags.tags} activeId={tags.state.activeId} onActivate={tags.activate} onRemove={tags.remove} onReorder={tags.reorder} onAdd={() => setSelectorOpen(true)} />
        <div className="mt-3 grid gap-2 lg:grid-cols-[1fr_auto_auto] lg:items-center">
          <div className="flex flex-wrap gap-1" aria-label="资讯分类">
            {CATEGORIES.map((item) => <button key={item.value} onClick={() => setCategory(item.value)} aria-pressed={category === item.value} className={cn("rounded-lg px-2.5 py-1 text-xs", category === item.value ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted/50")}>{item.label}</button>)}
          </div>
          <div className="flex gap-1" aria-label="时间范围">
            {WINDOWS.map((item) => <button key={item.value} onClick={() => setDays(item.value)} aria-pressed={days === item.value} className={cn("rounded-lg px-2.5 py-1 text-xs", days === item.value ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/50")}>{item.label}</button>)}
          </div>
          <div className="flex gap-1" aria-label="资讯排序">
            {SORTS.map((item) => <button key={item.value} onClick={() => setSort(item.value)} aria-pressed={sort === item.value} className={cn("rounded-lg px-2.5 py-1 text-xs", sort === item.value ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/50")}>{item.label}</button>)}
          </div>
        </div>
      </section>

      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-border/55 bg-muted/15 px-3 py-2 text-xs text-muted-foreground">
        <span>状态：{status}</span>
        {data && <span>{data.source_summary.total_sources} 个来源配置</span>}
        {data && data.source_summary.failed_sources > 0 && <span className="text-warning">{data.source_summary.failed_sources} 个来源失败</span>}
        {data?.ai_status === "unavailable" && <span>AI：摘要不可用</span>}
        {data?.generated_at && <span className="ml-auto">更新于 {new Date(data.generated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}</span>}
      </div>

      {error && <div role="alert" className="mb-4 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4 shrink-0" />{error}</div>}

      {queryFailedWithoutCache ? <div className="rounded-2xl border border-destructive/30 bg-destructive/5"><QueryFailureState /></div> : loading && !data && !noTags ? <div className="flex items-center justify-center gap-2 py-20 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取公开资讯与本地缓存</div> : emptyReason ? <div className="rounded-2xl border border-border/65 bg-background/55"><EmptyState reason={emptyReason} onAddTag={() => setSelectorOpen(true)} /></div> : data && <div className="grid gap-5 xl:grid-cols-[minmax(0,7fr)_minmax(280px,3fr)]">
        <main className="rounded-2xl border border-border/70 bg-gradient-to-b from-slate-950/55 to-background/45 px-5" aria-label="市场资讯事件列表">
          {loading && <p className="border-b border-border/45 py-2 text-xs text-muted-foreground">正在更新筛选结果…</p>}
          {data.events.map((event) => <EventCard key={event.event_id} event={event} onOpenDetails={(selectedEvent) => setDetailSelection({ queryKey, snapshotId: data.snapshot_id, event: selectedEvent })} />)}
        </main>
        <MarketNewsSidebar focus={data.focus_events} impact={data.impact_summary} days={data.filters.days} />
      </div>}

      <TagSelector open={selectorOpen} selectedIds={tags.state.ids} onCancel={() => setSelectorOpen(false)} onConfirm={(ids) => { tags.replace(ids); setSelectorOpen(false); }} />
      <DataInfoDialog open={infoOpen} onClose={() => setInfoOpen(false)} />
      <EventDetailDrawer open={detailEvent !== null} event={detailEvent} onClose={() => setDetailSelection(null)} />
    </div>
  );
}
