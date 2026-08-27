import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { FundDetailDrawer } from "@/features/fund-portfolio/FundDetailDrawer";
import type { FundAnalysis, FundHolding, PositionMetrics } from "@/lib/api";

const meta = {
  status: "disclosed", source_name: "东方财富", source_reference: "https://example.test",
  data_type: "fund_profile", as_of_date: "2026-06-30", fetched_at: "2026-08-16T12:00:00+08:00",
  is_cached: false, is_stale: false, provider: "akshare-eastmoney", fallback_used: false,
  message: "", original_status: null,
};

const holding: FundHolding = {
  schema_version: 3, code: "000001", input_mode: "amount_pnl", amount_snapshot: 1000,
  cumulative_pnl_snapshot: 100, snapshot_at: "2026-08-15T10:00:00+08:00",
  shares: 500, shares_source: "inferred", basis_nav: 2, basis_nav_date: "2026-08-14",
  shares_inference_note: "按正式净值推算，非用户确认份额", avg_unit_cost: null,
  avg_cost: null, buy_date: "", notes: "观察",
  custom_tag_ids: ["banking"], verification_status: "verified", manual_name: null,
  cost_confirmation_required: false, created_at: "", updated_at: "",
};

const position: PositionMetrics = {
  user_amount_snapshot: 1000, user_cumulative_pnl_snapshot: 100, snapshot_at: holding.snapshot_at,
  official_market_value: 1100, intraday_market_value: null, position_value: 1100,
  position_value_basis: "official_nav_from_inferred_shares", reference_total_cost: 900,
  today_estimated_profit_loss: null, intraday_change_pct: null,
  total_cost: 900, market_value: 1100, profit_loss: 100, return_rate: 11.1111,
};

const analysis: FundAnalysis = {
  code: "000001",
  profile: { data: { code: "000001", name: "华夏成长混合", full_name: "华夏成长证券投资基金", fund_type: "混合型-灵活", established_date: "2001-12-18", scale: 39.38, scale_unit: "亿元", scale_date: "2026-06-30", manager_names: ["郑晓辉"], management_company: "华夏基金", custodian: "建设银行", risk_level: null, risk_note: "以法律文件为准" }, meta },
  latest_nav: { data: { unit_nav: 1.348, cumulative_nav: 3.921, nav_date: "2026-08-14" }, meta: { ...meta, status: "official", data_type: "latest_official_nav" } },
  nav_history: { data: { points: [{ date: "2026-08-14", unit_nav: 1.348, cumulative_nav: 3.921, daily_change_pct: 0.37 }], latest: { unit_nav: 1.348, cumulative_nav: 3.921, nav_date: "2026-08-14" } }, meta: { ...meta, status: "official" } },
  performance: { returns: { "1m": 2, "3m": 3, "6m": 4, "1y": 5, "3y": 6 }, since_inception: 7, max_drawdown: -20, annualized_volatility: 25 },
  holdings: { data: { report_period: "2026-Q2", as_of_date: "2026-06-30", disclosure_date: null, public_date: null, is_top_ten: true, top10_coverage_pct: 43.72, holdings: [{ stock_code: "300308", stock_name: "中际旭创", weight_pct: 4.31, shares_10k: 20, market_value_10k: 11388 }] }, meta },
  industry_exposure: { data: {
    official_allocation: { exposure: [{ name: "制造业", display_name: "制造业（待穿透）", weight_pct: 86.67, requires_lookthrough: true }], stock_exposure_pct: 63.72, as_of_date: "2026-06-30", source_name: "基金官方行业配置", source_reference: "https://example.test/official" },
    lookthrough: { status: "disclosed", message: "", disclosed_coverage_pct: 43.72, primary: [{ name: "电子", weight_pct: 35 }], secondary: [{ name: "半导体", weight_pct: 35 }], detail: [{ name: "半导体设备", weight_pct: 30 }], identified_coverage_pct: 35, other_pct: 1, unknown_pct: 7.72, undisclosed_stock_pct: 20, non_stock_pct: 36.28, as_of_date: "2026-06-30", disclosure_date: null, source_name: "巨潮资讯股票行业分类", source_reference: "https://example.test/lookthrough", classification_standard: "证监会行业分类", calculation_basis: "公开前十大持仓占基金净值比例；未披露部分未归一化" },
    industry_chain_tags: [{ id: "semiconductor-equipment", name: "半导体设备", weight_pct: 30, evidence_level: "disclosed_stock_classification", source_name: "巨潮资讯股票行业分类" }],
    other_constituents: [], unknown_constituents: [{ stock_code: "300308", stock_name: "中际旭创", weight_pct: 7.72, reason: "行业数据缺失" }],
    primary: [{ name: "电子", weight_pct: 35 }], secondary: [{ name: "半导体", weight_pct: 35 }], broad: [], system_tags: [{ id: "semiconductor-equipment", name: "半导体设备", weight_pct: 30 }], identified_coverage_pct: 35, unidentified_disclosed_pct: 8.72, undisclosed_stock_pct: 20, non_stock_pct: 36.28, calculation_basis: "公开前十大持仓占基金净值比例；未披露部分未归一化", industry_classification_source: "巨潮资讯股票行业分类",
  }, meta },
  intraday_estimate: { data: { status: "unavailable", message: "盘中估算暂不可用" }, meta: { ...meta, status: "unavailable" } },
  data_quality: { profile: meta, latest_nav: meta, nav_history: meta, holdings: meta, industry_exposure: meta, intraday_estimate: meta, industry_allocation: meta, stock_industry_classification: { ...meta, source_name: "巨潮资讯股票行业分类", data_type: "stock_industry_classification" } },
};

it("shows five evidence regions, value provenance and complete data quality", () => {
  render(<FundDetailDrawer open holding={holding} analysis={analysis} position={position} onClose={() => {}} onEdit={() => {}} onDelete={() => {}} />);
  for (const title of ["基本资料", "净值与走势", "公开持仓披露", "行业暴露", "数据质量"]) {
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
  }
  expect(screen.getByText("基金持仓来自定期报告披露，不代表基金当前实时持仓。")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "官方行业配置" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "重仓股穿透后的行业暴露" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "产业链 / 主题标签" })).toBeInTheDocument();
  expect(screen.getByText("制造业（待穿透）")).toBeInTheDocument();
  expect(screen.getByText("用户标签")).toBeInTheDocument();
  expect(screen.getByText("基金名称未参与行业事实判断")).toBeInTheDocument();
  expect(screen.getByText("stock_industry_classification")).toBeInTheDocument();
  expect(screen.getByText("官方净值来源")).toBeInTheDocument();
  for (const label of ["用户录入金额", "正式净值参考值", "盘中估算参考值", "持仓来源", "更新时间", "是否使用缓存", "缺失字段", "估算可信度"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  expect(screen.getByText("持仓截至：2026-06-30")).toBeInTheDocument();
  expect(screen.getByText("公开日期：未提供")).toBeInTheDocument();
  expect(screen.queryByText("披露日期：2026-06-30")).not.toBeInTheDocument();
});

it("offers edit delete and close actions in the drawer footer", async () => {
  const user = userEvent.setup();
  const onEdit = vi.fn(), onDelete = vi.fn(), onClose = vi.fn();
  render(<FundDetailDrawer open holding={holding} analysis={analysis} position={position} onClose={onClose} onEdit={onEdit} onDelete={onDelete} />);

  await user.click(screen.getByRole("button", { name: "编辑持仓" }));
  await user.click(screen.getByRole("button", { name: "删除持仓" }));
  await user.click(screen.getByRole("button", { name: "关闭" }));

  expect(onEdit).toHaveBeenCalledOnce();
  expect(onDelete).toHaveBeenCalledOnce();
  expect(onClose).toHaveBeenCalledOnce();
});
