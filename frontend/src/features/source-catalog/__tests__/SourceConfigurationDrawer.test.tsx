import { StrictMode, useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SourceCatalogWorkspace } from "@/features/source-catalog/SourceCatalogWorkspace";
import { SourceConfigurationDrawer } from "@/features/source-catalog/SourceConfigurationDrawer";
import { SourceFamilyCard } from "@/features/source-catalog/SourceFamilyCard";
import type {
  AdapterView,
  CapabilityView,
  DataSourceCatalogResponse,
  DataSourceConfigurationResponse,
  DataSourceCostResponse,
  DataSourceUsageResponse,
  SourceFamilyView,
} from "@/features/source-catalog/types";
import { ApiError, api } from "@/lib/api";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}

const capability: CapabilityView = {
  capability_id: "daily_prices", capability_name: "日线行情", data_category: "market",
  freshness_max_age_seconds: 86400, probe_enabled: false, unit_policy: "source", frequency_policy: "daily",
  primary_families: ["fmp"], fallback_families: [], cross_check_families: [],
  probe_status: null, health_status: "unexamined", observed_final_reference: null,
  last_success_at: null, latency_ms: null, error_type: null, error_message_redacted: "",
};

const paidAdapter: AdapterView = {
  adapter_id: "fmp", adapter_name: "Financial Modeling Prep", source_family_id: "fmp",
  provider_type: "http_client", source_roles: ["fallback_data"], capability_ids: ["daily_prices"],
  billing_model: "paid_api", auth_type: "api_key", credential_env_names: ["FMP_API_KEY"],
  default_enabled: false, license_note: "按账户套餐", usage_note: "用量以账户为准", data_delay: "以套餐为准",
  quota_policy: "以账户为准", cost_policy: "付费", configured_reference: "https://site.financialmodelingprep.com/",
  current_provider_priority: 80, catalog_status: "unconfigured", enabled: false, health_status: "unexamined",
  capabilities: [{ ...capability }],
};

const enterpriseAdapter: AdapterView = {
  ...paidAdapter,
  adapter_id: "bloomberg-data-license",
  adapter_name: "Bloomberg Data License",
  source_family_id: "bloomberg",
  billing_model: "enterprise_license",
  auth_type: "enterprise_license",
  credential_env_names: [],
  catalog_status: "license_required",
  license_note: "需要企业许可证",
};

const freeAdapter: AdapterView = {
  ...paidAdapter,
  adapter_id: "public-feed",
  adapter_name: "公开行情",
  source_family_id: "public-feed",
  billing_model: "free_no_key",
  auth_type: "none",
  credential_env_names: [],
  catalog_status: "configured",
};

const paidFamily: SourceFamilyView = {
  source_family_id: "fmp",
  source_family_name: "Financial Modeling Prep",
  region: "US",
  market: "global",
  source_roles: ["fallback_data"],
  independent_evidence_eligible: false,
  commercial_use_status: "account_terms_apply",
  catalog_status: "unconfigured",
  health_status: "unexamined",
  adapters: [paidAdapter],
};

function configFor(adapter: AdapterView, overrides: Partial<DataSourceConfigurationResponse["adapters"][number]> = {}): DataSourceConfigurationResponse {
  return {
    free_only: true,
    adapters: [{
      adapter_id: adapter.adapter_id,
      billing_model: adapter.billing_model,
      catalog_status: adapter.catalog_status,
      enabled: adapter.enabled,
      usage_mode: null,
      daily_budget: null,
      monthly_budget: null,
      per_request_budget: null,
      daily_request_limit: null,
      monthly_request_limit: null,
      credential: {
        configured: adapter.credential_env_names.length === 0,
        status: adapter.catalog_status === "license_required" ? "license_required" : adapter.credential_env_names.length ? "unconfigured" : "not_required",
        last_validated_at: null,
        credential_source: "none",
      },
      ...overrides,
    }],
  };
}

function usageFor(adapter: AdapterView): DataSourceUsageResponse {
  return {
    as_of: "2026-08-20T00:00:00Z", timezone: "UTC", usage_status: "unobserved",
    adapters: [{ adapter_id: adapter.adapter_id, day: "2026-08-20", month: "2026-08", usage_status: "unobserved", daily_cost: null, monthly_cost: null, daily_request_count: null, monthly_request_count: null, daily_units: null, monthly_units: null, status_counts: {}, open_reservations: null }],
  };
}

function costFor(adapter: AdapterView): DataSourceCostResponse {
  return {
    as_of: "2026-08-20T00:00:00Z", timezone: "UTC", free_only: true, usage_status: "unobserved",
    adapters: [{ adapter_id: adapter.adapter_id, billing_model: adapter.billing_model, enabled: adapter.enabled, credential_configured: adapter.credential_env_names.length === 0, status: adapter.catalog_status === "license_required" ? "license_required" : "unconfigured", usage_status: "unobserved", day: "2026-08-20", month: "2026-08", daily_budget: null, monthly_budget: null, per_request_budget: null, daily_cost: null, monthly_cost: null, daily_remaining: null, monthly_remaining: null, open_reservations: null }],
  };
}

function mockReads(adapter: AdapterView, config = configFor(adapter)) {
  vi.spyOn(api, "dataSourceConfig").mockResolvedValue(config);
  vi.spyOn(api, "dataSourceUsage").mockResolvedValue(usageFor(adapter));
  vi.spyOn(api, "dataSourceCost").mockResolvedValue(costFor(adapter));
}

function ControlledDrawer({ adapter = paidAdapter }: { adapter?: AdapterView }) {
  const [open, setOpen] = useState(false);
  return <>
    <button onClick={() => setOpen(true)}>打开配置</button>
    <SourceConfigurationDrawer open={open} adapter={adapter} onClose={() => setOpen(false)} />
  </>;
}

describe("SourceConfigurationDrawer", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("shows honest states, Free-only on by default, and the authorization ladder", async () => {
    mockReads(paidAdapter);
    render(<StrictMode><SourceConfigurationDrawer open adapter={paidAdapter} onClose={vi.fn()} /></StrictMode>);

    expect(await screen.findByText("未配置")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Free-only 模式" })).toBeChecked();
    for (const label of ["凭据", "套餐/许可证", "Free-only", "预算", "启用"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("传输方式尚未支持")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "启用数据源" })).toBeDisabled();
  });

  it("renders plan unavailable without claiming a source failure", async () => {
    mockReads(paidAdapter, configFor(paidAdapter, { credential: { configured: true, status: "plan_unavailable", last_validated_at: null, credential_source: "keyring" } }));
    render(<SourceConfigurationDrawer open adapter={paidAdapter} onClose={vi.fn()} />);
    expect(await screen.findByText("当前套餐不可用")).toBeInTheDocument();
    expect(screen.queryByText("来源失败")).not.toBeInTheDocument();
  });

  it("limits enterprise controls to license configuration and capability viewing", async () => {
    mockReads(enterpriseAdapter);
    const user = userEvent.setup();
    render(<SourceConfigurationDrawer open adapter={enterpriseAdapter} onClose={vi.fn()} />);
    expect(await screen.findByText("需要许可证")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "配置许可证" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看能力" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Provider 凭据")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "启用数据源" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "验证配置" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "配置许可证" }));
    expect(screen.getByText("许可证配置尚未接入；当前仅展示本地能力边界。")).toBeInTheDocument();
  });

  it.each([
    ["catalog_only", "仅目录"],
    ["disabled", "已停用"],
  ] as const)("keeps the %s catalog barrier visible", async (catalogStatus, label) => {
    const blocked = { ...freeAdapter, adapter_id: `blocked-${catalogStatus}`, catalog_status: catalogStatus };
    mockReads(blocked);
    render(<SourceConfigurationDrawer open adapter={blocked} onClose={vi.fn()} />);
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "启用数据源" })).toBeDisabled();
  });

  it("clears a one-time secret after success and never persists or renders it", async () => {
    mockReads(paidAdapter);
    vi.spyOn(api, "dataSourcePutCredential").mockResolvedValue({ configured: true, status: "stored", last_validated_at: null, credential_source: "keyring" });
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();
    render(<SourceConfigurationDrawer open adapter={paidAdapter} onClose={vi.fn()} />);
    const input = await screen.findByLabelText("Provider 凭据") as HTMLInputElement;
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "new-password");
    await user.type(input, "task7-secret-success");
    await user.click(screen.getByRole("button", { name: "保存凭据" }));
    await waitFor(() => expect(input).toHaveValue(""));
    expect(api.dataSourcePutCredential).toHaveBeenCalledTimes(1);
    expect(api.dataSourcePutCredential).toHaveBeenCalledWith("fmp", "task7-secret-success");
    expect(storageWrite).not.toHaveBeenCalled();
    expect(document.body).not.toHaveTextContent("task7-secret-success");
  });

  it("sends a credential only in the one-time JSON body and strips unexpected response fields", async () => {
    const transport = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      configured: true,
      status: "stored",
      last_validated_at: null,
      credential_source: "keyring",
      credential: "must-not-survive-response-shaping",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    const state = await api.dataSourcePutCredential("fmp", "body-only-secret");

    expect(state).toEqual({ configured: true, status: "stored", last_validated_at: null, credential_source: "keyring" });
    expect(Object.keys(state)).toEqual(["configured", "status", "last_validated_at", "credential_source"]);
    const [path, init] = transport.mock.calls[0];
    expect(path).toBe("/api/data-sources/fmp/credentials");
    expect(String(path)).not.toContain("body-only-secret");
    expect(JSON.stringify(init?.headers || {})).not.toContain("body-only-secret");
    expect(JSON.parse(String(init?.body))).toEqual({ credential: "body-only-secret" });
  });

  it("clears a secret after failure and shows only a bounded public error", async () => {
    mockReads(paidAdapter);
    vi.spyOn(api, "dataSourcePutCredential").mockRejectedValue(new Error("task7-secret-failure backend trace"));
    const user = userEvent.setup();
    render(<SourceConfigurationDrawer open adapter={paidAdapter} onClose={vi.fn()} />);
    const input = await screen.findByLabelText("Provider 凭据") as HTMLInputElement;
    await user.type(input, "task7-secret-failure");
    await user.click(screen.getByRole("button", { name: "保存凭据" }));
    await waitFor(() => expect(input).toHaveValue(""));
    expect(screen.getByRole("alert")).toHaveTextContent("凭据保存失败，请检查本机安全存储状态后重试。");
    expect(document.body).not.toHaveTextContent("backend trace");
    expect(document.body).not.toHaveTextContent("task7-secret-failure");
  });

  it("clears the DOM value on unmount", async () => {
    mockReads(paidAdapter);
    const user = userEvent.setup();
    const view = render(<SourceConfigurationDrawer open adapter={paidAdapter} onClose={vi.fn()} />);
    const input = await screen.findByLabelText("Provider 凭据") as HTMLInputElement;
    await user.type(input, "task7-secret-unmount");
    view.unmount();
    expect(input.value).toBe("");
  });

  it("clears immediately on close and ignores a stale pending credential result", async () => {
    const pending = deferred<{ configured: boolean; status: string; last_validated_at: null; credential_source: string }>();
    mockReads(paidAdapter);
    vi.spyOn(api, "dataSourcePutCredential").mockReturnValue(pending.promise);
    const user = userEvent.setup();
    render(<ControlledDrawer />);
    const opener = screen.getByRole("button", { name: "打开配置" });
    await user.click(opener);
    const input = await screen.findByLabelText("Provider 凭据") as HTMLInputElement;
    await user.type(input, "task7-secret-pending");
    await user.click(screen.getByRole("button", { name: "保存凭据" }));
    await user.keyboard("{Escape}");
    expect(input.value).toBe("");
    expect(opener).toHaveFocus();

    pending.resolve({ configured: true, status: "stored", last_validated_at: null, credential_source: "keyring" });
    await user.click(opener);
    const reopened = await screen.findByLabelText("Provider 凭据") as HTMLInputElement;
    expect(reopened).toHaveValue("");
    expect(screen.queryByText("凭据已保存")).not.toBeInTheDocument();
    await user.type(reopened, "new-secret-after-stale-result");
    expect(screen.getByRole("button", { name: "保存凭据" })).toBeEnabled();
  });

  it("traps focus, closes with Escape, and restores the opener", async () => {
    mockReads(paidAdapter);
    const user = userEvent.setup();
    render(<ControlledDrawer />);
    const opener = screen.getByRole("button", { name: "打开配置" });
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "配置 Financial Modeling Prep" });
    const close = screen.getByRole("button", { name: "关闭数据源配置" });
    expect(close).toHaveFocus();
    const controls = Array.from(dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'));
    controls[controls.length - 1].focus();
    await user.keyboard("{Tab}");
    expect(controls[0]).toHaveFocus();
    controls[0].focus();
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(controls[controls.length - 1]).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("disables duplicate credential submits while a mutation is pending", async () => {
    const pending = deferred<{ configured: boolean; status: string; last_validated_at: null; credential_source: string }>();
    mockReads(paidAdapter);
    vi.spyOn(api, "dataSourcePutCredential").mockReturnValue(pending.promise);
    const user = userEvent.setup();
    render(<SourceConfigurationDrawer open adapter={paidAdapter} onClose={vi.fn()} />);
    await user.type(await screen.findByLabelText("Provider 凭据"), "single-dispatch-secret");
    const submit = screen.getByRole("button", { name: "保存凭据" });
    await user.click(submit);
    await user.click(submit);
    expect(api.dataSourcePutCredential).toHaveBeenCalledTimes(1);
    expect(submit).toBeDisabled();
    pending.resolve({ configured: true, status: "stored", last_validated_at: null, credential_source: "keyring" });
  });

  it("shows validation 409 as a configuration barrier rather than source failure", async () => {
    mockReads(paidAdapter, configFor(paidAdapter, { credential: { configured: true, status: "stored", last_validated_at: null, credential_source: "keyring" } }));
    vi.spyOn(api, "dataSourceValidate").mockRejectedValue(new ApiError("bounded", 409));
    const user = userEvent.setup();
    render(<SourceConfigurationDrawer open adapter={paidAdapter} onClose={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "验证配置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("传输方式尚未支持");
    expect(screen.queryByText("来源失败")).not.toBeInTheDocument();
  });

  it("allows a free no-key adapter action but keeps paid enablement gated", async () => {
    mockReads(freeAdapter);
    vi.spyOn(api, "dataSourceEnable").mockResolvedValue({ adapter_id: freeAdapter.adapter_id, action: "enable", status: "enabled", enabled: true, connected: false });
    const user = userEvent.setup();
    render(<SourceConfigurationDrawer open adapter={freeAdapter} onClose={vi.fn()} />);
    const enable = await screen.findByRole("button", { name: "启用数据源" });
    expect(enable).toBeEnabled();
    await user.click(enable);
    expect(api.dataSourceEnable).toHaveBeenCalledWith(freeAdapter.adapter_id, false);
  });

  it("opens one adapter configuration drawer from the existing family-card flow", async () => {
    mockReads(paidAdapter);
    const user = userEvent.setup();
    render(<SourceFamilyCard family={paidFamily} />);
    await user.click(screen.getByRole("button", { name: "展开接入方式 Financial Modeling Prep" }));
    await user.click(screen.getByRole("button", { name: "配置数据源 Financial Modeling Prep" }));
    expect(await screen.findByRole("dialog", { name: "配置 Financial Modeling Prep" })).toBeInTheDocument();
    expect(api.dataSourceConfig).toHaveBeenCalledTimes(1);
  });

  it("deduplicates Catalog and configuration reads under StrictMode", async () => {
    const catalogPending = deferred<DataSourceCatalogResponse>();
    const configPending = deferred<DataSourceConfigurationResponse>();
    vi.spyOn(api, "dataSourceCatalog").mockReturnValue(catalogPending.promise);
    vi.spyOn(api, "dataSourceConfig").mockReturnValue(configPending.promise);
    vi.spyOn(api, "dataSourceUsage").mockResolvedValue(usageFor(paidAdapter));
    vi.spyOn(api, "dataSourceCost").mockResolvedValue(costFor(paidAdapter));

    render(<StrictMode><SourceCatalogWorkspace /></StrictMode>);
    expect(api.dataSourceCatalog).toHaveBeenCalledTimes(1);
    catalogPending.resolve({ registration: { families: 0, adapters: 0, capabilities: 0, news_sources: 0, fingerprint: "empty" }, observed: { sources: 0, families: 0, adapters: 0, capabilities: 0 }, portfolio_relation: { status: "unavailable_no_holdings" }, families: [], capabilities: [] });

    render(<StrictMode><SourceConfigurationDrawer open adapter={paidAdapter} onClose={vi.fn()} /></StrictMode>);
    expect(api.dataSourceConfig).toHaveBeenCalledTimes(1);
    expect(api.dataSourceUsage).toHaveBeenCalledTimes(1);
    expect(api.dataSourceCost).toHaveBeenCalledTimes(1);
    configPending.resolve(configFor(paidAdapter));
  });
});
