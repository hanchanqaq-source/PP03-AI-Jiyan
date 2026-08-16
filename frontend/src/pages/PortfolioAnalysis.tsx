import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, Plus, ShieldCheck, Trash2, WalletCards } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { FundDetailDrawer } from "@/features/fund-portfolio/FundDetailDrawer";
import { HoldingDrawer } from "@/features/fund-portfolio/HoldingDrawer";
import { money, percent } from "@/features/fund-portfolio/format";
import { PortfolioCombination } from "@/features/fund-portfolio/PortfolioCombination";
import { PortfolioHoldingList } from "@/features/fund-portfolio/PortfolioHoldingList";
import type { FundAnalysis, FundHoldingInput, FundPortfolioAnalysisData, FundPortfolioData, PortfolioHoldingAnalysis } from "@/features/fund-portfolio/types";
import { api } from "@/lib/api";

function Metric({ label, value, note, tone = "default" }: { label: string; value: string; note: string; tone?: "default" | "up" | "down" }) {
  const valueTone = tone === "up" ? "text-success" : tone === "down" ? "text-destructive" : "text-foreground";
  return <GlassCard className="min-h-28"><p className="text-[11px] uppercase tracking-[0.13em] text-muted-foreground">{label}</p><p className={`mt-3 text-2xl font-bold ${valueTone}`}>{value}</p><p className="mt-1.5 text-[11px] text-muted-foreground">{note}</p></GlassCard>;
}

function shortDate(value: string | null | undefined) {
  return value ? value.slice(5, 10) : "暂无";
}

export function PortfolioAnalysis() {
  const [portfolio, setPortfolio] = useState<FundPortfolioData | null>(null);
  const [analysis, setAnalysis] = useState<FundPortfolioAnalysisData | null>(null);
  const [loading, setLoading] = useState(true);
  const [drawer, setDrawer] = useState<{ holding: PortfolioHoldingAnalysis | null } | null>(null);
  const [saving, setSaving] = useState(false);
  const [detail, setDetail] = useState<{ item: PortfolioHoldingAnalysis; analysis: FundAnalysis | null } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PortfolioHoldingAnalysis | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = async () => {
    const [portfolioResult, analysisResult] = await Promise.allSettled([api.fundPortfolio(), api.fundPortfolioAnalysis()]);
    if (portfolioResult.status === "fulfilled") setPortfolio(portfolioResult.value);
    else setError("本地持仓台账加载失败；未执行任何写入。请检查后端状态。");
    if (analysisResult.status === "fulfilled") setAnalysis(analysisResult.value);
    else setNotice("组合分析暂不可用；本地持仓仍可独立读取和编辑。");
    setLoading(false);
  };

  useEffect(() => { void load(); }, []);

  const save = async (payload: FundHoldingInput) => {
    setSaving(true);
    setError(null);
    try {
      setPortfolio(await api.upsertFundHolding(payload));
      try { setAnalysis(await api.fundPortfolioAnalysis()); }
      catch { setNotice("持仓已保存；公共数据分析刷新暂不可用，可稍后重试。"); }
      setNotice(payload.replace ? "持仓已更新。" : "基金已添加到本地持仓。");
      setDrawer(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "持仓保存失败；本地原始数据未确认变更。");
      throw reason;
    } finally {
      setSaving(false);
    }
  };

  const openDetail = async (item: PortfolioHoldingAnalysis) => {
    setDetail({ item, analysis: item.analysis });
    if (item.user_holding.verification_status !== "verified") return;
    try { setDetail({ item, analysis: await api.fundAnalysis(item.code) }); }
    catch { setNotice(`${item.name} 的详情数据暂不可用，继续显示已有缓存或空状态。`); }
  };

  const confirmRemove = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      setPortfolio(await api.deleteFundHolding(deleteTarget.code));
      setAnalysis(await api.fundPortfolioAnalysis());
      setNotice(`${deleteTarget.name} 已从本地持仓删除。`);
      setDeleteTarget(null);
      setDetail(null);
    } catch {
      setError("删除失败；本地数据未确认变更。");
    } finally { setDeleting(false); }
  };

  const overview = analysis?.overview;
  const count = overview?.fund_count ?? portfolio?.holdings.length ?? 0;
  const subtitle = `共 ${count} 只基金 · 官方净值更新至 ${shortDate(overview?.latest_nav_date)} · ${overview?.estimable_count ?? 0} 只可盘中估算 · ${overview?.official_only_count ?? 0} 只仅正式净值`;
  const hasHoldings = Boolean(portfolio?.holdings.length);
  const pnlTone = (overview?.profit_loss ?? 0) > 0 ? "up" : (overview?.profit_loss ?? 0) < 0 ? "down" : "default";
  const todayTone = (overview?.intraday_estimated_profit_loss ?? 0) > 0 ? "up" : (overview?.intraday_estimated_profit_loss ?? 0) < 0 ? "down" : "default";

  return (
    <div>
      <PageHeader title="我的持仓" subtitle={subtitle} actions={
        <button onClick={() => setDrawer({ holding: null })} className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-glow"><Plus className="h-4 w-4" />添加基金</button>
      } />

      <div className="mb-4 flex gap-2 rounded-xl border border-success/25 bg-success/5 p-3 text-xs text-muted-foreground">
        <ShieldCheck className="h-4 w-4 shrink-0 text-success" />
        <span>持仓仅保存在本机。用户快照不会被正式净值或盘中估算覆盖；无可靠数据时明确显示为空。</span>
      </div>
      {portfolio?.migration && <p className="mb-4 rounded-xl border border-warning/30 bg-warning/5 p-3 text-xs text-warning">已从 V{portfolio.migration.from_schema} 台账迁移到 schema v3；原文件备份为 {portfolio.migration.backup_file}。</p>}
      {error && <p className="mb-4 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4" />{error}</p>}
      {notice && <p className="mb-4 flex items-center gap-2 rounded-xl border border-primary/25 bg-primary/5 p-3 text-sm text-muted-foreground"><CheckCircle2 className="h-4 w-4 text-primary" />{notice}</p>}

      {loading ? <GlassCard className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin text-primary" />正在读取本地持仓与公开基金数据</GlassCard> : !hasHoldings ? (
        <GlassCard className="mx-auto max-w-3xl py-16 text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-primary/30 bg-primary/10 text-primary"><WalletCards className="h-7 w-7" /></div>
          <h2 className="mt-5 text-xl font-bold">还没有基金持仓</h2>
          <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-muted-foreground">先搜索公开基金，再填写当前持有金额，建立你的本地持仓看板</p>
          <button onClick={() => setDrawer({ holding: null })} className="mt-6 inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground"><Plus className="h-4 w-4" />添加第一只基金</button>
        </GlassCard>
      ) : (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="总持有金额" value={money(overview?.total_holding_value ?? overview?.market_value)} note="优先按最新正式净值参考值汇总" />
            <Metric label="今日估算盈亏" value={money(overview?.intraday_estimated_profit_loss)} note={overview?.intraday_message || "暂无可靠数据"} tone={todayTone} />
            <Metric label="累计持有盈亏" value={overview?.pnl_complete ? money(overview.profit_loss) : "待补充"} note={overview?.pnl_complete ? "基于用户成本或盈亏快照" : "部分持仓未填写累计盈亏"} tone={pnlTone} />
            <Metric label="累计收益率" value={overview?.pnl_complete ? percent(overview.return_rate) : "待补充"} note="仅在参考总成本大于 0 时计算" tone={pnlTone} />
          </div>

          {analysis && <PortfolioHoldingList data={analysis} onDetail={openDetail} onEdit={(item) => setDrawer({ holding: item })} onDelete={setDeleteTarget} />}
          {analysis && <PortfolioCombination data={analysis} />}
        </>
      )}

      <HoldingDrawer open={Boolean(drawer)} holding={drawer?.holding?.user_holding || null} identified={drawer?.holding || null}
        existingCodes={portfolio?.holdings.map((holding) => holding.code) || []} saving={saving} onClose={() => setDrawer(null)} onSave={save} />

      <FundDetailDrawer open={Boolean(detail)} holding={detail?.item.user_holding || null} analysis={detail?.analysis || null}
        position={detail?.item.position || null}
        onClose={() => setDetail(null)} onEdit={() => { if (detail) setDrawer({ holding: detail.item }); setDetail(null); }} onDelete={() => { if (detail) setDeleteTarget(detail.item); }} />

      {deleteTarget && <div role="dialog" aria-modal="true" aria-label="确认删除持仓" className="fixed inset-0 z-[70] flex items-center justify-center bg-black/75 px-4 backdrop-blur-sm">
        <div className="w-full max-w-md rounded-2xl border border-border bg-background/95 p-6 shadow-2xl">
          <div className="flex items-start gap-3"><div className="rounded-full bg-destructive/10 p-2 text-destructive"><AlertCircle className="h-5 w-5" /></div><div><h2 className="text-lg font-semibold">确认删除持仓</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">确认删除 {deleteTarget.name}（{deleteTarget.code}）？此操作只删除本地持仓记录。</p></div></div>
          <div className="mt-6 flex justify-end gap-2">
            <button aria-label="取消删除" onClick={() => setDeleteTarget(null)} disabled={deleting} className="rounded-xl border border-border px-4 py-2 text-sm text-muted-foreground">取消</button>
            <button aria-label="确认删除" onClick={confirmRemove} disabled={deleting} className="inline-flex items-center gap-2 rounded-xl bg-destructive px-4 py-2 text-sm font-semibold text-destructive-foreground disabled:opacity-50">{deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}删除</button>
          </div>
        </div>
      </div>}
    </div>
  );
}
