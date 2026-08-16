import { render, screen } from "@testing-library/react";
import { NavChart, splitNavSeries } from "@/features/fund-portfolio/NavChart";

const points = [
  { date: "2026-01-02", unit_nav: 1, cumulative_nav: 1, daily_change_pct: 0 },
  { date: "2026-01-05", unit_nav: 1.1, cumulative_nav: 1.1, daily_change_pct: 10 },
  { date: "2026-01-20", unit_nav: 1.2, cumulative_nav: 1.2, daily_change_pct: 9.09 },
];

it("offers all six requested ranges", () => {
  render(<NavChart points={points} />);
  for (const label of ["1月", "3月", "6月", "1年", "3年", "全部"]) {
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  }
});

it("splits lines across data gaps longer than seven days", () => {
  expect(splitNavSeries(points).map((series) => series.map((point) => point.date))).toEqual([
    ["2026-01-02", "2026-01-05"], ["2026-01-20"],
  ]);
});

it("shows an isolated empty state instead of crashing", () => {
  render(<NavChart points={[]} />);
  expect(screen.getByText("历史净值曲线暂不可用")).toBeInTheDocument();
});

