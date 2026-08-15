import { render, screen } from "@testing-library/react";
import { IndustryReport } from "../IndustryReport";
import { getIndustryTemplate } from "../templates";

describe("continuous industry report", () => {
  it("renders all eight report sections in reading order", () => {
    const { container } = render(<IndustryReport template={getIndustryTemplate("storage")!} />);
    const article = screen.getByRole("article", { name: "存储行业研究报告" });
    const ids = Array.from(article.querySelectorAll(":scope > section")).map((node) => node.id);

    expect(ids).toEqual(["overview", "cycle", "chain", "metrics", "capital", "companies", "funds", "news-risk"]);
    expect(container.querySelector('nav a[href="#metrics"]')).toHaveTextContent("核心数据");
  });

  it("shows evidence, source, update, confidence and invalidating conditions", () => {
    render(<IndustryReport template={getIndustryTemplate("storage")!} />);

    expect(screen.getByText("判断依据")).toBeInTheDocument();
    expect(screen.getAllByText("数据来源").length).toBeGreaterThan(0);
    expect(screen.getAllByText("数据更新时间").length).toBeGreaterThan(0);
    expect(screen.getByText("判断置信度")).toBeInTheDocument();
    expect(screen.getByText("可能失效条件")).toBeInTheDocument();
  });

  it("uses explicit truth and empty-data labels", () => {
    render(<IndustryReport template={getIndustryTemplate("storage")!} />);

    expect(screen.getAllByText("开发占位数据").length).toBeGreaterThan(0);
    expect(screen.getAllByText("暂无可靠数据").length).toBeGreaterThan(0);
    expect(screen.queryByText(/建议买入|建议卖出/)).not.toBeInTheDocument();
  });
});
