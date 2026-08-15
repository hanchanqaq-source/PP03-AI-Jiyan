import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api, type FundPortfolioData } from "@/lib/api";
import { PortfolioAnalysis } from "@/pages/PortfolioAnalysis";

const empty: FundPortfolioData = { holdings: [], total_amount: 0, updated: null };

describe("PortfolioAnalysis", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "fundPortfolio").mockResolvedValue(empty);
  });

  it("records all required fund fields and shows truth-separated values", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "upsertFundHolding").mockResolvedValue({
      holdings: [{
        code: "000001", name: "用户录入基金", amount: 1000, shares: 500, cost: 2,
        buy_date: "2026-08-16", notes: "观察", tag_ids: ["storage"],
        official_nav: null, official_nav_date: null, intraday_estimate: null,
        estimate_updated_at: null, estimate_confidence: null, holding_disclosure_date: null,
        top10_coverage: null, historical_nav: null,
      }],
      total_amount: 1000,
      updated: "2026-08-16 12:00",
    });
    render(<PortfolioAnalysis />);

    await user.type(screen.getByLabelText("基金代码"), "000001");
    await user.type(screen.getByLabelText("基金名称"), "用户录入基金");
    await user.type(screen.getByLabelText("持有金额"), "1000");
    await user.type(screen.getByLabelText("持有份额"), "500");
    await user.type(screen.getByLabelText("持仓成本"), "2");
    await user.type(screen.getByLabelText("买入日期"), "2026-08-16");
    await user.type(screen.getByLabelText("备注"), "观察");
    await user.click(screen.getByRole("checkbox", { name: "关联存储" }));
    await user.click(screen.getByRole("button", { name: "保存基金持仓" }));

    expect(await screen.findByText("用户录入基金")).toBeInTheDocument();
    expect(screen.getByText("¥1,000")).toBeInTheDocument();
    expect(screen.getAllByText("暂无可靠数据").length).toBeGreaterThan(0);
    expect(screen.getByText(/盘中估算不是实时净值/)).toBeInTheDocument();
  });

  it("keeps the empty state usable when the backend is unavailable", async () => {
    vi.spyOn(api, "fundPortfolio").mockRejectedValue(new Error("offline"));
    render(<PortfolioAnalysis />);

    expect(await screen.findByText("基金持仓加载失败，请确认本地后端已启动。" )).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存基金持仓" })).toBeEnabled();
  });
});
