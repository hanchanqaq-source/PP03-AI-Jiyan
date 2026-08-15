import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "@/lib/api";
import { ResearchHome } from "@/pages/ResearchHome";

describe("ResearchHome", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(api, "fundPortfolio").mockResolvedValue({ holdings: [], total_amount: 0, updated: null });
    vi.spyOn(api, "radar").mockResolvedValue({
      generated_at: null, recent_days: 7, industries: [],
      stats: { industries: 0, total_sources: 0 },
    });
  });

  it("prioritizes holdings, followed tags, related news, risks and recent industries", async () => {
    render(<ResearchHome />);

    expect(await screen.findByRole("heading", { name: "今日持仓变化" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "持仓总览" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "我的关注标签" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "与持仓相关的重要资讯" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "主要风险提醒" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "最近查看的行业" })).toBeInTheDocument();
    expect(screen.getAllByText("暂无可靠数据").length).toBeGreaterThan(0);
  });
});
