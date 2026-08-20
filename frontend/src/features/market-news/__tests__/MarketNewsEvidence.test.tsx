import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import * as apiModule from "@/lib/api";
import { MarketNews } from "@/pages/MarketNews";
import type { MarketNewsResponse, NewsPipelineStatusData } from "@/features/market-news/types";
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
      .mockResolvedValueOnce({ ...marketNewsResponse, snapshot_id: "next-query-hash", raw_snapshot_id: started.raw_snapshot_id, evidence_snapshot_id: "evidence-market-news", trusted_snapshot_id: started.raw_snapshot_id, data_status: "trusted", events: [next], focus_events: [next], filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
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
      .mockResolvedValueOnce({ ...marketNewsResponse, snapshot_id: "empty-query-hash", raw_snapshot_id: started.raw_snapshot_id, evidence_snapshot_id: "evidence-market-news", trusted_snapshot_id: started.raw_snapshot_id, data_status: "trusted", events: [], focus_events: [], empty_reason: "no_events", empty_message: null, filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
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

  it.each([
    ["evidence identity", { evidence_snapshot_id: "evidence-other", data_status: "trusted" }],
    ["trusted data status", { evidence_snapshot_id: "evidence-market-news", data_status: "cache" }],
  ])("rejects terminal GET %s mismatch and keeps the prior cards", async (_label, mismatch) => {
    const user = userEvent.setup();
    const replacement = { ...verified, event_id: "55555555555555555555", title: "不应提交的可信卡片" };
    vi.spyOn(api, "marketNewsEvents")
      .mockResolvedValueOnce({
        ...marketNewsResponse,
        events: [verified],
        focus_events: [verified],
        filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] },
      })
      .mockResolvedValueOnce({
        ...marketNewsResponse,
        snapshot_id: "new-filtered-snapshot",
        raw_snapshot_id: started.raw_snapshot_id,
        trusted_snapshot_id: started.raw_snapshot_id,
        evidence_snapshot_id: mismatch.evidence_snapshot_id,
        data_status: mismatch.data_status,
        events: [replacement],
        focus_events: [replacement],
        filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] },
      });
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("trusted_published"));

    render(<MarketNews />);
    await screen.findByRole("heading", { name: verified.title });
    await user.click(screen.getByRole("button", { name: "刷新资讯" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("最新可信快照已发布，但当前筛选加载失败");
    expect(screen.getByRole("heading", { name: verified.title })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: replacement.title })).not.toBeInTheDocument();
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

  it("announces one terminal pipeline failure instead of duplicating the page error alert", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({
      ...marketNewsResponse,
      events: [verified],
      focus_events: [verified],
      filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] },
    });
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(started);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(status("failed", 6, 0) as NewsPipelineStatusData);
    vi.mocked(api.newsPipelineStatus).mockResolvedValue({
      ...status("failed", 6, 0),
      redacted_error: "verification_failed",
    });

    render(<MarketNews />);
    await screen.findByRole("heading", { name: verified.title });
    await user.click(screen.getByRole("button", { name: "刷新资讯" }));

    await screen.findByText("确定性核验失败；继续显示上一份可信快照。");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
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

  it("preserves exact legacy-trusted and empty statuses emitted by the backend", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const legacyTrusted = {
      ...marketNewsResponse,
      raw_snapshot_id: "raw-legacy",
      trusted_snapshot_id: "raw-legacy",
      evidence_snapshot_id: null,
      data_status: "trusted",
      filters: query,
    };
    const empty = {
      ...marketNewsResponse,
      events: [],
      focus_events: [],
      impact_summary: null,
      data_status: "empty",
      source_summary: {
        ...marketNewsResponse.source_summary,
        total_sources: 0,
        cache_status: "empty",
        source_state: "empty" as const,
      },
      empty_reason: "no_events" as const,
      filters: query,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: legacyTrusted }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: empty }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));

    await expect(api.marketNewsEvents(query)).resolves.toMatchObject({
      data_status: "trusted",
      evidence_snapshot_id: null,
    });
    await expect(api.marketNewsEvents(query)).resolves.toMatchObject({
      data_status: "empty",
      source_summary: { cache_status: "empty" },
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("accepts backend-compatible missing source links and returns null instead of an unsafe anchor", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = structuredClone(marketNewsResponse) as any;
    delete payload.events[0].sources[0].original_url;
    payload.events[0].sources[0].source_url = "";
    payload.events[0].original_links = [];
    payload.focus_events = [structuredClone(payload.events[0])];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    const response = await api.marketNewsEvents(query);
    expect(response.events[0].sources[0]).toMatchObject({ source_url: null, original_url: null });
    expect(response.events[0].original_links).toEqual([]);
  });

  it.each([
    "http://127.0.0.1/internal",
    "http://0.0.0.0/internal",
    "http://10.2.3.4/internal",
    "http://172.16.0.1/internal",
    "http://192.168.0.1/internal",
    "http://100.64.0.1/internal",
    "http://169.254.1.1/internal",
    "http://192.0.2.1/internal",
    "http://[::1]/internal",
    "http://198.51.100.42/internal",
    "http://203.0.113.8/internal",
    "http://224.0.0.1/internal",
    "http://240.0.0.1/internal",
    "http://[::]/internal",
    "http://[fc00::1]/internal",
    "http://[fe80::1]/internal",
    "http://[fec0::1]/internal",
    "http://[2001:db8::1]/internal",
    "http://[ff02::1]/internal",
    "http://[::ffff:10.2.3.4]/internal",
    "http://[::ffff:198.51.100.42]/internal",
    "https://localhost/private",
    "https://feed.local/private",
    "https://user:password@public.example.org/feed",
    "https://public.example.org/feed?token=secret",
    "https://public.example.org/feed?access-key=secret",
    "https://public.example.org/feed?accessToken=secret",
    "https://public.example.org/feed?auth_token=secret",
    "https://public.example.org/feed?id_token=secret",
    "https://public.example.org/feed?api-key=secret",
    "https://public.example.org/feed?client-secret=secret",
    "https://public.example.org/feed?session=secret",
    "https://public.example.org/feed?user%5Baccess_key%5D=secret",
    "https://public.example.org/feed?redirect=https%3A%2F%2Fpublic.example.org%2Fcallback%3Ftoken%3Dsecret",
    "https://public.example.org/feed?redirect=https%253A%252F%252Fpublic.example.org%252Fcallback%253Faccess-key%253Dsecret",
    "https://public.example.org/feed#credential",
  ])("rejects a non-public or credential-bearing market-news URL through GET and retry: %s", async (unsafeUrl) => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = structuredClone(marketNewsResponse) as any;
    payload.events[0].original_links[0] = unsafeUrl;
    payload.focus_events = [structuredClone(payload.events[0])];
    payload.source_summary.source_statuses = [{
      source_id: "0123456789abcdef",
      source_name: "来源一",
      source_url: "https://public.example.org/feed",
      status: "ok",
      error_type: null,
      error_reason: null,
      last_success_at: null,
      used_cached_items: false,
      item_count: 1,
    }];
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));

    const [getResult, retryResult] = await Promise.allSettled([
      api.marketNewsEvents(query),
      api.marketNewsRetrySource("0123456789abcdef", query),
    ]);
    expect(getResult).toMatchObject({ status: "rejected", reason: { status: 502 } });
    expect(retryResult).toMatchObject({ status: "rejected", reason: { status: 502 } });
  });

  it("accepts a partial source observation without pretending it covers the full configured catalog", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = structuredClone(marketNewsResponse) as any;
    payload.data_status = "partial";
    payload.source_summary = {
      total_sources: 4,
      failed_sources: 2,
      cache_status: "partial",
      source_state: "partial_failure",
      refresh_failed: false,
      source_statuses: [{
        source_id: "0123456789abcdef",
        source_name: "已观测失败来源",
        source_url: "https://one.example.org/feed",
        status: "failed",
        error_type: "timeout",
        error_reason: "连接超时",
        last_success_at: null,
        used_cached_items: false,
        item_count: 0,
      }, {
        source_id: "fedcba9876543210",
        source_name: "已观测成功来源",
        source_url: "https://two.example.org/feed",
        status: "ok",
        error_type: null,
        error_reason: null,
        last_success_at: null,
        used_cached_items: false,
        item_count: 1,
      }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsEvents(query)).resolves.toMatchObject({
      source_summary: { total_sources: 4, failed_sources: 2, source_statuses: [{ status: "failed" }, { status: "ok" }] },
    });
  });

  it("rejects total_sources beyond the product cap even when no source rows are emitted", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = structuredClone(marketNewsResponse) as any;
    payload.source_summary.total_sources = 513;
    payload.source_summary.source_statuses = [];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
  });

  it("rejects refresh_failed paired with an all-success realtime aggregate", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = structuredClone(marketNewsResponse) as any;
    payload.source_summary = {
      total_sources: 1,
      failed_sources: 0,
      cache_status: "realtime",
      source_state: "all_success",
      refresh_failed: true,
      source_statuses: [{
        source_id: "0123456789abcdef",
        source_name: "来源一",
        source_url: "https://one.example.org/feed",
        status: "ok",
        error_type: null,
        error_reason: null,
        last_success_at: null,
        used_cached_items: false,
        item_count: 1,
      }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
  });

  it("binds a failed single-source retry to the exact requested source", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = {
      retry_succeeded: false,
      source_status: {
        source_id: "fedcba9876543210",
        source_name: "错误来源",
        source_url: "https://public.example.org/feed",
        status: "failed",
        error_type: "timeout",
        error_reason: "连接超时",
        last_success_at: null,
        used_cached_items: false,
        item_count: 0,
      },
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsRetrySource("0123456789abcdef", query)).rejects.toMatchObject({ status: 502 });
  });

  it("rejects a successful single-source retry response that omits the requested source row", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: marketNewsResponse }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsRetrySource("0123456789abcdef", query)).rejects.toMatchObject({ status: 502 });
  });

  it.each([
    ["source state", (payload: any) => {
      payload.source_summary.source_state = "all_success";
      payload.source_summary.cache_status = "realtime";
      payload.source_summary.source_statuses = [{
        source_id: "0123456789abcdef",
        source_name: "来源一",
        source_url: "https://one.example.org/feed",
        status: "ok",
        error_type: null,
        error_reason: null,
        last_success_at: null,
        used_cached_items: false,
        item_count: 1,
      }];
    }],
    ["impact facts", (payload: any) => {
      payload.impact_summary = {
        holding_related_count: 0,
        direct_count: 0,
        industry_count: 0,
        watch_count: 0,
        funds: [],
      };
    }],
  ])("rejects a market-news summary that contradicts emitted %s", async (_label, mutate) => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = structuredClone(marketNewsResponse) as any;
    mutate(payload);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
  });

  it.each([
    ["event extra key", (payload: any) => { payload.events[0].unexpected = "unsafe"; }],
    ["focus row mutation", (payload: any) => { payload.focus_events[0] = { ...payload.focus_events[0], title: "同 ID 的伪造焦点标题" }; }],
    ["event control text", (payload: any) => { payload.events[0].title = "\u0001伪造标题"; }],
    ["event tab control text", (payload: any) => { payload.events[0].title = "伪\t造标题"; }],
    ["verified row missing timestamp", (payload: any) => { payload.events[0].verified_at = null; payload.focus_events[0] = payload.events[0]; }],
    ["non-canonical tag ID", (payload: any) => { payload.filters.tag_ids = ["../unsafe"]; }],
    ["event javascript link", (payload: any) => { payload.events[0].original_links[0] = "javascript:alert(1)"; }],
    ["nested source data URL", (payload: any) => { payload.events[0].sources[0].source_url = "data:text/html,bad"; }],
    ["impact boolean count", (payload: any) => { payload.impact_summary.direct_count = true; }],
    ["source status unknown error", (payload: any) => {
      payload.source_summary.source_statuses = [{
        source_id: "0123456789abcdef",
        source_name: "来源一",
        source_url: "https://one.example.test/rss",
        status: "failed",
        error_type: "credential_dump",
        error_reason: "失败",
        last_success_at: null,
        used_cached_items: false,
        item_count: 0,
      }];
      payload.source_summary.failed_sources = 1;
    }],
  ])("rejects malformed nested market-news payload through GET and retry: %s", async (_label, mutate) => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = structuredClone(marketNewsResponse) as any;
    mutate(payload);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({ data: payload }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
    await expect(api.marketNewsRetrySource("0123456789abcdef", query)).rejects.toMatchObject({ status: 502 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("bounds market-news event arrays before they can enter page state", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload: MarketNewsResponse = {
      ...marketNewsResponse,
      events: Array.from({ length: 10_001 }, (_, index) => ({
        ...directEvent,
        event_id: index.toString(16).padStart(20, "0"),
      })),
      focus_events: [],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
  });

  it("rejects a response-level source capacity beyond the product catalog boundary", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const sourceStatuses = Array.from({ length: 513 }, (_, index) => ({
      source_id: index.toString(16).padStart(16, "0"),
      source_name: `来源 ${index}`,
      source_url: `https://source-${index}.example.org/feed`,
      status: "ok",
      error_type: null,
      error_reason: null,
      last_success_at: null,
      used_cached_items: false,
      item_count: 0,
    }));
    const payload = structuredClone(marketNewsResponse) as any;
    payload.source_summary = {
      total_sources: 513,
      failed_sources: 0,
      cache_status: "realtime",
      source_state: "all_success",
      refresh_failed: false,
      source_statuses: sourceStatuses,
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
  });

  it("bounds cumulative nested nodes across the whole market-news document", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = structuredClone(marketNewsResponse) as any;
    payload.events = Array.from({ length: 60 }, (_, index) => ({
      ...structuredClone(directEvent),
      event_id: index.toString(16).padStart(20, "0"),
      impact_basis: Array.from({ length: 1_000 }, () => "依据"),
    }));
    payload.focus_events = [];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
  });

  it("rejects an over-budget child array before bulk-enqueuing values beyond the node cap", async () => {
    const overBudget = new Proxy(Array.from({ length: 50_001 }, () => null), {
      get(target, property, receiver) {
        if (property === Symbol.iterator) throw new Error("bulk traversal attempted before enforcing node cap");
        return Reflect.get(target, property, receiver);
      },
    });
    const validateDocument = (apiModule as Record<string, unknown>).validateMarketNewsDocumentBudget as ((value: unknown) => void) | undefined;
    let thrown: unknown;
    try {
      validateDocument!(overBudget);
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toMatchObject({ status: 502 });
  });

  it("bounds cumulative text across the whole market-news document", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const payload = structuredClone(marketNewsResponse) as any;
    payload.events = Array.from({ length: 400 }, (_, index) => ({
      ...structuredClone(directEvent),
      event_id: index.toString(16).padStart(20, "0"),
      summary: "x".repeat(4_096),
    }));
    payload.focus_events = [];
    payload.impact_summary = null;
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
  });

  it("rejects an oversized JSON response before consuming its body", async () => {
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const response = new Response(JSON.stringify({ data: marketNewsResponse }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Content-Length": "5000000" },
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response);

    await expect(api.marketNewsEvents(query)).rejects.toMatchObject({ status: 502 });
    expect(response.bodyUsed).toBe(false);
  });

  it("labels a backend trusted snapshot accurately in Chinese", async () => {
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({
      ...marketNewsResponse,
      data_status: "trusted",
      raw_snapshot_id: "raw-existing",
      trusted_snapshot_id: "raw-existing",
      evidence_snapshot_id: "evidence-existing",
      events: [verified],
      focus_events: [verified],
      filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] },
    });

    render(<MarketNews />);

    expect(await screen.findByText("状态：可信资讯快照")).toBeInTheDocument();
  });

  it("keeps stale-cache aggregate facts unchanged when a failed source retry remains failed", async () => {
    const user = userEvent.setup();
    const query = { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] };
    const failedStatus = {
      source_id: "0123456789abcdef",
      source_name: "失败来源",
      source_url: "https://public.example.org/feed",
      status: "failed" as const,
      error_type: "timeout" as const,
      error_reason: "连接超时",
      last_success_at: null,
      used_cached_items: true,
      item_count: 0,
    };
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({
      ...marketNewsResponse,
      filters: query,
      source_summary: {
        total_sources: 108,
        failed_sources: 1,
        cache_status: "stale",
        source_state: "stale_cache",
        refresh_failed: true,
        source_statuses: [failedStatus],
      },
    });
    vi.spyOn(api, "marketNewsRetrySource").mockResolvedValue({
      retry_succeeded: false,
      source_status: { ...failedStatus, error_reason: "重试仍然超时" },
    });
    render(<MarketNews />);

    expect(await screen.findByText("来源：使用过期缓存")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "1 个来源失败，查看详情" }));
    await user.click(screen.getByRole("button", { name: "重试来源 失败来源" }));
    expect(await screen.findByText("该来源重试失败，请稍后再试。")).toBeInTheDocument();
    expect(screen.getByText("来源：使用过期缓存")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1 个来源失败，查看详情" })).toBeInTheDocument();
  });
});
