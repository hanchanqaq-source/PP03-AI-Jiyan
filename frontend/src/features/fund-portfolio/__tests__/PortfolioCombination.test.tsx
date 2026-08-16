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

const industryExposure = {
  official_allocation: {
    exposure: [
      { name: "制造业", display_name: "制造业（待穿透）", weight_pct: 86.67, requires_lookthrough: true },
      { name: "信息传输、软件和信息技术服务业", display_name: "信息传输、软件和信息技术服务业", weight_pct: 8.05, requires_lookthrough: false },
    ],
    stock_exposure_pct: 94.72,
    as_of_date: "2026-06-30",
    source_name: "基金官方行业配置",
    source_reference: "https://example.test/official",
  },
  lookthrough: {
    status: "disclosed", message: "", disclosed_coverage_pct: 43.72,
    primary: [{ name: "电子", weight_pct: 35 }],
    secondary: [{ name: "半导体", weight_pct: 35 }],
    detail: [{ name: "半导体设备", weight_pct: 30 }],
    identified_coverage_pct: 35, other_pct: 1, unknown_pct: 7.72,
    undisclosed_stock_pct: 51, non_stock_pct: 5.28,
    disclosure_date: "2026-06-30", source_name: "巨潮资讯股票行业分类",
    source_reference: "https://example.test/lookthrough", classification_standard: "证监会行业分类",
    calculation_basis: "公开前十大持仓占基金净值比例；未披露部分未归一化",
  },
  industry_chain_tags: [{ id: "semiconductor-equipment", name: "半导体设备", weight_pct: 30, evidence_level: "disclosed_stock_classification", source_name: "巨潮资讯股票行业分类" }],
  other_constituents: [], unknown_constituents: [],
  primary: [{ name: "电子", weight_pct: 35 }], secondary: [{ name: "半导体", weight_pct: 35 }],
  broad: [], system_tags: [{ id: "semiconductor-equipment", name: "半导体设备", weight_pct: 30 }],
  identified_coverage_pct: 35, unidentified_disclosed_pct: 8.72, undisclosed_stock_pct: 51,
  non_stock_pct: 5.28, calculation_basis: "公开前十大持仓占基金净值比例；未披露部分未归一化",
  industry_classification_source: "巨潮资讯股票行业分类",
};

const data = {
  overview,
  holdings: [
    { code: "000001", name: "甲基金", analysis: { industry_exposure: { data: industryExposure } } },
    { code: "000002", name: "乙基金", analysis: { industry_exposure: { data: { ...industryExposure, official_allocation: { ...industryExposure.official_allocation, exposure: [{ name: "制造业", display_name: "制造业（待穿透）", weight_pct: 60, requires_lookthrough: true }] }, industry_chain_tags: [{ id: "robotics", name: "机器人", weight_pct: 10, evidence_level: "disclosed_stock_classification", source_name: "巨潮资讯股票行业分类" }] } } } },
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
    primary: [{ name: "电子", weight_pct: 35 }], secondary: [{ name: "半导体", weight_pct: 35 }],
    detail: [{ name: "半导体设备", weight_pct: 30 }], exposure: [{ name: "电子", weight_pct: 35 }],
    industry_chain_tags: [{ id: "semiconductor-equipment", name: "半导体设备", weight_pct: 15 }, { id: "robotics", name: "机器人", weight_pct: 5 }],
    identified_coverage_pct: 35, other_pct: 1, unknown_pct: 7.72,
    undisclosed_stock_pct: 51, non_stock_pct: 5.28,
    calculation_basis: "当前参考市值权重 × 最新公开前十大持仓穿透行业；未披露部分未归一化",
  },
  risk_flags: [],
} as unknown as FundPortfolioAnalysisData;

it("shows only a compact overlap notice for one comparable fund", () => {
  render(<PortfolioCombination data={{ ...data, overview: { ...overview, fund_count: 1 }, holdings: data.holdings.slice(0, 1), overlap: [] }} />);

  expect(screen.getByText("至少需要两只可比较基金，才能计算组合重合度。")).toBeInTheDocument();
  expect(screen.queryByText("0 只重合证券")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "该基金行业暴露" })).toBeInTheDocument();
  expect(screen.getByText("制造业（待穿透）")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "组合行业集中度" })).not.toBeInTheDocument();
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

it("keeps official, lookthrough and chain evidence separate for a portfolio", () => {
  render(<PortfolioCombination data={data} />);

  expect(screen.getByText("组合行业集中度")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "官方行业配置" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "重仓股穿透后的行业暴露" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "产业链 / 主题标签" })).toBeInTheDocument();
  expect(screen.getAllByText("制造业（待穿透）")).toHaveLength(2);
  expect(screen.getByRole("button", { name: "未知 7.72%" })).toBeInTheDocument();
  expect(screen.getByText("半导体设备 15.00%")).toBeInTheDocument();
  expect(screen.getByText("机器人 5.00%")).toBeInTheDocument();
  expect(screen.getByText("基金名称未参与行业事实判断")).toBeInTheDocument();
});
