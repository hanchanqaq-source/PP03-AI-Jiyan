import type { IndustryReportTemplate } from "../types";
import { FieldGrid, ReportSection, TruthBadge } from "./shared";

export function SummarySection({ template }: { template: IndustryReportTemplate }) {
  return (
    <ReportSection id="overview" index="01" title="行业核心结论" eyebrow="Executive view">
      <div className="mb-6 rounded-xl border border-primary/25 bg-primary/5 p-5">
        <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs font-semibold uppercase tracking-widest text-primary">当前判断</p><TruthBadge status="development" /></div>
        <p className="mt-3 text-base leading-7">{template.judgement}</p>
      </div>
      <FieldGrid fields={template.summary} />
      <div className="mt-7 grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div>
          <h3 className="text-sm font-semibold">判断依据</h3>
          <ol className="mt-3 space-y-2 text-sm text-muted-foreground">{template.basis.map((item, index) => <li key={item} className="flex gap-3"><span className="font-mono text-primary">{index + 1}.</span><span>{item}</span></li>)}</ol>
        </div>
        <dl className="space-y-4 rounded-xl border border-border/60 p-4 text-sm">
          <div><dt className="text-xs text-muted-foreground">判断置信度</dt><dd className="mt-1 font-medium">{template.confidence}</dd></div>
          <div><dt className="text-xs text-muted-foreground">数据来源</dt><dd className="mt-1">PP03 任务书模板；实时数据尚未接入</dd></div>
          <div><dt className="text-xs text-muted-foreground">数据更新时间</dt><dd className="mt-1">尚未接入</dd></div>
        </dl>
      </div>
      <div className="mt-6"><h3 className="text-sm font-semibold">可能失效条件</h3><ul className="mt-3 grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">{template.invalidatingConditions.map((item) => <li key={item} className="border-l-2 border-warning/50 pl-3">{item}</li>)}</ul></div>
    </ReportSection>
  );
}
