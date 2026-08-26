import { FundRelationResolver } from "../FundRelationResolver";
import { ReportSection } from "./shared";

export function FundSection({ industryId }: { industryId: string }) {
  return <ReportSection id="funds" index="07" title="相关基金" eyebrow="Related funds"><FundRelationResolver industryId={industryId} /><p className="mt-4 text-xs text-muted-foreground">只解析本次显式基金代码选择；不会读取或保存用户资产明细、私人记录或账户信息，也不生成排行或投资建议。</p></ReportSection>;
}
