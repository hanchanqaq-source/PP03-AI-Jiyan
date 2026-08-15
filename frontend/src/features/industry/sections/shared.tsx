import type { PropsWithChildren, ReactNode } from "react";
import { AlertTriangle, Database, FlaskConical } from "lucide-react";
import type { DataField, TruthStatus } from "../types";

export const REPORT_SECTIONS = [
  ["overview", "总览"], ["cycle", "周期"], ["chain", "产业链"], ["metrics", "核心数据"],
  ["capital", "资金与估值"], ["companies", "核心公司"], ["funds", "相关基金"], ["news-risk", "新闻与风险"],
] as const;

export function TruthBadge({ status }: { status: TruthStatus }) {
  const cfg = status === "verified"
    ? { text: "真实数据", icon: Database, cls: "border-success/30 bg-success/10 text-success" }
    : status === "development"
      ? { text: "开发占位数据", icon: FlaskConical, cls: "border-warning/30 bg-warning/10 text-warning" }
      : { text: "暂无可靠数据", icon: AlertTriangle, cls: "border-border bg-muted/40 text-muted-foreground" };
  const Icon = cfg.icon;
  return <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] ${cfg.cls}`}><Icon className="h-3 w-3" />{cfg.text}</span>;
}

export function ReportSection({ id, index, title, eyebrow, children }: PropsWithChildren<{
  id: string; index: string; title: string; eyebrow: string;
}>) {
  return (
    <section id={id} className="scroll-mt-40 border-b border-border/60 px-5 py-9 last:border-0 sm:px-8">
      <header className="mb-6 flex items-start gap-4">
        <span className="font-mono text-xs font-semibold tracking-[0.22em] text-primary">{index}</span>
        <div>
          <p className="text-[10px] uppercase tracking-[0.24em] text-muted-foreground">{eyebrow}</p>
          <h2 className="mt-1 text-xl font-bold tracking-tight sm:text-2xl">{title}</h2>
        </div>
      </header>
      {children}
    </section>
  );
}

export function FieldGrid({ fields }: { fields: DataField[] }) {
  return (
    <div className="grid gap-px overflow-hidden rounded-xl border border-border/60 bg-border/60 sm:grid-cols-2 lg:grid-cols-3">
      {fields.map((field) => (
        <div key={field.label} className="bg-background/90 p-4">
          <div className="flex items-start justify-between gap-2"><p className="text-xs text-muted-foreground">{field.label}</p><TruthBadge status={field.status} /></div>
          <p className="mt-3 text-base font-semibold">{field.value == null ? "暂无可靠数据" : `${field.value}${field.unit || ""}`}</p>
          {field.note && <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{field.note}</p>}
          <dl className="mt-3 space-y-1 border-t border-border/40 pt-2 text-[10px] text-muted-foreground/70">
            <div className="flex justify-between gap-3"><dt>数据来源</dt><dd className="text-right">{field.source}</dd></div>
            <div className="flex justify-between gap-3"><dt>数据更新时间</dt><dd>{field.updatedAt || "尚未接入"}</dd></div>
          </dl>
        </div>
      ))}
    </div>
  );
}

export function EmptyEvidence({ children }: { children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-border/70 bg-muted/10 px-5 py-8 text-center">
      <p className="font-medium text-muted-foreground">暂无可靠数据</p>
      <p className="mt-1 text-xs text-muted-foreground/70">{children || "接入可核验数据后在此展示，不使用 AI 补造。"}</p>
    </div>
  );
}
