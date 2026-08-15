import type { IndustryReportTemplate } from "../types";
import { FieldGrid, ReportSection } from "./shared";

export function MetricsSection({ template }: { template: IndustryReportTemplate }) {
  return <ReportSection id="metrics" index="04" title="核心数据" eyebrow="Key metrics"><p className="mb-5 text-sm text-muted-foreground">指标按行业模板区分；未接可靠数据源前只展示指标定义。</p><FieldGrid fields={template.metrics} /></ReportSection>;
}
