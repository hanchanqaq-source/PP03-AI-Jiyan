import { render, screen } from "@testing-library/react";
import { NewsCard } from "../NewsCard";
import type { NormalizedNewsEvent } from "../normalize";

const event: NormalizedNewsEvent = {
  id: "hbm",
  title: "HBM memory demand expands",
  url: "https://example.com/hbm",
  publishedAt: "08-16 10:00",
  timestamp: 1,
  sources: ["Source A", "Source B"],
  tags: ["存储"],
  categories: ["全球", "产业"],
  summary: "原始资讯摘要",
  impactedIndustries: ["存储"],
  impactedCompanies: [],
  holdingRelated: true,
  sentiment: "影响不明确",
  priority: 10,
};

describe("NewsCard", () => {
  it("renders an auditable source link and non-advisory impact state", () => {
    render(<NewsCard event={event} />);

    expect(screen.getByRole("heading", { name: event.title })).toBeInTheDocument();
    expect(screen.getByText("Source A · Source B")).toBeInTheDocument();
    expect(screen.getByText("与持仓标签相关")).toBeInTheDocument();
    expect(screen.getByText("影响不明确")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看原始来源" })).toHaveAttribute("href", event.url);
    expect(screen.queryByText(/建议买入|建议卖出/)).not.toBeInTheDocument();
  });
});
