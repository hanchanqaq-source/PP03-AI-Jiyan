import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import {
  NewsPipelineStatus,
  runNewsPipelineRefresh,
} from "@/features/market-news/NewsPipelineStatus";
import type {
  NewsPipelineStarted,
  NewsPipelineStatusData,
} from "@/features/market-news/types";

const started: NewsPipelineStarted = {
  run_id: "run-stage-1",
  raw_snapshot_id: "raw-stage-1",
  phase: "queued",
};

function pipelineStatus(
  phase: NewsPipelineStatusData["phase"],
  overrides: Partial<NewsPipelineStatusData> = {},
): NewsPipelineStatusData {
  return {
    loaded: true,
    run_id: started.run_id,
    raw_snapshot_id: started.raw_snapshot_id,
    evidence_snapshot_id: phase === "queued" || phase === "fetching" || phase === "raw_saved" || phase === "verifying" ? null : "evidence-stage-1",
    trusted_snapshot_id: phase === "trusted_published" ? "trusted-stage-1" : null,
    phase,
    counts: {
      raw_event_count: 8,
      verified_count: 3,
      corroborated_count: 2,
      pending_count: 1,
      conflicting_count: 1,
      corrected_count: 0,
      disproved_count: 1,
      failed_source_count: 4,
    },
    admitted_count: 5,
    has_pending_evidence_message: false,
    created_at: "2026-08-20T09:00:00+00:00",
    updated_at: "2026-08-20T09:00:01+00:00",
    redacted_error: null,
    recovery_status: "ready",
    recovery_error: null,
    compatibility_error: null,
    displayed_trusted_snapshot_id: "trusted-old",
    displayed_trusted: {
      snapshot_id: "trusted-old",
      published_at: "2026-08-19T09:00:00+00:00",
      event_count: 6,
    },
    ...overrides,
  };
}

describe("NewsPipelineStatus", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders every independent count and snapshot identity", () => {
    render(<NewsPipelineStatus status={pipelineStatus("evidence_saved")} />);

    const region = screen.getByRole("status", { name: "资讯流水线状态" });
    expect(region).toHaveTextContent("已抓取8");
    expect(region).toHaveTextContent("已核验3");
    expect(region).toHaveTextContent("多源印证2");
    expect(region).toHaveTextContent("待核验1");
    expect(region).toHaveTextContent("存在冲突1");
    expect(region).toHaveTextContent("已更正0");
    expect(region).toHaveTextContent("已证伪1");
    expect(region).toHaveTextContent("来源失败4");
    expect(region).toHaveTextContent("raw-stage-1");
    expect(region).toHaveTextContent("evidence-stage-1");
    expect(region).toHaveTextContent("trusted-old");
  });

  it("keeps the truthful previous-snapshot message during verification", () => {
    render(<NewsPipelineStatus status={pipelineStatus("verifying")} />);

    expect(screen.getByText("新资讯已抓取，核验处理中；当前显示上一份可信快照。")).toBeInTheDocument();
    expect(screen.getByText("确定性核验", { selector: "span" })).toHaveAttribute("aria-current", "step");
  });

  it("explains positive raw count with zero admitted events", () => {
    render(<NewsPipelineStatus status={pipelineStatus("trusted_published", {
      admitted_count: 0,
      has_pending_evidence_message: true,
      counts: {
        ...pipelineStatus("trusted_published").counts!,
        raw_event_count: 6,
        verified_count: 0,
        corroborated_count: 0,
      },
    })} />);

    expect(screen.getByText("本次已抓取 6 条资讯，目前尚无完成核验的内容。待核验资讯可在证据中心查看。")).toBeInTheDocument();
  });

  it("polls only nonterminal phases and stops at trusted publication", async () => {
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    const status = vi.spyOn(api, "newsPipelineStatus")
      .mockResolvedValueOnce(pipelineStatus("raw_saved"))
      .mockResolvedValueOnce(pipelineStatus("trusted_published"));
    const phases: Array<NewsPipelineStatusData["phase"]> = [];

    const terminal = await runNewsPipelineRefresh({
      query: undefined,
      pollIntervalMs: 0,
      onStatus: (next) => { phases.push(next.phase); },
    });

    expect(api.marketNewsRefresh).toHaveBeenCalledTimes(1);
    expect(status).toHaveBeenCalledTimes(2);
    expect(api.marketNewsRefresh).toHaveBeenCalledWith(undefined, expect.any(AbortSignal));
    expect(status).toHaveBeenNthCalledWith(1, started.run_id, expect.any(AbortSignal));
    expect(status).toHaveBeenNthCalledWith(2, started.run_id, expect.any(AbortSignal));
    expect(phases).toEqual(["raw_saved", "trusted_published"]);
    expect(terminal?.phase).toBe("trusted_published");
  });

  it("stops polling when the owning view aborts", async () => {
    const controller = new AbortController();
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    const status = vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(pipelineStatus("fetching"));

    const terminal = await runNewsPipelineRefresh({
      signal: controller.signal,
      pollIntervalMs: 0,
      onStatus: () => controller.abort(),
    });

    expect(terminal).toBeNull();
    expect(status).toHaveBeenCalledTimes(1);
  });

  it("suppresses a late status failure after the owning view aborts", async () => {
    const controller = new AbortController();
    let rejectStatus!: (reason: Error) => void;
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    const status = vi.spyOn(api, "newsPipelineStatus").mockImplementation(() => new Promise((_, reject) => { rejectStatus = reject; }));

    const result = runNewsPipelineRefresh({ signal: controller.signal, pollIntervalMs: 0 });
    await waitFor(() => expect(status).toHaveBeenCalledTimes(1));
    controller.abort();
    rejectStatus(new Error("late transport failure"));

    await expect(result).resolves.toBeNull();
  });

  it("adds the write-intent header to all three pipeline refresh clients", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({ data: started }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    await api.marketNewsRefresh();
    await api.evidenceRefresh();
    await api.radarRefresh();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.headers).toMatchObject({ "X-PP03-Write-Intent": "1" });
    }
  });

  it("threads the caller signal into kickoff and selected-run fetch", async () => {
    const controller = new AbortController();
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(pipelineStatus("trusted_published"));

    await runNewsPipelineRefresh({ signal: controller.signal, pollIntervalMs: 0 });

    expect(api.marketNewsRefresh).toHaveBeenCalledWith(undefined, controller.signal);
    expect(api.newsPipelineStatus).toHaveBeenCalledWith(started.run_id, controller.signal);
  });

  it("threads AbortSignal through the pipeline clients to actual fetch", async () => {
    const controller = new AbortController();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: started }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: pipelineStatus("trusted_published") }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));

    await api.radarRefresh(controller.signal);
    await api.newsPipelineStatus(started.run_id, controller.signal);

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/radar/refresh", expect.objectContaining({ signal: controller.signal }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, `/api/news/pipeline-status?run_id=${started.run_id}`, expect.objectContaining({ signal: controller.signal }));
  });

  it("rejects malformed successful kickoff and status payloads instead of rendering impossible state", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ data: {
      run_id: "",
      raw_snapshot_id: "raw-stage-1",
      phase: "queued",
    } }), { status: 202, headers: { "Content-Type": "application/json" } }));

    await expect(api.marketNewsRefresh()).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ data: {
      ...pipelineStatus("failed"),
      counts: { ...pipelineStatus("failed").counts, pending_count: -1 },
      redacted_error: "C:\\Users\\private\\token.txt",
    } }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(api.newsPipelineStatus(started.run_id)).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ data: {
      ...pipelineStatus("failed"),
      redacted_error: "C:\\Users\\private\\token.txt",
    } }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(api.newsPipelineStatus(started.run_id)).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });
  });

  it("maps only closed safe failure codes to Chinese user copy", () => {
    render(<NewsPipelineStatus status={pipelineStatus("failed", { redacted_error: "verification_failed" })} />);

    expect(screen.getByText("确定性核验失败；继续显示上一份可信快照。")).toBeInTheDocument();
    expect(screen.queryByText("verification_failed")).not.toBeInTheDocument();
  });
});
