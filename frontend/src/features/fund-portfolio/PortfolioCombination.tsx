import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { IndustryExposurePanel } from "./IndustryExposurePanel";
import { percent } from "./format";
import type { FundPortfolioAnalysisData } from "./types";

export function PortfolioCombination({ data }: { data: FundPortfolioAnalysisData | null }) {
  const [expanded, setExpanded] = useState(false);
  const concentration = data?.industry_concentration;
  const overlaps = data?.overlap || [];
  const shownOverlaps = expanded ? overlaps : overlaps.slice(0, 5);
  const isSingleFund = data?.overview.fund_count === 1;
  const singleExposure = isSingleFund ? data?.holdings[0]?.analysis?.industry_exposure.data || null : null;
  const officialAllocations = (data?.holdings || []).map((holding) => ({
    fundCode: holding.code,
    fundName: holding.name,
    allocation: holding.analysis?.industry_exposure.data?.official_allocation || null,
  }));

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <GlassCard>
        <div className="mb-3 flex items-end justify-between gap-3">
          <div><h2 className="font-semibold">组合重合度</h2><p className="mt-1 text-xs text-muted-foreground">按当前参考市值权重与最新公开前十大持仓估算</p></div>
          {(data?.overview.fund_count || 0) >= 2 && <span className="text-xs text-muted-foreground">{overlaps.length} 只重合证券</span>}
        </div>
        {(data?.overview.fund_count || 0) < 2 ? (
          <p className="rounded-xl border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">至少需要两只可比较基金，才能计算组合重合度。</p>
        ) : !overlaps.length ? (
          <p className="rounded-xl border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">当前公开持仓中未识别到可比较的重合证券。</p>
        ) : (
          <>
            <div className="space-y-2">{shownOverlaps.map((stock) => (
              <div key={stock.stock_code} className="rounded-xl border border-border/50 p-3">
                <div className="flex items-center justify-between gap-3"><span className="font-medium">{stock.stock_name} <span className="font-mono text-xs text-muted-foreground">{stock.stock_code}</span></span><span className="shrink-0 font-semibold text-primary">组合估算暴露 {percent(stock.portfolio_exposure_pct)}</span></div>
                <div className="mt-2 flex flex-wrap gap-2">{stock.funds.map((fund) => <span key={fund.fund_code} className="rounded-full bg-muted/50 px-2 py-1 text-[11px]">{fund.fund_name} · 基金内 {percent(fund.weight_pct)}</span>)}</div>
              </div>
            ))}</div>
            {overlaps.length > 5 && <button aria-label={expanded ? "收起重合明细" : "查看全部重合"} onClick={() => setExpanded((value) => !value)} className="mt-3 inline-flex items-center gap-1 text-xs text-primary">{expanded ? <><ChevronUp className="h-3.5 w-3.5" />收起重合明细</> : <><ChevronDown className="h-3.5 w-3.5" />查看全部重合</>}</button>}
          </>
        )}
      </GlassCard>

      <IndustryExposurePanel
        title={isSingleFund ? "该基金行业暴露" : "组合行业集中度"}
        exposure={isSingleFund ? singleExposure : concentration || null}
        officialAllocations={isSingleFund ? undefined : officialAllocations}
      />
    </div>
  );
}
