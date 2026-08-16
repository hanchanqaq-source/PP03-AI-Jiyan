import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Loader2, Plus, Search, X } from "lucide-react";
import { getTag } from "@/features/tags/catalog";
import { TagSelector } from "@/features/tags/TagSelector";
import { api } from "@/lib/api";
import { DataStatus } from "./DataStatus";
import type { FundHolding, FundHoldingInput, FundSearchResult, PortfolioHoldingAnalysis } from "./types";

interface HoldingDrawerProps {
  open: boolean;
  holding: FundHolding | null;
  identified: PortfolioHoldingAnalysis | null;
  existingCodes: string[];
  saving: boolean;
  onClose: () => void;
  onSave: (payload: FundHoldingInput) => Promise<void>;
}

interface Draft {
  inputMode: "amount_pnl" | "shares_cost";
  amount: string;
  pnl: string;
  shares: string;
  avgCost: string;
  buyDate: string;
  notes: string;
  tagIds: string[];
}

const EMPTY: Draft = {
  inputMode: "amount_pnl", amount: "", pnl: "", shares: "", avgCost: "",
  buyDate: "", notes: "", tagIds: [],
};

function draftFromHolding(holding: FundHolding | null): Draft {
  if (!holding) return EMPTY;
  return {
    inputMode: holding.input_mode,
    amount: holding.amount_snapshot == null ? "" : String(holding.amount_snapshot),
    pnl: holding.cumulative_pnl_snapshot == null ? "" : String(holding.cumulative_pnl_snapshot),
    shares: holding.shares_source === "user" && holding.shares != null ? String(holding.shares) : "",
    avgCost: holding.avg_unit_cost == null ? "" : String(holding.avg_unit_cost),
    buyDate: holding.buy_date || "",
    notes: holding.notes || "",
    tagIds: [...holding.custom_tag_ids],
  };
}

const fieldClass = "mt-1.5 w-full rounded-xl border border-border bg-black/20 px-3 py-2.5 text-sm text-foreground outline-none transition-colors focus:border-primary/70";

export function HoldingDrawer({ open, holding, identified, existingCodes, saving, onClose, onSave }: HoldingDrawerProps) {
  const editing = Boolean(holding);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<FundSearchResult[]>([]);
  const [selected, setSelected] = useState<FundSearchResult | null>(null);
  const [searchMeta, setSearchMeta] = useState<Awaited<ReturnType<typeof api.searchFunds>>["meta"] | null>(null);
  const [searching, setSearching] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [tagSelectorOpen, setTagSelectorOpen] = useState(false);
  const [customTag, setCustomTag] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setDraft(draftFromHolding(holding));
    setAdvanced(Boolean(holding && (holding.input_mode === "shares_cost" || holding.cumulative_pnl_snapshot != null || holding.buy_date || holding.notes || holding.custom_tag_ids.length)));
    setQuery("");
    setResults([]);
    setSearchMeta(null);
    setCustomTag("");
    setError(null);
    if (holding) {
      setSelected({
        code: holding.code,
        name: identified?.name || holding.manual_name || holding.legacy_name || `基金 ${holding.code}`,
        fund_type: identified?.fund_type || null,
        latest_nav: identified?.analysis?.latest_nav.data?.unit_nav || holding.basis_nav,
        latest_nav_date: identified?.analysis?.latest_nav.data?.nav_date || holding.basis_nav_date,
        manager_names: identified?.analysis?.profile.data?.manager_names || [],
        management_company: identified?.analysis?.profile.data?.management_company || null,
      });
    } else {
      setSelected(null);
    }
  }, [holding, identified, open]);

  useEffect(() => {
    const normalized = query.trim();
    if (!open || editing || selected || normalized.length < 2) {
      setResults([]);
      setSearching(false);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        const response = await api.searchFunds(normalized);
        if (!cancelled) {
          setResults(response.data || []);
          setSearchMeta(response.meta);
        }
      } catch {
        if (!cancelled) setError("基金搜索暂不可用；可稍后重试，现有持仓不会受影响。");
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 300);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [editing, open, query, selected]);

  const tagNames = useMemo(() => draft.tagIds.map((id) => getTag(id)?.name || id.replace(/^custom:/, "")), [draft.tagIds]);
  if (!open) return null;

  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((current) => ({ ...current, [key]: value }));

  const choose = (fund: FundSearchResult) => {
    if (!editing && existingCodes.includes(fund.code)) {
      setError("该基金已在持仓中，请使用编辑操作更新。");
      return;
    }
    setSelected(fund);
    setQuery(`${fund.code} ${fund.name}`);
    setResults([]);
    setError(null);
  };

  const addCustomTag = () => {
    const name = customTag.trim();
    if (!name) return;
    const id = `custom:${name}`;
    if (!draft.tagIds.includes(id)) update("tagIds", [...draft.tagIds, id]);
    setCustomTag("");
  };

  const submit = async () => {
    if (!selected) { setError("请先搜索并选择基金。"); return; }
    const amount = draft.amount === "" ? null : Number(draft.amount);
    const pnl = draft.pnl === "" ? null : Number(draft.pnl);
    const shares = draft.shares === "" ? null : Number(draft.shares);
    const avgCost = draft.avgCost === "" ? null : Number(draft.avgCost);
    if (draft.inputMode === "amount_pnl" && (!(amount && amount > 0) || (pnl != null && !Number.isFinite(pnl)))) {
      setError("当前持有金额必须大于 0；累计盈亏可不填，但填写时必须是有效数字。");
      return;
    }
    if (draft.inputMode === "shares_cost" && (!(shares && shares > 0) || avgCost == null || avgCost < 0 || !draft.buyDate)) {
      setError("精确模式请填写有效份额、平均单位成本和买入日期。");
      return;
    }
    setError(null);
    await onSave({
      code: selected.code,
      input_mode: draft.inputMode,
      amount_snapshot: draft.inputMode === "amount_pnl" ? amount : holding?.amount_snapshot ?? null,
      cumulative_pnl_snapshot: draft.inputMode === "amount_pnl" ? pnl : holding?.cumulative_pnl_snapshot ?? null,
      shares: draft.inputMode === "shares_cost" ? shares : null,
      avg_unit_cost: draft.inputMode === "shares_cost" ? avgCost : null,
      avg_cost: draft.inputMode === "shares_cost" ? avgCost : null,
      buy_date: draft.buyDate,
      notes: draft.notes.trim(),
      custom_tag_ids: draft.tagIds,
      verification_status: "verified",
      manual_name: null,
      replace: editing,
    });
  };

  return (
    <div className="fixed inset-0 z-[60] bg-black/70 backdrop-blur-sm" onMouseDown={(event) => {
      if (event.currentTarget === event.target && !saving) onClose();
    }}>
      <aside role="dialog" aria-modal="true" aria-label={editing ? "编辑持仓" : "添加基金"}
        className="ml-auto flex h-full w-full max-w-xl flex-col border-l border-blue-400/20 bg-gradient-to-b from-slate-950 via-slate-950 to-blue-950/95 shadow-2xl">
        <header className="flex items-start justify-between border-b border-border/60 px-6 py-5">
          <div>
            <h2 className="text-xl font-bold">{editing ? "编辑持仓" : "添加基金"}</h2>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">先搜索基金，再填写当前持有金额。成本、份额和标签可以稍后补充。</p>
          </div>
          <button onClick={onClose} disabled={saving} aria-label="关闭持仓抽屉" className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-5 w-5" /></button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {error && <p className="rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}

          <section>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-primary">01 · 搜索基金</p>
            <label className="mt-3 block text-xs text-muted-foreground">搜索基金代码或名称
              <div className="mt-1.5 flex items-center gap-2 rounded-xl border border-border bg-black/20 px-3 focus-within:border-primary/70">
                {searching ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : <Search className="h-4 w-4" />}
                <input aria-label="搜索基金代码或名称" placeholder="例如：000001 或 华夏成长" value={query}
                  disabled={editing || Boolean(selected)} onChange={(event) => { setQuery(event.target.value); setError(null); }}
                  className="w-full bg-transparent py-2.5 text-sm outline-none disabled:opacity-70" />
                {!editing && selected && <button onClick={() => { setSelected(null); setQuery(""); }} className="text-xs text-primary">重选</button>}
              </div>
            </label>
            {!!results.length && <div className="mt-2 max-h-56 overflow-y-auto rounded-xl border border-border bg-background p-1 shadow-xl">{results.map((fund) => (
              <button key={`${fund.code}-${fund.name}`} aria-label={`选择基金 ${fund.name}`} onClick={() => choose(fund)}
                className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-muted/60">
                <span><b>{fund.name}</b><span className="ml-2 font-mono text-xs text-muted-foreground">{fund.code}</span><span className="mt-0.5 block text-xs text-muted-foreground">{fund.fund_type || "类型暂无可靠数据"}</span></span>
                <span className="text-right text-xs"><b>{fund.latest_nav ?? "—"}</b><span className="mt-0.5 block text-muted-foreground">{fund.latest_nav_date || "暂无净值日期"}</span></span>
              </button>
            ))}</div>}
            {searchMeta && <div className="mt-2"><DataStatus meta={searchMeta} compact /></div>}
          </section>

          {selected && <section className="rounded-xl border border-blue-400/20 bg-gradient-to-br from-slate-900/85 to-blue-950/45 p-4 text-sm">
            <p className="font-semibold">已识别基金：{selected.name}（{selected.code}）</p>
            <div className="mt-2 grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
              <span>类型：{selected.fund_type || "暂无可靠数据"}</span>
              <span>经理：{selected.manager_names.join("、") || "暂无可靠数据"}</span>
              <span className="sm:col-span-2">最新正式净值：{selected.latest_nav ?? "—"}（{selected.latest_nav_date || "暂无日期"}）</span>
            </div>
          </section>}

          <section>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-primary">02 · 核心输入</p>
            {draft.inputMode === "amount_pnl" ? <div className="mt-3 grid gap-4">
              <label className="text-xs text-muted-foreground">当前持有金额（必填）<input aria-label="当前持有金额" type="number" min="0" step="any" value={draft.amount} onChange={(event) => update("amount", event.target.value)} className={fieldClass} /><span className="mt-1 block text-[11px]">用于计算组合占比和今日估算</span></label>
            </div> : <p className="mt-3 rounded-xl border border-primary/25 bg-primary/5 p-3 text-xs text-muted-foreground">精确模式使用真实份额与单位成本跟踪正式净值。</p>}
          </section>

          <section className="rounded-xl border border-border/60 bg-slate-950/35 p-4">
            <button aria-expanded={advanced} onClick={() => setAdvanced((value) => !value)} className="flex w-full items-center justify-between text-sm font-semibold">
              {advanced ? "收起其他信息" : "展开其他信息"}{advanced ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
            {advanced && <div className="mt-4 space-y-4 border-t border-border/50 pt-4">
              {draft.inputMode === "amount_pnl" && <label className="block text-xs text-muted-foreground">当前累计盈亏（选填）<input aria-label="当前累计盈亏" type="number" step="any" value={draft.pnl} onChange={(event) => update("pnl", event.target.value)} className={fieldClass} /><span className="mt-1 block text-[11px]">不填也可以加入持仓，累计收益率将显示待补充</span></label>}
              <label className="block text-xs text-muted-foreground">买入日期（选填）<input aria-label="买入日期" type="date" value={draft.buyDate} onChange={(event) => update("buyDate", event.target.value)} className={fieldClass} /></label>
              <fieldset>
                <legend className="text-xs text-muted-foreground">录入方式</legend>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <label className={`rounded-xl border p-3 text-sm ${draft.inputMode === "amount_pnl" ? "border-primary/60 bg-primary/10" : "border-border"}`}><input type="radio" name="mode" aria-label="快速模式" checked={draft.inputMode === "amount_pnl"} onChange={() => update("inputMode", "amount_pnl")} className="mr-2" />快速模式</label>
                  <label className={`rounded-xl border p-3 text-sm ${draft.inputMode === "shares_cost" ? "border-primary/60 bg-primary/10" : "border-border"}`}><input type="radio" name="mode" aria-label="精确模式" checked={draft.inputMode === "shares_cost"} onChange={() => update("inputMode", "shares_cost")} className="mr-2" />精确模式</label>
                </div>
              </fieldset>
              {draft.inputMode === "shares_cost" && <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs text-muted-foreground">持有份额<input aria-label="持有份额" type="number" step="any" min="0" value={draft.shares} onChange={(event) => update("shares", event.target.value)} className={fieldClass} /></label>
                <label className="text-xs text-muted-foreground">平均单位成本<input aria-label="平均单位成本" type="number" step="any" min="0" value={draft.avgCost} onChange={(event) => update("avgCost", event.target.value)} className={fieldClass} /></label>
              </div>}
              <label className="block text-xs text-muted-foreground">备注（选填）<textarea aria-label="备注" value={draft.notes} onChange={(event) => update("notes", event.target.value)} className={`${fieldClass} min-h-20 resize-y`} /></label>
              <div>
                <p className="text-xs text-muted-foreground">用户标签</p>
                <div className="mt-2 flex flex-wrap gap-2">{tagNames.map((name) => <span key={name} className="rounded-full border border-border bg-muted/40 px-2 py-1 text-xs">{name}</span>)}</div>
                <div className="mt-2 flex gap-2">
                  <input aria-label="新增用户标签" value={customTag} onChange={(event) => setCustomTag(event.target.value)} className={`${fieldClass} mt-0`} placeholder="新增自定义标签" />
                  <button aria-label={`添加用户标签 ${customTag.trim()}`} onClick={addCustomTag} disabled={!customTag.trim()} className="shrink-0 rounded-xl border border-border px-3 text-sm disabled:opacity-40"><Plus className="h-4 w-4" /></button>
                </div>
                <button onClick={() => setTagSelectorOpen(true)} className="mt-2 text-xs text-primary">从标签库多选</button>
              </div>
            </div>}
          </section>
        </div>

        <footer className="flex justify-end gap-2 border-t border-border/60 px-6 py-4">
          <button onClick={onClose} disabled={saving} className="rounded-xl border border-border px-4 py-2 text-sm text-muted-foreground">取消</button>
          <button onClick={submit} disabled={saving || !selected} className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50">
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}{editing ? "保存持仓" : "添加到持仓"}
          </button>
        </footer>
      </aside>
      <TagSelector open={tagSelectorOpen} selectedIds={draft.tagIds} onCancel={() => setTagSelectorOpen(false)} onConfirm={(ids) => { update("tagIds", ids); setTagSelectorOpen(false); }} />
    </div>
  );
}
