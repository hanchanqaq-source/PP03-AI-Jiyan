import { ArrowRight } from "lucide-react";
import type { DisplayedIndustryReport } from "@/lib/api";
import { VerificationBadge } from "../IndustryTruthBadge";
import { ReportSection } from "./shared";

export function ChainSection({ nodes }: { nodes: DisplayedIndustryReport["chain"] }) {
  return <ReportSection id="chain" index="03" title="产业链" eyebrow="Value chain"><div className="flex flex-col gap-2 lg:flex-row lg:items-stretch">{nodes.map((node, index) => <div key={node.nodeId} className="contents">{index > 0 && <ArrowRight className="mx-auto h-5 w-5 shrink-0 rotate-90 self-center text-primary/60 lg:rotate-0" />}<article data-industry-id={node.industryId} className="min-w-0 flex-1 border-l-2 border-primary/30 bg-muted/10 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{node.label}</h3><VerificationBadge status={node.status} /></div><p className="mt-3 text-xs text-muted-foreground">观察：{node.observationIds.length ? node.observationIds.join(" · ") : "暂无可靠数据"}</p><p className="mt-2 text-xs text-muted-foreground">证据：{node.evidenceIds.length ? node.evidenceIds.join(" · ") : "暂无可靠数据"}</p></article></div>)}</div></ReportSection>;
}
