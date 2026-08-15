import type { IndustryReportTemplate } from "../types";
import { EmptyEvidence, ReportSection } from "./shared";

export function FundSection({ template }: { template: IndustryReportTemplate }) {
  return <ReportSection id="funds" index="07" title="相关基金" eyebrow="Related funds">{template.funds.length === 0 ? <EmptyEvidence>尚未接入可核验的基金净值、规模、经理、持仓、回撤和波动率来源，因此不生成候选排行榜。</EmptyEvidence> : null}<p className="mt-4 text-xs text-muted-foreground">候选规则将解释进入原因、优势、风险、持仓重合和失效条件，不提供唯一推荐。</p></ReportSection>;
}
