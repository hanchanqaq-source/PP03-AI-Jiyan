import { render, screen } from "@testing-library/react";
import { MarketNewsSidebar } from "@/features/market-news/MarketNewsSidebar";
import { marketNewsResponse } from "./fixtures";

describe("MarketNewsSidebar", () => {
  it("shows today's ranked focus, holding impact, and fixed relationship definitions", () => {
    const impact = marketNewsResponse.impact_summary!;
    render(<MarketNewsSidebar focus={[...marketNewsResponse.today_focus]} impact={{ ...impact, funds: [...impact.funds] }} />);

    expect(screen.getByRole("heading", { name: "今日重点" })).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("北方华创发布半年度报告")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "我的持仓影响" })).toBeInTheDocument();
    expect(screen.getByText("今天有 2 个事件与你的持仓相关")).toBeInTheDocument();
    expect(screen.getByText("直接涉及重仓公司：1")).toBeInTheDocument();
    expect(screen.getByText("涉及持仓行业：1")).toBeInTheDocument();
    expect(screen.getByText("只涉及关注标签：1")).toBeInTheDocument();
    expect(screen.getByText(/东方人工智能主题混合C.*2 个事件/)).toBeInTheDocument();
    expect(screen.getByText("直接持仓：新闻直接提到基金公开重仓公司")).toBeInTheDocument();
    expect(screen.getByText("产业关联：新闻涉及持仓公司的行业或产业链")).toBeInTheDocument();
    expect(screen.getByText("关注标签：新闻只匹配你主动关注的方向")).toBeInTheDocument();
  });
});
