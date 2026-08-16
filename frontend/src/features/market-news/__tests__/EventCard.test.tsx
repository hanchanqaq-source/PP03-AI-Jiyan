import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { EventCard } from "@/features/market-news/EventCard";
import { directEvent } from "./fixtures";

describe("MarketNews EventCard", () => {
  it("shows every required evidence summary and actions", async () => {
    const onOpenDetails = vi.fn();
    const user = userEvent.setup();
    render(<EventCard event={directEvent} onOpenDetails={onOpenDetails} />);

    expect(screen.getByRole("heading", { name: directEvent.title })).toBeInTheDocument();
    expect(screen.getByText(directEvent.summary)).toBeInTheDocument();
    expect(screen.getAllByText("直接持仓").length).toBeGreaterThan(0);
    expect(screen.getByText("半导体设备")).toBeInTheDocument();
    expect(screen.getByText(/东方人工智能主题混合C/)).toBeInTheDocument();
    expect(screen.getAllByText(/北方华创/).length).toBeGreaterThan(0);
    expect(screen.getByText("影响不明确")).toBeInTheDocument();
    expect(screen.getByText("高置信度")).toBeInTheDocument();
    expect(screen.getByText(/2 个公开来源/)).toBeInTheDocument();
    expect(screen.getAllByText(/10:35/).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: `查看事件详情 ${directEvent.title}` }));
    expect(onOpenDetails).toHaveBeenCalledWith(directEvent);
    const sourceLink = screen.getByRole("link", { name: `打开原始来源 ${directEvent.title}` });
    expect(sourceLink).toHaveAttribute("href", "https://one.example.test/a");
    expect(sourceLink).toHaveAttribute("target", "_blank");
    expect(sourceLink).toHaveAttribute("rel", "noreferrer");
  });

  it.each([
    ["industry_relation", "产业关联"],
    ["watch_tag", "关注标签"],
  ] as const)("renders %s as a distinct relationship", (relation_level, label) => {
    render(<EventCard event={{ ...directEvent, relation_level, related_funds: relation_level === "watch_tag" ? [] : directEvent.related_funds }} onOpenDetails={() => {}} />);
    expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    expect(screen.queryByText(relation_level === "industry_relation" ? "关注标签" : "产业关联")).not.toBeInTheDocument();
  });

  it("shows explicit AI and stale-cache degradation", () => {
    render(<EventCard event={{ ...directEvent, summary: "AI摘要暂不可用", summary_status: "ai_unavailable", data_status: "stale" }} onOpenDetails={() => {}} />);
    expect(screen.getByText("AI摘要暂不可用")).toBeInTheDocument();
    expect(screen.getByText("过期缓存")).toBeInTheDocument();
  });
});
