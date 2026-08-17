import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { DataInfoDialog } from "@/features/market-news/DataInfoDialog";
import { api } from "@/lib/api";


const cacheStatus = {
  total_bytes: 2 * 1024 * 1024,
  file_count: 8,
  expired_count: 2,
  reclaimable_bytes: 512 * 1024,
  categories: {
    translations: { bytes: 1024 * 1024, file_count: 1, expired_count: 1, reclaimable_bytes: 256 * 1024, pinned_count: 0 },
    fund_180d: { bytes: 1024 * 1024, file_count: 7, expired_count: 1, reclaimable_bytes: 256 * 1024, pinned_count: 1 },
  },
  last_auto_cleanup_at: "2026-08-17T04:00:00+00:00",
  limit_bytes: 500 * 1024 * 1024,
  over_limit_bytes: 0,
};


describe("MarketNews DataInfoDialog cache management", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("loads cache occupancy only while open and cleans expired data without a clear-all action", async () => {
    const user = userEvent.setup();
    const status = vi.spyOn(api as any, "cacheStatus").mockResolvedValue(cacheStatus);
    let resolveCleanup!: (value: unknown) => void;
    const cleanup = vi.spyOn(api as any, "cacheCleanupExpired").mockImplementation(
      () => new Promise((resolve) => { resolveCleanup = resolve; }),
    );

    render(<DataInfoDialog open onClose={() => {}} />);

    expect(await screen.findByText("当前占用：2.00 MB")).toBeInTheDocument();
    expect(screen.getByText(/上次清理：.*2026/)).toBeInTheDocument();
    expect(screen.getByText("可回收：512.00 KB")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /清空/ })).not.toBeInTheDocument();

    const button = screen.getByRole("button", { name: "清理过期缓存" });
    await user.click(button);
    expect(button).toBeDisabled();
    expect(cleanup).toHaveBeenCalledTimes(1);
    resolveCleanup({ manual: true, released_bytes: 512 * 1024, deleted_categories: ["translations"], status: { ...cacheStatus, reclaimable_bytes: 0 } });

    expect(await screen.findByText("本次释放：512.00 KB")).toBeInTheDocument();
    expect(status).toHaveBeenCalledTimes(1);
  });

  it("keeps the dialog usable when cache status cannot be loaded", async () => {
    vi.spyOn(api as any, "cacheStatus").mockRejectedValue(new Error("offline"));
    vi.spyOn(api as any, "cacheCleanupExpired").mockRejectedValue(new Error("offline"));

    render(<DataInfoDialog open onClose={() => {}} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("缓存状态暂不可用");
    expect(screen.getByRole("button", { name: "关闭数据说明" })).toBeEnabled();
  });
});

