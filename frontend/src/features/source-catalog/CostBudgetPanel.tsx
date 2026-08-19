import { Loader2, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type {
  AdapterConfigUpdate,
  AdapterConfigurationView,
  AdapterCostView,
  AdapterUsageView,
  BudgetGateState,
} from "./types";

const DECIMAL_TEXT = /^(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,8})?$/;
const MAX_INTEGER = "1000000000000";

function isWithinBudgetBound(value: string): boolean {
  const [integer, fraction = ""] = value.split(".");
  if (integer.length < MAX_INTEGER.length) return true;
  if (integer.length > MAX_INTEGER.length || integer > MAX_INTEGER) return false;
  return integer !== MAX_INTEGER || !/[1-9]/.test(fraction);
}
export function isPositiveCanonicalBudget(value: string): boolean {
  return DECIMAL_TEXT.test(value) && isWithinBudgetBound(value) && /[1-9]/.test(value);
}

function money(value: string | null): string {
  if (value === null) return "尚未观测";
  return /^0(?:\.0+)?$/.test(value) ? "¥0" : `¥${value}`;
}

interface BudgetDraft {
  daily_budget: string;
  monthly_budget: string;
  per_request_budget: string;
}

function draftFrom(configuration: AdapterConfigurationView): BudgetDraft {
  return {
    daily_budget: configuration.daily_budget ?? "",
    monthly_budget: configuration.monthly_budget ?? "",
    per_request_budget: configuration.per_request_budget ?? "",
  };
}

export function CostBudgetPanel({
  configuration,
  usage,
  cost,
  onSave,
  onGateChange,
}: {
  configuration: AdapterConfigurationView;
  usage: AdapterUsageView | null;
  cost: AdapterCostView | null;
  onSave: (updates: Required<Pick<AdapterConfigUpdate, "daily_budget" | "monthly_budget" | "per_request_budget">>) => Promise<void>;
  onGateChange?: (gate: BudgetGateState) => void;
}) {
  const initial = useMemo(() => draftFrom(configuration), [configuration]);
  const [draft, setDraft] = useState<BudgetDraft>(initial);
  const [saved, setSaved] = useState(() => Object.values(initial).every(isPositiveCanonicalBudget));
  const [pending, setPending] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const valid = Object.values(draft).every(isPositiveCanonicalBudget);

  useEffect(() => {
    setDraft(initial);
    setSaved(Object.values(initial).every(isPositiveCanonicalBudget));
    setSaveError(false);
  }, [configuration.adapter_id, initial]);

  useEffect(() => { onGateChange?.({ valid, saved }); }, [onGateChange, saved, valid]);

  const update = (field: keyof BudgetDraft, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setSaved(false);
    setSaveError(false);
  };

  const submit = async () => {
    if (!valid || pending) return;
    setPending(true);
    setSaveError(false);
    try {
      await onSave(draft);
      setSaved(true);
    } catch {
      setSaved(false);
      setSaveError(true);
    } finally {
      setPending(false);
    }
  };

  const anyDraft = Object.values(draft).some(Boolean);
  const observed = usage?.usage_status === "observed" && cost?.usage_status === "observed";

  return <section aria-labelledby={`budget-title-${configuration.adapter_id}`} className="rounded-xl border border-border/60 bg-muted/10 p-4">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h3 id={`budget-title-${configuration.adapter_id}`} className="text-sm font-semibold">预算与用量</h3><p className="mt-1 text-xs text-muted-foreground">金额以规范十进制字符串保存，不在浏览器中进行浮点换算。</p></div>
      <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-1 text-[11px] text-muted-foreground"><ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />服务端预算门槛</span>
    </div>

    <div className="mt-4 grid gap-3 sm:grid-cols-3">
      <label className="text-xs text-muted-foreground">每日预算
        <input aria-label="每日预算" inputMode="decimal" value={draft.daily_budget} onChange={(event) => update("daily_budget", event.target.value)} placeholder="例如 1.00" className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" />
      </label>
      <label className="text-xs text-muted-foreground">每月预算
        <input aria-label="每月预算" inputMode="decimal" value={draft.monthly_budget} onChange={(event) => update("monthly_budget", event.target.value)} placeholder="例如 20.00" className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" />
      </label>
      <label className="text-xs text-muted-foreground">单次预算
        <input aria-label="单次预算" inputMode="decimal" value={draft.per_request_budget} onChange={(event) => update("per_request_budget", event.target.value)} placeholder="例如 0.01" className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" />
      </label>
    </div>
    {anyDraft && !valid && <p role="alert" className="mt-2 text-xs text-warning">请输入大于 0 的规范十进制金额；整数最多 1 万亿元，小数最多 8 位。</p>}
    {saveError && <p role="alert" className="mt-2 text-xs text-warning">预算保存失败，原有服务端配置保持不变。</p>}
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <button type="button" onClick={submit} disabled={!valid || pending} className="inline-flex items-center gap-2 rounded-lg border border-primary/45 px-3 py-2 text-xs font-medium text-primary hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-45">{pending && <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />}保存预算</button>
      <span className="text-xs text-muted-foreground">{saved ? "预算已保存" : "预算尚未保存"}</span>
    </div>

    <div className="mt-4 border-t border-border/50 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-xs font-semibold text-foreground">已观测用量</h4><p className="text-[11px] text-muted-foreground">日 {usage?.day ?? cost?.day ?? "未知"} · 月 {usage?.month ?? cost?.month ?? "未知"}</p></div>
      {!observed ? <p className="mt-2 text-sm text-muted-foreground">用量尚未观测</p> : <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
        <p className="rounded-lg border border-border/50 bg-background/45 p-3"><span className="text-muted-foreground">当日费用</span><strong className="mt-1 block text-base text-foreground">{money(usage?.daily_cost ?? null)}</strong></p>
        <p className="rounded-lg border border-border/50 bg-background/45 p-3"><span className="text-muted-foreground">当月费用</span><strong className="mt-1 block text-base text-foreground">{money(usage?.monthly_cost ?? null)}</strong></p>
      </div>}
    </div>
  </section>;
}
