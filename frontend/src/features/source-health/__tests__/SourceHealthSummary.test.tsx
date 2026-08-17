import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DataInfoDialog } from "@/features/market-news/DataInfoDialog";
import type { SourceHealthSummaryData } from "@/features/source-health/types";
import { api, ApiError } from "@/lib/api";

const emptySummary: SourceHealthSummaryData = {
  rating_confidence: "initial", last_run_at: null,
  fund: { healthy: 0, usable: 0, degraded: 0, failed: 0 },
  news: { healthy: 0, usable: 0, degraded: 0, failed: 0 },
  total_sources: 0, reclaimable_bytes: 0,
};

const previousSummary: SourceHealthSummaryData = {
  rating_confidence: "initial", last_run_at: "2026-08-18T06:30:00+00:00",
  fund: { healthy: 8, usable: 2, degraded: 1, failed: 0 },
  news: { healthy: 100, usable: 2, degraded: 3, failed: 3 },
  total_sources: 119, reclaimable_bytes: 0,
};

const cacheStatus = { total_bytes: 0, file_count: 0, expired_count: 0, reclaimable_bytes: 0, categories: {}, last_auto_cleanup_at: null, limit_bytes: 0, over_limit_bytes: 0 };

function installHealthApi(summary: SourceHealthSummaryData = previousSummary) {
  (api as any).sourceHealthSummary = vi.fn().mockResolvedValue(summary);
  (api as any).sourceHealthStartFullRun = vi.fn();
  (api as any).sourceHealthRun = vi.fn();
  vi.spyOn(api, "cacheStatus").mockResolvedValue(cacheStatus);
}

describe("source health summary in DataInfoDialog", () => {
  beforeEach(() => { vi.restoreAllMocks(); installHealthApi(); });
  afterEach(() => vi.useRealTimers());

  it("shows a first-run action when no health report exists", async () => {
    installHealthApi(emptySummary);
    render(<DataInfoDialog open onClose={() => {}} onOpenSourceHealth={() => {}} />);
    expect(await screen.findByText("尚未完成数据源体检")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "运行首次全量体检" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "查看详情" })).not.toBeInTheDocument();
  });

  it("shows exact initial confidence and separate aggregate counts", async () => {
    const openDetails = vi.fn();
    const user = userEvent.setup();
    render(<DataInfoDialog open onClose={() => {}} onOpenSourceHealth={openDetails} />);
    expect(await screen.findByText("8 健康 / 2 基本可用 / 1 降级 / 0 失败")).toBeInTheDocument();
    expect(screen.getByText("100 健康 / 2 基本可用 / 3 降级 / 3 失败")).toBeInTheDocument();
    expect(screen.getByText(/评级状态：/)).toHaveTextContent("初始评级 · 样本不足");
    expect(screen.getByText(/最后体检：.*2026/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看详情" }));
    expect(openDetails).toHaveBeenCalledTimes(1);
  });

  it("starts once, polls every two seconds, reads progress, and refreshes the successful report", async () => {
    vi.useFakeTimers();
    const refreshed = { ...previousSummary, fund: { healthy: 9, usable: 1, degraded: 1, failed: 0 }, last_run_at: "2026-08-18T06:32:00+00:00" };
    const summary = (api as any).sourceHealthSummary.mockResolvedValueOnce(previousSummary).mockResolvedValueOnce(refreshed);
    const start = (api as any).sourceHealthStartFullRun.mockResolvedValue({ run_id: "run-1" });
    const run = (api as any).sourceHealthRun
      .mockResolvedValueOnce({ run_id: "run-1", scope: "full", status: "running", started_at: "2026-08-18T06:31:00Z", finished_at: null, total: 12, completed: 5, success: 5, partial: 0, failure: 0, current_source: "公开基金源" })
      .mockResolvedValueOnce({ run_id: "run-1", scope: "full", status: "completed", started_at: "2026-08-18T06:31:00Z", finished_at: "2026-08-18T06:32:00Z", total: 12, completed: 12, success: 11, partial: 1, failure: 0, current_source: "公开资讯源" });
    const audited = vi.fn();
    render(<DataInfoDialog open onClose={() => {}} onOpenSourceHealth={() => {}} onSourceHealthUpdated={audited} />);
    await act(async () => {});
    const button = screen.getByRole("button", { name: "运行全量体检" });
    fireEvent.click(button);
    fireEvent.click(button);
    await act(async () => {});
    expect(start).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(screen.getByText("体检中 · 0 / 0")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(run).toHaveBeenCalledTimes(1);
    expect(screen.getByText("体检中 · 5 / 12")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText("体检完成 · 12 / 12")).toBeInTheDocument();
    expect(screen.getByText("9 健康 / 1 基本可用 / 1 降级 / 0 失败")).toBeInTheDocument();
    expect(summary).toHaveBeenCalledTimes(2);
    expect(audited).toHaveBeenCalledTimes(1);
  });

  it("keeps the prior report when the backend returns 409", async () => {
    const user = userEvent.setup();
    (api as any).sourceHealthStartFullRun.mockRejectedValue(new ApiError("数据源体检正在运行", 409));
    render(<DataInfoDialog open onClose={() => {}} onOpenSourceHealth={() => {}} />);
    await screen.findByText("8 健康 / 2 基本可用 / 1 降级 / 0 失败");
    await user.click(screen.getByRole("button", { name: "运行全量体检" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("已有全量体检正在运行，当前成功报告保持不变");
    expect(screen.getByText("8 健康 / 2 基本可用 / 1 降级 / 0 失败")).toBeInTheDocument();
  });

  it("keeps the prior report and shows readable failure when a run fails", async () => {
    vi.useFakeTimers();
    (api as any).sourceHealthStartFullRun.mockResolvedValue({ run_id: "run-failed" });
    (api as any).sourceHealthRun.mockResolvedValue({ run_id: "run-failed", scope: "full", status: "failed", started_at: "2026-08-18T06:31:00Z", finished_at: "2026-08-18T06:32:00Z", total: 12, completed: 4, success: 3, partial: 0, failure: 1, current_source: "失败来源" });
    render(<DataInfoDialog open onClose={() => {}} onOpenSourceHealth={() => {}} />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "运行全量体检" }));
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByRole("alert")).toHaveTextContent("体检失败，已保留上一次成功报告");
    expect(screen.getByText("8 健康 / 2 基本可用 / 1 降级 / 0 失败")).toBeInTheDocument();
  });
});
