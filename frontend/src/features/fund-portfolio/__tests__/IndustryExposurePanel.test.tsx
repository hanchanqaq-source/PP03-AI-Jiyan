import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IndustryExposurePanel } from "@/features/fund-portfolio/IndustryExposurePanel";
import type { FundIndustryExposure } from "@/features/fund-portfolio/types";

const exposure = {
  official_allocation: {
    exposure: [{ name: "制造业", display_name: "制造业（待穿透）", weight_pct: 86.67, requires_lookthrough: true }],
    stock_exposure_pct: 94.72,
    as_of_date: "2026-06-30",
    source_name: "东方财富基金档案",
    source_reference: "https://example.test/official",
  },
  lookthrough: {
    status: "disclosed",
    message: "行业暴露仅基于公开持仓估算，未披露部分未归一化",
    primary: [{ name: "电子", weight_pct: 35.0 }],
    secondary: [{ name: "半导体", weight_pct: 35.0 }],
    detail: [{ name: "半导体设备", weight_pct: 30.0 }],
    disclosed_coverage_pct: 43.72,
    identified_coverage_pct: 43.72,
    other_pct: 2.0,
    unknown_pct: 6.72,
    undisclosed_stock_pct: 51.0,
    non_stock_pct: 5.28,
    disclosure_date: "2026-06-30",
    source_name: "巨潮资讯上市公司行业归属",
    source_reference: "https://example.test/lookthrough",
    classification_standard: "申银万国行业分类标准",
    calculation_basis: "最新公开前十大持仓原始占基金净值比例；未披露部分未归一化",
  },
  industry_chain_tags: [{
    id: "semiconductor-equipment", name: "半导体设备", weight_pct: 30.0,
    evidence_level: "disclosed_stock_classification", source_name: "巨潮资讯上市公司行业归属",
  }],
  other_constituents: [{ stock_code: "600001", stock_name: "已分类证券", weight_pct: 2.0, reason: "缺少一级行业名称" }],
  unknown_constituents: [
    { stock_code: "688256", stock_name: "未知证券", weight_pct: 6.72, reason: "股票行业分类缺失或请求失败" },
    { stock_code: "", stock_name: "未披露股票资产", weight_pct: 51.0, reason: "具体证券未披露" },
  ],
  primary: [{ name: "电子", weight_pct: 35.0 }],
  secondary: [{ name: "半导体", weight_pct: 35.0 }],
  broad: [{ name: "电子", weight_pct: 35.0 }],
  system_tags: [{ id: "semiconductor-equipment", name: "半导体设备", weight_pct: 30.0 }],
  identified_coverage_pct: 43.72,
  unidentified_disclosed_pct: 6.72,
  undisclosed_stock_pct: 51.0,
  non_stock_pct: 5.28,
  calculation_basis: "公开持仓穿透",
  industry_classification_source: "巨潮资讯上市公司行业归属",
} as FundIndustryExposure;

it("separates official, lookthrough and chain evidence and expands unresolved constituents", async () => {
  const user = userEvent.setup();
  render(<IndustryExposurePanel title="该基金行业暴露" exposure={exposure} />);

  expect(screen.getByRole("heading", { name: "该基金行业暴露" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "重仓股穿透后的行业暴露" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "官方行业配置" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "产业链 / 主题标签" })).toBeInTheDocument();
  expect(screen.getByText("制造业（待穿透）")).toBeInTheDocument();
  expect(screen.getByText("行业暴露仅基于公开持仓估算，未披露部分未归一化。")).toBeInTheDocument();
  expect(screen.getByText("已识别覆盖率 43.72%")).toBeInTheDocument();
  expect(screen.getByText(/申银万国行业分类标准/)).toBeInTheDocument();

  const rows = screen.getByTestId("lookthrough-rows");
  expect(within(rows).getByText("电子")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "二级行业" }));
  expect(within(rows).getByText("半导体")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "细分行业" }));
  expect(within(rows).getByText("半导体设备")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /其他 2\.00%/ }));
  expect(screen.getByText(/600001/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /未知 6\.72%/ }));
  expect(screen.getByText(/688256/)).toBeInTheDocument();
  expect(screen.getAllByText(/未披露股票资产/).length).toBeGreaterThan(1);
  expect(screen.getByText("基金名称未参与行业事实判断")).toBeInTheDocument();
});

it("does not replace an unavailable lookthrough with official allocation percentages", () => {
  render(<IndustryExposurePanel title="该基金行业暴露" exposure={{
    ...exposure,
    lookthrough: { ...exposure.lookthrough, status: "unavailable", primary: [], secondary: [], detail: [] },
  }} />);

  expect(screen.getByTestId("lookthrough-rows")).toHaveTextContent("暂无可靠数据");
  expect(screen.getByText("制造业（待穿透）")).toBeInTheDocument();
  expect(screen.queryByText("其他 86.67%")).not.toBeInTheDocument();
});
