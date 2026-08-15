import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { APP_ROUTES } from "@/router";

describe("PP03 shell routing", () => {
  it("redirects the root to the four-page PP03 shell", async () => {
    const router = createMemoryRouter(APP_ROUTES, { initialEntries: ["/"] });
    render(<RouterProvider router={router} />);

    expect(await screen.findByRole("heading", { name: "投研首页" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "01 投研首页" })).toHaveAttribute("href", "/research-home");
    expect(screen.getByRole("link", { name: "02 市场资讯" })).toHaveAttribute("href", "/market-news");
    expect(screen.getByRole("link", { name: "03 行业研究" })).toHaveAttribute("href", "/industry-research");
    expect(screen.getByRole("link", { name: "04 持仓分析" })).toHaveAttribute("href", "/portfolio-analysis");
  });

  it("keeps an original Vibe-Research page reachable", async () => {
    const router = createMemoryRouter(APP_ROUTES, { initialEntries: ["/sectors"] });
    render(<RouterProvider router={router} />);

    expect(await screen.findByRole("heading", { name: "板块中心" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "板块中心" })).toHaveAttribute("href", "/sectors");
    expect(screen.getByText("原有工具")).toBeInTheDocument();
  });
});
