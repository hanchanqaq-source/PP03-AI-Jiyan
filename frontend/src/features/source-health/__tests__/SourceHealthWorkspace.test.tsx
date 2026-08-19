import { StrictMode } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { SourceHealthSource, SourceHealthSummaryData } from "@/features/source-health/types";
import type { DataSourceCatalogResponse } from "@/features/source-catalog/types";
import { SourceHealthWorkspace } from "@/features/source-health/SourceHealthWorkspace";

const summary: SourceHealthSummaryData = {
  rating_confidence: "stable",
  last_run_at: "2026-08-18T06:30:00+00:00",
  fund: { healthy: 1, usable: 0, degraded: 0, failed: 1 },
  news: { healthy: 1, usable: 0, degraded: 1, failed: 1 },
  total_sources: 5,
  reclaimable_bytes: 0,
  group_status: { fund: { registered: 2, observed: 2, loaded: true }, news: { registered: 3, observed: 3, loaded: true } },
};

function source(overrides: Partial<SourceHealthSource>): SourceHealthSource {
  return {
    source_id: "source:default",
    source_name: "默认来源",
    group: "news",
    capability: "feed",
    started_at: "2026-08-18T06:29:59+00:00",
    finished_at: "2026-08-18T06:30:00+00:00",
    latency_ms: 200,
    probe_status: "success",
    error_type: "none",
    error_message_redacted: "",
    http_status: 200,
    returned_items: 2,
    data_as_of_date: "2026-08-18",
    freshness_seconds: 60,
    field_completeness_pct: 100,
    used_cache: false,
    cache_status: "not_used",
    fallback_available: true,
    redirected: false,
    final_reference: "https://example.test/public",
    rating_score: 95,
    rating: "healthy",
    rating_confidence: "stable",
    repair_value: "none",
    repair_reason: "无需处理",
    consecutive_failures: 0,
    last_success_at: "2026-08-18T06:30:00+00:00",
    ...overrides,
  };
}

const sources: SourceHealthSource[] = [
  source({ source_id: "fund:akshare-eastmoney:profile", source_name: "akshare-eastmoney", group: "fund", capability: "profile", latency_ms: 100 }),
  source({
    source_id: "fund:akshare-eastmoney:holdings", source_name: "akshare-eastmoney", group: "fund", capability: "holdings", latency_ms: 500,
    probe_status: "failure", error_type: "timeout", error_message_redacted: "公开请求超时，未包含凭证", http_status: null,
    returned_items: 0, data_as_of_date: null, freshness_seconds: null, field_completeness_pct: 0, fallback_available: false,
    final_reference: "https://fund.eastmoney.com/public", rating_score: 18, rating: "failed", repair_value: "worth_fixing",
    repair_reason: "保留并检查公开入口", consecutive_failures: 3, last_success_at: "2026-08-18T06:20:00+00:00",
  }),
  source({
    source_id: "news:finance", source_name: "财经资讯源", latency_ms: 10000, probe_status: "failure", error_type: "timeout",
    error_message_redacted: "公开请求超时，未包含凭证", http_status: null, returned_items: 0, data_as_of_date: null,
    freshness_seconds: null, field_completeness_pct: 0, used_cache: true, cache_status: "partial", fallback_available: false,
    final_reference: "https://news.example.test/public", rating_score: 10, rating: "failed", repair_value: "replace_candidate",
    repair_reason: "评估替换公开来源", consecutive_failures: 4, last_success_at: "2026-08-17T10:00:00+00:00",
  }),
  source({ source_id: "news:semiconductor", source_name: "半导体观察", latency_ms: 200 }),
  source({
    source_id: "news:policy", source_name: "政策公开源", latency_ms: 600, probe_status: "partial", freshness_seconds: 172800,
    rating: "degraded", rating_score: 45, repair_value: "observe", repair_reason: "继续观察",
  }),
];

describe("SourceHealthWorkspace", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "sourceHealthSummary").mockResolvedValue(summary);
    vi.spyOn(api, "sourceHealthSources").mockResolvedValue(sources);
    vi.spyOn(api, "sourceHealthStartFullRun").mockResolvedValue({ run_id: "run-1" });
    vi.spyOn(api, "sourceHealthRun").mockResolvedValue({
      run_id: "run-1", scope: "full", status: "completed", started_at: "2026-08-18T06:30:00Z",
      finished_at: "2026-08-18T06:31:00Z", total: 5, completed: 5, success: 3, partial: 1,
      failure: 1, current_source: "财经资讯源",
    });
    vi.spyOn(api, "dataSourceCatalog").mockRejectedValue(new Error("catalog unavailable"));
  });

  it("renders the real compact summary and complete source library directly on the page", async () => {
    render(<SourceHealthWorkspace />);
    const bar = await screen.findByRole("region", { name: "数据源健康状态栏" });
    expect(within(bar).getByText("1 健康 / 0 基本可用 / 0 降级 / 1 失败")).toBeInTheDocument();
    expect(within(bar).getByText("1 健康 / 0 基本可用 / 1 降级 / 1 失败")).toBeInTheDocument();
    expect(within(bar).getByText(/最后体检：.*2026/)).toBeInTheDocument();
    expect(within(bar).getByText("评级稳定")).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "运行全量体检" })).toBeEnabled();
    expect(await screen.findByText("AKShare / 东方财富基金档案")).toBeInTheDocument();
    expect(screen.getByText("财经资讯源")).toBeInTheDocument();
  });

  it("shows an honest unloaded snapshot instead of fabricated zero counts", async () => {
    vi.spyOn(api, "sourceHealthSummary").mockImplementation(() => new Promise(() => {}));
    render(<SourceHealthWorkspace />);
    const bar = screen.getByRole("region", { name: "数据源健康状态栏" });
    expect(within(bar).getByText("尚未载入健康快照")).toBeInTheDocument();
    expect(within(bar).queryByText(/0 健康 \/ 0 基本可用/)).not.toBeInTheDocument();
    expect(await screen.findByText("财经资讯源")).toBeInTheDocument();
  });

  it("does not turn a missing registered news group snapshot into zero counts", async () => {
    vi.spyOn(api, "sourceHealthSummary").mockResolvedValue({
      ...summary,
      news: { healthy: 0, usable: 0, degraded: 0, failed: 0 },
      total_sources: 2,
      group_status: { fund: { registered: 2, observed: 2, loaded: true }, news: { registered: 108, observed: 0, loaded: false } },
    });
    render(<SourceHealthWorkspace />);
    const bar = await screen.findByRole("region", { name: "数据源健康状态栏" });
    expect(within(bar).getByText("尚未载入资讯来源健康快照")).toBeInTheDocument();
    expect(within(bar).queryByText("0 健康 / 0 基本可用 / 0 降级 / 0 失败")).not.toBeInTheDocument();
  });

  it("distinguishes summary load failure from a snapshot that is still loading", async () => {
    vi.spyOn(api, "sourceHealthSummary").mockRejectedValue(new Error("summary unavailable"));
    render(<SourceHealthWorkspace />);
    const bar = screen.getByRole("region", { name: "数据源健康状态栏" });
    expect(await within(bar).findByText("健康快照读取失败")).toBeInTheDocument();
    expect(within(bar).queryByText("尚未载入健康快照")).not.toBeInTheDocument();
    expect(within(bar).queryByText(/0 健康 \/ 0 基本可用/)).not.toBeInTheDocument();
  });

  it("distinguishes a loaded snapshot with no completed audit", async () => {
    vi.spyOn(api, "sourceHealthSummary").mockResolvedValue({
      ...summary,
      last_run_at: null,
      total_sources: 0,
      fund: { healthy: 0, usable: 0, degraded: 0, failed: 0 },
      news: { healthy: 0, usable: 0, degraded: 0, failed: 0 },
    });
    render(<SourceHealthWorkspace />);
    const bar = screen.getByRole("region", { name: "数据源健康状态栏" });
    expect(await within(bar).findByText("尚未完成数据源体检")).toBeInTheDocument();
    expect(within(bar).queryByText("尚未载入健康快照")).not.toBeInTheDocument();
    expect(within(bar).queryByText(/0 健康 \/ 0 基本可用/)).not.toBeInTheDocument();
  });

  it("aggregates Provider capabilities by default and expands their independent results", async () => {
    const user = userEvent.setup();
    render(<SourceHealthWorkspace />);
    const provider = await screen.findByRole("article", { name: "Provider AKShare / 东方财富基金档案" });
    expect(within(provider).getByText("总体状态：失败")).toBeInTheDocument();
    expect(within(provider).getByText("健康能力：1")).toBeInTheDocument();
    expect(within(provider).getByText("失败能力：1")).toBeInTheDocument();
    expect(within(provider).getByText("平均响应：300 ms")).toBeInTheDocument();
    expect(within(provider).getByText("基金档案")).toBeInTheDocument();
    expect(within(provider).getByText("持仓披露")).toBeInTheDocument();
    expect(within(provider).queryByText("能力状态：健康")).not.toBeInTheDocument();
    await user.click(within(provider).getByRole("button", { name: "展开能力 AKShare / 东方财富基金档案" }));
    expect(within(provider).getByText("能力状态：健康")).toBeInTheDocument();
    expect(within(provider).getByText("能力状态：失败")).toBeInTheDocument();
    expect(within(provider).getAllByRole("button", { name: /查看能力详情/ })).toHaveLength(2);
  });

  it("filters and searches news rows and sorts response time without reordering hidden Providers", async () => {
    const user = userEvent.setup();
    render(<SourceHealthWorkspace />);
    await screen.findByText("财经资讯源");
    await user.click(screen.getByRole("button", { name: "资讯来源" }));
    expect(screen.queryByText("AKShare / 东方财富基金档案")).not.toBeInTheDocument();
    const search = screen.getByRole("searchbox", { name: "来源名称搜索" });
    await user.type(search, "半导体");
    expect(screen.getByText("半导体观察")).toBeInTheDocument();
    expect(screen.queryByText("财经资讯源")).not.toBeInTheDocument();
    await user.clear(search);
    await user.click(screen.getByRole("button", { name: "按状态筛选 失败" }));
    expect(screen.getByText("财经资讯源")).toBeInTheDocument();
    expect(screen.queryByText("半导体观察")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "按状态筛选 全部" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "来源排序" }), "latency");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("财经资讯源");
    expect(rows[1]).toHaveTextContent("政策公开源");
    expect(rows[2]).toHaveTextContent("半导体观察");
  });

  it("opens one selected source in a drawer without repeating the complete source list", async () => {
    const user = userEvent.setup();
    render(<SourceHealthWorkspace />);
    await user.click(await screen.findByRole("button", { name: "查看详情 财经资讯源" }));
    const drawer = screen.getByRole("dialog", { name: "单来源健康详情" });
    expect(within(drawer).getByRole("heading", { name: "财经资讯源" })).toBeInTheDocument();
    expect(drawer).toHaveTextContent("news:finance");
    expect(drawer).toHaveTextContent("https://news.example.test/public");
    expect(drawer).toHaveTextContent("连续失败：4");
    expect(drawer).toHaveTextContent("字段完整率：0%");
    expect(drawer).toHaveTextContent("缓存状态：部分缓存");
    expect(drawer).toHaveTextContent("公开请求超时，未包含凭证");
    expect(drawer).toHaveTextContent("最近健康历史（当前快照）");
    expect(within(drawer).queryByText("半导体观察")).not.toBeInTheDocument();
    expect(within(drawer).queryByRole("heading", { name: "基金与行情 Provider" })).not.toBeInTheDocument();
  });

  it("keeps single-source retry explicitly prototype-only", async () => {
    const user = userEvent.setup();
    render(<SourceHealthWorkspace />);
    await user.click(await screen.findByRole("button", { name: "单源重试 财经资讯源" }));
    expect(screen.getByRole("status")).toHaveTextContent("单源重试当前为前端原型，未发起网络请求");
  });

  it("opens the add-source prototype, shows the lifecycle, and keeps detected candidates out of the formal feed", async () => {
    const user = userEvent.setup();
    render(<SourceHealthWorkspace />);
    await user.click(await screen.findByRole("button", { name: "+ 添加数据源" }));
    const drawer = screen.getByRole("dialog", { name: "添加数据源原型" });
    for (const state of ["候选", "检测中", "待验证", "可启用", "已启用", "降级", "已停用"]) expect(within(drawer).getByText(state)).toBeInTheDocument();
    await user.type(within(drawer).getByLabelText("来源名称"), "北斗资讯候选");
    await user.type(within(drawer).getByLabelText("网址或 Feed 地址"), "https://candidate.example.test/feed");
    await user.selectOptions(within(drawer).getByLabelText("所属方向"), "semiconductor");
    await user.click(within(drawer).getByRole("button", { name: "检测来源" }));
    expect(await within(drawer).findByText("当前为前端原型，A1.1 后续阶段接入正式检测和保存。")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "关闭添加数据源" }));
    await user.click(screen.getByRole("button", { name: "候选来源" }));
    const candidate = screen.getByRole("article", { name: "候选来源 北斗资讯候选" });
    expect(candidate).toHaveTextContent("待验证");
    expect(candidate).toHaveTextContent("未进入正式资讯流");
    expect(candidate).not.toHaveTextContent("已启用");
  });

  it("keeps keyboard focus inside the add-source modal and restores its trigger", async () => {
    const user = userEvent.setup();
    render(<SourceHealthWorkspace />);
    const trigger = await screen.findByRole("button", { name: "+ 添加数据源" });
    await user.click(trigger);
    const drawer = screen.getByRole("dialog", { name: "添加数据源原型" });
    const close = within(drawer).getByRole("button", { name: "关闭添加数据源" });
    const detect = within(drawer).getByRole("button", { name: "检测来源" });
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(detect).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "添加数据源原型" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("keeps the official-evidence group empty when A1 supplies no verified identity field", async () => {
    const user = userEvent.setup();
    render(<SourceHealthWorkspace />);
    await screen.findByText("财经资讯源");
    await user.click(screen.getByRole("button", { name: "官方证据" }));
    expect(screen.getByText("A1 健康 API 未提供来源身份验证字段，当前不把普通来源标记为官方证据。")).toBeInTheDocument();
  });

  it("keeps a successful catalog visible without reloading it after its parent rerenders", async () => {
    const catalog: DataSourceCatalogResponse = {
      registration: { families: 1, adapters: 0, capabilities: 0, news_sources: 108, fingerprint: "catalog" },
      observed: { sources: 0, families: 0, adapters: 0, capabilities: 0 },
      portfolio_relation: { status: "unavailable_no_holdings" }, capabilities: [],
      families: [{ source_family_id: "news", source_family_name: "公开资讯家族", region: "CN", market: "news", source_roles: ["news_publisher"], independent_evidence_eligible: true, commercial_use_status: "publisher_terms_apply", catalog_status: "catalog_only", health_status: "unexamined", adapters: [] }],
    };
    const load = vi.spyOn(api, "dataSourceCatalog").mockResolvedValueOnce(catalog).mockRejectedValueOnce(new Error("later unrelated failure"));
    const legacy = vi.spyOn(api, "sourceHealthSources");
    const { rerender } = render(<SourceHealthWorkspace />);
    expect(await screen.findByText("公开资讯家族")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "数据源健康状态栏" })).toBeInTheDocument();
    expect(screen.getByText("108 个资讯来源")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(1);
    expect(legacy).not.toHaveBeenCalled();
    await act(async () => {
      rerender(<SourceHealthWorkspace />);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(load).toHaveBeenCalledTimes(1);
    expect(screen.getByText("公开资讯家族")).toBeInTheDocument();
    await waitFor(() => expect(legacy).not.toHaveBeenCalled());
  });

  it("shares one Catalog request across StrictMode mount replay without falling back after a second-request failure", async () => {
    const catalog: DataSourceCatalogResponse = {
      registration: { families: 1, adapters: 0, capabilities: 0, news_sources: 108, fingerprint: "catalog" },
      observed: { sources: 0, families: 0, adapters: 0, capabilities: 0 },
      portfolio_relation: { status: "unavailable_no_holdings" }, capabilities: [],
      families: [{ source_family_id: "news", source_family_name: "StrictMode 公开资讯家族", region: "CN", market: "news", source_roles: ["news_publisher"], independent_evidence_eligible: true, commercial_use_status: "publisher_terms_apply", catalog_status: "catalog_only", health_status: "unexamined", adapters: [] }],
    };
    const load = vi.spyOn(api, "dataSourceCatalog").mockResolvedValueOnce(catalog).mockRejectedValueOnce(new Error("second StrictMode request failed"));
    const legacy = vi.spyOn(api, "sourceHealthSources");
    render(<StrictMode><SourceHealthWorkspace /></StrictMode>);
    expect(await screen.findByText("StrictMode 公开资讯家族")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(1);
    expect(legacy).not.toHaveBeenCalled();
  });
});
