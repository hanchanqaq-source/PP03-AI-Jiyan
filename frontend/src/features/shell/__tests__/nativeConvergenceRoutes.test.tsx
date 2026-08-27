import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { vi } from "vitest";
import { api, type RadarData } from "@/lib/api";
import { APP_ROUTES } from "@/router";

const emptyRadar: RadarData = {
  generated_at: null,
  recent_days: 7,
  stats: { industries: 0, total_sources: 0 },
  industries: [],
};

function renderPath(path: string) {
  const router = createMemoryRouter(APP_ROUTES, { initialEntries: [path] });
  return { router, rendered: render(<RouterProvider router={router} />) };
}

describe("native Vibe-Research shell", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "radar").mockResolvedValue(emptyRadar);
  });

  it("uses daily review as the root and exposes only the native primary navigation", async () => {
    renderPath("/");

    expect(await screen.findByRole("heading", { name: "每日复盘" })).toBeInTheDocument();
    for (const [name, href] of [
      ["资讯雷达", "/intel/investment-news"],
      ["产业信号", "/signals"],
      ["个股数据", "/stock-data"],
      ["板块中心", "/sectors"],
      ["多空辩论", "/debate"],
      ["自选股", "/watchlist"],
      ["我的持仓", "/portfolio/funds"],
      ["我的研报", "/my-reports"],
      ["研究记录", "/notes"],
      ["接入 AI", "/settings"],
    ]) {
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", href);
    }
    for (const removed of ["01 投研首页", "02 市场资讯", "03 行业研究", "04 持仓分析", "05 证据中心"]) {
      expect(screen.queryByRole("link", { name: removed })).not.toBeInTheDocument();
    }
  });

  it.each([
    "/research-home",
    "/market-news",
    "/industry-research",
    "/evidence-center",
    "/provider-center",
    "/data-sources",
  ])("redirects removed and unknown product routes to the native home (%s)", async (path) => {
    renderPath(path);

    expect(await screen.findByRole("heading", { name: "每日复盘" })).toBeInTheDocument();
  });

  it("opens the current upstream industry signal page", async () => {
    renderPath("/signals");

    expect(await screen.findByRole("heading", { name: "产业信号" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "产业信号" })).toHaveAttribute("href", "/signals");
  });

  it("keeps native information-radar child routes inside the Intel page", async () => {
    renderPath("/intel/investment-news");

    expect(await screen.findByRole("heading", { name: "资讯雷达" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Investment News/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("redirects the intel parent route to the unique investment-news entry", async () => {
    const { router } = renderPath("/intel");

    await waitFor(() => expect(router.state.location.pathname).toBe("/intel/investment-news"));
    expect(screen.getByRole("button", { name: /Investment News/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("redirects an unknown intel child route to the unique investment-news entry", async () => {
    const { router } = renderPath("/intel/foo");

    await waitFor(() => expect(router.state.location.pathname).toBe("/intel/investment-news"));
    expect(screen.getByRole("button", { name: /Investment News/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps source management inside native intel", async () => {
    vi.spyOn(api, "radarSources").mockResolvedValue({
      store_status: "missing", store_error: null, api_adapters: [], industries: [],
      summary: { total: 0, built_in: 0, custom: 0, enabled: 0 }, sources: [],
    });
    renderPath("/intel/sources");
    expect(await screen.findByRole("heading", { name: "资讯雷达" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /资讯源管理/ })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText(/内置源保持在仓库中/)).toBeInTheDocument();
  });
});
