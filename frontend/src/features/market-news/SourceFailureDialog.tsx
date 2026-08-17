import { useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2, RefreshCw, X } from "lucide-react";
import type { MarketNewsSourceStatus } from "./types";

const ERROR_LABELS: Record<string, string> = {
  timeout: "超时",
  http_status: "HTTP 状态",
  tls: "TLS",
  dns: "DNS",
  connection: "连接错误",
  rss_parse: "XML / RSS 解析",
  unknown: "未知错误",
};

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "暂无记录";
}

export function SourceFailureDialog({
  open,
  statuses,
  onClose,
  onRetry,
}: {
  open: boolean;
  statuses: MarketNewsSourceStatus[];
  onClose: () => void;
  onRetry: (sourceId: string) => Promise<void>;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousRef = useRef<HTMLElement | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [retryError, setRetryError] = useState(false);
  const failures = statuses.filter((source) => source.status === "failed");

  useEffect(() => {
    if (!open) return;
    previousRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleKey);
    return () => { document.removeEventListener("keydown", handleKey); previousRef.current?.focus(); };
  }, [onClose, open]);

  const retry = async (sourceId: string) => {
    setRetryingId(sourceId);
    setRetryError(false);
    try {
      await onRetry(sourceId);
    } catch {
      setRetryError(true);
    } finally {
      setRetryingId(null);
    }
  };

  if (!open) return null;
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <section role="dialog" aria-modal="true" aria-label="失败来源详情" className="glass max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-warning/30 p-5 shadow-2xl">
      <header className="flex items-start justify-between gap-3"><div><h2 className="text-lg font-bold">失败来源详情</h2><p className="mt-1 text-xs text-muted-foreground">错误原因已限制长度并脱敏；重试仅请求所选公开来源。</p></div><button ref={closeRef} onClick={onClose} aria-label="关闭失败来源详情" className="rounded-lg p-2 text-muted-foreground hover:bg-muted"><X className="h-4 w-4" /></button></header>
      {retryError && <p role="alert" className="mt-4 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">该来源重试失败，请稍后再试。</p>}
      <div className="mt-4 space-y-3">{failures.map((source) => <article key={source.source_id} className="rounded-xl border border-border/60 bg-muted/10 p-4 text-xs">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-foreground">{source.source_name}</h3><p className="mt-1 text-warning">{ERROR_LABELS[source.error_type || "unknown"] || ERROR_LABELS.unknown}</p></div><button onClick={() => retry(source.source_id)} disabled={retryingId === source.source_id} aria-label={`重试来源 ${source.source_name}`} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 font-medium text-foreground hover:border-primary/45 disabled:cursor-not-allowed disabled:opacity-50">{retryingId === source.source_id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}重试</button></div>
        <p className="mt-2 leading-5 text-muted-foreground">{source.error_reason || "未提供可公开的错误原因"}</p>
        <div className="mt-3 grid gap-1 text-muted-foreground sm:grid-cols-2"><p>最后成功：{formatTime(source.last_success_at)}</p><p>使用旧缓存：{source.used_cached_items ? "是" : "否"}</p><p>本次返回：{source.item_count} 条</p><a href={source.source_url} target="_blank" rel="noreferrer" aria-label={`查看来源 ${source.source_name}`} className="inline-flex items-center gap-1 text-primary hover:underline">查看公开来源<ExternalLink className="h-3 w-3" /></a></div>
      </article>)}</div>
    </section>
  </div>;
}
