import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api, type RadarData } from "@/lib/api";
import { directEvent, marketNewsResponse } from "@/features/market-news/__tests__/fixtures";
import { MarketNews } from "@/pages/MarketNews";
import { IndustryResearch } from "@/pages/IndustryResearch";

const now = Math.floor(Date.now() / 1000);
const radar: RadarData = {
  generated_at: "2026-08-16 12:00", recent_days: 7,
  stats: { industries: 2, total_sources: 2 },
  industries: [
    { key: "semi", name: "半导体", accent: "#22d3ee", total: 1, items: [
      { title: "HBM存储需求跟踪", url: "https://example.com/hbm", time: "08-16 10:00", ts: now, source: "半导体资讯", summary: "HBM需求" },
    ] },
    { key: "robot", name: "机器人", accent: "#14b8a6", total: 1, items: [
      { title: "机器人量产进度更新", url: "https://example.com/robot", time: "08-16 09:00", ts: now, source: "机器人资讯", summary: "机器人量产" },
    ] },
  ],
};

describe("PP03 core pages", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(api, "radar").mockResolvedValue(radar);
    vi.spyOn(api, "fundPortfolio").mockResolvedValue({ schema_version: 2, holdings: [], total_cost: 0, updated: null, migration: null, data_status: "ok" });
  });

  it("keeps active filters while switching four market-news modes and tags", async () => {
    const user = userEvent.setup();
    const load = vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [directEvent] });
    render(<MarketNews />);

    expect(await screen.findByRole("heading", { name: directEvent.title })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "我的关注" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "全部" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "7天" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重要度" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "政策" }));
    await user.click(screen.getByRole("button", { name: "30天" }));
    await user.click(screen.getByRole("button", { name: "最新时间" }));
    await user.click(screen.getByRole("button", { name: "我的持仓" }));
    await user.click(screen.getByRole("button", { name: "全球科技" }));
    await user.click(screen.getByRole("button", { name: "国内政策" }));
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));

    expect(load).toHaveBeenLastCalledWith({ mode: "domestic_policy", tag_ids: ["robotics"], category: "policy", days: 30, sort: "latest" });
  });

  it("disables duplicate refresh and preserves the last successful events on failure", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [directEvent] });
    let rejectRefresh!: (reason: Error) => void;
    vi.spyOn(api, "marketNewsRefresh").mockImplementation(() => new Promise((_, reject) => { rejectRefresh = reject; }));
    render(<MarketNews />);
    expect(await screen.findByRole("heading", { name: directEvent.title })).toBeInTheDocument();

    const refresh = screen.getByRole("button", { name: "刷新资讯" });
    await user.click(refresh);
    expect(refresh).toBeDisabled();
    expect(screen.getByText("刷新中")).toBeInTheDocument();
    rejectRefresh(new Error("offline"));

    expect(await screen.findByText("资讯刷新失败；继续显示上次成功结果。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: directEvent.title })).toBeInTheDocument();
  });

  it("shows data explanation and all explicit degraded states", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({
      ...marketNewsResponse,
      events: [{ ...directEvent, summary: "AI摘要暂不可用", summary_status: "ai_unavailable", data_status: "stale" }],
      data_status: "stale", ai_status: "unavailable",
      source_summary: { ...marketNewsResponse.source_summary, failed_sources: 2, cache_status: "stale" },
    });
    render(<MarketNews />);
    expect(await screen.findByText("过期缓存")).toBeInTheDocument();
    expect(screen.getByText("2 个来源失败")).toBeInTheDocument();
    expect(screen.getByText("AI摘要暂不可用")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "数据说明" }));
    expect(screen.getByRole("dialog", { name: "市场资讯数据说明" })).toBeInTheDocument();
    expect(screen.getByText(/公开 RSS/)).toBeInTheDocument();
    expect(screen.getByText(/不会输出买入、卖出/)).toBeInTheDocument();
  });

  it("persists a genuinely empty market-news tag selection and shows guidance", async () => {
    localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: [], activeId: "" }));
    const load = vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [], empty_reason: "no_tags" });
    render(<MarketNews />);

    expect(await screen.findByText("还没有选择关注行业")).toBeInTheDocument();
    expect(screen.getByText("添加半导体、存储、机器人、医疗等标签后开始跟踪资讯")).toBeInTheDocument();
    expect(load).not.toHaveBeenCalled();
  });

  it("shows no-holdings and no-events responses without fake associations", async () => {
    const user = userEvent.setup();
    const load = vi.spyOn(api, "marketNewsEvents")
      .mockResolvedValueOnce({ ...marketNewsResponse, events: [directEvent] })
      .mockResolvedValueOnce({ ...marketNewsResponse, events: [], empty_reason: "no_holdings", portfolio_status: "empty" })
      .mockResolvedValueOnce({ ...marketNewsResponse, events: [], empty_reason: "no_events" });
    render(<MarketNews />);
    await screen.findByRole("heading", { name: directEvent.title });
    await user.click(screen.getByRole("button", { name: "我的持仓" }));
    expect(await screen.findByText("还没有基金持仓")).toBeInTheDocument();
    expect(screen.getByText("添加持仓后，系统会把基金公开重仓股与资讯关联")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "全球科技" }));
    expect(await screen.findByText("当前筛选暂无可靠资讯")).toBeInTheDocument();
    expect(screen.getByText("可以扩大时间范围、切换标签或刷新公开来源")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("switches the entire continuous industry report with the active top tag", async () => {
    const user = userEvent.setup();
    render(<IndustryResearch />);

    expect(screen.getByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    expect(screen.getByRole("article", { name: "机器人行业研究报告" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "存储行业研究报告" })).not.toBeInTheDocument();
  });
});
