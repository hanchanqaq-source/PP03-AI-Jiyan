import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type RadarData } from "@/lib/api";
import type { NewsPipelineStatusData } from "@/features/market-news/types";
import { Intel } from "@/pages/Intel";

const oldRadar: RadarData = {
  generated_at: "2026-08-19 09:00",
  recent_days: 7,
  stats: { industries: 1, total_sources: 108 },
  industries: [{ key: "ai", name: "人工智能", accent: "#22d3ee", total: 1, items: [{
    title: "上一份可信雷达资讯",
    url: "https://example.test/old",
    time: "08-19 09:00",
    source: "公开源",
  }] }],
};

const newRadar: RadarData = {
  ...oldRadar,
  generated_at: "2026-08-20 09:00",
  industries: [{ ...oldRadar.industries[0], items: [{
    title: "新可信雷达资讯",
    url: "https://example.test/new",
    time: "08-20 09:00",
    source: "公开源",
  }] }],
};

const started = { run_id: "radar-run", raw_snapshot_id: "radar-raw", phase: "queued" as const };

function status(phase: NewsPipelineStatusData["phase"]): NewsPipelineStatusData {
  const hasRaw = phase !== "queued" && phase !== "fetching";
  const hasEvidence = phase === "evidence_saved" || phase === "trusted_published";
  const published = phase === "trusted_published";
  return {
    loaded: true,
    run_id: started.run_id,
    raw_snapshot_id: started.raw_snapshot_id,
    evidence_snapshot_id: hasEvidence ? "radar-evidence" : null,
    trusted_snapshot_id: published ? started.raw_snapshot_id : null,
    phase,
    counts: { raw_event_count: hasRaw ? 2 : 0, verified_count: hasEvidence ? 1 : 0, corroborated_count: 0, pending_count: hasEvidence ? 1 : 0, conflicting_count: 0, corrected_count: 0, disproved_count: 0, failed_source_count: 0 },
    admitted_count: hasEvidence ? 1 : 0,
    has_pending_evidence_message: false,
    created_at: "2026-08-20T09:00:00+00:00",
    updated_at: "2026-08-20T09:00:01+00:00",
    redacted_error: null,
    recovery_status: "ready",
    recovery_error: null,
    compatibility_error: null,
    displayed_trusted_snapshot_id: published ? started.raw_snapshot_id : "radar-old",
    displayed_trusted: {
      snapshot_id: published ? started.raw_snapshot_id : "radar-old",
      published_at: "2026-08-19T09:00:00+00:00",
      event_count: published ? 1 : 2,
    },
  };
}

describe("legacy Intel investment-news pipeline compatibility", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("keeps old radar data in StrictMode and performs one terminal radar GET", async () => {
    const user = userEvent.setup();
    const radar = vi.spyOn(api, "radar").mockResolvedValue(oldRadar);
    vi.spyOn(api, "radarRefresh").mockResolvedValue(started);
    let resolveTerminal!: (value: NewsPipelineStatusData) => void;
    vi.spyOn(api, "newsPipelineStatus")
      .mockResolvedValueOnce(status("verifying"))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveTerminal = resolve; }));
    render(<StrictMode><Intel /></StrictMode>);
    expect(await screen.findByText("上一份可信雷达资讯")).toBeInTheDocument();
    const initialReads = radar.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(api.newsPipelineStatus).toHaveBeenCalledTimes(2));
    expect(screen.getByText("上一份可信雷达资讯")).toBeInTheDocument();
    expect(screen.getByText("新资讯已抓取，核验处理中；当前显示上一份可信快照。")).toBeInTheDocument();

    radar.mockResolvedValue(newRadar);
    resolveTerminal(status("trusted_published"));
    expect(await screen.findByText("新可信雷达资讯")).toBeInTheDocument();
    expect(api.radarRefresh).toHaveBeenCalledTimes(1);
    expect(radar).toHaveBeenCalledTimes(initialReads + 1);
  });

  it("aborts an in-flight kickoff when the Intel view unmounts", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "radar").mockResolvedValue(oldRadar);
    let kickoffSignal: AbortSignal | undefined;
    vi.spyOn(api, "radarRefresh").mockImplementation((signal?: AbortSignal) => {
      kickoffSignal = signal;
      return new Promise(() => undefined);
    });
    const view = render(<Intel />);
    await screen.findByText("上一份可信雷达资讯");

    await user.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(kickoffSignal).toBeInstanceOf(AbortSignal));
    view.unmount();

    expect(kickoffSignal?.aborted).toBe(true);
  });

  it("does not let a late initial GET overwrite the terminal trusted GET", async () => {
    const user = userEvent.setup();
    let resolveInitial!: (value: RadarData) => void;
    vi.spyOn(api, "radar")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveInitial = resolve; }))
      .mockResolvedValueOnce(newRadar);
    vi.spyOn(api, "radarRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("trusted_published"));
    render(<Intel />);

    await user.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByText("新可信雷达资讯")).toBeInTheDocument();
    await act(async () => resolveInitial(oldRadar));

    expect(screen.queryByText("上一份可信雷达资讯")).not.toBeInTheDocument();
    expect(screen.getByText("新可信雷达资讯")).toBeInTheDocument();
  });

  it("distinguishes terminal trusted-read failure and announces it", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "radar")
      .mockResolvedValueOnce(oldRadar)
      .mockRejectedValueOnce(new Error("terminal read failed"));
    vi.spyOn(api, "radarRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("trusted_published"));
    render(<Intel />);
    await screen.findByText("上一份可信雷达资讯");

    await user.click(screen.getByRole("button", { name: "刷新" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveAttribute("aria-live", "assertive");
    expect(alert).toHaveTextContent("可信资讯已发布，但最新资讯读取失败；继续显示上一份可信快照。");
    expect(alert).not.toHaveTextContent("流水线状态连接失败");
    expect(screen.getByText("上一份可信雷达资讯")).toBeInTheDocument();
  });

  it("starts only one refresh when the button is clicked twice before rerender", async () => {
    vi.spyOn(api, "radar").mockResolvedValue(oldRadar);
    const kickoffResolvers: Array<(value: typeof started) => void> = [];
    const kickoff = vi.spyOn(api, "radarRefresh").mockImplementation(() => new Promise((resolve) => { kickoffResolvers.push(resolve); }));
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("trusted_published"));
    render(<Intel />);
    await screen.findByText("上一份可信雷达资讯");
    const button = screen.getByRole("button", { name: "刷新" });

    act(() => {
      button.click();
      button.click();
    });

    expect(kickoff).toHaveBeenCalledTimes(1);
    await act(async () => kickoffResolvers.forEach((resolve) => resolve(started)));
  });

  it("keeps the previous radar and labels a nonfatal compatibility warning", async () => {
    const user = userEvent.setup();
    const radar = vi.spyOn(api, "radar").mockResolvedValue(oldRadar);
    vi.spyOn(api, "radarRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue({
      ...status("trusted_published"),
      redacted_error: "radar_compatibility_failed",
      compatibility_error: "radar_compatibility_failed",
    });
    render(<Intel />);
    await screen.findByText("上一份可信雷达资讯");
    const initialReads = radar.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "刷新" }));

    expect(await screen.findByText("资讯雷达兼容更新未完成；可信资讯快照仍可使用。")).toBeInTheDocument();
    expect(screen.getByText("上一份可信雷达资讯")).toBeInTheDocument();
    expect(radar).toHaveBeenCalledTimes(initialReads);
  });
});
