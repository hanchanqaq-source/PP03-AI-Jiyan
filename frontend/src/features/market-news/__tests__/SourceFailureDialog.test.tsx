import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { SourceFailureDialog } from "@/features/market-news/SourceFailureDialog";
import type { MarketNewsSourceStatus } from "@/features/market-news/types";


const failures: MarketNewsSourceStatus[] = [
  {
    source_id: "0123456789abcdef",
    source_name: "全球科技公开源",
    source_url: "https://feed.example.test/rss",
    status: "failed",
    error_type: "timeout",
    error_reason: "来源请求超时",
    last_success_at: "2026-08-16T10:35:00+08:00",
    used_cached_items: true,
    item_count: 3,
  },
  {
    source_id: "fedcba9876543210",
    source_name: "政策公开源",
    source_url: "https://policy.example.test/rss",
    status: "failed",
    error_type: "http_status",
    error_reason: "来源返回 HTTP 503",
    last_success_at: null,
    used_cached_items: false,
    item_count: 0,
  },
];


describe("MarketNews SourceFailureDialog", () => {
  it("shows auditable sanitized failure details and public source links", () => {
    render(<SourceFailureDialog open statuses={failures} onClose={() => {}} onRetry={async () => {}} />);

    expect(screen.getByRole("dialog", { name: "失败来源详情" })).toBeInTheDocument();
    expect(screen.getByText("全球科技公开源")).toBeInTheDocument();
    expect(screen.getByText("超时")).toBeInTheDocument();
    expect(screen.getByText("来源请求超时")).toBeInTheDocument();
    expect(screen.getByText(/最后成功.*2026/)).toBeInTheDocument();
    expect(screen.getByText("使用旧缓存：是")).toBeInTheDocument();
    expect(screen.getByText("本次返回：3 条")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看来源 全球科技公开源" })).toHaveAttribute("href", "https://feed.example.test/rss");
    expect(screen.getByText("HTTP 状态")).toBeInTheDocument();
    expect(screen.getByText("最后成功：暂无记录")).toBeInTheDocument();
  });

  it("retries only the selected configured source and reports a bounded failure", async () => {
    const user = userEvent.setup();
    let rejectRetry!: (reason: Error) => void;
    const retry = vi.fn(() => new Promise<void>((_, reject) => { rejectRetry = reject; }));
    render(<SourceFailureDialog open statuses={failures} onClose={() => {}} onRetry={retry} />);

    const first = screen.getByRole("button", { name: "重试来源 全球科技公开源" });
    const second = screen.getByRole("button", { name: "重试来源 政策公开源" });
    await user.click(first);

    expect(retry).toHaveBeenCalledWith("0123456789abcdef");
    expect(first).toBeDisabled();
    expect(second).not.toBeDisabled();
    rejectRetry(new Error("secret raw error"));
    expect(await screen.findByRole("alert")).toHaveTextContent("该来源重试失败，请稍后再试。");
  });
});

