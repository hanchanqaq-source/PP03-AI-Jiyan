import { FundRelationResolver } from "../FundRelationResolver";
import { EmptyEvidence, ReportSection } from "./shared";

export function FundSection({ industryId }: { industryId: string }) {
  return <ReportSection id="funds" index="07" title="相关基金" eyebrow="Related funds"><div className="space-y-7">
    <section role="region" aria-labelledby="my-holdings-relations" className="border-l-2 border-border pl-4"><h3 id="my-holdings-relations" className="text-base font-semibold">我的持仓关联</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">本 Work 不读取真实持仓；显式输入的基金代码不会被标记为你的持仓。</p><div className="mt-3"><EmptyEvidence>未读取真实持仓，当前没有可展示的持仓关联。</EmptyEvidence></div></section>
    <section role="region" aria-labelledby="public-fund-relations" className="border-l-2 border-primary/40 pl-4"><h3 id="public-fund-relations" className="text-base font-semibold">公开披露关联基金</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">只解析本次显式代码对应的官方行业配置或披露持仓穿透；不生成排行或投资建议。</p><div className="mt-4"><FundRelationResolver industryId={industryId} /></div></section>
  </div></ReportSection>;
}
