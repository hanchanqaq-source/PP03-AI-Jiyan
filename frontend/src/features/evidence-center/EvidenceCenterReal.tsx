import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Database, Loader2, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { NewsPipelineStatus, newsPipelineFailureMessage, runNewsPipelineRefresh } from "@/features/market-news/NewsPipelineStatus";
import type { NewsPipelineStatusData } from "@/features/market-news/types";
import { SourceHealthSummary } from "@/features/source-health/SourceHealthSummary";
import { SourceHealthWorkspace } from "@/features/source-health/SourceHealthWorkspace";
import { api, isAbortError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { EvidenceDrawer, STATUS_LABEL, StatusBadge } from "./EvidenceDrawer";
import type { EvidenceEventDetail, EvidenceEventList, EvidenceEventQuery, EvidenceEventSummary, EvidenceSummaryData, VerificationStatus } from "./types";

type Tab = "verification" | "health" | "corrections";
type Filter = "all" | VerificationStatus;
type Sort = "latest" | "status" | "holding_relevance";
type SnapshotLoadResult = { loaded: boolean; snapshotId: string | null };

const tabs: Array<{ value: Tab; label: string }> = [{ value: "verification", label: "资讯核验" }, { value: "health", label: "数据源健康" }, { value: "corrections", label: "更正记录" }];
const filters: Array<{ value: Filter; label: string }> = [{ value: "all", label: "全部" }, ...Object.entries(STATUS_LABEL).map(([value, label]) => ({ value: value as VerificationStatus, label }))];
const statusPriority: Record<VerificationStatus, number> = { conflicting: 0, disproved: 1, corrected: 2, unverified: 3, corroborated: 4, verified: 5 };
const holdingPriority: Record<string, number> = { direct_holding: 0, industry_relation: 1, watch_tag: 2, none: 3 };
const categoryLabel: Record<string, string> = { policy: "政策", industry: "产业", company: "公司", fund_notice: "基金公告", deep_content: "深度内容" };

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return <article className="rounded-xl border border-border/60 bg-gradient-to-b from-slate-950/55 to-background/45 p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-bold">{value}</p><p className="mt-1 text-[11px] text-muted-foreground">{note}</p></article>;
}

function snapshotLabel(snapshotId: string | null): string {
  if (!snapshotId) return "核验快照未知";
  return snapshotId.toLowerCase().startsWith("acceptance")
    ? "隔离验收快照"
    : `核验快照 ${snapshotId.slice(0, 8)}`;
}

export function EvidenceCenter() {
  const [tab, setTab] = useState<Tab>("verification");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("latest");
  const [summary, setSummary] = useState<EvidenceSummaryData | null>(null);
  const [events, setEvents] = useState<EvidenceEventSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [pipelineStatus, setPipelineStatus] = useState<NewsPipelineStatusData | null>(null);
  const [explanationOpen, setExplanationOpen] = useState(false);
  const [detail, setDetail] = useState<EvidenceEventDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [focusHistory, setFocusHistory] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const requestRef = useRef(0);
  const evidenceLoadAbortRef = useRef<AbortController | null>(null);
  const filterRef = useRef<Filter>("all");
  const pipelineCycleRef = useRef(0);
  const pipelineAbortRef = useRef<AbortController | null>(null);
  const refreshInFlightRef = useRef(false);
  const committedSummaryRef = useRef<EvidenceSummaryData | null>(summary);
  committedSummaryRef.current = summary;

  const queryForFilter = useCallback((nextFilter: Filter): EvidenceEventQuery => ({
    days: 7,
    ...(nextFilter === "all" ? {} : { verification_status: nextFilter }),
  }), []);

  const responseMatchesQuery = useCallback((result: EvidenceEventList, query: EvidenceEventQuery): boolean => {
    const expected = {
      verification_status: query.verification_status ?? null,
      tag_id: query.tag_id ?? null,
      category: query.category ?? null,
      days: query.days ?? 7,
      holding_relevance: query.holding_relevance ?? null,
    };
    const actual = result.filters;
    return actual.verification_status === expected.verification_status
      && actual.tag_id === expected.tag_id
      && actual.category === expected.category
      && actual.days === expected.days
      && actual.holding_relevance === expected.holding_relevance;
  }, []);

  const loadSnapshot = useCallback(async (
    nextFilter: Filter,
    expectedSnapshotId: string | null = null,
    failureMessage = "证据快照加载失败，请确认本地后端可用后重试。",
    commitFilter = false,
  ): Promise<SnapshotLoadResult> => {
    const requestId = ++requestRef.current;
    evidenceLoadAbortRef.current?.abort();
    const controller = new AbortController();
    evidenceLoadAbortRef.current = controller;
    const query = queryForFilter(nextFilter);
    setLoading(true); setError(null);
    try {
      const [nextSummary, nextEvents] = await Promise.all([
        api.evidenceSummary(controller.signal),
        api.evidenceEvents(query, controller.signal),
      ]);
      if (requestId !== requestRef.current || controller.signal.aborted) return { loaded: false, snapshotId: null };
      if (!responseMatchesQuery(nextEvents, query)
        || nextSummary.snapshot_id !== nextEvents.snapshot_id
        || (expectedSnapshotId !== null && nextSummary.snapshot_id !== expectedSnapshotId)) {
        throw new Error("evidence snapshot response mismatch");
      }
      setSummary(nextSummary); setEvents(nextEvents.events);
      if (commitFilter) {
        filterRef.current = nextFilter;
        setFilter(nextFilter);
      }
      return { loaded: true, snapshotId: nextSummary.snapshot_id };
    } catch (error) {
      const alreadyAborted = controller.signal.aborted;
      controller.abort();
      if (requestId !== requestRef.current || alreadyAborted || isAbortError(error)) return { loaded: false, snapshotId: null };
      setError(failureMessage);
      return { loaded: false, snapshotId: null };
    } finally {
      if (requestId === requestRef.current) {
        evidenceLoadAbortRef.current = null;
        setLoading(false);
      }
    }
  }, [queryForFilter, responseMatchesQuery]);

  useEffect(() => { void loadSnapshot(filterRef.current, null, "证据快照加载失败，请确认本地后端可用后重试。", true); }, [loadSnapshot]);
  useEffect(() => () => {
    requestRef.current += 1;
    evidenceLoadAbortRef.current?.abort();
    pipelineCycleRef.current += 1;
    pipelineAbortRef.current?.abort();
  }, []);
  useEffect(() => {
    const eventId = new URLSearchParams(window.location.search).get("event_id");
    if (!eventId) return;
    setDetailLoading(true);
    api.evidenceEvent(eventId).then(setDetail).catch(() => setNotice("指定证据事件不存在或尚未完成核验。" )).finally(() => setDetailLoading(false));
  }, []);

  const visibleEvents = useMemo(() => [...events].sort((left, right) => {
    if (sort === "status") return statusPriority[left.verification_status] - statusPriority[right.verification_status] || right.verified_at.localeCompare(left.verified_at);
    if (sort === "holding_relevance") return (holdingPriority[left.holding_relevance] ?? 9) - (holdingPriority[right.holding_relevance] ?? 9) || right.verified_at.localeCompare(left.verified_at);
    return right.verified_at.localeCompare(left.verified_at);
  }), [events, sort]);
  const corrections = events.filter((event) => event.latest_transition?.from_status && event.latest_transition.from_status !== event.latest_transition.to_status);
  const total = summary?.counts ? Object.values(summary.counts).reduce((sum, value) => sum + value, 0) : 0;
  const coverage = summary?.loaded && summary.admitted_count != null && total > 0 ? `${Math.round(summary.admitted_count / total * 100)}%` : "—";

  const openEvent = async (eventId: string, trigger?: HTMLButtonElement | null, history = false) => {
    triggerRef.current = trigger || null; setFocusHistory(history); setDetail(null); setDetailLoading(true);
    try { setDetail(await api.evidenceEvent(eventId)); } catch { setNotice("证据详情加载失败，请稍后重试。" ); setDetailLoading(false); return; }
    setDetailLoading(false);
  };
  const closeEvent = () => { setDetail(null); setDetailLoading(false); setFocusHistory(false); triggerRef.current?.focus(); };
  const changeFilter = async (next: Filter) => {
    if (next === filterRef.current && !error) return;
    await loadSnapshot(next, null, "证据列表筛选失败，请稍后重试。", true);
  };
  const refresh = async () => {
    if (refreshInFlightRef.current || refreshing) return;
    refreshInFlightRef.current = true;
    const pipelineCycle = ++pipelineCycleRef.current;
    pipelineAbortRef.current?.abort();
    const controller = new AbortController();
    pipelineAbortRef.current = controller;
    let loadedEvidenceSnapshotId: string | null = null;
    setRefreshing(true); setNotice(null); setPipelineStatus(null);
    try {
      const terminal = await runNewsPipelineRefresh({
        signal: controller.signal,
        onStatus: async (next) => {
          if (pipelineCycle !== pipelineCycleRef.current) return;
          setPipelineStatus(next);
          const evidenceDurable = next.phase === "evidence_saved" || next.phase === "trusted_published";
          const terminalFailure = next.phase === "failed" || next.phase === "interrupted";
          const durableEvidenceId = next.evidence_snapshot_id;
          if ((evidenceDurable || terminalFailure) && durableEvidenceId !== null
            && loadedEvidenceSnapshotId !== durableEvidenceId) {
            const result = await loadSnapshot(
              filterRef.current,
              durableEvidenceId,
            );
            if (result.loaded && result.snapshotId === durableEvidenceId) {
              loadedEvidenceSnapshotId = durableEvidenceId;
            }
          }
        },
      });
      if (!terminal || pipelineCycle !== pipelineCycleRef.current) return;
      if (terminal.evidence_snapshot_id !== null
        && loadedEvidenceSnapshotId !== terminal.evidence_snapshot_id) {
        const result = await loadSnapshot(
          filterRef.current,
          terminal.evidence_snapshot_id,
        );
        if (result.loaded && result.snapshotId === terminal.evidence_snapshot_id) {
          loadedEvidenceSnapshotId = terminal.evidence_snapshot_id;
        }
      }
      if (terminal.phase === "trusted_published") {
        if (loadedEvidenceSnapshotId === terminal.evidence_snapshot_id) setNotice("核验刷新完成，已载入最新成功快照。");
      } else setNotice(newsPipelineFailureMessage(terminal));
    }
    catch (error) {
      if (pipelineCycle === pipelineCycleRef.current && !controller.signal.aborted && !isAbortError(error)) {
        setNotice(committedSummaryRef.current?.loaded
          ? "核验流水线状态连接失败；继续显示上次成功快照。"
          : "核验流水线状态连接失败；当前尚无可显示的成功快照。");
      }
    }
    finally {
      if (pipelineCycle === pipelineCycleRef.current) {
        pipelineAbortRef.current = null;
        refreshInFlightRef.current = false;
        setRefreshing(false);
      }
    }
  };

  const terminalPipelineNotice = Boolean(notice && pipelineStatus && (
    (pipelineStatus.phase === "trusted_published" && notice === "核验刷新完成，已载入最新成功快照。")
    || ((pipelineStatus.phase === "failed" || pipelineStatus.phase === "interrupted")
      && notice === newsPipelineFailureMessage(pipelineStatus))
  ));

  return <div className="pb-8">
    <PageHeader title="证据中心" subtitle="查看资讯的核验状态、一手证据、独立来源与更正记录" actions={<><button aria-label="数据说明" onClick={() => setExplanationOpen((value) => !value)} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:border-primary/45 hover:text-foreground"><Database className="h-4 w-4" />数据说明</button><button aria-label="运行核验" onClick={refresh} disabled={refreshing} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground disabled:opacity-50">{refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}{refreshing ? "核验中" : "运行核验"}</button></>} />
    {explanationOpen && <section role="dialog" aria-label="数据说明" className="mb-4 rounded-xl border border-primary/25 bg-primary/5 p-4 text-xs text-muted-foreground"><p className="font-semibold text-foreground">真实性核验与数据源健康是两套独立机制。</p><p className="mt-2">仅明确官方证据或两个相互独立的来源链提供的一致证据可进入可信资讯流。</p><p className="mt-1">待核验金额、比例、数量和日期不会进入摘要、持仓影响或情绪判断。</p><p className="mt-1">AI 翻译、AI 摘要和来源数量不会自动提高核验等级。</p></section>}
    {notice && <p role={terminalPipelineNotice ? undefined : "status"} className="mb-4 rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-xs text-primary">{notice}</p>}
    {error && <p role="alert" className="mb-4 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">{error}</p>}
    <NewsPipelineStatus status={pipelineStatus} />

    {loading && !summary ? <section className="rounded-xl border border-border/60 p-4 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />正在载入核验快照…</section> : !summary?.loaded ? <section className="rounded-xl border border-border/60 bg-muted/10 p-4"><p className="font-semibold">尚无已完成的核验快照</p><p className="mt-1 text-xs text-muted-foreground">运行核验后才会显示真实计数；未加载状态不会显示为全部为 0。</p></section> : <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" aria-label="真实核验概览"><Metric label="核验覆盖率" value={coverage} note="准入事件占当前核验事件比例，不代表资讯整体为真" /><Metric label="已核验" value={String(summary.counts?.verified ?? 0)} note="有明确官方或一手证据" /><Metric label="多源印证" value={String(summary.counts?.corroborated ?? 0)} note="至少两个相互独立的来源链" /><Metric label="待核验" value={String(summary.counts?.unverified ?? 0)} note="证据不足，不进入可信资讯流" /><Metric label="冲突 / 更正" value={String((summary.counts?.conflicting ?? 0) + (summary.counts?.corrected ?? 0) + (summary.counts?.disproved ?? 0))} note={`${snapshotLabel(summary.snapshot_id)} · ${summary.generated_at ? new Date(summary.generated_at).toLocaleString("zh-CN", { hour12: false }) : "时间未知"}`} /></section>}

    <div role="tablist" aria-label="证据中心内容" className="mt-5 flex flex-wrap gap-1 border-b border-border/55">{tabs.map((item) => <button key={item.value} role="tab" aria-selected={tab === item.value} onClick={() => setTab(item.value)} className={cn("rounded-t-lg px-3 py-2 text-sm", tab === item.value ? "bg-primary/15 font-semibold text-primary" : "text-muted-foreground hover:bg-muted/50")}>{item.label}</button>)}</div>

    {tab === "verification" && <div className="mt-4 grid gap-5 xl:grid-cols-[minmax(0,7fr)_minmax(280px,3fr)]"><main className="min-w-0"><div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/55 bg-muted/10 p-3"><span className="mr-1 text-xs text-muted-foreground">筛选：</span>{filters.map((item) => <button key={item.value} onClick={() => void changeFilter(item.value)} aria-pressed={filter === item.value} className={cn("rounded-lg px-2.5 py-1 text-xs", filter === item.value ? "bg-primary/15 font-semibold text-primary" : "text-muted-foreground hover:bg-muted/50")}>{item.label}</button>)}<label className="ml-auto text-xs text-muted-foreground">排序方式<select aria-label="排序方式" value={sort} onChange={(event) => setSort(event.target.value as Sort)} className="ml-2 rounded-lg border border-border bg-background px-2 py-1.5 text-foreground"><option value="latest">最新核验</option><option value="status">核验状态</option><option value="holding_relevance">持仓关联</option></select></label></div><div className="mt-3 space-y-3">{visibleEvents.map((event) => <article key={event.event_id} className="rounded-xl border border-border/60 bg-background/45 p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><StatusBadge status={event.verification_status} /><h2 className="mt-2 font-semibold">{event.title}</h2><p className="mt-1 text-xs text-muted-foreground">发布时间：{event.published_at ? new Date(event.published_at).toLocaleString("zh-CN", { hour12: false }) : "未知"} · {categoryLabel[event.category] || event.category}</p></div><p className="text-xs text-muted-foreground">最后核验：{new Date(event.verified_at).toLocaleString("zh-CN", { hour12: false })}</p></div><p className="mt-3 text-sm">核心主张：{event.core_claim}</p><p className="mt-2 text-xs text-muted-foreground">{event.verification_reason}</p><div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-border/45 pt-3"><p className="text-xs text-muted-foreground">一手证据：{event.primary_evidence_count} · 独立来源：{event.independent_evidence_count} · 转载来源：{event.syndicated_copy_count} · 冲突来源：{event.contradicting_evidence_count}</p><button onClick={(buttonEvent) => void openEvent(event.event_id, buttonEvent.currentTarget)} aria-label="查看证据" className="rounded-lg border border-primary/45 px-3 py-1.5 text-xs font-medium text-primary">查看证据</button></div></article>)}{!loading && visibleEvents.length === 0 && <p className="rounded-xl border border-border/60 p-6 text-center text-sm text-muted-foreground">当前筛选没有核验记录。</p>}</div></main><aside className="space-y-3"><section className="rounded-xl border border-border/60 bg-muted/10 p-4"><h2 className="font-semibold">可信准入</h2><p className="mt-2 text-xs text-muted-foreground">主资讯流仅接收“已核验”和“多源印证”。其余状态留在证据中心。</p><p className="mt-3 text-sm">已准入：{summary?.admitted_count ?? "—"}</p><p className="mt-1 text-sm">隔离待查：{summary?.isolated_count ?? "—"}</p></section><section className="rounded-xl border border-border/60 bg-muted/10 p-4"><h2 className="font-semibold">数据源健康</h2><p className="mt-2 text-xs text-muted-foreground">来源可访问不等于具体消息已被证实。</p><SourceHealthSummary onOpenDetails={() => setTab("health")} /><button onClick={() => setTab("health")} className="mt-3 rounded-lg border border-border px-3 py-1.5 text-xs">查看健康详情</button></section><section className="rounded-xl border border-border/60 bg-muted/10 p-4"><h2 className="font-semibold">更正与冲突</h2><p className="mt-2 text-xs text-muted-foreground">状态迁移来自当前真实快照，不使用演示记录。</p><button onClick={() => setTab("corrections")} className="mt-3 rounded-lg border border-border px-3 py-1.5 text-xs">查看 {corrections.length} 条记录</button></section></aside></div>}

    {tab === "health" && <section className="mt-4"><div className="rounded-xl border border-border/60 bg-background/45 p-4"><h2 className="font-semibold">数据源健康</h2><p className="mt-2 text-sm text-muted-foreground">数据源健康表示来源能否访问、能否解析、是否新鲜；内容核验表示具体消息是否存在证据。前者不等于后者。</p></div><SourceHealthWorkspace /></section>}

    {tab === "corrections" && <section className="mt-4 rounded-xl border border-border/60 bg-background/45 p-4"><h2 className="font-semibold">更正记录</h2><p className="mt-1 text-xs text-muted-foreground">仅显示当前快照返回的真实状态迁移。</p>{corrections.length ? <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-muted-foreground"><tr><th className="pb-2">事件标题</th><th>原始状态</th><th>当前状态</th><th>变更时间</th><th>确定性依据</th><th /></tr></thead><tbody>{corrections.map((event) => { const change = event.latest_transition!; return <tr key={`${event.event_id}-${change.changed_at}`} className="border-t border-border/45"><td className="py-3 pr-3">{event.title}</td><td>{change.from_status ? STATUS_LABEL[change.from_status] : "初始"}</td><td>{STATUS_LABEL[change.to_status]}</td><td>{new Date(change.changed_at).toLocaleString("zh-CN", { hour12: false })}</td><td className="max-w-xs py-3 pr-3 text-muted-foreground">{change.reason}</td><td><button onClick={(buttonEvent) => void openEvent(event.event_id, buttonEvent.currentTarget, true)} aria-label={`查看记录 ${event.title}`} className="rounded-lg border border-primary/45 px-2 py-1 text-primary">查看记录</button></td></tr>; })}</tbody></table></div> : <p className="mt-4 text-sm text-muted-foreground">当前快照没有状态变更记录。</p>}</section>}

    <EvidenceDrawer event={detail} loading={detailLoading} focusHistory={focusHistory} onClose={closeEvent} />
  </div>;
}
