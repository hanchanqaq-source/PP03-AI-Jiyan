import { useEffect, useState } from "react";
import { AlertCircle, Database, Loader2, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { api, type FundHoldingInput, type FundPortfolioData } from "@/lib/api";
import { getTag } from "@/features/tags/catalog";

const TRACKED_TAGS = ["semiconductor", "storage", "robotics"];
const initialForm: Record<keyof FundHoldingInput, string | string[]> = {
  code: "", name: "", amount: "", shares: "", cost: "", buy_date: "", notes: "", tag_ids: [],
};

const money = (value: number) => new Intl.NumberFormat("zh-CN", {
  style: "currency", currency: "CNY", maximumFractionDigits: 0,
}).format(value);

export function PortfolioAnalysis() {
  const [data, setData] = useState<FundPortfolioData | null>(null);
  const [form, setForm] = useState(initialForm);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.fundPortfolio().then(setData).catch(() => setError("基金持仓加载失败，请确认本地后端已启动。"));
  }, []);

  const set = (key: keyof FundHoldingInput, value: string | string[]) => setForm((current) => ({ ...current, [key]: value }));
  const toggleTag = (id: string) => {
    const ids = form.tag_ids as string[];
    set("tag_ids", ids.includes(id) ? ids.filter((tagId) => tagId !== id) : [...ids, id]);
  };

  const save = async () => {
    const amount = Number(form.amount), shares = Number(form.shares), cost = Number(form.cost);
    if (!/^\d{6}$/.test(form.code as string) || !(form.name as string).trim() || !/^\d{4}-\d{2}-\d{2}$/.test(form.buy_date as string)) {
      setError("请填写 6 位基金代码、基金名称和 YYYY-MM-DD 买入日期。"); return;
    }
    if (![amount, shares, cost].every((value) => Number.isFinite(value) && value >= 0)) {
      setError("金额、份额和成本必须是非负数字。"); return;
    }
    setSaving(true); setError(null);
    try {
      const payload: FundHoldingInput = {
        code: form.code as string, name: (form.name as string).trim(), amount, shares, cost,
        buy_date: form.buy_date as string, notes: form.notes as string, tag_ids: form.tag_ids as string[],
      };
      setData(await api.upsertFundHolding(payload));
      setForm(initialForm);
    } catch {
      setError("基金持仓保存失败；如本地文件损坏，后端会停止写入以保护原始数据。");
    } finally { setSaving(false); }
  };

  const remove = async (code: string) => {
    try { setData(await api.deleteFundHolding(code)); }
    catch { setError("删除失败，本地数据未确认变更。"); }
  };

  return (
    <div>
      <PageHeader title="持仓分析" subtitle="手动录入基金，关联行业标签；净值与估算严格区分" />
      <div className="mb-4 flex gap-2 rounded-xl border border-success/25 bg-success/5 p-3 text-xs text-muted-foreground">
        <ShieldCheck className="h-4 w-4 shrink-0 text-success" />
        <span>基金持仓只保存在本机用户目录。首版没有可靠基金行情源，<b className="text-foreground">盘中估算不是实时净值</b>，缺失值不会由 AI 生成。</span>
      </div>

      <GlassCard className="mb-5">
        <h2 className="mb-4 flex items-center gap-2 font-semibold"><Plus className="h-4 w-4 text-primary" />手动录入基金</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {([
            ["code", "基金代码", "000001", "text"], ["name", "基金名称", "请输入名称", "text"],
            ["amount", "持有金额", "0", "number"], ["shares", "持有份额", "0", "number"],
            ["cost", "持仓成本", "0", "number"], ["buy_date", "买入日期", "", "date"],
          ] as const).map(([key, label, placeholder, type]) => (
            <label key={key} className="text-xs text-muted-foreground">{label}
              <input aria-label={label} type={type} min={type === "number" ? "0" : undefined} value={form[key] as string}
                placeholder={placeholder} onChange={(event) => set(key, event.target.value)}
                className="mt-1.5 w-full rounded-lg border border-border bg-black/15 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/60" />
            </label>
          ))}
          <label className="text-xs text-muted-foreground sm:col-span-2">备注
            <textarea aria-label="备注" value={form.notes as string} onChange={(event) => set("notes", event.target.value)}
              className="mt-1.5 min-h-20 w-full rounded-lg border border-border bg-black/15 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/60" />
          </label>
          <fieldset className="sm:col-span-2"><legend className="text-xs text-muted-foreground">当前持仓与行业标签的关联</legend>
            <div className="mt-2 flex flex-wrap gap-2">{TRACKED_TAGS.map((id) => <label key={id} className="rounded-full border border-border px-3 py-1.5 text-xs"><input type="checkbox" aria-label={`关联${getTag(id)?.name}`} checked={(form.tag_ids as string[]).includes(id)} onChange={() => toggleTag(id)} className="mr-1.5 accent-orange-500" />{getTag(id)?.name}</label>)}</div>
          </fieldset>
        </div>
        <button onClick={save} disabled={saving} className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}保存基金持仓
        </button>
      </GlassCard>

      {error && <div className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4" />{error}</div>}

      <div className="mb-5 grid gap-3 sm:grid-cols-3">
        <GlassCard><p className="text-xs text-muted-foreground">总持仓金额</p><p className="mt-2 text-2xl font-bold">{money(data?.total_amount || 0)}</p></GlassCard>
        <GlassCard><p className="text-xs text-muted-foreground">今日估算变化</p><p className="mt-2 font-semibold text-muted-foreground">暂无可靠数据</p></GlassCard>
        <GlassCard><p className="text-xs text-muted-foreground">累计盈亏</p><p className="mt-2 font-semibold text-muted-foreground">暂无可靠数据</p></GlassCard>
      </div>

      <GlassCard glow>
        <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">当前基金持仓</h2>{data?.updated && <span className="text-xs text-muted-foreground">更新于 {data.updated}</span>}</div>
        {!data?.holdings.length ? <p className="py-8 text-center text-sm text-muted-foreground">还没有基金持仓；上方表单可手动录入。</p> : (
          <div className="space-y-4">{data.holdings.map((holding) => <article key={holding.code} className="rounded-xl border border-border/60 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold">{holding.name}</h3><p className="font-mono text-xs text-muted-foreground">{holding.code} · {money(holding.amount)}</p></div><button onClick={() => remove(holding.code)} aria-label={`删除${holding.name}`} className="text-muted-foreground hover:text-destructive"><Trash2 className="h-4 w-4" /></button></div>
            <div className="mt-4 grid gap-3 text-xs sm:grid-cols-3"><div><p className="text-muted-foreground">官方净值</p><p className="mt-1">{holding.official_nav ?? "暂无可靠数据"}</p></div><div><p className="text-muted-foreground">盘中估算</p><p className="mt-1">{holding.intraday_estimate ?? "暂无可靠数据"}</p></div><div><p className="text-muted-foreground">历史净值</p><p className="mt-1">{holding.historical_nav ?? "暂无可靠数据"}</p></div></div>
            <div className="mt-4 flex flex-wrap gap-2">{holding.tag_ids.map((id) => <span key={id} className="rounded-full bg-primary/10 px-2 py-1 text-xs text-primary">{getTag(id)?.name || id}</span>)}</div>
            <p className="mt-3 text-xs text-muted-foreground">前十大持仓、行业暴露、基金重合度与相关新闻：需要可靠基金披露数据；当前仅按用户确认标签建立关系。</p>
          </article>)}</div>
        )}
      </GlassCard>
    </div>
  );
}
