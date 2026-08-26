import type { PropsWithChildren, ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import type { IndustryMetric } from "@/lib/api";
import { EvidenceDrawer } from "../EvidenceDrawer";
import { TruthAxes } from "../IndustryTruthBadge";

export const REPORT_SECTIONS = [
  ["overview", "总览"], ["cycle", "周期"], ["chain", "产业链"], ["metrics", "核心数据"],
  ["capital", "资金与估值"], ["companies", "核心公司"], ["funds", "相关基金"], ["news-risk", "新闻与风险"],
] as const;

export function ReportSection({ id, index, title, eyebrow, children }: PropsWithChildren<{
  id: string; index: string; title: string; eyebrow: string;
}>) {
  return (
    <section id={id} role="region" aria-label={title} className="relative scroll-mt-40 border-b border-border/60 px-5 py-9 pl-10 last:border-0 sm:px-8 sm:pl-14">
      <span aria-hidden="true" className="absolute bottom-0 left-5 top-0 w-px bg-border/80 sm:left-7" />
      <header className="relative mb-6 flex items-start gap-4">
        <span className="absolute -left-[1.85rem] inline-flex h-5 w-5 items-center justify-center rounded-full border border-primary/50 bg-background font-mono text-xs font-semibold text-primary sm:-left-[2.35rem]">{index}</span>
        <div>
          <p className="text-xs uppercase leading-5 tracking-[0.24em] text-muted-foreground">{eyebrow}</p>
          <h2 className="mt-1 text-xl font-bold tracking-tight sm:text-2xl">{title}</h2>
        </div>
      </header>
      {children}
    </section>
  );
}

const emptyReasonLabel: Record<string, string> = {
  source_unconfigured: "来源未配置", source_unavailable: "暂无可靠数据", source_failed: "暂无可靠数据",
  verifying: "待核验", not_applicable: "不适用", not_disclosed: "尚未披露",
  user_key_not_configured: "来源未配置", license_required: "来源未配置", expired: "已失效",
  conflicting: "数据冲突", no_reliable_data: "暂无可靠数据", insufficient_history: "历史样本不足",
};

export function formatEmptyReason(metric: IndustryMetric): string {
  return metric.currentValue == null ? (emptyReasonLabel[metric.emptyReason ?? ""] ?? "暂无可靠数据") : "";
}

function metricValue(metric: IndustryMetric) {
  if (metric.currentValue == null) return <span className="text-muted-foreground">{formatEmptyReason(metric)}</span>;
  return <span>{metric.currentValue}{metric.unit ? ` ${metric.unit}` : ""}</span>;
}

function commonSupportedFields(metric: IndustryMetric): string[] {
  const supporting = metric.evidence.filter((item) => item.supportsClaim && !item.contradictsClaim);
  if (supporting.length === 0) return [];
  return supporting.slice(1).reduce(
    (common, item) => common.filter((field) => item.supportsFields.includes(field)),
    [...supporting[0].supportsFields],
  );
}

function historicalPosition(metric: IndustryMetric): string {
  if (metric.historicalPosition) {
    return `${metric.historicalPosition.value}% · ${metric.historicalPosition.window} · ${metric.historicalPosition.method}`;
  }
  return metric.emptyReason === "insufficient_history" ? "历史样本不足" : "暂无可靠数据";
}

export function MetricRows({ metrics, compact = false }: { metrics: IndustryMetric[]; compact?: boolean }) {
  return (
    <div className="divide-y divide-border/60 overflow-hidden rounded-xl border border-border/70">
      {metrics.map((metric) => (
        <article key={`${metric.industryId}-${metric.metricId}`} data-industry-id={metric.industryId} className={`bg-background/40 ${compact ? "p-3" : "p-4 sm:p-5"}`}>
          <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
            <div className="min-w-0"><p className="text-sm font-semibold">{metric.label}</p><p className="mt-1 font-mono text-xs leading-5 text-muted-foreground">{metric.metricId}</p></div>
            <TruthAxes metric={metric} />
          </div>
          <div className="mt-4 grid gap-4 text-xs sm:grid-cols-2 xl:grid-cols-[1fr_0.7fr_1.2fr]">
            <dl><dt className="text-muted-foreground">当前值</dt><dd className="mt-1 text-base font-semibold">{metricValue(metric)}</dd></dl>
            <dl><dt className="text-muted-foreground">环比或同比</dt><dd className={`mt-1 font-mono ${metric.change && metric.change.value > 0 ? "text-destructive" : metric.change && metric.change.value < 0 ? "text-success" : ""}`}>{metric.change ? `${metric.change.value > 0 ? "+" : ""}${metric.change.value}% · ${metric.change.basis.toUpperCase()}` : "暂无可靠数据"}</dd></dl>
            <dl><dt className="text-muted-foreground">历史位置</dt><dd className="mt-1">{historicalPosition(metric)}</dd></dl>
          </div>
          {metric.verificationStatus === "corroborated" && <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 rounded-lg border border-purple-400/30 bg-purple-500/5 px-3 py-2 text-xs leading-5 text-muted-foreground">
            <span>{metric.independentSourceFamilies.length} 个独立来源族</span>
            <span>{metric.independentContentSources.length} 个独立内容来源</span>
            <span>{metric.independentOriginClusters.length} 个独立起源集群</span>
            <span>共同支持字段：{commonSupportedFields(metric).join(" · ") || "暂无可靠数据"}</span>
          </div>}
          <dl className="mt-4 grid gap-x-6 gap-y-3 border-t border-border/50 pt-4 text-xs sm:grid-cols-2">
            <div><dt className="text-muted-foreground">数据来源</dt><dd className="mt-1">{metric.evidence.length ? metric.evidence.map((item) => item.contentSource).join(" · ") : formatEmptyReason(metric)}</dd></div>
            <div><dt className="text-muted-foreground">数据更新时间</dt><dd className="mt-1 font-mono">{metric.asOfDate ?? metric.fetchedAt ?? "暂无可靠数据"}</dd></div>
            <div><dt className="text-muted-foreground">数据口径</dt><dd className="mt-1">{metric.methodology || "暂无可靠数据"}</dd></div>
            <div><dt className="text-muted-foreground">判断依据</dt><dd className="mt-1">{metric.judgmentBasis.length ? metric.judgmentBasis.join("；") : "暂无可靠数据"}</dd></div>
            <div><dt className="text-muted-foreground">失效条件</dt><dd className="mt-1">{metric.invalidatingConditions.length ? metric.invalidatingConditions.join("；") : "暂无可靠数据"}</dd></div>
            <div><dt className="text-muted-foreground">对应证据</dt><dd className="mt-1">{metric.evidence.length ? `${metric.evidence.length} 条 · ${metric.verificationStatus === "corroborated" ? "多源印证" : metric.verificationStatus === "verified" ? "已核验" : "待核验"}` : formatEmptyReason(metric)}</dd></div>
          </dl>
          <div className="mt-3"><EvidenceDrawer metric={metric} /></div>
        </article>
      ))}
    </div>
  );
}

export function EmptyEvidence({ children }: { children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-border/70 bg-muted/10 px-5 py-8 text-center">
      <AlertTriangle className="mx-auto h-5 w-5 text-muted-foreground" aria-hidden="true" /><p className="mt-2 font-medium text-muted-foreground">暂无可靠数据</p>
      <p className="mt-1 text-xs text-muted-foreground/70">{children || "接入可核验数据后在此展示，不使用 AI 补造。"}</p>
    </div>
  );
}
