import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, Loader2 } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import type { SourceHealthCounts, SourceHealthRun, SourceHealthSummaryData } from "./types";

interface SourceHealthSummaryProps {
  onOpenDetails?: () => void;
  detailsButtonRef?: React.RefObject<HTMLButtonElement | null>;
  autoFocusDetails?: boolean;
  refreshToken?: number;
  onUpdated?: () => void;
  variant?: "card" | "bar";
}

const NOOP = () => {};

const confidenceLabels: Record<string, string> = {
  initial: "初始评级 · 样本不足",
  growing: "评级积累中",
  stable: "评级稳定",
};

function formatCounts(counts: SourceHealthCounts): string {
  return `${counts.healthy} 健康 / ${counts.usable} 基本可用 / ${counts.degraded} 降级 / ${counts.failed} 失败`;
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

export function SourceHealthSummary({
  onOpenDetails = NOOP,
  detailsButtonRef,
  autoFocusDetails = false,
  refreshToken = 0,
  onUpdated = NOOP,
  variant = "card",
}: SourceHealthSummaryProps) {
  const [summary, setSummary] = useState<SourceHealthSummaryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [run, setRun] = useState<SourceHealthRun | null>(null);
  const [starting, setStarting] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const startingRef = useRef(false);
  const mountedRef = useRef(true);
  const onUpdatedRef = useRef(onUpdated);
  useEffect(() => { onUpdatedRef.current = onUpdated; }, [onUpdated]);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const loadSummary = useCallback(async () => {
    try {
      const next = await api.sourceHealthSummary();
      setSummary(next);
      setLoadError(false);
      return true;
    } catch {
      setLoadError(true);
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadSummary(); }, [loadSummary, refreshToken]);

  useEffect(() => {
    if (autoFocusDetails && summary?.last_run_at) detailsButtonRef?.current?.focus();
  }, [autoFocusDetails, detailsButtonRef, summary]);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const next = await api.sourceHealthRun(run.run_id);
        if (!mountedRef.current) return;
        setRun(next);
        if (next.status === "completed") {
          await loadSummary();
          if (!mountedRef.current) return;
          setNotice({ kind: "success", text: `体检完成 · ${next.completed} / ${next.total}` });
          onUpdatedRef.current();
        } else if (next.status === "failed") {
          setNotice({ kind: "error", text: "体检失败，已保留上一次成功报告" });
        }
      } catch {
        if (mountedRef.current) setNotice({ kind: "error", text: "体检状态读取失败，已保留上一次成功报告" });
      }
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [loadSummary, run]);

  const running = starting || run?.status === "queued" || run?.status === "running";
  const hasReport = Boolean(summary?.last_run_at && summary.total_sources > 0);
  const compact = variant === "bar";
  const compactStateMessage = loadError && !summary
    ? "健康快照读取失败"
    : loading && !summary
      ? "尚未载入健康快照"
      : !hasReport
        ? "尚未完成数据源体检"
        : null;

  const startFullRun = async () => {
    if (startingRef.current || running) return;
    startingRef.current = true;
    setStarting(true);
    setNotice(null);
    try {
      const started = await api.sourceHealthStartFullRun();
      if (!mountedRef.current) return;
      setRun({
        run_id: started.run_id, scope: "full", status: "queued", started_at: new Date().toISOString(),
        finished_at: null, total: 0, completed: 0, success: 0, partial: 0, failure: 0, current_source: "",
      });
    } catch (error) {
      if (!mountedRef.current) return;
      setNotice({
        kind: "error",
        text: error instanceof ApiError && error.status === 409
          ? "已有全量体检正在运行，当前成功报告保持不变"
          : "全量体检启动失败，已保留上一次成功报告",
      });
    } finally {
      startingRef.current = false;
      if (mountedRef.current) setStarting(false);
    }
  };

  if (compact) {
    return (
      <section className="rounded-xl border border-primary/25 bg-primary/5 px-4 py-3" aria-label="数据源健康状态栏">
        <div className="flex flex-wrap items-center gap-3 xl:flex-nowrap">
          <div className="flex shrink-0 items-center gap-2">
            <Activity className="h-4 w-4 text-primary" aria-hidden="true" />
            <h3 className="text-sm font-semibold text-foreground">健康快照</h3>
          </div>

          {compactStateMessage ? (
            <p className="min-w-[220px] flex-1 text-xs font-medium text-warning">{compactStateMessage}</p>
          ) : (
            <div className="grid min-w-0 flex-1 gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
              <div className="min-w-0 border-l border-border/60 pl-3">
                <p className="font-medium text-foreground">基金与行情</p>
                <p className="mt-0.5 leading-5 text-muted-foreground">{formatCounts(summary!.fund)}</p>
              </div>
              <div className="min-w-0 border-l border-border/60 pl-3">
                <p className="font-medium text-foreground">资讯来源</p>
                <p className="mt-0.5 leading-5 text-muted-foreground">{formatCounts(summary!.news)}</p>
              </div>
              <div className="border-l border-border/60 pl-3">
                <p className="font-medium text-foreground">最后体检</p>
                <p className="mt-0.5 text-muted-foreground">最后体检：{formatTime(summary!.last_run_at!)}</p>
              </div>
              <div className="border-l border-border/60 pl-3">
                <p className="font-medium text-foreground">评级置信度</p>
                <p className="mt-0.5 text-muted-foreground">{confidenceLabels[summary!.rating_confidence] || "评级状态未知"}</p>
              </div>
            </div>
          )}

          <div className="ml-auto flex shrink-0 flex-wrap items-center justify-end gap-2">
            {running && (
              <p className="inline-flex items-center gap-1.5 text-xs text-foreground" aria-live="polite">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                {starting && !run ? "体检中 · 等待任务启动" : `体检中 · ${run?.completed || 0} / ${run?.total || 0}`}
              </p>
            )}
            {notice && <p role={notice.kind === "error" ? "alert" : "status"} className={`text-xs ${notice.kind === "error" ? "text-warning" : "text-primary"}`}>{notice.text}</p>}
            <button
              onClick={startFullRun}
              disabled={running}
              aria-label="运行全量体检"
              className="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
            >{running ? "体检中" : "运行全量体检"}</button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="mt-5 rounded-xl border border-primary/25 bg-primary/5 p-4" aria-label="数据源健康">
      <div className="flex items-center gap-2">
        <Activity className="h-4 w-4 text-primary" aria-hidden="true" />
        <h3 className="font-semibold text-foreground">数据源健康</h3>
      </div>

      {loading && !summary && <p className="mt-3 text-xs text-muted-foreground">正在读取数据源健康摘要…</p>}
      {loadError && !summary && <p role="status" className="mt-3 text-xs text-warning">数据源健康摘要暂不可用。</p>}

      {!loading && summary && !hasReport && (
        <div className="mt-3">
          <p className="text-sm font-medium text-foreground">尚未完成数据源体检</p>
        </div>
      )}

      {summary && hasReport && (
        <div className="mt-3 grid gap-3 text-xs sm:grid-cols-2">
          <div className="rounded-lg border border-border/50 bg-background/35 p-3">
            <p className="font-medium text-foreground">基金与行情</p>
            <p className="mt-1 text-muted-foreground">{formatCounts(summary.fund)}</p>
          </div>
          <div className="rounded-lg border border-border/50 bg-background/35 p-3">
            <p className="font-medium text-foreground">资讯来源</p>
            <p className="mt-1 text-muted-foreground">{formatCounts(summary.news)}</p>
          </div>
          <p>最后体检：{formatTime(summary.last_run_at!)}</p>
          <p>评级状态：{confidenceLabels[summary.rating_confidence] || "评级状态未知"}</p>
        </div>
      )}

      {running && (
        <p className="mt-3 inline-flex items-center gap-2 text-xs text-foreground" aria-live="polite">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          {starting && !run ? "体检中 · 等待任务启动" : `体检中 · ${run?.completed || 0} / ${run?.total || 0}`}
        </p>
      )}
      {notice && <p role={notice.kind === "error" ? "alert" : "status"} className={`mt-3 text-xs ${notice.kind === "error" ? "text-warning" : "text-primary"}`}>{notice.text}</p>}

      <div className="mt-4 flex flex-wrap gap-2">
        {hasReport && <button ref={detailsButtonRef} onClick={onOpenDetails} className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:border-primary/45">查看详情</button>}
        <button
          onClick={startFullRun}
          disabled={running}
          aria-label={hasReport ? "运行全量体检" : "运行首次全量体检"}
          className="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >{running ? "体检中" : hasReport ? "运行全量体检" : "运行首次全量体检"}</button>
      </div>
    </section>
  );
}
