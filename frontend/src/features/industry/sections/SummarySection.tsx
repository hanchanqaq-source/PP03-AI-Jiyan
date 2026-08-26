import type { DisplayedIndustryReport } from "@/lib/api";
import { VerificationBadge } from "../IndustryTruthBadge";
import { ReportSection } from "./shared";

const stage = { recovery: "复苏", expansion: "扩张", peak: "高位", contraction: "收缩" } as const;
const outlook = { improving: "改善", stable: "平稳", weakening: "转弱" } as const;
const confidence = { high: "高", medium: "中", low: "低" } as const;

export function SummarySection({ report }: { report: DisplayedIndustryReport }) {
  const overview = report.overview;
  return (
    <ReportSection id="overview" index="01" title="行业总览" eyebrow="Executive view">
      <div className="mb-6 rounded-xl border border-primary/25 bg-primary/5 p-5">
        <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs font-semibold uppercase tracking-widest text-primary">可信结论</p><VerificationBadge status={overview.status} /></div>
        <p className="mt-3 text-base leading-7">{overview.text || "暂无可靠数据"}</p>
      </div>
      <dl className="grid gap-px overflow-hidden rounded-xl border border-border/70 bg-border/70 sm:grid-cols-2 xl:grid-cols-4">
        <div className="bg-background p-4"><dt className="text-xs text-muted-foreground">当前周期阶段</dt><dd className="mt-2 font-semibold">{overview.cycleStage ? stage[overview.cycleStage] : "暂无可靠数据"}</dd></div>
        <div className="bg-background p-4"><dt className="text-xs text-muted-foreground">景气方向</dt><dd className="mt-2 font-semibold">{overview.outlookDirection ? outlook[overview.outlookDirection] : "暂无可靠数据"}</dd></div>
        <div className="bg-background p-4"><dt className="text-xs text-muted-foreground">数据完整度</dt><dd className="mt-2 font-semibold">{overview.dataCompleteness.ratio == null ? "暂无可靠数据" : `${Math.round(overview.dataCompleteness.ratio * 100)}% (${overview.dataCompleteness.verifiedMetricCount}/${overview.dataCompleteness.requiredMetricCount})`}</dd></div>
        <div className="bg-background p-4"><dt className="text-xs text-muted-foreground">可信度</dt><dd className="mt-2 font-semibold">{overview.confidenceLevel ? confidence[overview.confidenceLevel] : "暂无可靠数据"}</dd></div>
      </dl>
      <div className="mt-7 grid gap-6 lg:grid-cols-[1.35fr_1fr]">
        <div><h3 className="text-sm font-semibold">判断依据</h3><ol className="mt-3 space-y-2 text-sm text-muted-foreground">{overview.basisMetricIds.length ? overview.basisMetricIds.map((item, index) => <li key={item} className="flex gap-3"><span className="font-mono text-primary">{index + 1}.</span><span>{item}</span></li>) : <li>暂无可靠数据</li>}</ol></div>
        <dl className="space-y-4 border-l border-border/70 pl-5 text-sm"><div><dt className="text-xs text-muted-foreground">报告快照</dt><dd className="mt-1 font-mono">{report.displayedTrustedSnapshotId ?? "暂无可靠数据"}</dd></div><div><dt className="text-xs text-muted-foreground">规则版本</dt><dd className="mt-1 font-mono">{overview.ruleVersion}</dd></div><div><dt className="text-xs text-muted-foreground">对应证据</dt><dd className="mt-1">{overview.evidenceIds.length ? overview.evidenceIds.join(" · ") : "暂无可靠数据"}</dd></div></dl>
      </div>
      <div className="mt-6"><h3 className="text-sm font-semibold">失效条件</h3><ul className="mt-3 grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">{overview.invalidatingConditions.length ? overview.invalidatingConditions.map((item) => <li key={item} className="border-l-2 border-warning/50 pl-3">{item}</li>) : <li>暂无可靠数据</li>}</ul></div>
    </ReportSection>
  );
}
