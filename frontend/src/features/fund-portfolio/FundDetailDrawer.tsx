import { X } from "lucide-react";
import { getTag } from "@/features/tags/catalog";
import { DataStatus } from "./DataStatus";
import { dateTime, number, percent } from "./format";
import { NavChart } from "./NavChart";
import type { FundAnalysis, FundHolding } from "./types";

interface FundDetailDrawerProps {
  open: boolean;
  holding: FundHolding | null;
  analysis: FundAnalysis | null;
  onClose: () => void;
}

const periodLabels: Record<string, string> = {
  "1m": "近1月", "3m": "近3月", "6m": "近6月", "1y": "近1年", "3y": "近3年",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-border/60 bg-black/10 p-4">
      <h3 className="mb-3 text-sm font-semibold tracking-wide">{title}</h3>
      {children}
    </section>
  );
}

export function FundDetailDrawer({ open, holding, analysis, onClose }: FundDetailDrawerProps) {
  if (!open || !holding) return null;
  const profile = analysis?.profile.data;
  const latest = analysis?.latest_nav.data;
  const history = analysis?.nav_history.data;
  const disclosed = analysis?.holdings.data;
  const exposure = analysis?.industry_exposure.data;
  const userTags = holding.custom_tag_ids.map((id) => getTag(id)?.name || id);

  return (
    <div className="fixed inset-0 z-50 bg-black/65" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onClose();
    }}>
      <aside role="dialog" aria-modal="true" aria-label={`${profile?.name || holding.manual_name || holding.code}基金详情`}
        className="ml-auto flex h-full w-full max-w-4xl flex-col border-l border-primary/25 bg-background/95 shadow-2xl backdrop-blur-xl">
        <header className="flex items-start justify-between border-b border-border/60 px-5 py-4">
          <div>
            <p className="font-mono text-xs text-primary">{holding.code}</p>
            <h2 className="mt-1 text-xl font-bold">{profile?.name || holding.manual_name || "未核验基金"}</h2>
            <p className="mt-1 text-xs text-muted-foreground">证据抽屉 · 用户持仓与公共基金数据分开展示</p>
          </div>
          <button onClick={onClose} aria-label="关闭基金详情" className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground">
            <X className="h-5 w-5" />
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          <Section title="基本资料">
            {profile ? (
              <div className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
                <div><p className="text-muted-foreground">基金全称</p><p className="mt-1">{profile.full_name || profile.name}</p></div>
                <div><p className="text-muted-foreground">基金类型</p><p className="mt-1">{profile.fund_type || "暂无可靠数据"}</p></div>
                <div><p className="text-muted-foreground">成立日期</p><p className="mt-1">{profile.established_date || "暂无可靠数据"}</p></div>
                <div><p className="text-muted-foreground">基金经理</p><p className="mt-1">{profile.manager_names.join("、") || "暂无可靠数据"}</p></div>
                <div><p className="text-muted-foreground">管理人</p><p className="mt-1">{profile.management_company || "暂无可靠数据"}</p></div>
                <div><p className="text-muted-foreground">规模</p><p className="mt-1">{profile.scale == null ? "暂无可靠数据" : `${number(profile.scale, 2)} ${profile.scale_unit || ""}`}</p></div>
              </div>
            ) : <p className="text-sm text-muted-foreground">基本资料暂不可用；手动名称不代表基金身份已核验。</p>}
            <div className="mt-3"><DataStatus meta={analysis?.profile.meta} /></div>
          </Section>

          <Section title="净值与历史表现">
            <div className="mb-4 grid gap-3 sm:grid-cols-3">
              <div><p className="text-xs text-muted-foreground">最新单位净值</p><p className="mt-1 text-lg font-semibold">{number(latest?.unit_nav)}</p></div>
              <div><p className="text-xs text-muted-foreground">累计净值</p><p className="mt-1 text-lg font-semibold">{number(latest?.cumulative_nav)}</p></div>
              <div><p className="text-xs text-muted-foreground">净值日期</p><p className="mt-1 text-lg font-semibold">{latest?.nav_date || "—"}</p></div>
            </div>
            <NavChart points={history?.points || []} />
            <div className="mt-4 grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
              {Object.entries(analysis?.performance.returns || {}).map(([period, value]) => (
                <div key={period} className="rounded-lg border border-border/50 p-2">
                  <p className="text-muted-foreground">{periodLabels[period] || period}</p>
                  <p className="mt-1 font-semibold">{percent(value)}</p>
                </div>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted-foreground">官方净值来源</span><DataStatus meta={analysis?.latest_nav.meta} />
            </div>
          </Section>

          <Section title="公开持仓披露">
            <p className="mb-3 rounded-lg border border-warning/30 bg-warning/5 p-2 text-xs text-warning">基金持仓来自定期报告披露，不代表基金当前实时持仓。</p>
            {!disclosed?.holdings.length ? <p className="py-5 text-center text-sm text-muted-foreground">前十大持仓暂无可靠数据</p> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-xs">
                  <thead className="text-muted-foreground"><tr><th className="pb-2">证券</th><th className="pb-2">代码</th><th className="pb-2 text-right">占基金净值</th><th className="pb-2 text-right">市值（万元）</th></tr></thead>
                  <tbody>{disclosed.holdings.map((stock) => <tr key={stock.stock_code} className="border-t border-border/40"><td className="py-2">{stock.stock_name}</td><td className="py-2 font-mono">{stock.stock_code}</td><td className="py-2 text-right">{percent(stock.weight_pct)}</td><td className="py-2 text-right">{number(stock.market_value_10k, 2)}</td></tr>)}</tbody>
                </table>
              </div>
            )}
            <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span>报告期：{disclosed?.report_period || "—"}</span><span>披露日期：{disclosed?.disclosure_date || "—"}</span><span>前十大覆盖：{percent(disclosed?.top10_coverage_pct)}</span><DataStatus meta={analysis?.holdings.meta} />
            </div>
          </Section>

          <Section title="行业和产业链暴露">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <p className="mb-2 text-xs font-medium"><span className="text-muted-foreground">系统识别：</span></p>
                <div className="flex flex-wrap gap-2">{exposure?.system_tags.length ? exposure.system_tags.map((tag) => <span key={tag.id} className="rounded-full border border-primary/30 bg-primary/10 px-2 py-1 text-xs text-primary">{tag.name} {percent(tag.weight_pct)}</span>) : <span className="text-xs text-muted-foreground">暂无可靠数据</span>}</div>
              </div>
              <div>
                <p className="mb-2 text-xs font-medium"><span className="text-muted-foreground">用户标签：</span></p>
                <div className="flex flex-wrap gap-2">{userTags.length ? userTags.map((name) => <span key={name} className="rounded-full border border-border bg-muted/40 px-2 py-1 text-xs">{name}</span>) : <span className="text-xs text-muted-foreground">未添加</span>}</div>
              </div>
            </div>
            <div className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
              {(exposure?.primary || []).map((item) => <div key={item.name} className="flex justify-between rounded-lg border border-border/40 px-3 py-2"><span>{item.name}</span><span>{percent(item.weight_pct)}</span></div>)}
            </div>
            <p className="mt-3 text-[11px] text-muted-foreground">{exposure?.calculation_basis || "行业暴露暂不可用"} · 已识别 {percent(exposure?.identified_coverage_pct)} · 未识别 {percent(exposure?.unidentified_disclosed_pct)}</p>
          </Section>

          <Section title="数据质量">
            <div className="mb-3 grid gap-2 text-xs sm:grid-cols-2">
              <div className="rounded-lg border border-border/50 p-3"><p className="text-muted-foreground">主数据源是否可用</p><p className="mt-1 font-semibold">{Object.values(analysis?.data_quality || {}).some((item) => !item.fallback_used && !["error", "unavailable"].includes(item.status)) ? "是" : "否 / 未确认"}</p></div>
              <div className="rounded-lg border border-border/50 p-3"><p className="text-muted-foreground">本次详情获取</p><p className="mt-1 font-semibold">{dateTime(analysis?.profile.meta.fetched_at)}</p></div>
            </div>
            <div className="space-y-2">{Object.entries(analysis?.data_quality || {}).map(([key, item]) => (
              <div key={key} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/40 px-3 py-2 text-xs">
                <span>{item.data_type || key}</span><div className="flex items-center gap-2"><span className="text-muted-foreground">数据截至 {item.as_of_date || "—"}</span><DataStatus meta={item} compact /></div>
              </div>
            ))}</div>
          </Section>
        </div>
      </aside>
    </div>
  );
}
