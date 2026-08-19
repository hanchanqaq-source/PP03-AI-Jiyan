import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SourceCatalogWorkspace } from "@/features/source-catalog/SourceCatalogWorkspace";
import type { DataSourceCatalogResponse } from "@/features/source-catalog/types";
import { api } from "@/lib/api";

const eastmoneyFamily = {
  source_family_id: "eastmoney", source_family_name: "东方财富数据家族", region: "CN", market: "CN",
  source_roles: ["primary_data"], independent_evidence_eligible: false,
  commercial_use_status: "public_upstream_terms_apply", catalog_status: "configured", health_status: "unexamined",
  adapters: [
    { adapter_id: "eastmoney-direct", adapter_name: "东方财富直连", source_family_id: "eastmoney", provider_type: "http_client", source_roles: ["primary_data"], capability_ids: ["profile"], billing_model: "free_no_key", auth_type: "none", credential_env_names: [], default_enabled: true, license_note: "公开入口", usage_note: "仅访问公开数据", data_delay: "以实际披露为准", quota_policy: "合理限速", cost_policy: "免费", configured_reference: "https://fund.eastmoney.com/", current_provider_priority: 20, catalog_status: "configured", enabled: true, health_status: "unexamined", capabilities: [{ capability_id: "profile", capability_name: "基金档案", data_category: "fund", freshness_max_age_seconds: null, probe_enabled: true, unit_policy: "none", frequency_policy: "event_driven", primary_families: ["eastmoney"], fallback_families: [], cross_check_families: [], probe_status: null, health_status: "unexamined", observed_final_reference: null, last_success_at: null, latency_ms: null, error_type: null, error_message_redacted: "" }] },
    { adapter_id: "akshare-eastmoney", adapter_name: "AKShare／东方财富", source_family_id: "eastmoney", provider_type: "akshare", source_roles: ["fallback_data"], capability_ids: ["profile"], billing_model: "free_no_key", auth_type: "none", credential_env_names: [], default_enabled: true, license_note: "公开入口", usage_note: "访问路径不增加独立性", data_delay: "以实际披露为准", quota_policy: "合理限速", cost_policy: "免费", configured_reference: "https://fund.eastmoney.com/", current_provider_priority: 40, catalog_status: "configured", enabled: true, health_status: "unexamined", capabilities: [{ capability_id: "profile", capability_name: "基金档案", data_category: "fund", freshness_max_age_seconds: null, probe_enabled: true, unit_policy: "none", frequency_policy: "event_driven", primary_families: ["eastmoney"], fallback_families: [], cross_check_families: [], probe_status: null, health_status: "unexamined", observed_final_reference: null, last_success_at: null, latency_ms: null, error_type: null, error_message_redacted: "" }] },
  ],
} satisfies DataSourceCatalogResponse["families"][number];

const catalog: DataSourceCatalogResponse = {
  registration: { families: 109, adapters: 114, capabilities: 8, news_sources: 108, fingerprint: "catalog-fingerprint" },
  observed: { sources: 0, families: 0, adapters: 0, capabilities: 0 },
  portfolio_relation: { status: "unavailable_no_holdings" },
  families: [eastmoneyFamily], capabilities: [],
};

describe("SourceCatalogWorkspace", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "dataSourceCatalog").mockResolvedValue(catalog);
  });

  it("shows the complete catalog with no holdings", async () => {
    render(<SourceCatalogWorkspace />);
    expect(await screen.findByText("东方财富数据家族")).toBeInTheDocument();
    expect(screen.getByText("108 个资讯来源")).toBeInTheDocument();
    expect(screen.getByText("暂无基金持仓，无法建立关联")).toBeInTheDocument();
  });

  it("expands eastmoney adapters without duplicating family cards", async () => {
    const user = userEvent.setup();
    render(<SourceCatalogWorkspace />);
    await screen.findByText("东方财富数据家族");
    expect(screen.getAllByLabelText("来源家族 东方财富数据家族")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "展开接入方式 东方财富数据家族" }));
    expect(screen.getByText("AKShare／东方财富")).toBeInTheDocument();
    expect(screen.getByText("东方财富直连")).toBeInTheDocument();
    expect(screen.getAllByText("体检：尚未体检").length).toBeGreaterThan(1);
  });

  it("filters by adapter, capability, billing, catalog, and health without hiding registration", async () => {
    const user = userEvent.setup();
    render(<SourceCatalogWorkspace />);
    await screen.findByText("东方财富数据家族");
    await user.type(screen.getByRole("searchbox", { name: "来源目录搜索" }), "AKShare");
    expect(screen.getByText("东方财富数据家族")).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "按计费模式过滤" }), "free_no_key");
    await user.selectOptions(screen.getByRole("combobox", { name: "按目录状态过滤" }), "configured");
    await user.selectOptions(screen.getByRole("combobox", { name: "按健康状态过滤" }), "unexamined");
    expect(screen.getByText("1 个来源家族")).toBeInTheDocument();
  });

  it("combines text and select filters instead of bypassing the text phrase", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "dataSourceCatalog").mockResolvedValue({ ...catalog, families: [eastmoneyFamily, { ...eastmoneyFamily, source_family_id: "tencent", source_family_name: "腾讯行情", adapters: [{ ...eastmoneyFamily.adapters[0], adapter_id: "tencent-quote", adapter_name: "腾讯行情" }] }] });
    render(<SourceCatalogWorkspace />);
    await screen.findByText("东方财富数据家族");
    await user.type(screen.getByRole("searchbox", { name: "来源目录搜索" }), "AKShare");
    await user.selectOptions(screen.getByRole("combobox", { name: "按计费模式过滤" }), "free_no_key");
    expect(screen.getByText("东方财富数据家族")).toBeInTheDocument();
    expect(screen.queryByText("腾讯行情")).not.toBeInTheDocument();
  });

  it("finds a family by its displayed aggregate health status", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "dataSourceCatalog").mockResolvedValue({ ...catalog, families: [{ ...eastmoneyFamily, health_status: "partial_degraded" }] });
    render(<SourceCatalogWorkspace />);
    await screen.findByText("东方财富数据家族");
    await user.selectOptions(screen.getByRole("combobox", { name: "按健康状态过滤" }), "partial_degraded");
    expect(screen.getByText("东方财富数据家族")).toBeInTheDocument();
  });
});
