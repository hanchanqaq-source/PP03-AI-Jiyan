import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api, type RadarData } from "@/lib/api";
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

  it("switches market news content with the active top tag", async () => {
    const user = userEvent.setup();
    render(<MarketNews />);

    expect(await screen.findByRole("heading", { name: "HBM存储需求跟踪" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "7天" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    expect(await screen.findByRole("heading", { name: "机器人量产进度更新" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "HBM存储需求跟踪" })).not.toBeInTheDocument();
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
