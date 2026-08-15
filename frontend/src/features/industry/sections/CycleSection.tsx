import type { IndustryReportTemplate } from "../types";
import { FieldGrid, ReportSection } from "./shared";

export function CycleSection({ template }: { template: IndustryReportTemplate }) {
  return <ReportSection id="cycle" index="02" title="周期位置" eyebrow="Cycle position"><div className="mb-6 flex h-20 items-center rounded-xl border border-border/60 bg-gradient-to-r from-muted/20 via-primary/10 to-muted/20 px-5"><div className="h-px flex-1 bg-border" /><span className="mx-4 rounded-full border border-warning/40 bg-warning/10 px-3 py-1 text-xs text-warning">当前位置待真实数据定位</span><div className="h-px flex-1 bg-border" /></div><FieldGrid fields={template.cycle} /></ReportSection>;
}
