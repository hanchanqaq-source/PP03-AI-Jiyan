import { ArrowDown } from "lucide-react";
import type { IndustryReportTemplate } from "../types";
import { ReportSection, TruthBadge } from "./shared";

export function ChainSection({ template }: { template: IndustryReportTemplate }) {
  return <ReportSection id="chain" index="03" title="产业链全景" eyebrow="Value chain"><div className="mx-auto max-w-3xl">{template.chain.map((node, index) => <div key={node.name}>{index > 0 && <ArrowDown className="mx-auto my-2 h-5 w-5 text-primary/60" />}<div className="rounded-xl border border-border/70 bg-muted/10 p-5"><div className="flex items-center justify-between gap-3"><h3 className="font-semibold">{node.name}</h3><TruthBadge status={node.status} /></div><p className="mt-2 text-sm text-muted-foreground">{node.description}</p><p className="mt-3 text-xs text-muted-foreground/70">代表公司 / 相关资讯 / 相关基金：暂无可靠数据</p></div></div>)}</div></ReportSection>;
}
