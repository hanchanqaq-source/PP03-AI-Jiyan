import { StrictMode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
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
  return {
    loaded: true,
    run_id: started.run_id,
    raw_snapshot_id: started.raw_snapshot_id,
    evidence_snapshot_id: phase === "trusted_published" ? "radar-evidence" : null,
    trusted_snapshot_id: phase === "trusted_published" ? "radar-trusted" : null,
    phase,
    counts: { raw_event_count: 2, verified_count: 1, corroborated_count: 0, pending_count: 1, conflicting_count: 0, corrected_count: 0, disproved_count: 0, failed_source_count: 0 },
    admitted_count: 1,
    has_pending_evidence_message: false,
    created_at: "2026-08-20T09:00:00+00:00",
    updated_at: "2026-08-20T09:00:01+00:00",
    redacted_error: null,
    recovery_status: "ready",
    recovery_error: null,
    compatibility_error: null,
    displayed_trusted_snapshot_id: "radar-old",
    displayed_trusted: null,
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
});
