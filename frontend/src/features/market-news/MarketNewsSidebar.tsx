import { BarChart3, Link2, Radar } from "lucide-react";
import type { MarketNewsEvent, MarketNewsImpactSummary } from "./types";

function Panel({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return <section className="rounded-2xl border border-border/65 bg-gradient-to-br from-slate-900/80 via-slate-950/70 to-blue-950/35 p-4 shadow-sm"><h2 className="flex items-center gap-2 text-sm font-semibold">{icon}{title}</h2>{children}</section>;
}

const relationLabel = { direct_holding: "直接持仓", industry_relation: "产业关联", watch_tag: "关注标签", none: "普通资讯" } as const;

export function MarketNewsSidebar({ focus, impact, days }: { focus: MarketNewsEvent[]; impact: MarketNewsImpactSummary | null; days: number }) {
  const rangeLabel = days === 1 ? "今天" : `过去 ${days} 天`;
  return <aside className="space-y-3 xl:sticky xl:top-4 xl:self-start" aria-label="市场资讯重点面板">
    <Panel title="当前筛选重点" icon={<Radar className="h-4 w-4 text-primary" />}>
      <div className="mt-3 space-y-2">{focus.length ? focus.slice(0, 5).map((event, index) => <div key={event.event_id} className="grid grid-cols-[24px_1fr] gap-2 rounded-xl border border-border/45 bg-black/10 p-2.5">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/15 font-mono text-xs font-bold text-primary">{index + 1}</span>
        <div><p className="line-clamp-2 text-xs font-medium leading-5">{event.title}</p><p className="mt-1 text-[10px] text-muted-foreground">{relationLabel[event.relation_level]}{event.related_funds.length ? ` · 关联 ${event.related_funds.length} 只持仓基金` : ` · ${event.related_tags.map((tag) => tag.name).join("、")}`}</p></div>
      </div>) : <p className="py-4 text-center text-xs text-muted-foreground">当前筛选暂无可靠重点事件</p>}</div>
    </Panel>

    <Panel title="当前筛选影响" icon={<BarChart3 className="h-4 w-4 text-sky-300" />}>
      {impact ? <>
        <p className="mt-3 text-sm font-semibold">{rangeLabel}有 {impact.holding_related_count} 个事件与你的持仓相关</p>
        <div className="mt-3 grid gap-2 text-xs text-muted-foreground"><p>直接涉及重仓公司：{impact.direct_count}</p><p>涉及持仓行业：{impact.industry_count}</p><p>只涉及关注标签：{impact.watch_count}</p></div>
        {impact.funds.length > 0 && <div className="mt-3 border-t border-border/45 pt-3"><p className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">受关联事件最多的基金</p>{impact.funds.map((fund) => <p key={fund.fund_code} className="mt-2 text-xs">{fund.fund_name}（{fund.fund_code}） · {fund.event_count} 个事件</p>)}</div>}
      </> : <p role="status" className="mt-3 text-sm font-semibold text-warning">持仓数据读取失败，暂无法计算关联</p>}
    </Panel>

    <Panel title="关联强度说明" icon={<Link2 className="h-4 w-4 text-violet-300" />}>
      <div className="mt-3 space-y-2 text-[11px] leading-5 text-muted-foreground"><p>直接持仓：新闻直接提到基金公开重仓公司</p><p>产业关联：新闻涉及持仓公司的行业或产业链</p><p>关注标签：新闻只匹配你主动关注的方向</p></div>
    </Panel>
  </aside>;
}
