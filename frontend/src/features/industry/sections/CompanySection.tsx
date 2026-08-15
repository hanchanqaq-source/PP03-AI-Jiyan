import type { IndustryReportTemplate } from "../types";
import { EmptyEvidence, ReportSection } from "./shared";

export function CompanySection({ template }: { template: IndustryReportTemplate }) {
  return <ReportSection id="companies" index="06" title="核心公司" eyebrow="Companies">{template.companies.length === 0 ? <EmptyEvidence>需要公司业务、产业链位置、事件、风险和基金持仓来源。</EmptyEvidence> : null}</ReportSection>;
}
