import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { EventCard } from "@/features/market-news/EventCard";
import { directEvent, translatedEnglishEvent } from "./fixtures";

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

  it("defaults to Chinese translation and lets the user switch to the immutable original", async () => {
    const user = userEvent.setup();
    render(<EventCard event={translatedEnglishEvent} onOpenDetails={() => {}} />);

    expect(screen.getByRole("heading", { name: "美光（Micron）发布 HBM3E" })).toBeInTheDocument();
    expect(screen.getByText("本季度开始出货。")).toBeInTheDocument();
    expect(screen.queryByText("Shipments begin this quarter.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "查看原文" }));
    expect(screen.getByRole("heading", { name: "Micron launches HBM3E" })).toBeInTheDocument();
    expect(screen.getByText("Shipments begin this quarter.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "中文" }));
    expect(screen.getByRole("heading", { name: "美光（Micron）发布 HBM3E" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开原始来源/ })).toHaveAttribute("href", "https://news.example.test/micron-hbm3e");
  });

  it("keeps the original readable when Chinese translation is unavailable", () => {
    render(<EventCard event={{
      ...translatedEnglishEvent,
      translated_title_zh: undefined,
      translated_summary_zh: undefined,
      translation_status: "unavailable",
    }} onOpenDetails={() => {}} />);

    expect(screen.getByRole("heading", { name: "Micron launches HBM3E" })).toBeInTheDocument();
    expect(screen.getByText("中文翻译暂不可用")).toBeInTheDocument();
  });

  it("does not render a credential-bearing original URL even when passed directly to the card", () => {
    const unsafeUrl = "https://public.example.org/article?access-key=secret";
    render(<EventCard event={{
      ...directEvent,
      original_links: [unsafeUrl],
      sources: directEvent.sources.map((source) => ({ ...source, original_url: null })),
    }} onOpenDetails={() => {}} />);

    expect(screen.queryByRole("link", { name: `打开原始来源 ${directEvent.title}` })).not.toBeInTheDocument();
    expect(document.querySelector(`a[href="${unsafeUrl}"]`)).not.toBeInTheDocument();
  });

  it("does not render a multiply encoded credential key passed directly to the card", () => {
    const unsafeUrl = "https://public.example.org/article?%2574oken=secret";
    render(<EventCard event={{
      ...directEvent,
      original_links: [unsafeUrl],
      sources: directEvent.sources.map((source) => ({ ...source, original_url: null })),
    }} onOpenDetails={() => {}} />);

    expect(screen.queryByRole("link", { name: `打开原始来源 ${directEvent.title}` })).not.toBeInTheDocument();
    expect(document.querySelector(`a[href="${unsafeUrl}"]`)).not.toBeInTheDocument();
  });

  it("does not render a lowercase compound secret assignment passed directly to the card", () => {
    const unsafeUrl = "https://public.example.org/article?note=public%20(mytoken%3Dsecret)";
    render(<EventCard event={{
      ...directEvent,
      original_links: [unsafeUrl],
      sources: directEvent.sources.map((source) => ({ ...source, original_url: null })),
    }} onOpenDetails={() => {}} />);

    expect(screen.queryByRole("link", { name: `打开原始来源 ${directEvent.title}` })).not.toBeInTheDocument();
    expect(document.querySelector(`a[href="${unsafeUrl}"]`)).not.toBeInTheDocument();
  });

  it("only offers evidence navigation for a trusted backend verification", async () => {
    const user = userEvent.setup();
    const onViewEvidence = vi.fn();
    const unverified = { ...directEvent, verification_status: "unverified" as const };
    const { rerender } = render(<EventCard event={unverified} onOpenDetails={() => {}} onViewEvidence={onViewEvidence} />);

    expect(screen.queryByText("已核验")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看证据" })).not.toBeInTheDocument();

    const verified = { ...directEvent, verification_status: "verified" as const, verification_reason: "已有明确官方证据", verified_at: "2026-08-18T08:30:00+00:00", verified_key_fields: [] };
    rerender(<EventCard event={verified} onOpenDetails={() => {}} onViewEvidence={onViewEvidence} />);
    expect(screen.getByText("已核验")).toBeInTheDocument();
    expect(screen.queryByText(/前端演示 Fixture/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "查看证据" }));
    expect(onViewEvidence).toHaveBeenCalledWith(verified);
  });
});
