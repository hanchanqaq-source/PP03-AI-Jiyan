import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { directEvent, marketNewsResponse } from "@/features/market-news/__tests__/fixtures";
import { api } from "@/lib/api";
import { MarketNews } from "@/pages/MarketNews";

const healthSummary = {
  rating_confidence: "initial", last_run_at: "2026-08-18T06:30:00+00:00",
  fund: { healthy: 1, usable: 0, degraded: 0, failed: 0 },
  news: { healthy: 0, usable: 0, degraded: 0, failed: 1 }, total_sources: 2, reclaimable_bytes: 0,
};

const sources = [
  {
    source_id: "fund:eastmoney:profile", source_name: "东方财富基金", group: "fund", capability: "基金资料",
    started_at: "2026-08-18T06:29:59+00:00", finished_at: "2026-08-18T06:30:00+00:00", latency_ms: 120,
    probe_status: "success", error_type: "none", error_message_redacted: "", http_status: 200, returned_items: 1,
    data_as_of_date: "2026-08-18", freshness_seconds: 0, field_completeness_pct: 100, used_cache: false,
    cache_status: "not_used", fallback_available: true, redirected: false, final_reference: "https://fund.example.test/public",
    rating_score: 96, rating: "healthy", rating_confidence: "initial", repair_value: "none", repair_reason: "无需处理", consecutive_failures: 0, last_success_at: "2026-08-18T06:30:00+00:00",
    source_family_id: "eastmoney", adapter_id: "eastmoney-direct", capability_id: "profile",
    configured_reference: "https://fund.example.test/configured", observed_final_reference: "https://fund.example.test/public",
  },
  {
    source_id: "news:finance", source_name: "财经资讯源", group: "news", capability: "公开资讯",
    started_at: "2026-08-18T06:29:50+00:00", finished_at: "2026-08-18T06:30:00+00:00", latency_ms: 10000,
    probe_status: "failure", error_type: "timeout", error_message_redacted: "公开请求超时，未包含凭证", http_status: null, returned_items: 0,
    data_as_of_date: null, freshness_seconds: null, field_completeness_pct: 0, used_cache: false,
    cache_status: "not_used", fallback_available: false, redirected: true, final_reference: "https://news.example.test/public",
    rating_score: 10, rating: "failed", rating_confidence: "initial", repair_value: "replace_candidate", repair_reason: "评估替换公开来源", consecutive_failures: 3,
    last_success_at: "2026-08-17T10:00:00+00:00",
    source_family_id: "news-publisher:finance", adapter_id: "news-feed:finance", capability_id: "feed",
    configured_reference: "https://news.example.test/configured", observed_final_reference: "https://news.example.test/public",
    headers: { Authorization: "Bearer secret-token" }, Cookie: "private-cookie", Token: "secret-token",
    stack: "C:\\Users\\private\\project\\backend.py line 9", local_path: "D:\\private\\debug.txt",
  },
];

const cacheStatus = { total_bytes: 0, file_count: 0, expired_count: 0, reclaimable_bytes: 0, categories: {}, last_auto_cleanup_at: null, limit_bytes: 0, over_limit_bytes: 0 };

describe("source health drawer through MarketNews", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [directEvent] });
    vi.spyOn(api, "cacheStatus").mockResolvedValue(cacheStatus);
    (api as any).sourceHealthSummary = vi.fn().mockResolvedValue(healthSummary);
    (api as any).sourceHealthSources = vi.fn().mockResolvedValue(sources);
    (api as any).sourceHealthStartFullRun = vi.fn();
    (api as any).sourceHealthRun = vi.fn();
  });

  async function openDrawer() {
    const user = userEvent.setup();
    render(<MarketNews />);
    await screen.findByRole("heading", { name: directEvent.title });
    await user.click(screen.getByRole("button", { name: "数据说明" }));
    await user.click(await screen.findByRole("button", { name: "查看详情" }));
    return user;
  }

  it("groups sources, filters by status, and expands only redacted failure details", async () => {
    const user = await openDrawer();
    expect(screen.getByRole("dialog", { name: "数据源健康详情" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "基金与行情 Provider" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "资讯来源" })).toBeInTheDocument();
    expect(screen.getByText("东方财富基金")).toBeInTheDocument();
    expect(screen.getByText("财经资讯源")).toBeInTheDocument();
    expect(screen.getByText("响应时间：10000 ms")).toBeInTheDocument();
    expect(screen.getByText("返回条数：0")).toBeInTheDocument();
    expect(screen.getByText("字段完整率：0%")).toBeInTheDocument();
    expect(screen.getByText("连续失败：3")).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "按状态过滤" }), "failed");
    expect(screen.queryByText("东方财富基金")).not.toBeInTheDocument();
    expect(screen.getByText("财经资讯源")).toBeInTheDocument();
    expect(screen.getByText(/最近成功：/)).not.toHaveTextContent("暂无记录");
    await user.click(screen.getByRole("button", { name: "查看失败详情 财经资讯源" }));
    expect(screen.getByText("错误类型：超时")).toBeInTheDocument();
    expect(screen.getByText("脱敏原因：公开请求超时，未包含凭证")).toBeInTheDocument();
    expect(screen.getByText("稳定身份：news-publisher:finance / news-feed:finance / feed")).toBeInTheDocument();
    expect(screen.getByText("配置公开地址：https://news.example.test/configured")).toBeInTheDocument();
    expect(screen.getByText("观测公开地址：https://news.example.test/public")).toBeInTheDocument();
    expect(screen.getByText("是否重定向：是")).toBeInTheDocument();
    expect(screen.getByText("是否有备用来源：否")).toBeInTheDocument();
    expect(screen.getByText("建议处理：评估替换公开来源")).toBeInTheDocument();
    expect(screen.queryByText(/Bearer secret-token|private-cookie|C:\\Users\\private|D:\\private/)).not.toBeInTheDocument();
  });

  it("supports group-only views without treating color as the status label", async () => {
    const user = await openDrawer();
    await user.click(screen.getByRole("button", { name: "只看基金与行情 Provider" }));
    expect(screen.getByText("东方财富基金")).toBeInTheDocument();
    expect(screen.getByText("状态：健康")).toBeInTheDocument();
    expect(screen.queryByText("财经资讯源")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只看资讯来源" }));
    expect(screen.queryByText("东方财富基金")).not.toBeInTheDocument();
    expect(screen.getByText("财经资讯源")).toBeInTheDocument();
    expect(screen.getByText("状态：失败")).toBeInTheDocument();
  });

  it("shows configured and observed public references for a healthy source without failure expansion", async () => {
    const user = await openDrawer();
    await user.click(screen.getByRole("button", { name: "只看基金与行情 Provider" }));
    expect(screen.getByText("稳定身份：eastmoney / eastmoney-direct / profile")).toBeInTheDocument();
    expect(screen.getByText("配置公开地址：https://fund.example.test/configured")).toBeInTheDocument();
    expect(screen.getByText("观测公开地址：https://fund.example.test/public")).toBeInTheDocument();
  });

  it("contains Tab navigation and keeps Escape closing the drawer", async () => {
    const user = await openDrawer();
    const drawer = screen.getByRole("dialog", { name: "数据源健康详情" });
    const close = within(drawer).getByRole("button", { name: "关闭数据源健康详情" });
    const last = within(drawer).getByRole("button", { name: "查看失败详情 财经资讯源" });
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(last).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "数据源健康详情" })).not.toBeInTheDocument();
  });
});
