import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, FileSearch, X } from "lucide-react";
import type { IndustryMetric } from "@/lib/api";
import { VerificationBadge } from "./IndustryTruthBadge";

function safeExternalUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

export function EvidenceDrawer({ metric, triggerLabel }: { metric: IndustryMetric; triggerLabel?: string }) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const closeAndRestoreFocus = useCallback(() => {
    setOpen(false);
    requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeAndRestoreFocus();
      } else if (event.key === "Tab") {
        const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])') ?? []);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [closeAndRestoreFocus, open]);
  if (metric.evidence.length === 0) return <span className="text-xs text-muted-foreground">无可展开证据</span>;
  return (
    <>
      <button ref={triggerRef} type="button" aria-label={triggerLabel ?? `查看 ${metric.label}证据`} onClick={() => setOpen(true)}
        className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-xs text-foreground hover:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
        <FileSearch className="h-4 w-4" aria-hidden="true" />查看证据
      </button>
      {open && <div className="fixed inset-0 z-50 flex justify-end bg-black/55" onMouseDown={(event) => { if (event.currentTarget === event.target) closeAndRestoreFocus(); }}>
        <aside ref={drawerRef} role="dialog" aria-modal="true" aria-label={`${metric.label}证据`} className="h-full w-full max-w-lg overflow-y-auto border-l border-border bg-card p-6 shadow-2xl motion-reduce:transition-none">
          <div className="flex items-start justify-between gap-4"><div><p className="font-mono text-xs uppercase leading-5 tracking-[0.22em] text-primary">Evidence trail</p><h2 className="mt-2 text-xl font-bold">{metric.label}证据</h2></div>
            <button ref={closeRef} type="button" aria-label="关闭证据" onClick={closeAndRestoreFocus} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><X className="h-4 w-4" /></button></div>
          <dl className="mt-6 grid gap-3 rounded-xl border border-border/70 p-4 text-xs">
            <div><dt className="text-muted-foreground">原始快照</dt><dd className="mt-1 font-mono">{metric.rawSnapshotId}</dd></div>
            <div><dt className="text-muted-foreground">证据快照</dt><dd className="mt-1 font-mono">{metric.evidenceSnapshotId}</dd></div>
            <div><dt className="text-muted-foreground">数据日期</dt><dd className="mt-1 font-mono">{metric.asOfDate ?? metric.fetchedAt ?? "暂无可靠数据"}</dd></div>
            <div><dt className="text-muted-foreground">可信状态</dt><dd className="mt-1"><VerificationBadge status={metric.verificationStatus} /></dd></div>
            <div><dt className="text-muted-foreground">数据口径</dt><dd className="mt-1">{metric.methodology}</dd></div>
            <div><dt className="text-muted-foreground">判断依据</dt><dd className="mt-1">{metric.judgmentBasis.join("；") || "暂无可靠数据"}</dd></div>
            <div><dt className="text-muted-foreground">失效条件</dt><dd className="mt-1">{metric.invalidatingConditions.join("；") || "暂无可靠数据"}</dd></div>
          </dl>
          <div className="mt-5 space-y-3">{metric.evidence.map((evidence) => {
            const safeUrl = safeExternalUrl(evidence.finalUrl);
            return <article key={evidence.evidenceId} className="rounded-xl border border-border/70 p-4 text-xs">
            <div className="flex items-start justify-between gap-3"><div><p className="font-semibold">{evidence.contentSource}</p><p className="mt-1 font-mono text-muted-foreground">{evidence.evidenceId}</p></div><span className="rounded-full border border-border px-2 py-1">{evidence.contradictsClaim ? "反驳" : "支持"}</span></div>
            <dl className="mt-3 space-y-2 text-muted-foreground"><div><dt className="inline">来源族：</dt><dd className="inline">{evidence.sourceFamilyId}</dd></div><div><dt className="inline">起源集群：</dt><dd className="inline">{evidence.originCluster}</dd></div><div><dt className="inline">数据日期：</dt><dd className="inline">{evidence.asOfDate ?? "暂无可靠数据"}</dd></div><div><dt className="inline">核验时间：</dt><dd className="inline">{evidence.verifiedAt}</dd></div></dl>
            {safeUrl ? <a href={safeUrl} target="_blank" rel="noopener noreferrer" className="mt-3 inline-flex min-h-11 items-center gap-2 text-primary">打开原始引用<ExternalLink className="h-3 w-3" /></a>
              : <p className="mt-3 text-xs leading-5 text-muted-foreground">原始引用不可安全打开</p>}
          </article>;
          })}</div>
        </aside>
      </div>}
    </>
  );
}
