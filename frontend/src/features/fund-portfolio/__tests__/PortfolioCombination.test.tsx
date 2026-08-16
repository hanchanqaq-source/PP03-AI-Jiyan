import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PortfolioCombination } from "@/features/fund-portfolio/PortfolioCombination";
import type { FundPortfolioAnalysisData } from "@/lib/api";

const overview = {
  fund_count: 2, total_cost: 2000, market_value: 2200, total_holding_value: 2200,
  profit_loss: 200, return_rate: 10, intraday_change_pct: null,
  intraday_estimated_profit_loss: null, intraday_message: "暂无可靠数据",
  nav_dates: ["2026-08-14"], latest_nav_date: "2026-08-14", inconsistent_nav_dates: false,
  cost_incomplete: false, pnl_complete: true, estimable_count: 0, official_only_count: 2,
  updated_at: "2026-08-16T12:00:00+08:00",
};

const data = {
  overview,
  holdings: [
    { code: "000001", name: "甲基金", analysis: { industry_exposure: { data: { system_tags: [{ id: "semiconductor", name: "半导体", weight_pct: 20 }] } } } },
    { code: "000002", name: "乙基金", analysis: { industry_exposure: { data: { system_tags: [{ id: "robotics", name: "机器人", weight_pct: 10 }] } } } },
  ],
  overlap: Array.from({ length: 6 }, (_, index) => ({
    stock_code: `60000${index}`, stock_name: `证券${index + 1}`,
    funds: [
      { fund_code: "000001", fund_name: "甲基金", weight_pct: 10, portfolio_exposure_pct: 5 },
      { fund_code: "000002", fund_name: "乙基金", weight_pct: 8, portfolio_exposure_pct: 4 },
    ],
    portfolio_exposure_pct: 9 - index,
    calculation_basis: "正式净值参考值权重 × 公开持仓比例",
  })),
  industry_concentration: {
    exposure: [{ name: "科技", weight_pct: 45 }, { name: "消费", weight_pct: 15 }],
    identified_coverage_pct: 60, unknown_pct: 40, calculation_basis: "公开行业暴露",
  },
  risk_flags: [],
} as unknown as FundPortfolioAnalysisData;

it("shows only a compact overlap notice for one comparable fund", () => {
  render(<PortfolioCombination data={{ ...data, overview: { ...overview, fund_count: 1 }, holdings: data.holdings.slice(0, 1), overlap: [] }} />);

  expect(screen.getByText("至少需要两只可比较基金，才能计算组合重合度。")).toBeInTheDocument();
  expect(screen.queryByText("0 只重合证券")).not.toBeInTheDocument();
});

it("shows five overlaps by default and expands all securities", async () => {
  const user = userEvent.setup();
  render(<PortfolioCombination data={data} />);

  expect(screen.getByText("6 只重合证券")).toBeInTheDocument();
  expect(screen.getByText("证券5")).toBeInTheDocument();
  expect(screen.queryByText("证券6")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "查看全部重合" }));
  expect(screen.getByText("证券6")).toBeInTheDocument();
  expect(screen.getAllByText(/甲基金/).length).toBeGreaterThan(0);
});

it("keeps unknown industry exposure and shows fine-direction tags", () => {
  render(<PortfolioCombination data={data} />);

  expect(screen.getByText("组合行业集中度")).toBeInTheDocument();
  expect(screen.getByText("未知部分")).toBeInTheDocument();
  expect(screen.getByText("+40.00%")).toBeInTheDocument();
  expect(screen.getByText("半导体")).toBeInTheDocument();
  expect(screen.getByText("机器人")).toBeInTheDocument();
});
