import { useMemo, useState } from "react";
import { Edit3, Eye, Trash2 } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { money, number, percent } from "./format";
import type { FundPortfolioAnalysisData, PortfolioHoldingAnalysis } from "./types";

type Filter = "all" | "up" | "down" | "profit" | "loss" | "pending";
type Sort = "position_value" | "today_change" | "today_pnl" | "profit_loss" | "return_rate" | "fund_name";

const FILTERS: Array<[Filter, string]> = [
  ["all", "全部"], ["up", "今日上涨"], ["down", "今日下跌"],
  ["profit", "盈利"], ["loss", "亏损"], ["pending", "数据待补充"],
];

const SORTS: Array<[Sort, string]> = [
  ["position_value", "持有金额"], ["today_change", "今日涨跌"], ["today_pnl", "今日盈亏"],
  ["profit_loss", "累计盈亏"], ["return_rate", "累计收益率"], ["fund_name", "基金名称"],
];

function statusLabel(item: PortfolioHoldingAnalysis) {
  const latest = item.analysis?.latest_nav.meta;
  const disclosed = item.analysis?.holdings.meta;
  const estimate = item.analysis?.intraday_estimate.data;
  if (latest?.is_cached || latest?.is_stale) return "缓存数据";
  if (latest?.status === "official" && estimate?.status === "estimated") return "正式 + 估算";
  if (latest?.status === "official") return "正式净值";
  if (disclosed?.status === "disclosed") return "仅披露持仓";
  return "数据待补充";
}

function matches(item: PortfolioHoldingAnalysis, filter: Filter) {
  const position = item.position;
  if (filter === "up") return (position.intraday_change_pct ?? 0) > 0;
  if (filter === "down") return (position.intraday_change_pct ?? 0) < 0;
  if (filter === "profit") return (position.profit_loss ?? 0) > 0;
  if (filter === "loss") return (position.profit_loss ?? 0) < 0;
  if (filter === "pending") return statusLabel(item) === "数据待补充" || position.profit_loss == null || position.position_value == null;
  return true;
}

function sortValue(item: PortfolioHoldingAnalysis, sort: Sort): number | string | null {
  if (sort === "fund_name") return item.name;
  if (sort === "today_change") return item.position.intraday_change_pct;
  if (sort === "today_pnl") return item.position.today_estimated_profit_loss;
  if (sort === "profit_loss") return item.position.profit_loss;
  if (sort === "return_rate") return item.position.return_rate;
  return item.position.position_value;
}

function movementTone(value: number | null | undefined) {
  if (value == null || value === 0) return "text-muted-foreground";
  return value > 0 ? "text-danger" : "text-success";
}

function systemIndustryTags(item: PortfolioHoldingAnalysis) {
  const exposure = item.analysis?.industry_exposure.data;
  const candidates = [
    ...(exposure?.industry_chain_tags ?? []),
    ...(exposure?.lookthrough?.secondary ?? []),
    ...(exposure?.lookthrough?.primary ?? []),
  ];
  const unique = new Map<string, { name: string; weight_pct: number }>();
  candidates.forEach((candidate) => {
    const name = candidate.name.trim();
    if (name && !unique.has(name)) unique.set(name, { name, weight_pct: candidate.weight_pct });
  });
  return [...unique.values()].slice(0, 4);
}

export function PortfolioHoldingList({ data, onDetail, onEdit, onDelete }: {
  data: FundPortfolioAnalysisData;
  onDetail: (item: PortfolioHoldingAnalysis) => void;
  onEdit: (item: PortfolioHoldingAnalysis) => void;
  onDelete: (item: PortfolioHoldingAnalysis) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("position_value");
  const visible = useMemo(() => data.holdings.filter((item) => matches(item, filter)).sort((left, right) => {
    const a = sortValue(left, sort), b = sortValue(right, sort);
    if (sort === "fund_name") return String(a).localeCompare(String(b), "zh-CN");
    if (a == null && b == null) return left.name.localeCompare(right.name, "zh-CN");
    if (a == null) return 1;
    if (b == null) return -1;
    return Number(b) - Number(a);
  }), [data.holdings, filter, sort]);

  return (
    <GlassCard className="mb-5 overflow-hidden bg-gradient-to-br from-slate-900/80 via-slate-950/60 to-blue-950/30 p-0">
      <div className="border-b border-border/60 px-4 py-3.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">当前基金持仓</h2>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap gap-1" aria-label="持仓筛选">{FILTERS.map(([value, label]) => (
              <button key={value} aria-label={`筛选${label}`} aria-pressed={filter === value} onClick={() => setFilter(value)}
                className={`rounded-md px-2.5 py-1.5 text-xs transition-colors ${filter === value ? "bg-primary text-primary-foreground" : "border border-border/70 bg-slate-950/35 text-muted-foreground hover:border-primary/40 hover:text-foreground"}`}>{label}</button>
            ))}</div>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">排序
              <select aria-label="排序方式" value={sort} onChange={(event) => setSort(event.target.value as Sort)} className="rounded-md border border-border/70 bg-slate-950/70 px-2.5 py-1.5 text-xs text-foreground outline-none focus:border-primary/60">
                {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
          </div>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table aria-label="当前基金持仓" className="w-full min-w-[960px] table-fixed text-left">
          <colgroup><col className="w-[25%]" /><col className="w-[16%]" /><col className="w-[16%]" /><col className="w-[16%]" /><col className="w-[15%]" /><col className="w-[12%]" /></colgroup>
          <thead className="bg-slate-950/45 text-[11px] text-muted-foreground">
            <tr><th className="px-4 py-2.5 font-medium">基金名称 / 代码</th><th className="px-3 py-2.5 font-medium">持有金额 / 占比</th><th className="px-3 py-2.5 font-medium">今日估算</th><th className="px-3 py-2.5 font-medium">累计盈亏</th><th className="px-3 py-2.5 font-medium">官方净值 / 日期</th><th className="px-3 py-2.5 text-right font-medium">操作</th></tr>
          </thead>
          <tbody className="divide-y divide-border/45">
            {!visible.length && <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-muted-foreground">当前筛选条件下没有持仓</td></tr>}
            {visible.map((item) => {
              const position = item.position;
              const latest = item.analysis?.latest_nav.data;
              const officialNav = latest?.unit_nav ?? item.user_holding.basis_nav;
              const navDate = latest?.nav_date ?? item.user_holding.basis_nav_date;
              const industryTags = systemIndustryTags(item);
              return (
                <tr key={item.code} data-testid="holding-row" className="h-[88px] bg-slate-950/10 transition-colors hover:bg-blue-950/20">
                  <td className="px-4 py-2.5 align-middle">
                    <div className="flex items-center gap-2"><button onClick={() => onDetail(item)} className="truncate text-sm font-semibold hover:text-primary">{item.name}</button><span className="shrink-0 rounded-full border border-blue-400/20 bg-blue-400/5 px-1.5 py-0.5 text-[9px] text-blue-200/70">{statusLabel(item)}</span></div>
                    <div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground"><span className="font-mono">{item.code}</span><span className="truncate">{item.fund_type || "类型待补充"}</span></div>
                    <div className="mt-1 flex min-h-4 flex-wrap items-center gap-1">
                      {industryTags.length ? industryTags.map((tag) => (
                        <span key={tag.name} data-testid="system-industry-tag" title={`重仓股穿透 ${percent(tag.weight_pct)}`} className="rounded border border-cyan-400/20 bg-cyan-400/5 px-1.5 py-0.5 text-[9px] leading-none text-cyan-100/75">{tag.name}</span>
                      )) : <span className="text-[9px] text-muted-foreground/70">行业待识别</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 align-middle"><p className="text-sm font-semibold tabular-nums">{money(position.position_value)}</p><p className="mt-1 text-[11px] text-muted-foreground">占比 {percent(item.weight_pct)}</p></td>
                  <td className="px-3 py-2.5 align-middle"><p data-testid="today-pnl" className={`text-sm font-semibold tabular-nums ${movementTone(position.today_estimated_profit_loss)}`}>{money(position.today_estimated_profit_loss)}</p><p className={`mt-1 text-[11px] tabular-nums ${movementTone(position.intraday_change_pct)}`}>{percent(position.intraday_change_pct)}</p></td>
                  <td className="px-3 py-2.5 align-middle">{position.profit_loss == null ? <p data-testid="cumulative-pnl" className="text-sm text-warning">待补充</p> : <><p data-testid="cumulative-pnl" className={`text-sm font-semibold tabular-nums ${movementTone(position.profit_loss)}`}>{money(position.profit_loss)}</p><p className={`mt-1 text-[11px] tabular-nums ${movementTone(position.return_rate)}`}>{percent(position.return_rate)}</p></>}</td>
                  <td className="px-3 py-2.5 align-middle"><p className="text-sm font-semibold tabular-nums">{number(officialNav)}</p><p className="mt-1 text-[11px] text-muted-foreground">{navDate || "日期待补充"}</p></td>
                  <td className="px-3 py-2.5 align-middle"><div className="flex items-center justify-end gap-1"><button onClick={() => onDetail(item)} aria-label={`查看详情 ${item.name}`} className="inline-flex items-center gap-1 rounded-md border border-primary/35 bg-primary/5 px-2 py-1.5 text-xs text-primary hover:bg-primary/10"><Eye className="h-3.5 w-3.5" />详情</button><button onClick={() => onEdit(item)} aria-label={`编辑持仓 ${item.name}`} title="编辑持仓" className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"><Edit3 className="h-3.5 w-3.5" /></button><button onClick={() => onDelete(item)} aria-label={`删除持仓 ${item.name}`} title="删除持仓" className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></button></div></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
}
