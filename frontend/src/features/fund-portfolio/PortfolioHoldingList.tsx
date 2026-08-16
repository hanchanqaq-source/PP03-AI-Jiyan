import { useMemo, useState } from "react";
import { Edit3, Eye, Trash2 } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { getTag } from "@/features/tags/catalog";
import { dateTime, money, number, percent } from "./format";
import type { FundPortfolioAnalysisData, PortfolioHoldingAnalysis } from "./types";

type Filter = "all" | "up" | "down" | "profit" | "loss" | "pending";
type Sort = "position_value" | "today_change" | "today_pnl" | "profit_loss" | "return_rate" | "fund_name";

const FILTERS: Array<[Filter, string]> = [
  ["all", "全部"], ["up", "上涨"], ["down", "下跌"], ["profit", "盈利"], ["loss", "亏损"], ["pending", "待补充"],
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
  if (filter === "pending") return position.profit_loss == null || position.position_value == null;
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
    <GlassCard glow className="mb-5 p-0">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 px-5 py-4">
        <div>
          <h2 className="font-semibold">当前基金持仓</h2>
          <p className="mt-1 text-xs text-muted-foreground">用户快照、正式净值参考值和盘中估算分轨展示</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap gap-1" aria-label="持仓筛选">{FILTERS.map(([value, label]) => (
            <button key={value} aria-label={`筛选${label}`} aria-pressed={filter === value} onClick={() => setFilter(value)}
              className={`rounded-full border px-2.5 py-1 text-xs ${filter === value ? "border-primary/60 bg-primary/15 text-primary" : "border-border text-muted-foreground hover:text-foreground"}`}>{label}</button>
          ))}</div>
          <select aria-label="排序方式" value={sort} onChange={(event) => setSort(event.target.value as Sort)} className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs outline-none focus:border-primary/60">
            {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
      </div>

      <div className="space-y-3 p-4">
        {!visible.length && <p className="rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted-foreground">当前筛选条件下没有持仓</p>}
        {visible.map((item) => {
          const holding = item.user_holding;
          const position = item.position;
          const profile = item.analysis?.profile.data;
          const latest = item.analysis?.latest_nav.data;
          const disclosed = item.analysis?.holdings.data;
          const confidence = item.analysis?.intraday_estimate.data?.confidence;
          const valueBasis = position.position_value_basis === "user_amount_snapshot" ? "用户金额快照回退" : "最新正式净值参考";
          return (
            <article key={item.code} data-testid="holding-card" className="rounded-2xl border border-border/60 bg-black/10 p-4 transition-colors hover:border-primary/30">
              <div className="grid gap-4 xl:grid-cols-[minmax(220px,1.3fr)_repeat(3,minmax(145px,1fr))_auto] xl:items-start">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-semibold">{item.name}</h3>
                    <span className="font-mono text-xs text-muted-foreground">{item.code}</span>
                    <span className="rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] text-primary">{statusLabel(item)}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{item.fund_type || "基金类型暂无可靠数据"}</p>
                  <div className="mt-2 flex flex-wrap gap-1">{holding.custom_tag_ids.map((id) => <span key={id} className="rounded-full bg-muted/50 px-2 py-0.5 text-[10px]">{getTag(id)?.name || id.replace(/^custom:/, "")}</span>)}</div>
                  <div className="mt-3 grid gap-1 text-[11px] text-muted-foreground sm:grid-cols-2 xl:grid-cols-1">
                    <span>经理：{profile?.manager_names.join("、") || "暂无可靠数据"}</span>
                    <span>规模：{profile?.scale == null ? "暂无可靠数据" : `${number(profile.scale, 2)} ${profile.scale_unit || ""}`}</span>
                    <span>报告期：{disclosed?.report_period || "暂无可靠数据"}</span>
                    <span>估算可信度：{confidence || "暂无可靠数据"}</span>
                  </div>
                </div>

                <div className="space-y-2 text-sm">
                  <p className="text-[11px] text-muted-foreground">持有金额 · {valueBasis}</p>
                  <p className="text-lg font-bold">{money(position.position_value)}</p>
                  <p className="text-xs text-muted-foreground">持仓占比 {percent(item.weight_pct)}</p>
                  <p className="text-xs text-muted-foreground">正式净值 {number(latest?.unit_nav)} · {latest?.nav_date || "暂无日期"}</p>
                </div>

                <div className="space-y-2 text-sm">
                  <p className="text-[11px] text-muted-foreground">今日估算</p>
                  <p className={position.today_estimated_profit_loss == null ? "text-muted-foreground" : position.today_estimated_profit_loss >= 0 ? "font-semibold text-success" : "font-semibold text-destructive"}>{money(position.today_estimated_profit_loss)} · {percent(position.intraday_change_pct)}</p>
                  <p className="text-[11px] text-muted-foreground">累计盈亏</p>
                  <p className={position.profit_loss == null ? "text-warning" : position.profit_loss >= 0 ? "font-semibold text-success" : "font-semibold text-destructive"}>{position.profit_loss == null ? "待补充" : `${money(position.profit_loss)} · ${percent(position.return_rate)}`}</p>
                </div>

                <div className="space-y-2 rounded-xl border border-border/50 bg-background/30 p-3 text-xs">
                  <p><span className="block text-[10px] text-muted-foreground">用户录入金额</span>{money(position.user_amount_snapshot)} <span className="text-muted-foreground">{dateTime(position.snapshot_at)}</span></p>
                  <p><span className="block text-[10px] text-muted-foreground">正式净值参考值</span>{money(position.official_market_value)}</p>
                  <p><span className="block text-[10px] text-muted-foreground">盘中估算参考值</span>{money(position.intraday_market_value)}</p>
                </div>

                <div className="flex gap-1 xl:flex-col">
                  <button onClick={() => onDetail(item)} aria-label={`查看详情 ${item.name}`} className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-2 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"><Eye className="h-3.5 w-3.5" />详情</button>
                  <button onClick={() => onEdit(item)} aria-label={`编辑持仓 ${item.name}`} className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-2 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"><Edit3 className="h-3.5 w-3.5" />编辑</button>
                  <button onClick={() => onDelete(item)} aria-label={`删除持仓 ${item.name}`} className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-2 text-xs text-muted-foreground hover:border-destructive/40 hover:text-destructive"><Trash2 className="h-3.5 w-3.5" />删除</button>
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </GlassCard>
  );
}
