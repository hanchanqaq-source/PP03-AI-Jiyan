import { Check, Eye, KeyRound, Loader2, ShieldAlert, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { CostBudgetPanel } from "./CostBudgetPanel";
import type {
  AdapterConfigurationView,
  AdapterCostView,
  AdapterUsageView,
  AdapterView,
  BudgetGateState,
  DataSourceConfigurationResponse,
  DataSourceCostResponse,
  DataSourceUsageResponse,
} from "./types";

interface ConfigurationBundle {
  configuration: DataSourceConfigurationResponse;
  usage: DataSourceUsageResponse;
  cost: DataSourceCostResponse;
}

type OperationKind = "load" | "credential" | "delete" | "budget" | "free-only" | "action";

interface OperationIdentity {
  adapterId: string;
  drawerEpoch: number;
  sequence: number;
}

const operationKinds: OperationKind[] = ["load", "credential", "delete", "budget", "free-only", "action"];

const inFlightReads = new Map<string, Promise<ConfigurationBundle>>();

function readConfiguration(adapterId: string): Promise<ConfigurationBundle> {
  const current = inFlightReads.get(adapterId);
  if (current) return current;
  const request = Promise.all([
    api.dataSourceConfig(),
    api.dataSourceUsage(adapterId),
    api.dataSourceCost(adapterId),
  ]).then(([configuration, usage, cost]) => ({ configuration, usage, cost }));
  inFlightReads.set(adapterId, request);
  request.then(
    () => { if (inFlightReads.get(adapterId) === request) inFlightReads.delete(adapterId); },
    () => { if (inFlightReads.get(adapterId) === request) inFlightReads.delete(adapterId); },
  );
  return request;
}

const statusLabels: Record<string, string> = {
  connected: "已连接",
  configured: "已配置",
  stored: "凭据已保存",
  validated: "已验证",
  unconfigured: "未配置",
  plan_unavailable: "当前套餐不可用",
  license_required: "需要许可证",
  catalog_only: "仅目录",
  disabled: "已停用",
  credential_store_unavailable: "本机凭据存储不可用",
  not_required: "无需凭据",
};

const billingLabels: Record<AdapterView["billing_model"], string> = {
  free_no_key: "免费免密钥",
  free_key: "免费需密钥",
  freemium: "免费额度",
  paid_api: "付费 API",
  enterprise_license: "企业许可证",
  internal_only: "内部专用",
};

function configurationTruth(adapter: AdapterView, configuration: AdapterConfigurationView): string {
  if (adapter.catalog_status === "license_required") return "需要许可证";
  if (adapter.catalog_status === "catalog_only" || adapter.catalog_status === "disabled") return statusLabels[adapter.catalog_status];
  if (configuration.credential.status === "plan_unavailable") return "当前套餐不可用";
  if (adapter.credential_env_names.length && !configuration.credential.configured) return "未配置";
  if (configuration.enabled) return "已启用";
  return statusLabels[configuration.credential.status] || statusLabels[configuration.catalog_status] || "状态未知";
}

function GateStep({ label, ready, detail }: { label: string; ready: boolean; detail: string }) {
  return <li className="min-w-0 flex-1">
    <div className={`h-1 rounded-full ${ready ? "bg-success" : "bg-border"}`} aria-hidden="true" />
    <p className="mt-2 text-[11px] font-semibold text-foreground">{label}</p>
    <p className={`mt-0.5 text-[10px] leading-4 ${ready ? "text-success" : "text-muted-foreground"}`}>{detail}</p>
  </li>;
}

function boundedActionError(action: "credential" | "delete" | "budget" | "free-only" | "action"): string {
  if (action === "credential") return "凭据保存失败，请检查本机安全存储状态后重试。";
  if (action === "delete") return "凭据删除失败，现有配置未被标记为成功。";
  if (action === "budget") return "预算保存失败，原有服务端配置保持不变。";
  if (action === "free-only") return "Free-only 设置保存失败，当前继续按开启状态处理。";
  return "配置操作未完成，请检查授权门槛后重试。";
}

export function SourceConfigurationDrawer({
  open,
  adapter,
  onClose,
}: {
  open: boolean;
  adapter: AdapterView | null;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const capabilityRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const secretInputRef = useRef<HTMLInputElement>(null);
  const openRef = useRef(open);
  const mountedRef = useRef(true);
  const adapterIdentityRef = useRef<string | null>(open ? adapter?.adapter_id ?? null : null);
  const drawerEpochRef = useRef(0);
  const operationSequenceRef = useRef(0);
  const latestMutationSequenceRef = useRef(0);
  const operationTokensRef = useRef<Record<OperationKind, OperationIdentity | null>>({
    load: null,
    credential: null,
    delete: null,
    budget: null,
    "free-only": null,
    action: null,
  });
  const [configuration, setConfiguration] = useState<AdapterConfigurationView | null>(null);
  const [usage, setUsage] = useState<AdapterUsageView | null>(null);
  const [cost, setCost] = useState<AdapterCostView | null>(null);
  const [verifiedTimezone, setVerifiedTimezone] = useState<"UTC" | null>(null);
  const [freeOnly, setFreeOnly] = useState(true);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [secret, setSecret] = useState("");
  const [credentialPending, setCredentialPending] = useState(false);
  const [deletePending, setDeletePending] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [freeOnlyPending, setFreeOnlyPending] = useState(false);
  const [actionPending, setActionPending] = useState(false);
  const [budgetGate, setBudgetGate] = useState<BudgetGateState>({ valid: false, saved: false });
  const [confirmation, setConfirmation] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [licenseNotice, setLicenseNotice] = useState(false);

  openRef.current = open;
  adapterIdentityRef.current = open ? adapter?.adapter_id ?? null : null;

  const invalidateOperations = useCallback(() => {
    drawerEpochRef.current += 1;
    latestMutationSequenceRef.current = ++operationSequenceRef.current;
    for (const kind of operationKinds) operationTokensRef.current[kind] = null;
  }, []);

  const beginOperation = useCallback((kind: OperationKind, adapterId: string) => {
    const token: OperationIdentity = {
      adapterId,
      drawerEpoch: drawerEpochRef.current,
      sequence: ++operationSequenceRef.current,
    };
    operationTokensRef.current[kind] = token;
    latestMutationSequenceRef.current = token.sequence;
    return token;
  }, []);

  const isCurrentOperation = useCallback((kind: OperationKind, token: OperationIdentity, requireLatest = false) => (
    mountedRef.current
    && openRef.current
    && adapterIdentityRef.current === token.adapterId
    && drawerEpochRef.current === token.drawerEpoch
    && operationTokensRef.current[kind]?.sequence === token.sequence
    && (!requireLatest || latestMutationSequenceRef.current === token.sequence)
  ), []);

  const clearSecret = useCallback((updateState = true) => {
    if (secretInputRef.current) secretInputRef.current.value = "";
    if (updateState && mountedRef.current) setSecret("");
  }, []);

  useLayoutEffect(() => {
    mountedRef.current = true;
    invalidateOperations();
    if (!open) {
      if (secretInputRef.current) secretInputRef.current.value = "";
      setSecret("");
      setCredentialPending(false);
      setDeletePending(false);
      setFreeOnlyPending(false);
      setActionPending(false);
      return;
    }
    adapterIdentityRef.current = adapter?.adapter_id ?? null;
    setCredentialPending(false);
    setDeletePending(false);
    setFreeOnlyPending(false);
    setActionPending(false);
    return () => {
      invalidateOperations();
      if (secretInputRef.current) secretInputRef.current.value = "";
    };
  }, [adapter?.adapter_id, invalidateOperations, open]);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const closeDrawer = useCallback(() => {
    invalidateOperations();
    adapterIdentityRef.current = null;
    clearSecret();
    setCredentialPending(false);
    setDeletePending(false);
    setFreeOnlyPending(false);
    setActionPending(false);
    setFeedback(null);
    setError(null);
    setConfirmDelete(false);
    setConfirmation(false);
    onClose();
  }, [clearSecret, invalidateOperations, onClose]);

  useEffect(() => {
    if (!open || !adapter) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDrawer();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])') || []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      previousFocusRef.current?.focus();
    };
  }, [adapter, closeDrawer, open]);

  const applyBundle = useCallback((bundle: ConfigurationBundle, token: OperationIdentity) => {
    if (!isCurrentOperation("load", token, true)) return;
    const row = bundle.configuration.adapters.find((item) => item.adapter_id === token.adapterId);
    if (!row) throw new Error("missing adapter configuration");
    setConfiguration(row);
    setUsage(bundle.usage.adapters.find((item) => item.adapter_id === token.adapterId) || null);
    setCost(bundle.cost.adapters.find((item) => item.adapter_id === token.adapterId) || null);
    setVerifiedTimezone(bundle.usage.timezone === "UTC" && bundle.cost.timezone === "UTC" ? "UTC" : null);
    setFreeOnly(bundle.configuration.free_only);
  }, [isCurrentOperation]);

  useEffect(() => {
    if (!open || !adapter) return;
    const token = beginOperation("load", adapter.adapter_id);
    setConfiguration(null);
    setUsage(null);
    setCost(null);
    setVerifiedTimezone(null);
    setFreeOnly(true);
    setLoading(true);
    setLoadError(false);
    setFeedback(null);
    setError(null);
    setLicenseNotice(false);
    setConfirmation(false);
    readConfiguration(adapter.adapter_id).then((bundle) => {
      if (!isCurrentOperation("load", token, true)) return;
      try { applyBundle(bundle, token); }
      catch { if (isCurrentOperation("load", token, true)) setLoadError(true); }
    }).catch(() => {
      if (isCurrentOperation("load", token, true)) setLoadError(true);
    }).finally(() => {
      if (isCurrentOperation("load", token)) setLoading(false);
    });
  }, [adapter, applyBundle, beginOperation, isCurrentOperation, open]);

  const saveCredential = async () => {
    if (!adapter || !secret || credentialPending) return;
    const token = beginOperation("credential", adapter.adapter_id);
    let submitted = secret;
    setCredentialPending(true);
    setFeedback(null);
    setError(null);
    try {
      const state = await api.dataSourcePutCredential(token.adapterId, submitted);
      if (isCurrentOperation("credential", token, true)) {
        setConfiguration((current) => current ? { ...current, credential: state, enabled: false } : current);
        setFeedback("凭据已保存");
      }
    } catch {
      if (isCurrentOperation("credential", token, true)) setError(boundedActionError("credential"));
    } finally {
      submitted = "";
      if (isCurrentOperation("credential", token)) {
        clearSecret();
        setCredentialPending(false);
      }
    }
  };

  const deleteCredential = async () => {
    if (!adapter || deletePending) return;
    if (!confirmDelete) { setConfirmDelete(true); return; }
    const token = beginOperation("delete", adapter.adapter_id);
    setDeletePending(true);
    setFeedback(null);
    setError(null);
    try {
      const state = await api.dataSourceDeleteCredential(token.adapterId);
      if (isCurrentOperation("delete", token, true)) {
        setConfiguration((current) => current ? { ...current, credential: state, enabled: false } : current);
        setConfirmDelete(false);
        setFeedback("凭据已删除");
      }
    } catch {
      if (isCurrentOperation("delete", token, true)) setError(boundedActionError("delete"));
    } finally {
      if (isCurrentOperation("delete", token)) {
        clearSecret();
        setDeletePending(false);
      }
    }
  };

  const saveBudget = async (updates: { daily_budget: string; monthly_budget: string; per_request_budget: string }) => {
    if (!adapter) return;
    const token = beginOperation("budget", adapter.adapter_id);
    try {
      await api.dataSourceUpdateAdapterConfig(token.adapterId, updates);
      if (isCurrentOperation("budget", token, true)) {
        setConfiguration((current) => current ? { ...current, ...updates } : current);
        setFeedback("预算已保存");
        setError(null);
      }
    } catch {
      if (isCurrentOperation("budget", token, true)) setError(boundedActionError("budget"));
      throw new Error("budget save failed");
    }
  };

  const updateFreeOnly = async (next: boolean) => {
    if (!adapter || freeOnlyPending) return;
    const token = beginOperation("free-only", adapter.adapter_id);
    setFreeOnlyPending(true);
    setFeedback(null);
    setError(null);
    try {
      const document = await api.dataSourceUpdateFreeOnly(next);
      if (isCurrentOperation("free-only", token, true)) {
        setFreeOnly(document.free_only);
        const row = document.adapters.find((item) => item.adapter_id === token.adapterId);
        if (row) setConfiguration(row);
        setFeedback(document.free_only ? "Free-only 已开启" : "Free-only 已关闭");
      }
    } catch {
      if (isCurrentOperation("free-only", token, true)) {
        setFreeOnly(true);
        setError(boundedActionError("free-only"));
      }
    } finally {
      if (isCurrentOperation("free-only", token)) setFreeOnlyPending(false);
    }
  };

  const runAction = async (action: "enable" | "disable" | "validate") => {
    if (!adapter || actionPending) return;
    const token = beginOperation("action", adapter.adapter_id);
    setActionPending(true);
    setFeedback(null);
    setError(null);
    try {
      const result = action === "enable"
        ? await api.dataSourceEnable(token.adapterId, confirmation)
        : action === "disable"
          ? await api.dataSourceDisable(token.adapterId)
          : await api.dataSourceValidate(token.adapterId);
      if (isCurrentOperation("action", token, true)) {
        if (action !== "validate" && typeof result.enabled === "boolean") setConfiguration((current) => current ? { ...current, enabled: result.enabled! } : current);
        setFeedback(action === "validate" ? (result.status === "unconfigured" ? "未配置" : "配置验证完成") : action === "enable" ? "数据源已启用；连接状态仍以实际观测为准" : "数据源已停用");
      }
    } catch (reason) {
      if (isCurrentOperation("action", token, true)) {
        if (action === "validate" && reason instanceof ApiError && reason.status === 409) setError("传输方式尚未支持");
        else setError(boundedActionError("action"));
      }
    } finally {
      if (isCurrentOperation("action", token)) setActionPending(false);
    }
  };

  if (!open || !adapter) return null;

  const enterprise = adapter.billing_model === "enterprise_license" || adapter.catalog_status === "license_required";
  const paid = adapter.billing_model === "paid_api" || adapter.billing_model === "freemium";
  const keyed = adapter.credential_env_names.length === 1;
  const credentialReady = !keyed || Boolean(configuration?.credential.configured);
  const planReady = !enterprise && (!paid || configuration?.credential.status === "validated");
  const trustedTransport = !keyed && adapter.auth_type === "none";
  const freeOnlyReady = !paid || !freeOnly;
  const budgetReady = !paid || (budgetGate.valid && budgetGate.saved);
  const confirmationReady = !paid || confirmation;
  const catalogAllowsAction = !["license_required", "catalog_only", "disabled"].includes(adapter.catalog_status);
  const canEnable = Boolean(configuration && catalogAllowsAction && credentialReady && planReady && trustedTransport && freeOnlyReady && budgetReady && confirmationReady && !configuration.enabled);
  const titleId = `configuration-title-${adapter.adapter_id}`;
  const descriptionId = `configuration-description-${adapter.adapter_id}`;

  return <div className="fixed inset-0 z-50 bg-black/70" onMouseDown={(event) => { if (event.currentTarget === event.target) closeDrawer(); }}>
    <aside ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} className="ml-auto flex h-[100dvh] w-full max-w-3xl flex-col overflow-hidden border-l border-primary/25 bg-background/95 shadow-2xl backdrop-blur-xl">
      <header className="flex shrink-0 items-start justify-between gap-4 border-b border-border/60 px-4 py-4 sm:px-5">
        <div className="min-w-0"><p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-primary">数据源授权</p><h2 id={titleId} className="mt-1 break-words text-xl font-bold">配置 {adapter.adapter_name}</h2><p id={descriptionId} className="mt-1 text-xs text-muted-foreground">只管理当前接入方式；凭据写入本机安全存储，不会回显或保存在浏览器中。</p></div>
        <button ref={closeRef} type="button" onClick={closeDrawer} aria-label="关闭数据源配置" className="shrink-0 rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><X className="h-5 w-5" aria-hidden="true" /></button>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto overscroll-contain p-4 sm:p-5">
        <section className="rounded-xl border border-border/60 bg-muted/10 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold">{adapter.adapter_name}</h3><p className="mt-1 break-all text-xs text-muted-foreground">接入 ID：{adapter.adapter_id} · {billingLabels[adapter.billing_model]}</p></div>{configuration && <span className="rounded-full border border-border px-3 py-1 text-xs">{configurationTruth(adapter, configuration)}</span>}</div>
          <p className="mt-3 text-xs text-muted-foreground">{adapter.license_note || "许可边界未说明"} · {adapter.usage_note || "用量规则未说明"}</p>
        </section>

        {loading && <p className="inline-flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />正在读取配置、用量与费用…</p>}
        {loadError && <p role="alert" className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning">配置读取失败；未显示推测的连接、用量或费用状态。</p>}

        {configuration && !loadError && <>
          <section aria-label="授权门槛" className="rounded-xl border border-primary/20 bg-primary/[0.04] p-4">
            <div className="flex items-center gap-2"><ShieldAlert className="h-4 w-4 text-primary" aria-hidden="true" /><h3 className="text-sm font-semibold">启用授权门槛</h3></div>
            <ol className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
              <GateStep label="凭据" ready={credentialReady} detail={credentialReady ? "已满足" : "待配置"} />
              <GateStep label="套餐/许可证" ready={planReady} detail={planReady ? "边界可用" : "尚不可用"} />
              <GateStep label="Free-only" ready={freeOnlyReady} detail={freeOnly ? "保护开启" : "已明确关闭"} />
              <GateStep label="预算" ready={budgetReady} detail={budgetReady ? "已保存" : "待保存"} />
              <GateStep label="启用" ready={canEnable || configuration.enabled} detail={configuration.enabled ? "已启用" : "受门槛控制"} />
            </ol>
            {keyed && !trustedTransport && <p className="mt-3 rounded-lg border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-warning">传输方式尚未支持</p>}
          </section>

          {enterprise ? <section className="rounded-xl border border-border/60 bg-muted/10 p-4">
            <h3 className="text-sm font-semibold">企业许可边界</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">当前仅登记能力与许可证要求；未安装企业 SDK，也不尝试连接企业账户。</p>
            <div className="mt-3 flex flex-wrap gap-2"><button type="button" onClick={() => setLicenseNotice(true)} className="rounded-lg border border-primary/45 px-3 py-2 text-xs font-medium text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">配置许可证</button><button type="button" onClick={() => capabilityRef.current?.focus()} className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><Eye className="h-3.5 w-3.5" aria-hidden="true" />查看能力</button></div>
            {licenseNotice && <p className="mt-3 rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs text-warning">许可证配置尚未接入；当前仅展示本地能力边界。</p>}
          </section> : <>
            {keyed && <section className="rounded-xl border border-border/60 bg-muted/10 p-4">
              <div className="flex items-center gap-2"><KeyRound className="h-4 w-4 text-primary" aria-hidden="true" /><h3 className="text-sm font-semibold">本机安全凭据</h3></div><p className="mt-1 text-xs text-muted-foreground">当前状态：{statusLabels[configuration.credential.status] || "状态未知"}。输入值只发送一次，不会从 API 回填。</p>
              <label className="mt-3 block text-xs text-muted-foreground">Provider 凭据<input ref={secretInputRef} type="password" autoComplete="new-password" aria-label="Provider 凭据" value={secret} onChange={(event) => { setSecret(event.target.value); setFeedback(null); setError(null); }} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" /></label>
              <div className="mt-3 flex flex-wrap gap-2"><button type="button" onClick={saveCredential} disabled={!secret || credentialPending} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-45">{credentialPending && <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />}保存凭据</button>{configuration.credential.configured && <button type="button" onClick={deleteCredential} disabled={deletePending} className="inline-flex items-center gap-1 rounded-lg border border-destructive/45 px-3 py-2 text-xs font-medium text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive disabled:opacity-45"><Trash2 className="h-3.5 w-3.5" aria-hidden="true" />{confirmDelete ? "确认删除凭据" : "删除凭据"}</button>}</div>
            </section>}

            {paid && <CostBudgetPanel configuration={configuration} usage={usage} cost={cost} timezone={verifiedTimezone} onSave={saveBudget} onGateChange={setBudgetGate} />}

            <section className="rounded-xl border border-border/60 bg-muted/10 p-4">
              <h3 className="text-sm font-semibold">保护与操作</h3>
              <label className="mt-3 flex items-start gap-3 rounded-lg border border-border/55 bg-background/45 p-3 text-sm"><input type="checkbox" aria-label="Free-only 模式" checked={freeOnly} disabled={freeOnlyPending} onChange={(event) => updateFreeOnly(event.target.checked)} className="mt-0.5 h-4 w-4 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" /><span><strong className="font-medium">Free-only 模式</strong><span className="mt-1 block text-xs text-muted-foreground">默认开启；付费或企业请求在服务端继续被阻止。</span></span></label>
              {paid && <label className="mt-3 flex items-start gap-3 text-xs text-muted-foreground"><input type="checkbox" aria-label="确认可能产生费用" checked={confirmation} onChange={(event) => setConfirmation(event.target.checked)} className="mt-0.5 h-4 w-4 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" />我已确认当前配置可能产生费用；此确认不代表已购买套餐。</label>}
              <div className="mt-3 flex flex-wrap gap-2">{configuration.enabled ? <button type="button" onClick={() => runAction("disable")} disabled={actionPending} className="rounded-lg border border-border px-3 py-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-45">停用数据源</button> : <button type="button" onClick={() => runAction("enable")} disabled={!canEnable || actionPending} className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-45">启用数据源</button>}{keyed && <button type="button" onClick={() => runAction("validate")} disabled={!credentialReady || actionPending} className="rounded-lg border border-border px-3 py-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-45">验证配置</button>}</div>
            </section>
          </>}

          <section ref={capabilityRef} tabIndex={-1} aria-labelledby={`capability-title-${adapter.adapter_id}`} className="rounded-xl border border-border/60 bg-muted/10 p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
            <h3 id={`capability-title-${adapter.adapter_id}`} className="text-sm font-semibold">能力范围</h3><div className="mt-3 space-y-2">{adapter.capabilities.map((capability) => <div key={capability.capability_id} className="rounded-lg border border-border/50 bg-background/45 p-3 text-xs"><p className="font-medium text-foreground">{capability.capability_name}</p><p className="mt-1 text-muted-foreground">{capability.capability_id} · {capability.frequency_policy} · {capability.unit_policy}</p></div>)}</div>
          </section>
        </>}

        {feedback && <p role="status" className="inline-flex items-center gap-2 rounded-lg border border-success/30 bg-success/5 px-3 py-2 text-xs text-success"><Check className="h-3.5 w-3.5" aria-hidden="true" />{feedback}</p>}
        {error && <p role="alert" className="rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">{error}</p>}
      </div>
    </aside>
  </div>;
}
