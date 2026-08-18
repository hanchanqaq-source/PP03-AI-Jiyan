import { useEffect, useRef, useState, type RefObject } from "react";
import { Loader2, Trash2, X } from "lucide-react";
import type { CacheStatus } from "./types";
import { api } from "@/lib/api";
import { SourceHealthSummary } from "@/features/source-health/SourceHealthSummary";

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(2)} KB`;
  return `${bytes} B`;
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "暂无记录";
}

interface DataInfoDialogProps {
  open: boolean;
  onClose: () => void;
  onOpenSourceHealth?: () => void;
  onSourceHealthUpdated?: () => void;
  sourceHealthDetailsRef?: RefObject<HTMLButtonElement | null>;
  autoFocusSourceHealthDetails?: boolean;
}

export function DataInfoDialog({
  open,
  onClose,
  onOpenSourceHealth = () => {},
  onSourceHealthUpdated,
  sourceHealthDetailsRef,
  autoFocusSourceHealthDetails = false,
}: DataInfoDialogProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousRef = useRef<HTMLElement | null>(null);
  const [hasOpened, setHasOpened] = useState(open);
  const [status, setStatus] = useState<CacheStatus | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [releasedBytes, setReleasedBytes] = useState<number | null>(null);
  useEffect(() => {
    if (!open) return;
    setHasOpened(true);
    previousRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (autoFocusSourceHealthDetails) sourceHealthDetailsRef?.current?.focus();
    else closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleKey);
    return () => { document.removeEventListener("keydown", handleKey); previousRef.current?.focus(); };
  }, [autoFocusSourceHealthDetails, onClose, open, sourceHealthDetailsRef]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setStatus(null);
    setStatusError(false);
    setReleasedBytes(null);
    api.cacheStatus().then((nextStatus) => {
      if (active) setStatus(nextStatus);
    }).catch(() => {
      if (active) setStatusError(true);
    });
    return () => { active = false; };
  }, [open]);

  const cleanup = async () => {
    if (cleaning) return;
    setCleaning(true);
    setReleasedBytes(null);
    try {
      const result = await api.cacheCleanupExpired();
      setStatus(result.status);
      setStatusError(false);
      setReleasedBytes(result.released_bytes);
    } catch {
      setStatusError(true);
    } finally {
      setCleaning(false);
    }
  };
  if (!open && !hasOpened) return null;
  return <div hidden={!open} aria-hidden={!open ? "true" : undefined} className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><section role="dialog" aria-modal="true" aria-label="市场资讯数据说明" className="glass max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-primary/30 p-5 shadow-2xl">
    <header className="flex items-start justify-between gap-3"><div><h2 className="text-lg font-bold">数据说明</h2><p className="mt-1 text-xs text-muted-foreground">资讯、持仓与 AI 的边界</p></div><button ref={closeRef} onClick={onClose} aria-label="关闭数据说明" className="rounded-lg p-2 text-muted-foreground hover:bg-muted"><X className="h-4 w-4" /></button></header>
    <div className="mt-4 space-y-3 text-sm leading-6 text-muted-foreground"><p><strong className="text-foreground">资讯来源：</strong>公开 RSS、公司公告、政策与公开产业内容；每个事件保留原始链接、发布时间和来源状态。</p><p><strong className="text-foreground">持仓依据：</strong>只使用基金最新公开前十大持仓与有来源的上市公司行业分类，不根据基金名称生成直接关系。</p><p><strong className="text-foreground">更新时间：</strong>刷新失败时继续显示最后一次有效缓存，并明确标记缓存、过期或来源失败。</p><p><strong className="text-foreground">AI 边界：</strong>AI 仅可辅助摘要、解释和原文翻译；不可编造新闻、持仓或数字，也不会输出买入、卖出、加仓或减仓指令。</p><section className="rounded-lg border border-primary/25 bg-primary/5 p-3 text-xs"><p className="font-semibold text-foreground">资讯核验与数据源健康是两套不同机制。</p><p className="mt-1">数据源健康只表示来源当前是否可访问、可解析和足够新鲜；资讯核验表示事件的核心主张是否有一手证据或独立来源支持。</p><p className="mt-1">AI 翻译、AI 摘要和来源数量不会自动提高核验等级。</p><p className="mt-1">本轮只做页面原型，实际核验能力将在 A1.1-W1 实现。</p></section></div>
    <SourceHealthSummary
      onOpenDetails={onOpenSourceHealth}
      onUpdated={onSourceHealthUpdated}
      detailsButtonRef={sourceHealthDetailsRef}
      autoFocusDetails={autoFocusSourceHealthDetails}
    />
    <section className="mt-5 rounded-xl border border-border/60 bg-muted/15 p-4" aria-label="缓存管理">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-foreground">缓存管理</h3><p className="mt-1 text-xs text-muted-foreground">只清理已过期缓存和临时文件，不清空持仓、标签、设置或有效缓存。</p></div><button onClick={cleanup} disabled={cleaning} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:border-primary/45 disabled:cursor-not-allowed disabled:opacity-50">{cleaning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}清理过期缓存</button></div>
      {status && <div className="mt-3 grid gap-1 text-xs text-muted-foreground sm:grid-cols-2"><p>当前占用：{formatBytes(status.total_bytes)}</p><p>可回收：{formatBytes(status.reclaimable_bytes)}</p><p>文件数量：{status.file_count}</p><p>过期数量：{status.expired_count}</p><p className="sm:col-span-2">上次清理：{formatTime(status.last_auto_cleanup_at)}</p></div>}
      {!status && !statusError && <p className="mt-3 text-xs text-muted-foreground">正在读取缓存状态…</p>}
      {statusError && <p role="alert" className="mt-3 text-xs text-warning">缓存状态暂不可用，资讯和持仓关联仍可正常使用。</p>}
      {releasedBytes !== null && <p className="mt-3 text-xs font-medium text-primary">本次释放：{formatBytes(releasedBytes)}</p>}
    </section>
    <footer className="mt-5 flex justify-end"><button onClick={onClose} className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground">知道了</button></footer>
  </section></div>;
}
