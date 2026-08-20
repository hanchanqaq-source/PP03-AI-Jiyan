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
  const hasRaw = phase !== "queued" && phase !== "fetching";
  const hasEvidence = phase === "evidence_saved" || phase === "trusted_published";
  const published = phase === "trusted_published";
  return {
    loaded: true,
    run_id: started.run_id,
    raw_snapshot_id: started.raw_snapshot_id,
    evidence_snapshot_id: hasEvidence ? "evidence-stage-1" : null,
    trusted_snapshot_id: published ? started.raw_snapshot_id : null,
    phase,
    counts: {
      raw_event_count: hasRaw ? 8 : 0,
      verified_count: hasEvidence ? 3 : 0,
      corroborated_count: hasEvidence ? 2 : 0,
      pending_count: hasEvidence ? 1 : 0,
      conflicting_count: hasEvidence ? 1 : 0,
      corrected_count: 0,
      disproved_count: hasEvidence ? 1 : 0,
      failed_source_count: hasRaw ? 4 : 0,
    },
    admitted_count: hasEvidence ? 5 : 0,
    has_pending_evidence_message: false,
    created_at: "2026-08-20T09:00:00+00:00",
    updated_at: "2026-08-20T09:00:01+00:00",
    redacted_error: null,
    recovery_status: "ready",
    recovery_error: null,
    compatibility_error: null,
    displayed_trusted_snapshot_id: published ? started.raw_snapshot_id : "trusted-old",
    displayed_trusted: {
      snapshot_id: published ? started.raw_snapshot_id : "trusted-old",
      published_at: "2026-08-19T09:00:00+00:00",
      event_count: published ? 5 : 6,
    },
    ...overrides,
  };
}

describe("NewsPipelineStatus", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders every independent count and snapshot identity", () => {
    render(<NewsPipelineStatus status={pipelineStatus("evidence_saved")} />);

    const region = screen.getByRole("region", { name: "资讯流水线状态" });
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

  it("uses a distinct first-run message when verification has no previous trusted snapshot", () => {
    render(<NewsPipelineStatus status={pipelineStatus("verifying", {
      displayed_trusted_snapshot_id: null,
      displayed_trusted: null,
    })} />);

    expect(screen.getByRole("status")).toHaveTextContent("当前尚无可显示的可信快照");
    expect(screen.queryByText(/当前显示上一份可信快照/)).not.toBeInTheDocument();
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

  it.each([
    ["phase regression", pipelineStatus("raw_saved", { updated_at: "2026-08-20T09:00:02+00:00" })],
    ["count regression", pipelineStatus("evidence_saved", {
      counts: { ...pipelineStatus("evidence_saved").counts!, raw_event_count: 7, pending_count: 0 },
      admitted_count: 5,
    })],
    ["evidence identity mutation", pipelineStatus("evidence_saved", { evidence_snapshot_id: "evidence-stage-other" })],
    ["displayed pointer mutation before publication", pipelineStatus("verifying", {
      displayed_trusted_snapshot_id: "trusted-other",
      displayed_trusted: { snapshot_id: "trusted-other", published_at: "2026-08-20T09:00:02+00:00", event_count: 6 },
    })],
  ])("rejects poll sequence %s before notifying consumers", async (_label, invalidNext) => {
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus")
      .mockResolvedValueOnce(pipelineStatus("evidence_saved"))
      .mockResolvedValueOnce(invalidNext);
    const onStatus = vi.fn();

    await expect(runNewsPipelineRefresh({ pollIntervalMs: 0, onStatus })).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });
    expect(onStatus).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["materialized raw counts increase", pipelineStatus("verifying", {
      counts: { ...pipelineStatus("verifying").counts!, raw_event_count: 9 },
    })],
    ["materialized raw failure count decreases", pipelineStatus("verifying", {
      counts: { ...pipelineStatus("verifying").counts!, failed_source_count: 3 },
    })],
    ["evidence facts are recomputed", pipelineStatus("trusted_published", {
      counts: {
        raw_event_count: 9,
        verified_count: 4,
        corroborated_count: 2,
        pending_count: 1,
        conflicting_count: 1,
        corrected_count: 0,
        disproved_count: 1,
        failed_source_count: 4,
      },
      admitted_count: 6,
      displayed_trusted: {
        snapshot_id: started.raw_snapshot_id,
        published_at: "2026-08-20T09:00:02+00:00",
        event_count: 6,
      },
    })],
    ["displayed snapshot metadata mutates", pipelineStatus("verifying", {
      displayed_trusted: {
        snapshot_id: "trusted-old",
        published_at: "2026-08-20T09:00:02+00:00",
        event_count: 7,
      },
    })],
  ])("freezes materialized poll facts: %s", async (_label, invalidNext) => {
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus")
      .mockResolvedValueOnce(pipelineStatus("raw_saved"))
      .mockResolvedValueOnce(invalidNext);
    const onStatus = vi.fn();

    await expect(runNewsPipelineRefresh({ pollIntervalMs: 0, onStatus })).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });
    expect(onStatus).toHaveBeenCalledTimes(1);
  });

  it("rejects a selected status from another run before notifying consumers", async () => {
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(pipelineStatus("trusted_published", {
      run_id: "run-other",
      raw_snapshot_id: "raw-other",
      trusted_snapshot_id: "raw-other",
      displayed_trusted_snapshot_id: "raw-other",
      displayed_trusted: {
        snapshot_id: "raw-other",
        published_at: "2026-08-20T09:00:01+00:00",
        event_count: 5,
      },
    }));
    const onStatus = vi.fn();

    await expect(runNewsPipelineRefresh({ pollIntervalMs: 0, onStatus })).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });
    expect(onStatus).not.toHaveBeenCalled();
    expect(api.newsPipelineStatus).toHaveBeenCalledTimes(1);
  });

  it("rejects a selected status that changes the kickoff raw snapshot", async () => {
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(pipelineStatus("trusted_published", {
      raw_snapshot_id: "raw-other",
      trusted_snapshot_id: "raw-other",
      displayed_trusted_snapshot_id: "raw-other",
      displayed_trusted: {
        snapshot_id: "raw-other",
        published_at: "2026-08-20T09:00:01+00:00",
        event_count: 5,
      },
    }));

    await expect(runNewsPipelineRefresh({ pollIntervalMs: 0 })).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });
    expect(api.newsPipelineStatus).toHaveBeenCalledTimes(1);
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
    const terminal = pipelineStatus("trusted_published");
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: started }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { ...terminal, ...terminal.counts } }), {
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

    const failed = pipelineStatus("failed", { redacted_error: "verification_failed" });
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ data: {
      ...failed,
      ...failed.counts,
      counts: { ...failed.counts, pending_count: -1 },
      redacted_error: "C:\\Users\\private\\token.txt",
    } }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(api.newsPipelineStatus(started.run_id)).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ data: {
      ...failed,
      ...failed.counts,
      redacted_error: "C:\\Users\\private\\token.txt",
    } }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(api.newsPipelineStatus(started.run_id)).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });
  });

  it.each([
    ["missing flat count", (value: Record<string, unknown>) => { delete value.pending_count; }],
    ["queued with evidence", (value: Record<string, unknown>) => {
      value.phase = "queued";
      value.trusted_snapshot_id = null;
      value.displayed_trusted_snapshot_id = "trusted-old";
      value.displayed_trusted = { snapshot_id: "trusted-old", published_at: "2026-08-19T09:00:00+00:00", event_count: 6 };
    }],
    ["published without trusted lineage", (value: Record<string, unknown>) => { value.trusted_snapshot_id = "trusted-other"; }],
    ["displayed identity mismatch", (value: Record<string, unknown>) => { value.displayed_trusted_snapshot_id = "trusted-other"; }],
    ["success with failure code", (value: Record<string, unknown>) => { value.redacted_error = "verification_failed"; }],
  ])("rejects impossible loaded status: %s", async (_label, mutate) => {
    const base = pipelineStatus("trusted_published");
    const payload: Record<string, unknown> = { ...base, ...base.counts };
    mutate(payload);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.newsPipelineStatus(started.run_id)).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });
  });

  it("accepts an older selected terminal run whose displayed snapshot is the newer current pointer", async () => {
    const selected = pipelineStatus("trusted_published", {
      displayed_trusted_snapshot_id: "raw-current-newer",
      displayed_trusted: {
        snapshot_id: "raw-current-newer",
        published_at: "2026-08-20T10:00:00+00:00",
        event_count: 9,
      },
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: {
      ...selected,
      ...selected.counts,
    } }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(api.newsPipelineStatus(started.run_id)).resolves.toMatchObject({
      run_id: started.run_id,
      raw_snapshot_id: started.raw_snapshot_id,
      displayed_trusted_snapshot_id: "raw-current-newer",
    });
  });

  it("rejects a kickoff terminal poll unless that run became the displayed trusted snapshot", async () => {
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(pipelineStatus("trusted_published", {
      displayed_trusted_snapshot_id: "trusted-old",
      displayed_trusted: {
        snapshot_id: "trusted-old",
        published_at: "2026-08-19T09:00:00+00:00",
        event_count: 6,
      },
    }));
    const onStatus = vi.fn();

    await expect(runNewsPipelineRefresh({ pollIntervalMs: 0, onStatus })).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });
    expect(onStatus).not.toHaveBeenCalled();
  });

  it("maps only closed safe failure codes to Chinese user copy", () => {
    render(<NewsPipelineStatus status={pipelineStatus("failed", { redacted_error: "verification_failed" })} />);

    expect(screen.getByText("确定性核验失败；继续显示上一份可信快照。")).toBeInTheDocument();
    expect(screen.queryByText("verification_failed")).not.toBeInTheDocument();
  });

  it("accepts the producer's raw-saved storage failure without inventing evidence", async () => {
    const failed = pipelineStatus("failed", {
      evidence_snapshot_id: null,
      trusted_snapshot_id: null,
      redacted_error: "storage_error",
      admitted_count: 0,
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: {
      ...failed,
      ...failed.counts,
    } }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(api.newsPipelineStatus(started.run_id)).resolves.toMatchObject({
      phase: "failed",
      redacted_error: "storage_error",
      counts: { raw_event_count: 8, failed_source_count: 4 },
      evidence_snapshot_id: null,
    });
  });

  it("renders radar compatibility failure as a nonfatal trusted warning", () => {
    render(<NewsPipelineStatus status={pipelineStatus("trusted_published", {
      redacted_error: "radar_compatibility_failed",
      compatibility_error: "radar_compatibility_failed",
    })} />);

    expect(screen.getByText("资讯雷达兼容更新未完成；可信资讯快照仍可使用。")).toBeInTheDocument();
    expect(screen.queryByText("运行失败")).not.toBeInTheDocument();
  });

  it("labels the backend displayed pointer as current and the selected run identity separately", () => {
    render(<NewsPipelineStatus status={pipelineStatus("trusted_published", {
      displayed_trusted_snapshot_id: "raw-current-newer",
      displayed_trusted: {
        snapshot_id: "raw-current-newer",
        published_at: "2026-08-20T10:00:00+00:00",
        event_count: 9,
      },
    })} />);

    const region = screen.getByRole("region", { name: "资讯流水线状态" });
    expect(region).toHaveTextContent("本轮可信快照：raw-stage-1");
    expect(region).toHaveTextContent("当前可信快照：raw-current-newer");
  });

  it("keeps frequently changing counts and IDs out of the live region", () => {
    render(<NewsPipelineStatus status={pipelineStatus("evidence_saved")} />);

    const panel = screen.getByRole("region", { name: "资讯流水线状态" });
    const live = screen.getByRole("status");
    expect(panel).toHaveTextContent("已抓取8");
    expect(live).toHaveTextContent("核验处理中");
    expect(live).not.toHaveTextContent("已抓取8");
    expect(live).not.toHaveTextContent("raw-stage-1");
  });

  it("announces failed and interrupted copy as an alert", () => {
    render(<NewsPipelineStatus status={pipelineStatus("failed", { redacted_error: "verification_failed" })} />);

    expect(screen.getByRole("alert")).toHaveTextContent("确定性核验失败");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it.each([
    ["collection_failed", {
      redacted_error: "collection_failed",
      counts: { ...pipelineStatus("failed").counts!, raw_event_count: 1 },
    }],
    ["verification_failed with evidence", {
      redacted_error: "verification_failed",
      evidence_snapshot_id: "evidence-illegal",
      counts: pipelineStatus("evidence_saved").counts,
      admitted_count: pipelineStatus("evidence_saved").admitted_count,
    }],
    ["evidence_persistence_failed with evidence totals", {
      redacted_error: "evidence_persistence_failed",
      counts: pipelineStatus("evidence_saved").counts,
      admitted_count: pipelineStatus("evidence_saved").admitted_count,
    }],
    ["publication_failed without evidence", {
      redacted_error: "publication_failed",
      evidence_snapshot_id: null,
    }],
  ])("rejects impossible failure durability: %s", async (_label, overrides) => {
    const failed = pipelineStatus("failed", overrides as Partial<NewsPipelineStatusData>);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: {
      ...failed,
      ...failed.counts,
    } }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await expect(api.newsPipelineStatus(started.run_id)).rejects.toMatchObject({
      message: "资讯流水线响应无效",
      status: 502,
    });
  });
});
