import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { SourceHealthRating, SourceHealthSource } from "./types";

interface SourceHealthDrawerProps {
  open: boolean;
  onClose: () => void;
  refreshToken?: number;
}

type GroupFilter = "all" | "fund" | "news";
type RatingFilter = "all" | SourceHealthRating;

const ratingLabels: Record<SourceHealthRating, string> = {
  healthy: "健康", usable: "基本可用", degraded: "降级", failed: "失败",
};

const errorLabels: Record<string, string> = {
  none: "无", timeout: "超时", dns: "DNS 解析失败", tls: "TLS 失败", connection: "连接失败",
  http: "HTTP 错误", redirect: "重定向异常", rate_limit: "访问频率受限", authentication: "鉴权失败",
  parse: "解析失败", empty_payload: "返回为空", schema_changed: "字段结构变化", stale_data: "数据过期", unknown: "未知错误",
};

const repairLabels: Record<string, string> = {
  none: "无需修复", immediate_fix: "立即修复", worth_fixing: "值得修复", observe: "继续观察",
  replace_candidate: "建议替换", disable_candidate: "建议停用",
};

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "暂无记录";
}

function safePublicReference(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? value : null;
  } catch {
    return null;
  }
}

function PublicReference({ label, value, emptyLabel }: { label: string; value: string | null | undefined; emptyLabel: string }) {
  const reference = safePublicReference(value);
  return <p className="break-all">{label}：{reference ? <a href={reference} target="_blank" rel="noreferrer" className="text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background">{reference}</a> : value ? "未提供可用公开地址" : emptyLabel}</p>;
}

function SourceRow({ source }: { source: SourceHealthSource }) {
  const [expanded, setExpanded] = useState(false);
  const hasFailureDetails = source.probe_status === "failure" || source.rating === "failed";
  const lastSuccess = formatTime(source.last_success_at);
  return (
    <article className="rounded-xl border border-border/55 bg-background/45 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div><h4 className="font-semibold text-foreground">{source.source_name}</h4><p className="mt-1 text-xs text-muted-foreground">能力：{source.capability}</p></div>
        <span className="rounded-full border border-border px-2 py-1 text-xs">状态：{ratingLabels[source.rating]}</span>
      </div>
      <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-3">
        {(source.source_family_id || source.adapter_id || source.capability_id) && <p className="break-all sm:col-span-2 lg:col-span-3">稳定身份：{source.source_family_id || "未提供"} / {source.adapter_id || "未提供"} / {source.capability_id || source.capability}</p>}
        <p>响应时间：{source.latency_ms} ms</p><p>返回条数：{source.returned_items}</p>
        <p>数据日期：{source.data_as_of_date || "暂无记录"}</p>
        <p>字段完整率：{source.field_completeness_pct == null ? "暂无记录" : `${source.field_completeness_pct}%`}</p>
        <p>最近成功：{lastSuccess}</p><p>连续失败：{source.consecutive_failures}</p>
        <p>修复价值：{repairLabels[source.repair_value] || source.repair_value || "暂无建议"}</p>
      </div>
      <div className="mt-3 grid gap-2 border-t border-border/45 pt-3 text-xs text-muted-foreground sm:grid-cols-2">
        <PublicReference label="配置公开地址" value={source.configured_reference} emptyLabel="暂无公开地址" />
        <PublicReference label="观测公开地址" value={source.observed_final_reference} emptyLabel="尚未体检" />
      </div>
      {hasFailureDetails && (
        <div className="mt-3 border-t border-border/45 pt-3">
          <button onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} aria-label={`${expanded ? "收起" : "查看"}失败详情 ${source.source_name}`} className="inline-flex items-center gap-1 text-xs font-medium text-warning hover:text-foreground">
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}{expanded ? "收起失败详情" : "查看失败详情"}
          </button>
          {expanded && (
            <div className="mt-3 grid gap-2 rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs text-muted-foreground sm:grid-cols-2">
              <p>错误类型：{errorLabels[source.error_type] || "未知错误"}</p>
              <p>脱敏原因：{source.error_message_redacted || "暂无公开错误说明"}</p>
              <p>是否重定向：{source.redirected ? "是" : "否"}</p>
              <p>是否有备用来源：{source.fallback_available ? "是" : "否"}</p>
              <p className="sm:col-span-2">建议处理：{source.repair_reason || "继续观察公开来源"}</p>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

export function SourceHealthDrawer({ open, onClose, refreshToken = 0 }: SourceHealthDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const [sources, setSources] = useState<SourceHealthSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [group, setGroup] = useState<GroupFilter>("all");
  const [rating, setRating] = useState<RatingFilter>("all");

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onClose(); return; }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])') || []);
      if (!focusable.length) return;
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose, open]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setError(false);
    api.sourceHealthSources().then((rows) => {
      if (active) setSources(rows);
    }).catch(() => {
      if (active) setError(true);
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [open, refreshToken]);

  const visible = useMemo(() => sources.filter((source) => {
    const groupMatches = group === "all" || (group === "news" ? source.group === "news" : source.group !== "news");
    return groupMatches && (rating === "all" || source.rating === rating);
  }), [group, rating, sources]);
  const fundRows = visible.filter((source) => source.group !== "news");
  const newsRows = visible.filter((source) => source.group === "news");

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/65" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
      <aside ref={dialogRef} role="dialog" aria-modal="true" aria-label="数据源健康详情" className="ml-auto flex h-full w-full max-w-5xl flex-col border-l border-primary/25 bg-background/95 shadow-2xl backdrop-blur-xl">
        <header className="flex items-start justify-between border-b border-border/60 px-5 py-4">
          <div><h2 className="text-xl font-bold">数据源健康详情</h2><p className="mt-1 text-xs text-muted-foreground">仅显示公开探测结果和脱敏后的修复信息</p></div>
          <button ref={closeRef} onClick={onClose} aria-label="关闭数据源健康详情" className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-5 w-5" /></button>
        </header>

        <div className="border-b border-border/50 px-5 py-3">
          <div className="flex flex-wrap items-center gap-2" aria-label="来源分组过滤">
            <button onClick={() => setGroup("all")} aria-pressed={group === "all"} className="rounded-lg border border-border px-3 py-1.5 text-xs">显示全部来源</button>
            <button onClick={() => setGroup("fund")} aria-pressed={group === "fund"} className="rounded-lg border border-border px-3 py-1.5 text-xs">只看基金与行情 Provider</button>
            <button onClick={() => setGroup("news")} aria-pressed={group === "news"} className="rounded-lg border border-border px-3 py-1.5 text-xs">只看资讯来源</button>
            <label className="ml-auto text-xs text-muted-foreground">状态
              <select aria-label="按状态过滤" value={rating} onChange={(event) => setRating(event.target.value as RatingFilter)} className="ml-2 rounded-lg border border-border bg-background px-2 py-1.5 text-foreground">
                <option value="all">全部</option><option value="healthy">健康</option><option value="usable">基本可用</option><option value="degraded">降级</option><option value="failed">失败</option>
              </select>
            </label>
          </div>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto p-5">
          {loading && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取数据源健康详情…</p>}
          {error && <p role="alert" className="text-sm text-warning">数据源健康详情加载失败，请稍后重试。</p>}
          {!loading && !error && group !== "news" && <section><h3 className="mb-3 text-sm font-semibold">基金与行情 Provider</h3><div className="space-y-3">{fundRows.length ? fundRows.map((source) => <SourceRow key={source.source_id} source={source} />) : <p className="text-xs text-muted-foreground">当前筛选没有基金与行情来源。</p>}</div></section>}
          {!loading && !error && group !== "fund" && <section><h3 className="mb-3 text-sm font-semibold">资讯来源</h3><div className="space-y-3">{newsRows.length ? newsRows.map((source) => <SourceRow key={source.source_id} source={source} />) : <p className="text-xs text-muted-foreground">当前筛选没有资讯来源。</p>}</div></section>}
        </div>
      </aside>
    </div>
  );
}
