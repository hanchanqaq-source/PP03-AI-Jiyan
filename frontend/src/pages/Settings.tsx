import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  CircleAlert,
  CircleCheck,
  KeyRound,
  Loader2,
  LogIn,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { GlassCard } from "@/components/ui/GlassCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { loadAccessKey, saveAccessKey } from "@/lib/api";
import { apiModels, aiModels, PROVIDER_BASE, type ProviderId } from "@/lib/ai-models";
import { clearLlm, loadLlm, saveLlm } from "@/lib/llm";
import {
  cancelCodexTest,
  loadSubscriptionProviders,
  startCodexLogin,
  testCodexConnection,
  type ConnectionTestStatus,
  type SubscriptionAuthStatus,
  type SubscriptionProviderStatus,
} from "@/lib/subscription-ai";

type Mode = "subscription" | "api";

const authLabels: Record<SubscriptionAuthStatus, string> = {
  not_installed: "不可登录",
  installed_not_logged_in: "未登录",
  logged_in_chatgpt: "已通过 ChatGPT 登录",
  logged_in_api_key: "API Key 登录",
  unsupported_version: "版本不支持",
  status_failed: "状态检测失败",
};

const testLabels: Record<ConnectionTestStatus, string> = {
  not_tested: "尚未测试",
  running: "测试中",
  success: "连接成功",
  not_installed: "未安装",
  not_logged_in: "未登录",
  wrong_auth_mode: "不是会员登录",
  quota_or_rate_limited: "额度或频率受限",
  timeout: "测试超时",
  cancelled: "测试已停止",
  process_failed: "Codex 运行失败",
  unexpected_output: "响应不符合预期",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

export function Settings() {
  const existing = loadLlm();
  const firstApi = apiModels[0];
  const existingApi = existing && !existing.provider.startsWith("cli-") ? existing : null;

  const [mode, setMode] = useState<Mode>("subscription");
  const [provider, setProvider] = useState<SubscriptionProviderStatus | null>(null);
  const [loadingProvider, setLoadingProvider] = useState(true);
  const [providerError, setProviderError] = useState("");
  const [action, setAction] = useState<"login" | "test" | "refresh" | null>(null);
  const [actionMessage, setActionMessage] = useState("");
  const [testStatusOverride, setTestStatusOverride] = useState<ConnectionTestStatus | null>(null);
  const [codexIsDefault, setCodexIsDefault] = useState(existing?.provider === "cli-codex");

  const [apiId, setApiId] = useState(existingApi?.model || firstApi.id);
  const [baseURL, setBaseURL] = useState(existingApi?.baseURL || PROVIDER_BASE[firstApi.provider] || "");
  const [modelName, setModelName] = useState(existingApi?.model || firstApi.id);
  const [apiKey, setApiKey] = useState(existingApi?.apiKey || "");
  const [accessKey, setAccessKey] = useState(loadAccessKey());

  const loginPollRef = useRef<number | null>(null);
  const loginPollAttemptsRef = useRef(0);
  const testAbortRef = useRef<AbortController | null>(null);

  const stopLoginPolling = useCallback(() => {
    if (loginPollRef.current !== null) {
      window.clearInterval(loginPollRef.current);
      loginPollRef.current = null;
    }
  }, []);

  const refreshProvider = useCallback(async (force = false, initial = false) => {
    if (initial) setLoadingProvider(true);
    setProviderError("");
    try {
      const result = await loadSubscriptionProviders(force);
      const codex = result.find((item) => item.provider_id === "codex") || null;
      setProvider(codex);
      if (codex?.auth_status === "logged_in_chatgpt") {
        stopLoginPolling();
      }
      return codex;
    } catch (error) {
      setProviderError(errorMessage(error));
      return null;
    } finally {
      if (initial) setLoadingProvider(false);
    }
  }, [stopLoginPolling]);

  useEffect(() => {
    void refreshProvider(false, true);
    return () => {
      stopLoginPolling();
      testAbortRef.current?.abort();
    };
  }, [refreshProvider, stopLoginPolling]);

  const handleRefresh = async () => {
    setAction("refresh");
    setActionMessage("");
    await refreshProvider(true);
    setAction(null);
  };

  const handleLogin = async () => {
    if (!provider || !provider.installed) return;
    if (provider.auth_status === "logged_in_chatgpt") return;
    let confirmSwitch = false;
    if (provider.auth_status === "logged_in_api_key") {
      confirmSwitch = window.confirm("当前 Codex 使用 API Key。继续官方 ChatGPT 登录可能替换当前认证方式，是否继续？");
      if (!confirmSwitch) return;
    }
    setAction("login");
    setActionMessage("");
    try {
      const result = await startCodexLogin(confirmSwitch);
      setProvider(result);
      const refreshed = await refreshProvider(true);
      if (refreshed?.auth_status !== "logged_in_chatgpt") {
        stopLoginPolling();
        loginPollAttemptsRef.current = 0;
        loginPollRef.current = window.setInterval(() => {
          loginPollAttemptsRef.current += 1;
          if (loginPollAttemptsRef.current >= 300) {
            stopLoginPolling();
            setActionMessage("登录状态等待已结束，请完成官方登录后点击“重新检测”。");
            return;
          }
          void refreshProvider(true);
        }, 2000);
      }
    } catch (error) {
      setActionMessage(errorMessage(error));
    } finally {
      setAction(null);
    }
  };

  const handleTest = async () => {
    if (!provider?.available) return;
    const controller = new AbortController();
    testAbortRef.current = controller;
    setAction("test");
    setActionMessage("");
    setTestStatusOverride("running");
    try {
      const result = await testCodexConnection(controller.signal);
      setProvider(result);
      setTestStatusOverride(null);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setTestStatusOverride("process_failed");
        setActionMessage(errorMessage(error));
      }
    } finally {
      if (testAbortRef.current === controller) testAbortRef.current = null;
      setAction(null);
    }
  };

  const handleStopTest = async () => {
    testAbortRef.current?.abort();
    try {
      const result = await cancelCodexTest();
      setProvider(result);
      setTestStatusOverride(null);
    } catch (error) {
      setTestStatusOverride("process_failed");
      setActionMessage(errorMessage(error));
    } finally {
      setAction(null);
      testAbortRef.current = null;
    }
  };

  const setCodexDefault = () => {
    if (provider?.auth_status !== "logged_in_chatgpt" || !provider.available || effectiveTestStatus !== "success") return;
    saveLlm({ provider: "cli-codex", baseURL: "", apiKey: "", model: "codex" });
    setCodexIsDefault(true);
    toast.success("已将 Codex（ChatGPT 登录）设为默认 AI");
  };

  const providerOf = (id: string): ProviderId => aiModels.find((item) => item.id === id)?.provider ?? "openai-compatible";

  const pickApiModel = (id: string) => {
    const model = apiModels.find((item) => item.id === id);
    if (!model) return;
    setApiId(id);
    setModelName(id);
    setBaseURL(PROVIDER_BASE[model.provider] || "");
  };

  const saveApi = () => {
    if (!baseURL.trim() || !apiKey.trim() || !modelName.trim()) {
      toast.error("请填完 Base URL、OpenAI API Key、Model");
      return;
    }
    saveLlm({ provider: providerOf(apiId), baseURL: baseURL.trim(), apiKey: apiKey.trim(), model: modelName.trim() });
    setCodexIsDefault(false);
    toast.success("API 高级接入已保存到本地浏览器");
  };

  const forget = () => {
    clearLlm();
    setApiKey("");
    setCodexIsDefault(false);
    toast.success("已清除本地 AI 配置");
  };

  const saveAccess = () => {
    const key = accessKey.trim();
    saveAccessKey(key);
    setAccessKey(key);
    toast.success(key ? "已保存后端访问密钥（仅存本地）" : "已清除后端访问密钥");
  };

  const effectiveTestStatus = testStatusOverride || provider?.test_status || "not_tested";
  const canTest = Boolean(provider?.available && action !== "test");
  const canSetDefault = Boolean(
    provider?.auth_status === "logged_in_chatgpt"
    && provider.available
    && effectiveTestStatus === "success"
  );

  return (
    <div>
      <PageHeader title="接入 AI" subtitle="优先使用已经购买的会员或本地 AI，不必另外填写 API Key" />

      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <button type="button" onClick={() => setMode("subscription")} className="text-left">
          <GlassCard glow={mode === "subscription"} className={mode === "subscription" ? "ring-1 ring-primary/40" : "opacity-75"}>
            <div className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-primary" />
              <h3 className="font-semibold">会员 / 免费额度接入</h3>
              {mode === "subscription" && <Check className="ml-auto h-4 w-4 text-primary" />}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">优先选择会员路径，不填写模型 API Key。</p>
          </GlassCard>
        </button>
        <button type="button" onClick={() => setMode("api")} className="text-left">
          <GlassCard glow={mode === "api"} className={mode === "api" ? "ring-1 ring-primary/40" : "opacity-75"}>
            <div className="flex items-center gap-2">
              <KeyRound className="h-5 w-5 text-primary" />
              <h3 className="font-semibold">API 高级接入</h3>
              {mode === "api" && <Check className="ml-auto h-4 w-4 text-primary" />}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">仅在你主动配置后使用，可能按量计费。</p>
          </GlassCard>
        </button>
      </div>

      {mode === "subscription" ? (
        <GlassCard>
          {loadingProvider ? (
            <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> 正在检查本机 Codex 状态…
            </div>
          ) : provider ? (
            <div className="space-y-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                  <div className="rounded-xl border border-primary/25 bg-primary/10 p-2.5">
                    <Terminal className="h-6 w-6 text-primary" />
                  </div>
                  <div>
                    <h2 className="font-semibold">Codex</h2>
                    <p className="mt-1 text-xs text-muted-foreground">使用你的 ChatGPT/Codex 套餐额度；额度和限制由 OpenAI 官方管理。</p>
                  </div>
                </div>
                <div className={`rounded-full px-2.5 py-1 text-xs ${codexIsDefault ? "bg-success/15 text-success" : "bg-muted/60 text-muted-foreground"}`}>
                  {codexIsDefault ? "当前默认" : "尚未设为默认"}
                </div>
              </div>

              <div className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
                <StatusCell label="安装状态" value={provider.installed ? "已安装" : "未安装"} ok={provider.installed} />
                <StatusCell label="版本" value={provider.version || "未检测到"} />
                <StatusCell label="登录方式" value={authLabels[provider.auth_status]} ok={provider.auth_status === "logged_in_chatgpt"} />
                <StatusCell label="连接测试" value={testLabels[effectiveTestStatus]} ok={effectiveTestStatus === "success"} />
              </div>

              <div className="rounded-lg border border-border/70 bg-black/10 p-3 text-xs text-muted-foreground">
                <p>{provider.message}</p>
                {provider.auth_status === "logged_in_api_key" && (
                  <p className="mt-2 font-medium text-warning">当前 Codex 使用 API Key，不属于会员额度接入。</p>
                )}
                {(provider.auth_status === "installed_not_logged_in" || provider.auth_status === "status_failed") && (
                  <div className="mt-3 rounded-md border border-border/70 bg-black/15 p-2 font-mono text-[11px] text-foreground">
                    <p>set CODEX_HOME=%VR_DATA_DIR%\codex-home</p>
                    <p>codex login</p>
                  </div>
                )}
                {actionMessage && <p className="mt-2">{actionMessage}</p>}
              </div>

              <div className="grid gap-2 rounded-lg border border-border/60 p-3 text-xs text-muted-foreground sm:grid-cols-2">
                <p><CircleCheck className="mr-1.5 inline h-3.5 w-3.5 text-success" />当前能力：分析页面已经提供的数据</p>
                <p><CircleAlert className="mr-1.5 inline h-3.5 w-3.5 text-muted-foreground" />后续能力：主动查询基金、股票、公告、财务和资讯工具</p>
                <p>可完成复盘、资讯摘要与个股页面上下文问答</p>
                <p>测试连接会消耗极少量 Codex 会员额度。</p>
              </div>

              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={handleRefresh} disabled={action !== null}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted/40 disabled:opacity-50">
                  <RefreshCw className={`h-4 w-4 ${action === "refresh" ? "animate-spin" : ""}`} /> 重新检测
                </button>
                <button type="button" onClick={handleLogin} disabled={!provider.installed || provider.auth_status === "logged_in_chatgpt" || action !== null}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted/40 disabled:opacity-50">
                  <LogIn className="h-4 w-4" /> {provider.auth_status === "logged_in_api_key" ? "切换为 ChatGPT 登录" : "打开 Codex 官方登录"}
                </button>
                {action === "test" ? (
                  <button type="button" onClick={handleStopTest}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning">
                    <Square className="h-3.5 w-3.5" /> 停止测试
                  </button>
                ) : (
                  <button type="button" onClick={handleTest} disabled={!canTest}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/25 disabled:opacity-50">
                    <Sparkles className="h-4 w-4" /> 测试连接
                  </button>
                )}
                <button type="button" onClick={setCodexDefault} disabled={!canSetDefault}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-success/15 px-3 py-2 text-sm font-medium text-success hover:bg-success/25 disabled:opacity-40">
                  <Check className="h-4 w-4" /> 设为默认 AI
                </button>
                {(codexIsDefault || existing) && (
                  <button type="button" onClick={forget} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-muted-foreground hover:text-destructive">
                    <Trash2 className="h-4 w-4" /> 清除
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="py-8 text-sm text-muted-foreground">未收到 Codex Provider 状态，请重新检测。</div>
          )}
          {providerError && <p className="mt-3 text-sm text-destructive">{providerError}</p>}
        </GlassCard>
      ) : (
        <GlassCard>
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-warning/35 bg-warning/10 p-3 text-sm text-warning">
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>API 可能按量计费；系统不会自动从会员接入切换到 API。</span>
          </div>
          <div className="space-y-4 text-sm">
            <div>
              <label htmlFor="api-model-preset" className="mb-1.5 block text-xs font-medium text-muted-foreground">选择模型</label>
              <select id="api-model-preset" value={apiId} onChange={(event) => pickApiModel(event.target.value)}
                className="w-full rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50">
                {apiModels.map((model) => <option key={model.id} value={model.id}>{model.name} —— {model.description}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="api-base-url" className="mb-1.5 block text-xs font-medium text-muted-foreground">Base URL</label>
              <input id="api-base-url" value={baseURL} onChange={(event) => setBaseURL(event.target.value)} placeholder="https://api.example.com/v1"
                className="w-full rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50" />
            </div>
            <div>
              <label htmlFor="api-model-name" className="mb-1.5 block text-xs font-medium text-muted-foreground">Model</label>
              <input id="api-model-name" value={modelName} onChange={(event) => setModelName(event.target.value)} placeholder="模型名称"
                className="w-full rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50" />
            </div>
            <div>
              <label htmlFor="model-api-key" className="mb-1.5 block text-xs font-medium text-muted-foreground">OpenAI API Key</label>
              <input id="model-api-key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="sk-…"
                className="w-full rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50" />
            </div>
            <div className="flex items-center gap-2">
              <button type="button" onClick={saveApi} className="rounded-lg bg-primary/15 px-4 py-2 font-medium text-primary hover:bg-primary/25">保存（存本地）</button>
              {existing && <button type="button" onClick={forget} className="inline-flex items-center gap-1.5 px-3 py-2 text-muted-foreground hover:text-destructive"><Trash2 className="h-4 w-4" />清除</button>}
            </div>
          </div>
        </GlassCard>
      )}

      <GlassCard className="mt-4">
        <h3 className="mb-1 flex items-center gap-1.5 text-sm font-semibold">
          <ShieldCheck className="h-4 w-4 text-primary" /> 后端访问密钥（独立鉴权）
        </h3>
        <p className="mb-3 text-xs text-muted-foreground">它只用于访问你自己的 PP03 后端，不是模型 API Key；本机未设置 VR_API_KEY 时留空。</p>
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <label htmlFor="backend-access-key" className="mb-1.5 block text-xs font-medium text-muted-foreground">后端访问密钥（可选）</label>
            <input id="backend-access-key" type="password" value={accessKey} onChange={(event) => setAccessKey(event.target.value)} placeholder="与后端 VR_API_KEY 保持一致"
              className="w-full rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50" />
          </div>
          <button type="button" onClick={saveAccess} className="rounded-lg bg-primary/15 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/25">保存</button>
        </div>
      </GlassCard>
    </div>
  );
}

function StatusCell({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="rounded-lg border border-border/60 bg-black/10 p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={`mt-1 break-words font-medium ${ok ? "text-success" : "text-foreground"}`}>{value}</div>
    </div>
  );
}
