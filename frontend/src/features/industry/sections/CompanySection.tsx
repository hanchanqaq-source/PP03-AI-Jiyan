import type { DisplayedIndustryReport } from "@/lib/api";
import { EmptyEvidence, ReportSection } from "./shared";

type Company = DisplayedIndustryReport["companies"][number];

function relationLabel(company: Company): string {
  return company.relationType === "official_disclosure" ? "官方披露" : "公开分类";
}

function evidenceLabel(company: Company): string {
  return company.evidenceIds.join(" · ") || "暂无可靠数据";
}

export function CompanySection({ companies }: { companies: DisplayedIndustryReport["companies"] }) {
  return <ReportSection id="companies" index="06" title="核心公司" eyebrow="Companies">
    <p className="mb-5 text-sm font-medium text-warning">观察对象，不构成推荐</p>
    {companies.length === 0 ? <EmptyEvidence>公司关系缺少证券代码、官方披露或分类证据时不会展示。</EmptyEvidence> : <>
      <div className="hidden overflow-x-auto rounded-xl border border-border/70 md:block">
        <table className="w-full min-w-[860px] text-left text-sm">
          <thead className="bg-muted/20 text-xs leading-5 text-muted-foreground"><tr><th className="p-3">公司</th><th className="p-3">产业链位置</th><th className="p-3">关键指标</th><th className="p-3">关系与来源</th><th className="p-3">数据日期 / 证据</th></tr></thead>
          <tbody className="divide-y divide-border/60">{companies.map((company) => <tr key={company.securityCode} data-industry-id={company.industryId}>
            <td className="p-3"><p className="font-semibold">{company.companyName}</p><p className="font-mono text-xs leading-5 text-muted-foreground">{company.securityCode}</p></td>
            <td className="p-3">{company.chainNodeId}</td>
            <td className="p-3"><p className="text-xs leading-5 text-muted-foreground">与当前结论的关系</p><p>{company.keyMetricIds.length ? company.keyMetricIds.join(" · ") : "暂无可靠数据"}</p></td>
            <td className="p-3"><p className="text-xs leading-5 text-muted-foreground">关系类型</p><p>{relationLabel(company)}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">数据来源</p><p>来源未配置</p></td>
            <td className="p-3"><p>{company.asOfDate}</p><p className="mt-1 font-mono text-xs leading-5 text-muted-foreground">{evidenceLabel(company)}</p></td>
          </tr>)}</tbody>
        </table>
      </div>
      <div className="space-y-3 md:hidden">{companies.map((company) => <article key={company.securityCode} data-testid={`company-mobile-${company.securityCode}`} data-industry-id={company.industryId} className="rounded-xl border border-border/70 p-4 text-sm">
        <div data-mobile-field-group><p className="font-semibold">{company.companyName}</p><p className="font-mono text-xs leading-5 text-muted-foreground">{company.securityCode}</p><p className="mt-2 text-xs leading-5">产业链位置：{company.chainNodeId}</p></div>
        <div data-mobile-field-group className="mt-3 border-t border-border/50 pt-3"><p className="text-xs leading-5 text-muted-foreground">与当前结论的关系</p><p>{company.keyMetricIds.length ? company.keyMetricIds.join(" · ") : "暂无可靠数据"}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">关系类型</p><p>{relationLabel(company)}</p></div>
        <div data-mobile-field-group className="mt-3 border-t border-border/50 pt-3"><p className="text-xs leading-5 text-muted-foreground">数据来源</p><p>来源未配置</p><p className="mt-2">{company.asOfDate}</p><p className="mt-1 font-mono text-xs leading-5 text-muted-foreground">{evidenceLabel(company)}</p></div>
      </article>)}</div>
    </>}
  </ReportSection>;
}
