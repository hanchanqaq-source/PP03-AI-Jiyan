import { GlassCard } from "@/components/ui/GlassCard";
import { percent } from "./format";
import type { FundPortfolioAnalysisData } from "./types";

export function PortfolioCombination({ data }: { data: FundPortfolioAnalysisData | null }) {
  const concentration = data?.industry_concentration;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <GlassCard>
        <div className="mb-3 flex items-end justify-between gap-3">
          <div><h2 className="font-semibold">组合重合度</h2><p className="mt-1 text-xs text-muted-foreground">按基金公开前十大持仓和当前组合市值权重计算</p></div>
          <span className="text-xs text-muted-foreground">{data?.overlap.length || 0} 只重合证券</span>
        </div>
        {!data?.overlap.length ? <p className="py-8 text-center text-sm text-muted-foreground">至少两只基金拥有可比的公开持仓后才显示重合度。</p> : (
          <div className="space-y-2">{data.overlap.map((stock) => (
            <div key={stock.stock_code} className="rounded-xl border border-border/50 p-3">
              <div className="flex items-center justify-between"><span className="font-medium">{stock.stock_name} <span className="font-mono text-xs text-muted-foreground">{stock.stock_code}</span></span><span className="font-semibold text-primary">{percent(stock.portfolio_exposure_pct)}</span></div>
              <div className="mt-2 flex flex-wrap gap-2">{stock.funds.map((fund) => <span key={fund.fund_code} className="rounded-full bg-muted/50 px-2 py-1 text-[11px]">{fund.fund_name} · 基金内 {percent(fund.weight_pct)}</span>)}</div>
            </div>
          ))}</div>
        )}
      </GlassCard>

      <GlassCard>
        <h2 className="font-semibold">组合行业集中度</h2>
        <p className="mt-1 text-xs text-muted-foreground">{concentration?.calculation_basis || "仅按已识别公开持仓计算"}</p>
        <div className="mt-4 space-y-3">{concentration?.exposure.length ? concentration.exposure.map((item) => (
          <div key={item.name}>
            <div className="mb-1 flex justify-between text-xs"><span>{item.name}</span><span>{percent(item.weight_pct)}</span></div>
            <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, item.weight_pct))}%` }} /></div>
          </div>
        )) : <p className="py-6 text-center text-sm text-muted-foreground">组合行业暴露暂无可靠数据</p>}</div>
        <div className="mt-5 grid grid-cols-2 gap-2 text-xs">
          <div className="rounded-lg border border-success/25 bg-success/5 p-3"><p className="text-muted-foreground">已识别覆盖</p><p className="mt-1 font-semibold text-success">{percent(concentration?.identified_coverage_pct)}</p></div>
          <div className="rounded-lg border border-warning/25 bg-warning/5 p-3"><p className="text-muted-foreground">未知部分</p><p className="mt-1 font-semibold text-warning">{percent(concentration?.unknown_pct)}</p></div>
        </div>
        {!!data?.risk_flags.length && <div className="mt-4 space-y-1">{data.risk_flags.map((flag) => <p key={flag} className="rounded-lg border border-warning/25 bg-warning/5 p-2 text-xs text-warning">{flag}</p>)}</div>}
      </GlassCard>
    </div>
  );
}
