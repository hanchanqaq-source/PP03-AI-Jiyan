import { useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, Database, Edit3, Eye, Loader2, Plus, RefreshCw, Search, ShieldCheck, Trash2 } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataStatus } from "@/features/fund-portfolio/DataStatus";
import { FundDetailDrawer } from "@/features/fund-portfolio/FundDetailDrawer";
import { money, percent } from "@/features/fund-portfolio/format";
import { PortfolioCombination } from "@/features/fund-portfolio/PortfolioCombination";
import type { FundAnalysis, FundHolding, FundHoldingInput, FundPortfolioAnalysisData, FundPortfolioData, FundSearchResult } from "@/features/fund-portfolio/types";
import { getTag } from "@/features/tags/catalog";
import { TagSelector } from "@/features/tags/TagSelector";
import { api } from "@/lib/api";

interface FormState {
  shares: string;
  avgCost: string;
  buyDate: string;
  notes: string;
  tagIds: string[];
  manualCode: string;
  manualName: string;
}

const EMPTY_FORM: FormState = { shares: "", avgCost: "", buyDate: "", notes: "", tagIds: [], manualCode: "", manualName: "" };

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return <GlassCard className="min-h-24"><p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{label}</p><p className="mt-2 text-xl font-bold">{value}</p>{note && <p className="mt-1 text-[10px] text-muted-foreground">{note}</p>}</GlassCard>;
}

function holdingName(holding: FundHolding, analysis: FundPortfolioAnalysisData | null) {
  return analysis?.holdings.find((item) => item.code === holding.code)?.name || holding.manual_name || holding.legacy_name || `基金 ${holding.code}`;
}

export function PortfolioAnalysis() {
  const [portfolio, setPortfolio] = useState<FundPortfolioData | null>(null);
  const [analysis, setAnalysis] = useState<FundPortfolioAnalysisData | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<FundSearchResult[]>([]);
  const [searchMeta, setSearchMeta] = useState<Awaited<ReturnType<typeof api.searchFunds>>["meta"] | null>(null);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<FundSearchResult | null>(null);
  const [manual, setManual] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingCode, setEditingCode] = useState<string | null>(null);
  const [tagSelectorOpen, setTagSelectorOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [detail, setDetail] = useState<{ holding: FundHolding; analysis: FundAnalysis | null } | null>(null);
  const [detailLoading, setDetailLoading] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ holding: FundHolding; name: string } | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = async () => {
    const [portfolioResult, analysisResult] = await Promise.allSettled([api.fundPortfolio(), api.fundPortfolioAnalysis()]);
    if (portfolioResult.status === "fulfilled") setPortfolio(portfolioResult.value);
    else setError("本地持仓台账加载失败；未执行任何写入。请检查后端状态。");
    if (analysisResult.status === "fulfilled") setAnalysis(analysisResult.value);
    else setNotice("组合分析部分暂不可用；本地持仓台账仍可独立使用。");
  };

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    const normalized = query.trim();
    if (manual || selected || normalized.length < 2) {
      setResults([]);
      setSearching(false);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        const response = await api.searchFunds(normalized);
        if (!cancelled) { setResults(response.data || []); setSearchMeta(response.meta); }
      } catch {
        if (!cancelled) { setResults([]); setSearchMeta(null); setNotice("基金检索源暂不可用，可切换手动录入；手动信息会明确标记为未核验。"); }
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 350);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [manual, query, selected]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((current) => ({ ...current, [key]: value }));

  const resetForm = () => {
    setQuery(""); setResults([]); setSelected(null); setManual(false); setForm(EMPTY_FORM); setEditingCode(null); setError(null);
  };

  const selectFund = (fund: FundSearchResult) => {
    if (!editingCode && portfolio?.holdings.some((holding) => holding.code === fund.code)) {
      setError("该基金已在持仓中，请使用编辑操作更新。");
      return;
    }
    setError(null); setSelected(fund); setQuery(`${fund.code} ${fund.name}`); setResults([]);
  };

  const save = async () => {
    const code = manual ? form.manualCode.trim() : selected?.code || editingCode || "";
    const manualName = manual ? form.manualName.trim() : null;
    const shares = Number(form.shares), avgCost = Number(form.avgCost);
    if (!/^\d{6}$/.test(code)) { setError("基金代码必须是 6 位数字。"); return; }
    if (manual && !manualName) { setError("手动录入时必须填写基金名称，并将保存为未核验信息。"); return; }
    if (!(shares > 0) || !Number.isFinite(avgCost) || avgCost < 0) { setError("持有份额必须大于 0，平均单位成本必须是非负数字。"); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(form.buyDate)) { setError("请填写有效的买入日期。"); return; }
    if (!editingCode && portfolio?.holdings.some((holding) => holding.code === code)) { setError("该基金已在持仓中，请使用编辑操作更新。"); return; }
    setSaving(true); setError(null); setNotice(null);
    try {
      const payload: FundHoldingInput = {
        code, shares, avg_cost: avgCost, buy_date: form.buyDate, notes: form.notes.trim(),
        custom_tag_ids: form.tagIds, verification_status: manual ? "manual_unverified" : "verified",
        manual_name: manualName, replace: Boolean(editingCode),
      };
      setPortfolio(await api.upsertFundHolding(payload));
      setNotice(editingCode ? "持仓已更新。" : "测试持仓已添加到本地台账。");
      resetForm();
      try { setAnalysis(await api.fundPortfolioAnalysis()); } catch { setNotice("持仓已保存；组合分析刷新暂不可用，可稍后重试。"); }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "持仓保存失败；本地原始数据未确认变更。");
    } finally { setSaving(false); }
  };

  const edit = (holding: FundHolding) => {
    const item = analysis?.holdings.find((entry) => entry.code === holding.code);
    setEditingCode(holding.code);
    setManual(holding.verification_status === "manual_unverified");
    setSelected(holding.verification_status === "verified" ? {
      code: holding.code, name: item?.name || holding.legacy_name || `基金 ${holding.code}`,
      fund_type: item?.fund_type || null, latest_nav: item?.analysis?.latest_nav.data?.unit_nav || null,
      latest_nav_date: item?.analysis?.latest_nav.data?.nav_date || null,
      manager_names: item?.analysis?.profile.data?.manager_names || [], management_company: item?.analysis?.profile.data?.management_company || null,
    } : null);
    setQuery(holding.verification_status === "verified" ? `${holding.code} ${item?.name || ""}` : "");
    setForm({ shares: String(holding.shares), avgCost: holding.avg_cost == null ? "" : String(holding.avg_cost), buyDate: holding.buy_date, notes: holding.notes, tagIds: holding.custom_tag_ids, manualCode: holding.code, manualName: holding.manual_name || holding.legacy_name || "" });
    setError(null); window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const remove = (holding: FundHolding) => {
    const name = holdingName(holding, analysis);
    setError(null);
    setDeleteTarget({ holding, name });
  };

  const confirmRemove = async () => {
    if (!deleteTarget) return;
    const { holding, name } = deleteTarget;
    setDeleting(true);
    setError(null);
    try {
      setPortfolio(await api.deleteFundHolding(holding.code));
      setAnalysis(await api.fundPortfolioAnalysis());
      setNotice(`${name} 已从本地持仓删除。`);
      setDeleteTarget(null);
    }
    catch { setError("删除失败；本地数据未确认变更。"); }
    finally { setDeleting(false); }
  };

  const openDetail = async (holding: FundHolding) => {
    const embedded = analysis?.holdings.find((entry) => entry.code === holding.code)?.analysis || null;
    setDetail({ holding, analysis: embedded });
    if (holding.verification_status !== "verified") return;
    setDetailLoading(holding.code);
    try { setDetail({ holding, analysis: await api.fundAnalysis(holding.code) }); }
    catch { setNotice(`${holdingName(holding, analysis)} 的详情数据暂不可用，仍显示已有缓存或空状态。`); }
    finally { setDetailLoading(null); }
  };

  const refresh = async (holding: FundHolding) => {
    if (holding.verification_status !== "verified") { setNotice("未核验基金不能刷新公共基金数据。"); return; }
    setDetailLoading(holding.code);
    try { const refreshed = await api.refreshFund(holding.code); setDetail({ holding, analysis: refreshed }); await load(); setNotice(`${holdingName(holding, analysis)} 已刷新。`); }
    catch { setError("刷新失败；页面继续保留上次可用数据，并展示其缓存/过期状态。"); }
    finally { setDetailLoading(null); }
  };

  const overview = analysis?.overview;
  const tagNames = useMemo(() => form.tagIds.map((id) => getTag(id)?.name || id), [form.tagIds]);

  return (
    <div>
      <PageHeader title="持仓分析" subtitle="基金身份来自公开数据，份额、成本和标签来自你的本地台账" />
      <div className="mb-4 flex gap-2 rounded-xl border border-success/25 bg-success/5 p-3 text-xs text-muted-foreground">
        <ShieldCheck className="h-4 w-4 shrink-0 text-success" />
        <span>持仓仅保存在本机。正式净值、公开披露、估算数据与用户输入会分别标记；<b className="text-foreground">无可靠数据时明确显示为空</b>。</span>
      </div>

      {portfolio?.migration && <div className="mb-4 rounded-xl border border-warning/30 bg-warning/5 p-3 text-xs text-warning">已从 V{portfolio.migration.from_schema} 台账迁移；原文件备份为 {portfolio.migration.backup_file}。有 {portfolio.migration.cost_confirmation_required_count} 条旧成本需要确认。</div>}
      {error && <div className="mb-4 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4 shrink-0" />{error}</div>}
      {notice && <div className="mb-4 flex items-center gap-2 rounded-xl border border-primary/25 bg-primary/5 p-3 text-sm text-muted-foreground"><CheckCircle2 className="h-4 w-4 shrink-0 text-primary" />{notice}</div>}

      <GlassCard className="mb-5 overflow-visible">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="flex items-center gap-2 font-semibold"><Plus className="h-4 w-4 text-primary" />{editingCode ? "编辑基金持仓" : "添加基金持仓"}</h2><p className="mt-1 text-xs text-muted-foreground">先核验基金身份，再填写份额、成本、日期和用户标签。</p></div>
          <button onClick={() => { setManual((value) => !value); setSelected(null); setQuery(""); setResults([]); setError(null); }} aria-label="切换手动录入" className="rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground">{manual ? "返回公开基金检索" : "切换手动录入"}</button>
        </div>

        {manual ? (
          <div className="mb-4 rounded-xl border border-warning/30 bg-warning/5 p-3">
            <p className="text-sm font-medium text-warning">手动录入，不代表基金信息已核验</p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-muted-foreground">基金代码<input aria-label="手动基金代码" value={form.manualCode} onChange={(event) => set("manualCode", event.target.value)} maxLength={6} className="mt-1.5 w-full rounded-lg border border-border bg-black/15 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/60" /></label>
              <label className="text-xs text-muted-foreground">基金名称<input aria-label="手动基金名称" value={form.manualName} onChange={(event) => set("manualName", event.target.value)} className="mt-1.5 w-full rounded-lg border border-border bg-black/15 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/60" /></label>
            </div>
          </div>
        ) : (
          <div className="relative mb-4">
            <label className="text-xs text-muted-foreground">搜索公开基金
              <div className="mt-1.5 flex items-center gap-2 rounded-xl border border-border bg-black/15 px-3 focus-within:border-primary/60">
                {searching ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : <Search className="h-4 w-4 text-muted-foreground" />}
                <input aria-label="输入基金代码或基金名称" value={query} disabled={Boolean(selected)} onChange={(event) => { setQuery(event.target.value); setError(null); }} placeholder="例如：000001 或 华夏成长" className="w-full bg-transparent py-2.5 text-sm outline-none disabled:opacity-80" />
                {selected && <button onClick={() => { setSelected(null); setQuery(""); }} className="text-xs text-primary">重选</button>}
              </div>
            </label>
            {!!results.length && <div className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-xl border border-primary/25 bg-background/95 p-1 shadow-2xl backdrop-blur-xl">{results.map((fund) => (
              <button key={`${fund.code}-${fund.name}`} onClick={() => selectFund(fund)} aria-label={`选择基金 ${fund.name}`} className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-muted/60">
                <span><span className="font-medium">{fund.name}</span><span className="ml-2 font-mono text-xs text-muted-foreground">{fund.code}</span><span className="ml-2 text-xs text-muted-foreground">{fund.fund_type || "类型未提供"}</span></span>
                <span className="text-right text-xs"><span className="block">{fund.latest_nav ?? "—"}</span><span className="text-muted-foreground">{fund.latest_nav_date || "暂无净值日期"}</span></span>
              </button>
            ))}</div>}
            {searchMeta && <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground"><DataStatus meta={searchMeta} compact /><span>基金检索结果来自公开数据源</span></div>}
            {selected && <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-success/25 bg-success/5 p-3 text-xs"><CheckCircle2 className="h-4 w-4 text-success" /><b>{selected.name}</b><span className="font-mono">{selected.code}</span><span className="text-muted-foreground">{selected.fund_type || "类型暂无可靠数据"}</span><span className="text-muted-foreground">经理：{selected.manager_names.join("、") || "暂无可靠数据"}</span></div>}
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs text-muted-foreground">持有份额<input aria-label="持有份额" type="number" min="0" step="any" value={form.shares} onChange={(event) => set("shares", event.target.value)} className="mt-1.5 w-full rounded-lg border border-border bg-black/15 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/60" /></label>
          <label className="text-xs text-muted-foreground">平均单位成本<input aria-label="平均单位成本" type="number" min="0" step="any" value={form.avgCost} onChange={(event) => set("avgCost", event.target.value)} className="mt-1.5 w-full rounded-lg border border-border bg-black/15 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/60" /></label>
          <label className="text-xs text-muted-foreground">买入日期<input aria-label="买入日期" type="date" value={form.buyDate} onChange={(event) => set("buyDate", event.target.value)} className="mt-1.5 w-full rounded-lg border border-border bg-black/15 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/60" /></label>
          <label className="text-xs text-muted-foreground">备注<input aria-label="备注" value={form.notes} onChange={(event) => set("notes", event.target.value)} className="mt-1.5 w-full rounded-lg border border-border bg-black/15 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/60" /></label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button onClick={() => setTagSelectorOpen(true)} aria-label="选择用户标签" className="rounded-lg border border-border px-3 py-2 text-xs hover:border-primary/40">选择用户标签</button>
          {tagNames.map((name) => <span key={name} className="rounded-full border border-border bg-muted/40 px-2 py-1 text-xs">{name}</span>)}
          {!tagNames.length && <span className="text-xs text-muted-foreground">尚未选择标签</span>}
        </div>
        <div className="mt-4 flex gap-2">
          <button onClick={save} disabled={saving || (!manual && !selected && !editingCode)} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50">{saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}{editingCode ? "保存编辑" : "添加到持仓"}</button>
          {editingCode && <button onClick={resetForm} className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground">取消编辑</button>}
        </div>
      </GlassCard>

      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
        <Metric label="基金数量" value={String(overview?.fund_count ?? portfolio?.holdings.length ?? 0)} />
        <Metric label="总成本" value={money(overview?.total_cost ?? portfolio?.total_cost)} />
        <Metric label="参考市值" value={money(overview?.market_value)} note="按各基金最新正式净值" />
        <Metric label="累计盈亏" value={money(overview?.profit_loss)} />
        <Metric label="累计收益率" value={percent(overview?.return_rate)} />
        <Metric label="盘中估算" value={percent(overview?.intraday_change_pct)} note={overview?.intraday_message || "暂无可靠数据"} />
        <Metric label="净值日期" value={overview?.nav_dates.length ? overview.nav_dates.join(" / ") : "—"} note={overview?.inconsistent_nav_dates ? "不同基金净值日期不一致" : "正式净值"} />
      </div>

      <GlassCard glow className="mb-5">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3"><div><h2 className="font-semibold">当前基金持仓</h2><p className="mt-1 text-xs text-muted-foreground">用户台账与公共基金数据按代码关联；单只基金失败不会阻塞其他持仓。</p></div><span className="text-xs text-muted-foreground">更新于 {portfolio?.updated || overview?.updated_at || "—"}</span></div>
        {!portfolio?.holdings.length ? <p className="py-10 text-center text-sm text-muted-foreground">还没有基金持仓。可先搜索公开基金，再填写本地持仓事实。</p> : (
          <div className="overflow-x-auto"><table className="w-full min-w-[980px] text-left text-xs">
            <thead className="text-muted-foreground"><tr><th className="pb-3">基金</th><th className="pb-3 text-right">份额</th><th className="pb-3 text-right">单位成本</th><th className="pb-3 text-right">总成本</th><th className="pb-3 text-right">参考市值</th><th className="pb-3 text-right">盈亏 / 收益率</th><th className="pb-3">数据状态</th><th className="pb-3 text-right">操作</th></tr></thead>
            <tbody>{portfolio.holdings.map((holding) => {
              const item = analysis?.holdings.find((entry) => entry.code === holding.code);
              const meta = item?.analysis?.latest_nav.meta;
              const name = holdingName(holding, analysis);
              return <tr key={holding.code} className="border-t border-border/50 align-top">
                <td className="py-3"><p className="font-medium">{name}</p><p className="mt-1 font-mono text-[11px] text-muted-foreground">{holding.code} · {item?.fund_type || (holding.verification_status === "manual_unverified" ? "手动未核验" : "类型暂无")}</p><div className="mt-2 flex flex-wrap gap-1">{holding.custom_tag_ids.map((id) => <span key={id} className="rounded-full bg-muted/50 px-1.5 py-0.5 text-[10px]">{getTag(id)?.name || id}</span>)}</div></td>
                <td className="py-3 text-right">{holding.shares.toLocaleString("zh-CN")}</td><td className="py-3 text-right">{holding.avg_cost == null ? "待确认" : money(holding.avg_cost, 4)}</td><td className="py-3 text-right">{money(item?.position.total_cost)}</td><td className="py-3 text-right">{money(item?.position.market_value)}</td><td className="py-3 text-right"><p>{money(item?.position.profit_loss)}</p><p className="mt-1 text-muted-foreground">{percent(item?.position.return_rate)}</p></td>
                <td className="py-3"><DataStatus meta={meta} compact />{holding.cost_confirmation_required && <p className="mt-1 text-[10px] text-warning">旧成本待确认</p>}</td>
                <td className="py-3"><div className="flex justify-end gap-1"><button onClick={() => openDetail(holding)} aria-label={`查看 ${name} 详情`} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"><Eye className="h-4 w-4" /></button><button onClick={() => refresh(holding)} aria-label={`刷新 ${name}`} disabled={detailLoading === holding.code} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"><RefreshCw className={`h-4 w-4 ${detailLoading === holding.code ? "animate-spin" : ""}`} /></button><button onClick={() => edit(holding)} aria-label={`编辑 ${name}`} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"><Edit3 className="h-4 w-4" /></button><button onClick={() => remove(holding)} aria-label={`删除 ${name}`} className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"><Trash2 className="h-4 w-4" /></button></div></td>
              </tr>;
            })}</tbody>
          </table></div>
        )}
      </GlassCard>

      <PortfolioCombination data={analysis} />
      {deleteTarget && (
        <div role="dialog" aria-modal="true" aria-labelledby="delete-holding-title" className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 px-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-border bg-background/95 p-6 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="rounded-full bg-destructive/10 p-2 text-destructive"><AlertCircle className="h-5 w-5" /></div>
              <div>
                <h2 id="delete-holding-title" className="text-lg font-semibold text-foreground">确认删除持仓</h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">确认删除 {deleteTarget.name}（{deleteTarget.holding.code}）？此操作只删除本地持仓记录。</p>
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button aria-label="取消删除" onClick={() => setDeleteTarget(null)} disabled={deleting} className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50">取消</button>
              <button aria-label="确认删除" onClick={confirmRemove} disabled={deleting} className="inline-flex items-center gap-2 rounded-lg bg-destructive px-4 py-2 text-sm font-semibold text-destructive-foreground disabled:opacity-50">{deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}删除</button>
            </div>
          </div>
        </div>
      )}
      <TagSelector open={tagSelectorOpen} selectedIds={form.tagIds} onCancel={() => setTagSelectorOpen(false)} onConfirm={(ids) => { set("tagIds", ids); setTagSelectorOpen(false); }} />
      <FundDetailDrawer open={Boolean(detail)} holding={detail?.holding || null} analysis={detail?.analysis || null} onClose={() => setDetail(null)} />
    </div>
  );
}
