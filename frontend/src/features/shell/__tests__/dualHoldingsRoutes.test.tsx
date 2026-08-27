import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { vi } from "vitest";
import { api } from "@/lib/api";
import { APP_ROUTES } from "@/router";

function renderPath(path: string) {
  const router = createMemoryRouter(APP_ROUTES, { initialEntries: [path] });
  return { router, rendered: render(<RouterProvider router={router} />) };
}

describe("dual holdings routes", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "portfolio").mockResolvedValue({
      holdings: [],
      closed: [],
      totals: { market_value: 0, cost: 0, pnl: 0, pnl_pct: 0 },
      updated: null,
    } as never);
    vi.spyOn(api, "fundPortfolio").mockResolvedValue({
      schema_version: 3,
      holdings: [],
      total_cost: 0,
      updated: null,
      migration: null,
      data_status: "ok",
    });
    vi.spyOn(api, "fundPortfolioAnalysis").mockResolvedValue({
      overview: {
        fund_count: 0,
        total_cost: 0,
        market_value: 0,
        total_holding_value: 0,
        profit_loss: null,
        return_rate: null,
        intraday_change_pct: null,
        intraday_estimated_profit_loss: null,
        intraday_message: "暂无可靠数据",
        nav_dates: [],
        latest_nav_date: null,
        inconsistent_nav_dates: false,
        cost_incomplete: false,
        pnl_complete: false,
        estimable_count: 0,
        official_only_count: 0,
        intraday_estimate_complete: false,
        intraday_covered_count: 0,
        intraday_total_count: 0,
        updated_at: "",
      },
      holdings: [],
      overlap: [],
      industry_concentration: {
        primary: [], secondary: [], detail: [], exposure: [], industry_chain_tags: [],
        identified_coverage_pct: 0, other_pct: 0, unknown_pct: 0,
        undisclosed_stock_pct: 0, non_stock_pct: 0, calculation_basis: "",
      },
      risk_flags: [],
    });
    vi.spyOn(api, "searchFunds").mockResolvedValue({
      data: [],
      meta: {
        source_name: "isolated test", source_reference: "local-test", data_type: "fund_search",
        as_of_date: null, fetched_at: "", status: "unavailable", is_cached: false,
        is_stale: false, provider: "test", fallback_used: false, message: "暂无可靠数据",
        original_status: null,
      },
    });
  });

  it("redirects the holdings entry to the fund tab by default", async () => {
    const { router } = renderPath("/portfolio");

    await waitFor(() => expect(router.state.location.pathname).toBe("/portfolio/funds"));
  });

  it("redirects the historical portfolio-analysis route to the fund tab", async () => {
    const { router } = renderPath("/portfolio-analysis");

    await waitFor(() => expect(router.state.location.pathname).toBe("/portfolio/funds"));
  });

  it("keeps exactly one sidebar holdings entry and points it at funds", async () => {
    renderPath("/daily-review");

    const links = await screen.findAllByRole("link", { name: "我的持仓" });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/portfolio/funds");
  });

  it("shows one page title and both holdings tabs", async () => {
    renderPath("/portfolio/funds");

    expect(await screen.findAllByRole("heading", { name: "我的持仓" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "基金持仓" })).toHaveAttribute("href", "/portfolio/funds");
    expect(screen.getByRole("link", { name: "股票持仓" })).toHaveAttribute("href", "/portfolio/stocks");
  });

  it("preserves an unsaved stock draft while switching tabs", async () => {
    const user = userEvent.setup();
    const { router } = renderPath("/portfolio/stocks");
    const code = await screen.findByRole("textbox", { name: "股票代码" });
    await user.type(code, "600519");

    await user.click(screen.getByRole("link", { name: "基金持仓" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/portfolio/funds"));
    await user.click(screen.getByRole("link", { name: "股票持仓" }));

    expect(await screen.findByRole("textbox", { name: "股票代码" })).toHaveValue("600519");
  });

  it("preserves an unsaved fund search while switching tabs", async () => {
    const user = userEvent.setup();
    const { router } = renderPath("/portfolio/funds");
    await user.click(await screen.findByRole("button", { name: "添加基金" }));
    const query = screen.getByRole("textbox", { name: "搜索基金代码或名称" });
    await user.type(query, "000001");

    await user.click(screen.getByRole("link", { name: "股票持仓" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/portfolio/stocks"));
    await user.click(screen.getByRole("link", { name: "基金持仓" }));

    expect(screen.getByRole("textbox", { name: "搜索基金代码或名称" })).toHaveValue("000001");
  });
});
