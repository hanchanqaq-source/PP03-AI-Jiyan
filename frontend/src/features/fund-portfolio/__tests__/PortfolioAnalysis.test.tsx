import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api, type FundPortfolioAnalysisData, type FundPortfolioData } from "@/lib/api";
import { PortfolioAnalysis } from "@/pages/PortfolioAnalysis";

const emptyPortfolio: FundPortfolioData = {
  schema_version: 2, holdings: [], total_cost: 0, updated: null, migration: null, data_status: "ok",
};

const emptyAnalysis: FundPortfolioAnalysisData = {
  overview: {
    fund_count: 0, total_cost: 0, market_value: 0, profit_loss: 0, return_rate: null,
    intraday_change_pct: null,
    intraday_message: "盘中估算暂不可用：当前基金类型或公开持仓不足以形成可靠估算",
    nav_dates: [], inconsistent_nav_dates: false, cost_incomplete: false, updated_at: "2026-08-16T12:00:00+08:00",
  },
  holdings: [], overlap: [],
  industry_concentration: { exposure: [], identified_coverage_pct: 0, unknown_pct: 0, calculation_basis: "公开持仓" },
  risk_flags: [],
};

const savedPortfolio: FundPortfolioData = {
  ...emptyPortfolio,
  holdings: [{
    code: "000001", shares: 500, avg_cost: 2, buy_date: "2026-08-16", notes: "观察",
    custom_tag_ids: ["banking"], verification_status: "verified", manual_name: null,
    cost_confirmation_required: false, created_at: "2026-08-16T12:00:00+08:00", updated_at: "2026-08-16T12:00:00+08:00",
  }],
  total_cost: 1000,
};

describe("PortfolioAnalysis", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "fundPortfolio").mockResolvedValue(emptyPortfolio);
    vi.spyOn(api, "fundPortfolioAnalysis").mockResolvedValue(emptyAnalysis);
  });

  it("searches by code or name and only asks for user truth after verification", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "searchFunds").mockResolvedValue({
      data: [{
        code: "000001", name: "华夏成长混合", fund_type: "混合型-灵活", latest_nav: 1.348,
        latest_nav_date: "2026-08-14", manager_names: ["郑晓辉"], management_company: "华夏基金",
      }],
      meta: { status: "disclosed", source_name: "东方财富", source_reference: "https://example.test", data_type: "fund_search", as_of_date: "2026-08-14", fetched_at: "2026-08-16", is_cached: false, is_stale: false, provider: "eastmoney-direct", fallback_used: false, message: "", original_status: null },
    });
    const upsert = vi.spyOn(api, "upsertFundHolding").mockResolvedValue(savedPortfolio);
    render(<PortfolioAnalysis />);

    await user.type(screen.getByLabelText("输入基金代码或基金名称"), "华夏成长");
    await user.click(await screen.findByRole("button", { name: "选择基金 华夏成长混合" }));

    expect(screen.queryByLabelText("基金名称")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("持有份额"), "500");
    await user.type(screen.getByLabelText("平均单位成本"), "2");
    await user.type(screen.getByLabelText("买入日期"), "2026-08-16");
    await user.type(screen.getByLabelText("备注"), "观察");
    await user.click(screen.getByRole("button", { name: "选择用户标签" }));
    await user.click(screen.getByRole("checkbox", { name: "银行" }));
    await user.click(screen.getByRole("button", { name: "确认添加" }));
    await user.click(screen.getByRole("button", { name: "添加到持仓" }));

    await waitFor(() => expect(upsert).toHaveBeenCalledWith(expect.objectContaining({
      code: "000001", shares: 500, avg_cost: 2, custom_tag_ids: ["banking"],
      verification_status: "verified", replace: false,
    })));
  });

  it("prevents adding a fund already held", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "fundPortfolio").mockResolvedValue(savedPortfolio);
    vi.spyOn(api, "searchFunds").mockResolvedValue({
      data: [{ code: "000001", name: "华夏成长混合", fund_type: "混合型", latest_nav: 1.3, latest_nav_date: "2026-08-14", manager_names: [], management_company: null }],
      meta: { status: "disclosed", source_name: "东方财富", source_reference: "", data_type: "fund_search", as_of_date: "2026-08-14", fetched_at: "", is_cached: false, is_stale: false, provider: "eastmoney-direct", fallback_used: false, message: "", original_status: null },
    });
    const upsert = vi.spyOn(api, "upsertFundHolding");
    render(<PortfolioAnalysis />);

    await user.type(screen.getByLabelText("输入基金代码或基金名称"), "000001");
    await user.click(await screen.findByRole("button", { name: "选择基金 华夏成长混合" }));

    expect(await screen.findByText("该基金已在持仓中，请使用编辑操作更新。" )).toBeInTheDocument();
    expect(upsert).not.toHaveBeenCalled();
  });

  it("marks manual fallback as unverified", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "upsertFundHolding").mockResolvedValue(savedPortfolio);
    render(<PortfolioAnalysis />);

    await user.click(screen.getByRole("button", { name: "切换手动录入" }));
    expect(screen.getByText("手动录入，不代表基金信息已核验")).toBeInTheDocument();
    expect(screen.getByLabelText("手动基金名称")).toBeInTheDocument();
  });

  it("requires a second confirmation before deletion", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "fundPortfolio").mockResolvedValue(savedPortfolio);
    vi.spyOn(api, "fundPortfolioAnalysis").mockResolvedValue({
      ...emptyAnalysis,
      overview: { ...emptyAnalysis.overview, fund_count: 1 },
      holdings: [{
        code: "000001", name: "华夏成长混合", fund_type: "混合型", user_holding: savedPortfolio.holdings[0],
        position: { total_cost: 1000, market_value: 1100, profit_loss: 100, return_rate: 10 },
        analysis: null,
      }],
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const remove = vi.spyOn(api, "deleteFundHolding");
    render(<PortfolioAnalysis />);
    await screen.findByText("华夏成长混合");
    await user.click(screen.getByRole("button", { name: "删除 华夏成长混合" }));
    expect(confirm).toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
  });
});
