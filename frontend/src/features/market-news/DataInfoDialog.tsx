import { useEffect, useRef } from "react";
import { X } from "lucide-react";

export function DataInfoDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open) return;
    previousRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleKey);
    return () => { document.removeEventListener("keydown", handleKey); previousRef.current?.focus(); };
  }, [onClose, open]);
  if (!open) return null;
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><section role="dialog" aria-modal="true" aria-label="市场资讯数据说明" className="glass w-full max-w-2xl rounded-2xl border border-primary/30 p-5 shadow-2xl">
    <header className="flex items-start justify-between gap-3"><div><h2 className="text-lg font-bold">数据说明</h2><p className="mt-1 text-xs text-muted-foreground">资讯、持仓与 AI 的边界</p></div><button ref={closeRef} onClick={onClose} aria-label="关闭数据说明" className="rounded-lg p-2 text-muted-foreground hover:bg-muted"><X className="h-4 w-4" /></button></header>
    <div className="mt-4 space-y-3 text-sm leading-6 text-muted-foreground"><p><strong className="text-foreground">资讯来源：</strong>公开 RSS、公司公告、政策与公开产业内容；每个事件保留原始链接、发布时间和来源状态。</p><p><strong className="text-foreground">持仓依据：</strong>只使用基金最新公开前十大持仓与有来源的上市公司行业分类，不根据基金名称生成直接关系。</p><p><strong className="text-foreground">更新时间：</strong>刷新失败时继续显示最后一次有效缓存，并明确标记缓存、过期或来源失败。</p><p><strong className="text-foreground">AI 边界：</strong>AI 仅可辅助摘要和解释；不可编造新闻、持仓或数字，也不会输出买入、卖出、加仓或减仓指令。</p></div>
    <footer className="mt-5 flex justify-end"><button onClick={onClose} className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground">知道了</button></footer>
  </section></div>;
}
