import { render, screen } from "@testing-library/react";
import { IndustryReport } from "../IndustryReport";
import { candidateEvidence, displayedReport } from "./fixtures";

describe("continuous evidence-bound industry report", () => {
  it("renders all eight sections in the fixed reading order on one report spine", () => {
    render(<IndustryReport report={displayedReport()} candidate={candidateEvidence()} industryName="存储" windowDays={90} onWindowDaysChange={() => undefined} />);
    const article = screen.getByRole("article", { name: "存储行业研究报告" });
    const ids = Array.from(article.querySelectorAll(":scope > section")).map((node) => node.id);

    expect(ids).toEqual(["overview", "cycle", "chain", "metrics", "capital", "companies", "funds", "news-risk"]);
    expect(article).toHaveAttribute("data-industry-id", "storage");
    expect(article.querySelectorAll(".grid.lg\\:grid-cols-3")).toHaveLength(0);
  });

  it("shows every truth field beside values and uses an accurate empty reason beside nulls", () => {
    render(<IndustryReport report={displayedReport()} candidate={candidateEvidence()} industryName="存储" windowDays={90} onWindowDaysChange={() => undefined} />);

    expect(screen.getAllByText("当前值").length).toBeGreaterThan(0);
    expect(screen.getAllByText("数据来源").length).toBeGreaterThan(0);
    expect(screen.getAllByText("数据更新时间").length).toBeGreaterThan(0);
    expect(screen.getAllByText("数据口径").length).toBeGreaterThan(0);
    expect(screen.getAllByText("判断依据").length).toBeGreaterThan(0);
    expect(screen.getAllByText("失效条件").length).toBeGreaterThan(0);
    expect(screen.getAllByText("对应证据").length).toBeGreaterThan(0);
    expect(screen.getAllByText("暂无可靠数据").length).toBeGreaterThan(0);
    expect(screen.getAllByText("来源未配置").length).toBeGreaterThan(0);
  });

  it("keeps candidate and conflict evidence outside the trusted overview", () => {
    render(<IndustryReport report={displayedReport()} candidate={candidateEvidence()} industryName="存储" windowDays={90} onWindowDaysChange={() => undefined} />);

    const overview = screen.getByRole("region", { name: "行业总览" });
    expect(overview).toHaveTextContent("storage_primary");
    expect(overview).not.toHaveTextContent("hbm_demand");
    expect(screen.getByText("HBM 需求候选")).toBeInTheDocument();
    expect(screen.getAllByText("待核验").length).toBeGreaterThan(0);
    expect(screen.getByText("family-a · 上升")).toBeInTheDocument();
    expect(screen.getByText("family-b · 下降")).toBeInTheDocument();
    expect(screen.queryByText(/冲突综合值/)).not.toBeInTheDocument();
  });

  it("marks companies as observations and does not turn them into recommendations", () => {
    render(<IndustryReport report={displayedReport()} candidate={candidateEvidence()} industryName="存储" windowDays={90} onWindowDaysChange={() => undefined} />);

    expect(screen.getByText("观察对象，不构成推荐")).toBeInTheDocument();
    expect(screen.queryByText(/建议买入|建议卖出|基金排行榜/)).not.toBeInTheDocument();
  });
});
