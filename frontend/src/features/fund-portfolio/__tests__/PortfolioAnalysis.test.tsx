import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api, type FundPortfolioAnalysisData, type FundPortfolioData } from "@/lib/api";
import { PortfolioAnalysis } from "@/pages/PortfolioAnalysis";

const emptyPortfolio: FundPortfolioData = {
  schema_version: 3, holdings: [], total_cost: 0, updated: null, migration: null, data_status: "ok",
};

const emptyAnalysis: FundPortfolioAnalysisData = {
  overview: {
    fund_count: 0, total_cost: 0, market_value: 0, total_holding_value: 0,
    profit_loss: null, return_rate: null, intraday_change_pct: null,
    intraday_estimated_profit_loss: null,
    intraday_message: "盘中估算暂不可用：当前基金类型或公开持仓不足以形成可靠估算",
    nav_dates: [], latest_nav_date: null, inconsistent_nav_dates: false,
    cost_incomplete: false, pnl_complete: false, estimable_count: 0, official_only_count: 0,
    updated_at: "2026-08-16T12:00:00+08:00",
  },
  holdings: [], overlap: [],
  industry_concentration: { exposure: [], identified_coverage_pct: 0, unknown_pct: 0, calculation_basis: "公开持仓" },
  risk_flags: [],
};

const firstHolding = {
  schema_version: 3 as const, code: "000001", input_mode: "amount_pnl" as const,
  amount_snapshot: 1000, cumulative_pnl_snapshot: 100, snapshot_at: "2026-08-15T10:00:00+08:00",
  shares: 800, shares_source: "inferred" as const, basis_nav: 1.25, basis_nav_date: "2026-08-14",
  shares_inference_note: "按正式净值推算，非用户确认份额", avg_unit_cost: null, avg_cost: null,
  buy_date: "", notes: "观察", custom_tag_ids: ["banking"], verification_status: "verified" as const,
  manual_name: null, cost_confirmation_required: false,
  created_at: "2026-08-15T10:00:00+08:00", updated_at: "2026-08-15T10:00:00+08:00",
};

const secondHolding = {
  ...firstHolding, code: "000002", amount_snapshot: 1200, cumulative_pnl_snapshot: -120,
  shares: null, shares_source: null, basis_nav: null, basis_nav_date: null,
  shares_inference_note: null, notes: "待补份额",
};

const savedPortfolio: FundPortfolioData = {
  ...emptyPortfolio, holdings: [firstHolding, secondHolding], total_cost: 2220,
  updated: "2026-08-16T12:00:00+08:00",
};

const populatedAnalysis: FundPortfolioAnalysisData = {
  ...emptyAnalysis,
  overview: {
    ...emptyAnalysis.overview, fund_count: 2, total_cost: 2220, market_value: 2080,
    total_holding_value: 2080, profit_loss: -20, return_rate: -0.9009,
    latest_nav_date: "2026-08-14", nav_dates: ["2026-08-14"],
    intraday_change_pct: 1.2, intraday_estimated_profit_loss: 10.56,
    intraday_message: "这是估算，不是官方净值", estimable_count: 1, official_only_count: 1,
    pnl_complete: true,
  },
  holdings: [
    {
      code: "000001", name: "华夏成长混合", fund_type: "混合型-灵活", user_holding: firstHolding,
      weight_pct: 42.3077,
      position: {
        user_amount_snapshot: 1000, user_cumulative_pnl_snapshot: 100, snapshot_at: firstHolding.snapshot_at,
        official_market_value: 880, intraday_market_value: 890.56, position_value: 880,
        position_value_basis: "official_nav_from_inferred_shares", reference_total_cost: 900,
        today_estimated_profit_loss: 10.56, intraday_change_pct: 1.2,
        total_cost: 900, market_value: 880, profit_loss: 100, return_rate: 11.1111,
      },
      analysis: null,
    },
    {
      code: "000002", name: "测试价值基金", fund_type: "股票型", user_holding: secondHolding,
      weight_pct: 57.6923,
      position: {
        user_amount_snapshot: 1200, user_cumulative_pnl_snapshot: -120, snapshot_at: secondHolding.snapshot_at,
        official_market_value: null, intraday_market_value: null, position_value: 1200,
        position_value_basis: "user_amount_snapshot", reference_total_cost: 1320,
        today_estimated_profit_loss: null, intraday_change_pct: null,
        total_cost: 1320, market_value: 1200, profit_loss: -120, return_rate: -9.0909,
      },
      analysis: null,
    },
  ],
};

const searchResponse = {
  data: [{
    code: "000001", name: "华夏成长混合", fund_type: "混合型-灵活", latest_nav: 1.348,
    latest_nav_date: "2026-08-14", manager_names: ["郑晓辉"], management_company: "华夏基金",
  }],
  meta: {
    status: "disclosed", source_name: "东方财富", source_reference: "https://example.test",
    data_type: "fund_search", as_of_date: "2026-08-14", fetched_at: "2026-08-16",
    is_cached: false, is_stale: false, provider: "eastmoney-direct", fallback_used: false,
    message: "", original_status: null,
  },
};

describe("PortfolioAnalysis schema v3 redesign", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "fundPortfolio").mockResolvedValue(emptyPortfolio);
    vi.spyOn(api, "fundPortfolioAnalysis").mockResolvedValue(emptyAnalysis);
  });

  it("shows one focused empty state and hides overview and combination modules", async () => {
    render(<PortfolioAnalysis />);

    expect(await screen.findByRole("heading", { name: "我的持仓" })).toBeInTheDocument();
    expect(screen.getByText("还没有基金持仓")).toBeInTheDocument();
    expect(screen.getByText("先搜索公开基金，再填写当前持有金额，建立你的本地持仓看板")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加第一只基金" })).toBeInTheDocument();
    expect(screen.queryByText("组合重合度")).not.toBeInTheDocument();
    expect(screen.queryByText("总持有金额")).not.toBeInTheDocument();
  });

  it("adds a fund through the right drawer in quick mode by default", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "searchFunds").mockResolvedValue(searchResponse);
    const upsert = vi.spyOn(api, "upsertFundHolding").mockResolvedValue({ ...emptyPortfolio, holdings: [firstHolding] });
    render(<PortfolioAnalysis />);

    await user.click(await screen.findByRole("button", { name: "添加基金" }));
    expect(screen.getByRole("dialog", { name: "添加基金" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("搜索基金代码或名称"), "华夏成长");
    await user.click(await screen.findByRole("button", { name: "选择基金 华夏成长混合" }));

    expect(screen.getByText("已识别基金：华夏成长混合（000001）")).toBeInTheDocument();
    expect(screen.getByLabelText("当前持有金额")).toBeInTheDocument();
    expect(screen.getByLabelText("当前累计盈亏")).toBeInTheDocument();
    expect(screen.queryByLabelText("持有份额")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("当前持有金额"), "1000");
    await user.type(screen.getByLabelText("当前累计盈亏"), "100");
    await user.click(screen.getByRole("button", { name: "添加到持仓" }));

    await waitFor(() => expect(upsert).toHaveBeenCalledWith(expect.objectContaining({
      code: "000001", input_mode: "amount_pnl", amount_snapshot: 1000,
      cumulative_pnl_snapshot: 100, replace: false,
    })));
  });

  it("keeps exact mode and custom tags inside the collapsed advanced section", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "searchFunds").mockResolvedValue(searchResponse);
    const upsert = vi.spyOn(api, "upsertFundHolding").mockResolvedValue({ ...emptyPortfolio, holdings: [firstHolding] });
    render(<PortfolioAnalysis />);

    await user.click(await screen.findByRole("button", { name: "添加基金" }));
    await user.type(screen.getByLabelText("搜索基金代码或名称"), "000001");
    await user.click(await screen.findByRole("button", { name: "选择基金 华夏成长混合" }));
    await user.click(screen.getByRole("button", { name: "展开高级信息" }));
    await user.click(screen.getByRole("radio", { name: "精确模式" }));
    await user.type(screen.getByLabelText("持有份额"), "500");
    await user.type(screen.getByLabelText("平均单位成本"), "2");
    await user.type(screen.getByLabelText("买入日期"), "2026-08-16");
    await user.type(screen.getByLabelText("新增用户标签"), "家庭核心");
    await user.click(screen.getByRole("button", { name: "添加用户标签 家庭核心" }));
    await user.click(screen.getByRole("button", { name: "添加到持仓" }));

    await waitFor(() => expect(upsert).toHaveBeenCalledWith(expect.objectContaining({
      input_mode: "shares_cost", shares: 500, avg_unit_cost: 2, buy_date: "2026-08-16",
      custom_tag_ids: ["custom:家庭核心"],
    })));
  });

  it("renders four overview cards and dense cards with filters and sorting", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "fundPortfolio").mockResolvedValue(savedPortfolio);
    vi.spyOn(api, "fundPortfolioAnalysis").mockResolvedValue(populatedAnalysis);
    render(<PortfolioAnalysis />);

    expect(await screen.findByText("华夏成长混合")).toBeInTheDocument();
    for (const label of ["总持有金额", "今日估算盈亏", "累计持有盈亏", "累计收益率"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.queryByText("基金数量")).not.toBeInTheDocument();
    expect(screen.getByText("共 2 只基金 · 官方净值更新至 08-14 · 1 只可盘中估算 · 1 只仅正式净值")).toBeInTheDocument();
    for (const filter of ["全部", "上涨", "下跌", "盈利", "亏损", "待补充"]) {
      expect(screen.getByRole("button", { name: `筛选${filter}` })).toBeInTheDocument();
    }
    const cards = screen.getAllByTestId("holding-card");
    expect(within(cards[0]).getByText("用户录入金额")).toBeInTheDocument();
    expect(within(cards[0]).getByText("正式净值参考值")).toBeInTheDocument();
    expect(within(cards[0]).getByText("盘中估算参考值")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "筛选亏损" }));
    expect(screen.queryByText("华夏成长混合")).not.toBeInTheDocument();
    expect(screen.getByText("测试价值基金")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("排序方式"), "fund_name");
  });

  it("edits a quick holding and confirms deletion with an in-page modal", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "fundPortfolio").mockResolvedValue(savedPortfolio);
    vi.spyOn(api, "fundPortfolioAnalysis").mockResolvedValue(populatedAnalysis);
    const upsert = vi.spyOn(api, "upsertFundHolding").mockResolvedValue(savedPortfolio);
    const remove = vi.spyOn(api, "deleteFundHolding").mockResolvedValue(emptyPortfolio);
    render(<PortfolioAnalysis />);
    await screen.findByText("华夏成长混合");

    await user.click(screen.getByRole("button", { name: "编辑持仓 华夏成长混合" }));
    expect(screen.getByRole("dialog", { name: "编辑持仓" })).toBeInTheDocument();
    const amount = screen.getByLabelText("当前持有金额");
    await user.clear(amount);
    await user.type(amount, "1100");
    await user.click(screen.getByRole("button", { name: "保存持仓" }));
    await waitFor(() => expect(upsert).toHaveBeenCalledWith(expect.objectContaining({ amount_snapshot: 1100, replace: true })));

    await user.click(screen.getByRole("button", { name: "删除持仓 华夏成长混合" }));
    expect(screen.getByRole("dialog", { name: "确认删除持仓" })).toBeInTheDocument();
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("000001"));
  });
});
