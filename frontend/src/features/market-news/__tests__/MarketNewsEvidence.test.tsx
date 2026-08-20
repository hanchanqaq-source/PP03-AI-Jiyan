import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { MarketNews } from "@/pages/MarketNews";
import type { NewsPipelineStatusData } from "@/features/market-news/types";
import { directEvent, marketNewsResponse } from "./fixtures";

const verified = { ...directEvent, event_id: "11111111111111111111", title: "交易所公告：星河科技建设存储算力中心", summary: "两个相互独立的来源链提供了一致证据。", verification_status: "verified" as const, verification_reason: "两个相互独立的来源链提供了一致证据。", verified_at: "2026-08-18T08:30:00+00:00", verified_key_fields: [] };
const unverified = { ...directEvent, event_id: "22222222222222222222", title: "未核验金额 12亿元", summary: "未经核验的 12亿元", verification_status: "unverified" as const };
const started = { run_id: "run-market-news", raw_snapshot_id: "raw-market-news", phase: "queued" as const };

function status(phase: NewsPipelineStatusData["phase"], raw = 6, admitted = 2): NewsPipelineStatusData {
  const hasRaw = phase !== "queued" && phase !== "fetching";
  const hasEvidence = phase === "evidence_saved" || phase === "trusted_published";
  const published = phase === "trusted_published";
  const displayedTrustedSnapshotId = published ? started.raw_snapshot_id : "trusted-previous";
  return {
    loaded: true, run_id: started.run_id, raw_snapshot_id: started.raw_snapshot_id,
    evidence_snapshot_id: hasEvidence ? "evidence-market-news" : null,
    trusted_snapshot_id: published ? started.raw_snapshot_id : null,
    phase,
    counts: { raw_event_count: hasRaw ? raw : 0, verified_count: hasEvidence ? admitted : 0, corroborated_count: 0, pending_count: hasEvidence ? raw - admitted : 0, conflicting_count: 0, corrected_count: 0, disproved_count: 0, failed_source_count: hasRaw ? 1 : 0 },
    admitted_count: hasEvidence ? admitted : 0,
    has_pending_evidence_message: published && raw > 0 && admitted === 0,
    created_at: "2026-08-20T09:00:00+00:00", updated_at: "2026-08-20T09:00:01+00:00",
    redacted_error: null, recovery_status: "ready", recovery_error: null, compatibility_error: null,
    displayed_trusted_snapshot_id: displayedTrustedSnapshotId,
    displayed_trusted: { snapshot_id: displayedTrustedSnapshotId, published_at: "2026-08-20T09:00:01+00:00", event_count: published ? admitted : 1 },
  };
}

describe("MarketNews trusted evidence admission", () => {
  beforeEach(() => { localStorage.clear(); localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: ["semiconductor"], activeId: "semiconductor" })); vi.restoreAllMocks(); window.history.replaceState({}, "", "/market-news"); });

  it("defensively renders only trusted backend events and links to their evidence", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [verified, unverified], focus_events: [verified, unverified], evidence_snapshot_id: "e".repeat(20), filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
    render(<MarketNews />);
    const card = await screen.findByRole("article", { name: `${verified.title}事件卡` });
    expect(within(card).getByText("已核验")).toBeInTheDocument(); expect(within(card).getByText("两个相互独立的来源链提供了一致证据。")).toBeInTheDocument(); expect(card).not.toHaveTextContent("12亿元"); expect(screen.queryByText("未核验金额 12亿元")).not.toBeInTheDocument(); expect(screen.queryByText(/origin cluster/i)).not.toBeInTheDocument(); expect(screen.queryByText(/前端演示 Fixture/)).not.toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "查看证据" }));
    expect(window.location.pathname).toBe("/evidence-center"); expect(window.location.search).toBe("?event_id=11111111111111111111");
  });

  it("uses the backend trusted-empty explanation without fixture fallback", async () => {
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [], focus_events: [], evidence_snapshot_id: "e".repeat(20), empty_reason: "no_events", empty_message: "当前筛选无已核验或多源印证的资讯。", filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
    render(<MarketNews />);
    expect(await screen.findByText("当前筛选无已核验或多源印证的资讯。")).toBeInTheDocument(); expect(screen.queryByText(/星河科技发布算力中心建设公告/)).not.toBeInTheDocument();
  });

  it("keeps the previous trusted snapshot while verification runs", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [verified], focus_events: [verified], filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("verifying"));

    const view = render(<MarketNews />);
    await screen.findByRole("heading", { name: verified.title });
    await user.click(screen.getByRole("button", { name: "刷新资讯" }));

    expect(await screen.findByText("新资讯已抓取，核验处理中；当前显示上一份可信快照。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: verified.title })).toBeInTheDocument();
    expect(api.marketNewsEvents).toHaveBeenCalledTimes(1);
    view.unmount();
  });

  it("loads the new trusted list exactly once after publication", async () => {
    const user = userEvent.setup();
    const next = { ...verified, event_id: "33333333333333333333", title: "新可信快照事件" };
    const events = vi.spyOn(api, "marketNewsEvents")
      .mockResolvedValueOnce({ ...marketNewsResponse, events: [verified], focus_events: [verified], filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } })
      .mockResolvedValueOnce({ ...marketNewsResponse, snapshot_id: "next-query-hash", raw_snapshot_id: started.raw_snapshot_id, trusted_snapshot_id: started.raw_snapshot_id, events: [next], focus_events: [next], filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("trusted_published"));

    render(<MarketNews />);
    await screen.findByRole("heading", { name: verified.title });
    await user.click(screen.getByRole("button", { name: "刷新资讯" }));

    expect(await screen.findByRole("heading", { name: next.title })).toBeInTheDocument();
    await waitFor(() => expect(events).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("heading", { name: verified.title })).not.toBeInTheDocument();
  });

  it("shows the explicit pending-evidence message when raw is positive and admission is zero", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "marketNewsEvents")
      .mockResolvedValueOnce({ ...marketNewsResponse, events: [verified], focus_events: [verified], filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } })
      .mockResolvedValueOnce({ ...marketNewsResponse, snapshot_id: "empty-query-hash", raw_snapshot_id: started.raw_snapshot_id, trusted_snapshot_id: started.raw_snapshot_id, events: [], focus_events: [], empty_reason: "no_events", empty_message: null, filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("trusted_published", 6, 0));

    render(<MarketNews />);
    await screen.findByRole("heading", { name: verified.title });
    await user.click(screen.getByRole("button", { name: "刷新资讯" }));

    expect(await screen.findByText("本次已抓取 6 条资讯，目前尚无完成核验的内容。待核验资讯可在证据中心查看。")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: verified.title })).not.toBeInTheDocument();
  });

  it("rejects a terminal trusted GET from another lineage and keeps the previous cards", async () => {
    const user = userEvent.setup();
    const stale = { ...verified, event_id: "44444444444444444444", title: "错误血缘中的旧卡片" };
    vi.spyOn(api, "marketNewsEvents")
      .mockResolvedValueOnce({ ...marketNewsResponse, raw_snapshot_id: "trusted-old", trusted_snapshot_id: "trusted-old", events: [verified], focus_events: [verified], filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } })
      .mockResolvedValueOnce({ ...marketNewsResponse, snapshot_id: "stale-query-hash", raw_snapshot_id: "raw-other", trusted_snapshot_id: "raw-other", events: [stale], focus_events: [stale], filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("trusted_published", 6, 0));

    render(<MarketNews />);
    await screen.findByRole("heading", { name: verified.title });
    await user.click(screen.getByRole("button", { name: "刷新资讯" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("最新可信快照已发布，但当前筛选加载失败");
    expect(screen.getByRole("heading", { name: verified.title })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: stale.title })).not.toBeInTheDocument();
  });

  it("starts only one Market News controller on two synchronous clicks", async () => {
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [verified], focus_events: [verified], filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
    let resolveKickoff!: (value: typeof started) => void;
    const kickoff = vi.spyOn(api, "marketNewsRefresh").mockImplementation(() => new Promise((resolve) => { resolveKickoff = resolve; }));
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("trusted_published"));
    render(<MarketNews />);
    await screen.findByRole("heading", { name: verified.title });
    const button = screen.getByRole("button", { name: "刷新资讯" });

    act(() => {
      button.click();
      button.click();
    });

    expect(kickoff).toHaveBeenCalledTimes(1);
    await act(async () => resolveKickoff(started));
  });

  it("shapes exact market-news lineage while preserving the query snapshot hash", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = {
      ...marketNewsResponse,
      snapshot_id: "query-result-hash",
      raw_snapshot_id: started.raw_snapshot_id,
      trusted_snapshot_id: started.raw_snapshot_id,
      evidence_snapshot_id: "evidence-market-news",
      data_status: "trusted",
      filters: query,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsEvents(query)).resolves.toMatchObject({
      snapshot_id: "query-result-hash",
      raw_snapshot_id: started.raw_snapshot_id,
      trusted_snapshot_id: started.raw_snapshot_id,
    });

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ data: {
      ...payload,
      trusted_snapshot_id: "raw-other",
    } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });

    const missingLineage = { ...payload } as Record<string, unknown>;
    delete missingLineage.raw_snapshot_id;
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ data: missingLineage }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
  });
});
