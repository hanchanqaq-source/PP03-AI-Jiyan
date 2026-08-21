import { useEffect, useRef } from "react";
import { ExternalLink, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { EvidenceEventDetail, EvidenceItem, VerificationStatus } from "./types";

export const STATUS_LABEL: Record<VerificationStatus, string> = {
  verified: "已核验", corroborated: "多源印证", unverified: "待核验", conflicting: "存在冲突", corrected: "已更正", disproved: "已证伪",
};
const STATUS_TONE: Record<VerificationStatus, string> = {
  verified: "border-primary/50 bg-primary/10 text-primary", corroborated: "border-sky-400/50 bg-sky-400/10 text-sky-300",
  unverified: "border-slate-400/45 bg-slate-400/10 text-slate-300", conflicting: "border-orange-400/55 bg-orange-400/10 text-orange-300",
  corrected: "border-primary/50 bg-primary/10 text-primary", disproved: "border-destructive/55 bg-destructive/10 text-destructive",
};

export function StatusBadge({ status }: { status: VerificationStatus }) {
  return <span className={cn("rounded-full border px-2 py-0.5 text-[11px] font-semibold", STATUS_TONE[status])}>{STATUS_LABEL[status]}</span>;
}

function EvidenceList({ title, rows, empty }: { title: string; rows: EvidenceItem[]; empty: string }) {
  return <section><h3 className="font-semibold">{title}</h3>{rows.length ? <ul className="mt-2 space-y-2">{rows.map((row) => <li key={row.evidence_id} className="rounded-lg border border-border/50 bg-background/45 p-3 text-xs"><div className="flex flex-wrap items-center justify-between gap-2"><strong>{row.title || row.content_source}</strong>{row.canonical_url ? <a href={row.canonical_url} target="_blank" rel="noreferrer" aria-label={`打开公开证据 ${row.title || row.content_source}`} className="inline-flex items-center gap-1 text-primary">打开公开证据<ExternalLink className="h-3 w-3" /></a> : <span className="text-muted-foreground">未提供公开链接</span>}</div><p className="mt-1 text-muted-foreground">内容来源：{row.content_source} · 采集来源：{row.collector_source}</p><p className="mt-1 text-muted-foreground">独立来源链：{row.origin_cluster}</p><p className="mt-2 leading-5 text-muted-foreground">{row.excerpt || "未提供公开摘录"}</p></li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">{empty}</p>}</section>;
}

export function EvidenceDrawer({ event, loading, focusHistory, onClose }: { event: EvidenceEventDetail | null; loading: boolean; focusHistory: boolean; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const historyRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!loading && !event) return;
    (focusHistory ? historyRef.current : closeRef.current)?.focus();
    const handler = (keyboardEvent: KeyboardEvent) => {
      if (keyboardEvent.key === "Escape") { onClose(); return; }
      if (keyboardEvent.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'));
      if (!focusable.length) return;
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (keyboardEvent.shiftKey && document.activeElement === first) { keyboardEvent.preventDefault(); last.focus(); }
      else if (!keyboardEvent.shiftKey && document.activeElement === last) { keyboardEvent.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [event, focusHistory, loading, onClose]);
  if (!loading && !event) return null;
  return <div className="fixed inset-0 z-50 bg-black/65" onMouseDown={(mouseEvent) => { if (mouseEvent.currentTarget === mouseEvent.target) onClose(); }}>
    <aside ref={dialogRef} role="dialog" aria-modal="true" aria-label="证据详情" className="ml-auto flex h-full w-full max-w-2xl flex-col border-l border-primary/25 bg-background/95 shadow-2xl backdrop-blur-xl">
      <header className="flex items-start justify-between border-b border-border/60 px-5 py-4"><div><p className="text-[11px] font-semibold tracking-[0.16em] text-primary">确定性证据</p><h2 className="mt-1 text-xl font-bold">{event?.title || "正在载入证据"}</h2>{event && <div className="mt-2 flex items-center gap-2"><StatusBadge status={event.verification_status} /><span className="text-xs text-muted-foreground">最后核验：{new Date(event.verified_at).toLocaleString("zh-CN", { hour12: false })}</span></div>}</div><button ref={closeRef} onClick={onClose} aria-label="关闭证据详情" className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-5 w-5" /></button></header>
      {loading || !event ? <div className="p-5 text-sm text-muted-foreground">正在读取真实证据链…</div> : <div className="flex-1 space-y-5 overflow-y-auto p-5 text-sm">
        <section><h3 className="font-semibold">核心主张</h3><p className="mt-2 rounded-lg border border-border/55 bg-muted/15 p-3">{event.core_claim}</p><p className="mt-2 text-xs text-muted-foreground">判定依据：{event.verification_reason}</p></section>
        <section><h3 className="font-semibold">关键字段</h3>{event.key_fields.length ? <div className="mt-2 space-y-2">{event.key_fields.map((field) => <div key={`${field.field_name}-${field.raw_value}`} className="rounded-lg border border-border/50 p-3 text-xs"><div className="flex flex-wrap items-center justify-between gap-2"><span>{field.field_name}：{field.raw_value}</span><StatusBadge status={field.verification_status} /></div><p className="mt-1 text-muted-foreground">{field.reason}</p></div>)}</div> : <p className="mt-2 text-xs text-muted-foreground">未提取到需要单独核验的金额、比例、数量或日期字段。</p>}</section>
        <EvidenceList title="一手证据" rows={event.primary_evidence} empty="暂无一手证据。" />
        <EvidenceList title="独立来源链" rows={event.independent_evidence} empty="暂无满足独立来源链要求的证据。" />
        <section className="rounded-lg border border-border/55 bg-muted/15 p-3"><h3 className="font-semibold">转载链</h3><p className="mt-1 text-xs">共 {event.syndicated_copies.length} 个转载来源</p><p className="mt-1 text-xs text-muted-foreground">同一原始稿件的转载不会重复计算为独立证据。</p></section>
        <EvidenceList title="冲突与反证" rows={event.contradicting_evidence} empty="当前没有已识别的可靠冲突证据。" />
        <section ref={historyRef} tabIndex={-1} className="rounded-lg border border-primary/20 bg-primary/5 p-3"><h3 className="font-semibold">状态历史</h3>{event.status_history.length ? <ol className="mt-2 space-y-2 text-xs text-muted-foreground">{event.status_history.map((row) => <li key={`${row.changed_at}-${row.to_status}`}>{new Date(row.changed_at).toLocaleString("zh-CN", { hour12: false })} · {row.from_status ? STATUS_LABEL[row.from_status] : "初始"} → {STATUS_LABEL[row.to_status]} · {row.reason}</li>)}</ol> : <p className="mt-2 text-xs text-muted-foreground">暂无状态变更记录。</p>}</section>
      </div>}
      <footer className="border-t border-border/60 p-4 text-xs text-muted-foreground">证据链只使用事件现存公开链接与已配置来源；AI 摘要和来源数量不会提高核验等级。</footer>
    </aside>
  </div>;
}
