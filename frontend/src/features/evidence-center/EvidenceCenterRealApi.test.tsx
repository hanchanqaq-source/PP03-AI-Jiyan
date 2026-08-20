import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { NewsPipelineStatusData } from "@/features/market-news/types";
import type { EvidenceEventDetail, EvidenceEventList, EvidenceEventSummary, EvidenceSummaryData, EvidenceTransition } from "./types";
import { EvidenceCenter } from "./EvidenceCenterReal";

vi.mock("@/features/source-health/SourceHealthSummary", () => ({ SourceHealthSummary: ({ onOpenDetails }: { onOpenDetails: () => void }) => <button onClick={onOpenDetails}>A1 健康摘要</button> }));
vi.mock("@/features/source-health/SourceHealthWorkspace", () => ({ SourceHealthWorkspace: () => <section aria-label="来源库直接清单">完整数据源清单</section> }));

const transition: EvidenceTransition = { from_status: "corroborated", to_status: "conflicting", changed_at: "2026-08-18T08:30:00+00:00", reason: "官方披露与转载金额不一致" };
const row: EvidenceEventSummary = {
  event_id: "0123456789abcdef0123", title: "交易所公告：星河科技建设存储算力中心", published_at: "2026-08-18T07:00:00+00:00", verified_at: "2026-08-18T08:30:00+00:00", evidence_as_of: "2026-08-18T08:30:00+00:00", category: "company", related_tags: [{ id: "semiconductor", name: "半导体" }], core_claim: "公司公告建设存储算力中心", verification_status: "verified", verification_reason: "已有明确官方证据", verified_key_fields: [], pending_key_field_count: 1, conflicting_key_field_count: 0, primary_evidence_count: 1, independent_evidence_count: 1, syndicated_copy_count: 2, contradicting_evidence_count: 0, holding_relevance: "none", status_change_count: 1, latest_transition: transition,
};
const detail: EvidenceEventDetail = {
  ...row, summary: "交易所公告确认星河科技建设存储算力中心。", key_fields: [{ field_name: "money", raw_value: "12亿元", normalized_value: "CNY:1200000000", verification_status: "unverified", evidence_ids: [], reason: "金额尚待核验" }],
  primary_evidence: [{ evidence_id: "official-1", content_source: "交易所公告", collector_source: "现有资讯源", canonical_url: "https://www.sse.com.cn/disclosure/a", published_at: "2026-08-18T07:00:00+00:00", source_role: "primary", origin_cluster: "publisher:sse.com.cn", supports_claim: true, supports_fields: ["money"], contradicts_claim: false, is_official: true, title: "官方公告", excerpt: "公开证据摘要" }],
  independent_evidence: [{ evidence_id: "independent-1", content_source: "独立来源", collector_source: "现有资讯源二", canonical_url: "https://example.org/report", published_at: "2026-08-18T07:10:00+00:00", source_role: "independent", origin_cluster: "publisher:example.org", supports_claim: true, supports_fields: [], contradicts_claim: false, is_official: false, title: "独立报道", excerpt: "一致摘要" }],
  syndicated_copies: [{ evidence_id: "copy-1", content_source: "转载来源", collector_source: "现有资讯源三", canonical_url: "https://example.net/copy", published_at: "2026-08-18T07:20:00+00:00", source_role: "syndicated", origin_cluster: "syndication:abc", supports_claim: true, supports_fields: [], contradicts_claim: false, is_official: false, title: "转载", excerpt: "同稿" }],
  contradicting_evidence: [], status_history: [transition],
};
const summary: EvidenceSummaryData = { loaded: true, snapshot_id: "acceptance-snapshot", generated_at: "2026-08-18T08:30:00+00:00", counts: { verified: 3, corroborated: 3, unverified: 1, conflicting: 1, corrected: 0, disproved: 0 }, field_counts: { verified: 2, corroborated: 0, unverified: 1, conflicting: 0 }, admitted_count: 6, isolated_count: 2, last_refresh: { status: "completed", attempted_at: "2026-08-18T08:30:00+00:00" } };
const listing: EvidenceEventList = { events: [row], snapshot_id: summary.snapshot_id, generated_at: summary.generated_at, total: 1, filters: { verification_status: null, tag_id: null, category: null, days: 7, holding_relevance: null } };
const pipelineStarted = { run_id: "run-evidence", raw_snapshot_id: "raw-evidence", phase: "queued" as const };
const pipelineDone: NewsPipelineStatusData = {
  loaded: true, run_id: pipelineStarted.run_id, raw_snapshot_id: pipelineStarted.raw_snapshot_id,
  evidence_snapshot_id: "acceptance-snapshot", trusted_snapshot_id: pipelineStarted.raw_snapshot_id, phase: "trusted_published",
  counts: { raw_event_count: 8, verified_count: 3, corroborated_count: 3, pending_count: 1, conflicting_count: 1, corrected_count: 0, disproved_count: 0, failed_source_count: 2 },
  admitted_count: 6, has_pending_evidence_message: false,
  created_at: "2026-08-20T09:00:00+00:00", updated_at: "2026-08-20T09:00:01+00:00",
  redacted_error: null, recovery_status: "ready", recovery_error: null, compatibility_error: null,
  displayed_trusted_snapshot_id: pipelineStarted.raw_snapshot_id, displayed_trusted: { snapshot_id: pipelineStarted.raw_snapshot_id, published_at: "2026-08-20T09:00:01+00:00", event_count: 6 },
};

describe("EvidenceCenter real verification workspace", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/evidence-center");
    vi.restoreAllMocks();
    vi.spyOn(api as any, "evidenceSummary").mockResolvedValue(summary);
    vi.spyOn(api as any, "evidenceEvents").mockResolvedValue(listing);
    vi.spyOn(api as any, "evidenceEvent").mockResolvedValue(detail);
    vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(pipelineStarted);
    vi.spyOn(api, "newsPipelineStatus").mockResolvedValue(pipelineDone);
    vi.spyOn(api as any, "evidenceRefresh").mockResolvedValue(summary);
  });

  it("renders real summary and event rows without W0 fixture claims", async () => {
    render(<EvidenceCenter />);
    expect(await screen.findByText("75%")).toBeInTheDocument();
    expect(screen.getByText("交易所公告：星河科技建设存储算力中心")).toBeInTheDocument();
    expect(screen.getByText(/隔离验收快照/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "真实核验概览" })).getByText("已核验").nextElementSibling).toHaveTextContent("3");
    expect(screen.queryByText(/前端演示 Fixture/)).not.toBeInTheDocument();
  });

  it("keeps unloaded counts explicit instead of inventing zeros", async () => {
    vi.mocked((api as any).evidenceSummary).mockResolvedValue({ ...summary, loaded: false, counts: null, field_counts: null, admitted_count: null, isolated_count: null });
    vi.mocked((api as any).evidenceEvents).mockResolvedValue({ ...listing, events: [], total: 0, snapshot_id: null });
    render(<EvidenceCenter />);
    expect(await screen.findByText("尚无已完成的核验快照")).toBeInTheDocument();
    expect(screen.queryByText("已核验 0")).not.toBeInTheDocument();
  });

  it("requests real status filters and supports sorting", async () => {
    const user = userEvent.setup(); render(<EvidenceCenter />); await screen.findByText("交易所公告：星河科技建设存储算力中心");
    await user.click(screen.getByRole("button", { name: "待核验" }));
    await waitFor(() => expect((api as any).evidenceEvents).toHaveBeenLastCalledWith(
      { days: 7, verification_status: "unverified" },
      expect.any(AbortSignal),
    ));
    await user.selectOptions(screen.getByLabelText("排序方式"), "holding_relevance");
    expect(screen.getByLabelText("排序方式")).toHaveValue("holding_relevance");
  });

  it("opens one real event with chains, public links and focus restoration", async () => {
    const user = userEvent.setup(); render(<EvidenceCenter />); const trigger = await screen.findByRole("button", { name: "查看证据" }); await user.click(trigger);
    const drawer = await screen.findByRole("dialog", { name: "证据详情" });
    expect(drawer).toHaveTextContent("一手证据"); expect(drawer).toHaveTextContent("独立来源链"); expect(drawer).toHaveTextContent("转载链"); expect(drawer).toHaveTextContent("状态历史");
    expect(within(drawer).getByRole("heading", { name: "交易所公告：星河科技建设存储算力中心" })).toBeInTheDocument();
    expect(within(drawer).getByText("money：12亿元")).toBeInTheDocument();
    expect(within(drawer).getByText("待核验")).toBeInTheDocument();
    expect(drawer.querySelector("header")).not.toHaveTextContent("12亿元");
    expect(drawer).toHaveTextContent("确定性证据");
    expect(drawer).not.toHaveTextContent("origin cluster");
    expect(within(drawer).getByRole("link", { name: /官方公告/ })).toHaveAttribute("href", "https://www.sse.com.cn/disclosure/a");
    fireEvent.keyDown(document, { key: "Escape" }); expect(screen.queryByRole("dialog", { name: "证据详情" })).not.toBeInTheDocument(); expect(trigger).toHaveFocus();
  });

  it("auto-opens query detail and runs verification through the shared pipeline", async () => {
    const user = userEvent.setup(); window.history.replaceState({}, "", "/evidence-center?event_id=0123456789abcdef0123"); render(<EvidenceCenter />);
    expect(await screen.findByRole("dialog", { name: "证据详情" })).toHaveTextContent("交易所公告：星河科技建设存储算力中心");
    await user.click(screen.getByRole("button", { name: "关闭证据详情" })); await user.click(screen.getByRole("button", { name: "运行核验" }));
    expect(await screen.findByText("核验刷新完成，已载入最新成功快照。")).toBeInTheDocument();
    expect(api.marketNewsRefresh).toHaveBeenCalledTimes(1);
    expect((api as any).evidenceRefresh).not.toHaveBeenCalled();
    expect(api.newsPipelineStatus).toHaveBeenCalledWith(pipelineStarted.run_id, expect.any(AbortSignal));
  });

  it("announces a successful terminal Evidence refresh only once", async () => {
    const user = userEvent.setup();
    render(<EvidenceCenter />);
    await screen.findByText(row.title);

    await user.click(screen.getByRole("button", { name: "运行核验" }));

    await screen.findByText("核验刷新完成，已载入最新成功快照。");
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("rejects a list whose echoed filters do not match the requested filter", async () => {
    const user = userEvent.setup();
    render(<EvidenceCenter />);
    await screen.findByText(row.title);
    vi.mocked(api.evidenceEvents).mockResolvedValueOnce({
      ...listing,
      filters: { ...listing.filters, verification_status: null },
    });

    await user.click(screen.getByRole("button", { name: "待核验" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("证据列表筛选失败");
    expect(screen.getByText(row.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "待核验" })).toHaveAttribute("aria-pressed", "false");
  });

  it("rejects summary and list from different evidence snapshots atomically", async () => {
    vi.mocked(api.evidenceSummary).mockResolvedValueOnce({ ...summary, snapshot_id: "summary-snapshot" });
    vi.mocked(api.evidenceEvents).mockResolvedValueOnce({ ...listing, snapshot_id: "list-snapshot" });

    render(<EvidenceCenter />);

    expect(
      await screen.findByText("证据快照加载失败，请确认本地后端可用后重试。"),
    ).toHaveAttribute("role", "alert");
    expect(screen.queryByText(row.title)).not.toBeInTheDocument();
  });

  it("aborts the sibling evidence request when either half of the atomic load fails", async () => {
    let listSignal: AbortSignal | undefined;
    let siblingAborted = false;
    vi.mocked(api.evidenceSummary).mockRejectedValueOnce(new Error("summary failed"));
    vi.mocked(api.evidenceEvents).mockImplementationOnce((_query, signal) => new Promise((_, reject) => {
      listSignal = signal;
      signal?.addEventListener("abort", () => {
        siblingAborted = true;
        reject(new DOMException("aborted", "AbortError"));
      }, { once: true });
    }));

    render(<EvidenceCenter />);

    expect(await screen.findByRole("alert")).toHaveTextContent("证据快照加载失败");
    expect(listSignal?.aborted).toBe(true);
    expect(siblingAborted).toBe(true);
  });

  it("reloads the exact current status filter when evidence becomes durable", async () => {
    const user = userEvent.setup();
    vi.mocked(api.evidenceEvents).mockImplementation((query) => Promise.resolve({
      ...listing,
      filters: { ...listing.filters, verification_status: query?.verification_status ?? null },
    }));
    render(<EvidenceCenter />);
    await screen.findByText(row.title);

    await user.click(screen.getByRole("button", { name: "待核验" }));
    await waitFor(() => expect(api.evidenceEvents).toHaveBeenLastCalledWith(
      { days: 7, verification_status: "unverified" },
      expect.any(AbortSignal),
    ));
    await user.click(screen.getByRole("button", { name: "运行核验" }));

    await waitFor(() => expect(api.evidenceEvents).toHaveBeenLastCalledWith(
      { days: 7, verification_status: "unverified" },
      expect.any(AbortSignal),
    ));
  });

  it("uses one generation authority across delayed filter and durable snapshot loads", async () => {
    const user = userEvent.setup();
    const staleRow = { ...row, event_id: "aaaaaaaaaaaaaaaaaaab", title: "迟到的筛选结果" };
    const freshRow = { ...row, event_id: "bbbbbbbbbbbbbbbbbbbc", title: "新证据快照筛选结果" };
    let resolveStale!: (value: EvidenceEventList) => void;
    vi.mocked(api.evidenceEvents)
      .mockResolvedValueOnce(listing)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve; }))
      .mockResolvedValueOnce({ ...listing, events: [freshRow], filters: { ...listing.filters, verification_status: null } });

    render(<EvidenceCenter />);
    await screen.findByText(row.title);
    await user.click(screen.getByRole("button", { name: "待核验" }));
    await waitFor(() => expect(api.evidenceEvents).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "运行核验" }));
    expect(await screen.findByText(freshRow.title)).toBeInTheDocument();

    await act(async () => resolveStale({ ...listing, events: [staleRow], filters: { ...listing.filters, verification_status: "unverified" } }));
    expect(screen.queryByText(staleRow.title)).not.toBeInTheDocument();
    expect(screen.getByText(freshRow.title)).toBeInTheDocument();
  });

  it("reloads evidence only after the shared run reaches an evidence-durable phase", async () => {
    const user = userEvent.setup();
    let resolveStatus!: (value: NewsPipelineStatusData) => void;
    vi.mocked(api.newsPipelineStatus).mockImplementation(() => new Promise((resolve) => { resolveStatus = resolve; }));
    render(<EvidenceCenter />);
    await screen.findByText("交易所公告：星河科技建设存储算力中心");
    const initialSummaryCalls = vi.mocked(api.evidenceSummary).mock.calls.length;
    const initialListCalls = vi.mocked(api.evidenceEvents).mock.calls.length;

    await user.click(screen.getByRole("button", { name: "运行核验" }));
    expect(api.evidenceSummary).toHaveBeenCalledTimes(initialSummaryCalls);
    expect(api.evidenceEvents).toHaveBeenCalledTimes(initialListCalls);

    resolveStatus({
      ...pipelineDone,
      phase: "evidence_saved",
      trusted_snapshot_id: null,
      displayed_trusted_snapshot_id: "trusted-old",
      displayed_trusted: { ...pipelineDone.displayed_trusted!, snapshot_id: "trusted-old" },
    });
    await waitFor(() => expect(api.evidenceSummary).toHaveBeenCalledTimes(initialSummaryCalls + 1));
    expect(api.evidenceEvents).toHaveBeenCalledTimes(initialListCalls + 1);
  });

  it("does not let an older snapshot load overwrite the evidence-saved reload", async () => {
    const user = userEvent.setup();
    const freshRow = { ...row, event_id: "fedcba9876543210fedc", title: "新证据快照事件" };
    let resolveOldSummary!: (value: typeof summary) => void;
    let resolveOldList!: (value: EvidenceEventList) => void;
    vi.mocked(api.evidenceSummary)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOldSummary = resolve; }))
      .mockResolvedValueOnce({ ...summary, snapshot_id: "fresh-evidence" });
    vi.mocked(api.evidenceEvents)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOldList = resolve; }))
      .mockResolvedValueOnce({ ...listing, snapshot_id: "fresh-evidence", events: [freshRow] });
    vi.mocked(api.newsPipelineStatus).mockResolvedValueOnce({
      ...pipelineDone,
      evidence_snapshot_id: "fresh-evidence",
    });

    render(<EvidenceCenter />);
    await waitFor(() => expect(api.evidenceSummary).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "运行核验" }));
    expect(await screen.findByText(freshRow.title)).toBeInTheDocument();

    await act(async () => {
      resolveOldSummary(summary);
      resolveOldList(listing);
    });
    await waitFor(() => expect(screen.getByText(freshRow.title)).toBeInTheDocument());
    expect(screen.queryByText(row.title)).not.toBeInTheDocument();
  });

  it("retries an aborted durable reload and never commits a new list with an old summary", async () => {
    const user = userEvent.setup();
    const freshSummary = {
      ...summary,
      snapshot_id: "evidence-complete",
      admitted_count: 4,
      isolated_count: 2,
      counts: { verified: 4, corroborated: 0, unverified: 1, conflicting: 1, corrected: 0, disproved: 0 },
    };
    const freshRow = { ...row, event_id: "cccccccccccccccccccc", title: "同快照筛选结果" };
    const freshListing: EvidenceEventList = {
      ...listing,
      snapshot_id: "evidence-complete",
      events: [freshRow],
      filters: { ...listing.filters, verification_status: "unverified" },
    };
    let durableSummarySignal: AbortSignal | undefined;
    vi.mocked(api.newsPipelineStatus)
      .mockResolvedValueOnce({ ...pipelineDone, phase: "evidence_saved", evidence_snapshot_id: "evidence-complete", trusted_snapshot_id: null, displayed_trusted_snapshot_id: "trusted-old", displayed_trusted: { ...pipelineDone.displayed_trusted!, snapshot_id: "trusted-old" } })
      .mockResolvedValueOnce({ ...pipelineDone, evidence_snapshot_id: "evidence-complete" });
    vi.mocked(api.evidenceSummary)
      .mockResolvedValueOnce(summary)
      .mockImplementationOnce((signal) => new Promise((_, reject) => {
        durableSummarySignal = signal;
        signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
      }))
      .mockResolvedValue(freshSummary);
    vi.mocked(api.evidenceEvents)
      .mockResolvedValueOnce(listing)
      .mockImplementationOnce((_query, signal) => new Promise((_, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
      }))
      .mockResolvedValue(freshListing);

    render(<EvidenceCenter />);
    await screen.findByText(row.title);
    await user.click(screen.getByRole("button", { name: "运行核验" }));
    await waitFor(() => expect(durableSummarySignal).toBeInstanceOf(AbortSignal));
    await user.click(screen.getByRole("button", { name: "待核验" }));

    expect(await screen.findByText(freshRow.title)).toBeInTheDocument();
    expect(await screen.findByText("核验刷新完成，已载入最新成功快照。")).toBeInTheDocument();
    expect(screen.getByText("67%")).toBeInTheDocument();
    expect(api.evidenceSummary).toHaveBeenCalledTimes(4);
  });

  it("keeps the prior evidence snapshot when a failed run has no durable evidence ID", async () => {
    const user = userEvent.setup();
    const failed: NewsPipelineStatusData = {
      ...pipelineDone,
      phase: "failed",
      evidence_snapshot_id: null,
      trusted_snapshot_id: null,
      counts: { ...pipelineDone.counts!, verified_count: 0, corroborated_count: 0, pending_count: 0, conflicting_count: 0 },
      admitted_count: 0,
      redacted_error: "verification_failed",
      compatibility_error: null,
      displayed_trusted_snapshot_id: "trusted-old",
      displayed_trusted: { ...pipelineDone.displayed_trusted!, snapshot_id: "trusted-old" },
    };
    vi.mocked(api.newsPipelineStatus).mockResolvedValue(failed);
    render(<EvidenceCenter />);
    await screen.findByText(row.title);
    const summaryCalls = vi.mocked(api.evidenceSummary).mock.calls.length;
    const eventCalls = vi.mocked(api.evidenceEvents).mock.calls.length;

    await user.click(screen.getByRole("button", { name: "运行核验" }));

    expect((await screen.findAllByText("确定性核验失败；继续显示上一份可信快照。")).length).toBeGreaterThan(0);
    expect(api.evidenceSummary).toHaveBeenCalledTimes(summaryCalls);
    expect(api.evidenceEvents).toHaveBeenCalledTimes(eventCalls);
    expect(screen.getByText(row.title)).toBeInTheDocument();
  });

  it("announces a terminal Evidence failure only once", async () => {
    const user = userEvent.setup();
    const failed: NewsPipelineStatusData = {
      ...pipelineDone,
      phase: "failed",
      evidence_snapshot_id: null,
      trusted_snapshot_id: null,
      counts: { ...pipelineDone.counts!, verified_count: 0, corroborated_count: 0, pending_count: 0, conflicting_count: 0 },
      admitted_count: 0,
      redacted_error: "verification_failed",
      compatibility_error: null,
      displayed_trusted_snapshot_id: "trusted-old",
      displayed_trusted: { ...pipelineDone.displayed_trusted!, snapshot_id: "trusted-old" },
    };
    vi.mocked(api.newsPipelineStatus).mockResolvedValue(failed);
    render(<EvidenceCenter />);
    await screen.findByText(row.title);

    await user.click(screen.getByRole("button", { name: "运行核验" }));

    await screen.findByRole("alert");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("uses the latest committed snapshot truth after an asynchronous status failure", async () => {
    const user = userEvent.setup();
    let resolveSummary!: (value: EvidenceSummaryData) => void;
    let resolveListing!: (value: EvidenceEventList) => void;
    let rejectStatus!: (reason: Error) => void;
    vi.mocked(api.evidenceSummary).mockImplementationOnce(() => new Promise((resolve) => { resolveSummary = resolve; }));
    vi.mocked(api.evidenceEvents).mockImplementationOnce(() => new Promise((resolve) => { resolveListing = resolve; }));
    vi.mocked(api.newsPipelineStatus).mockImplementation(() => new Promise((_, reject) => { rejectStatus = reject; }));

    render(<EvidenceCenter />);
    await user.click(screen.getByRole("button", { name: "运行核验" }));
    await act(async () => {
      resolveSummary(summary);
      resolveListing(listing);
    });
    await screen.findByText(row.title);
    await act(async () => rejectStatus(new Error("status transport failed")));

    expect(await screen.findByText("核验流水线状态连接失败；继续显示上次成功快照。")).toBeInTheDocument();
    expect(screen.queryByText("核验流水线状态连接失败；当前尚无可显示的成功快照。")).not.toBeInTheDocument();
  });

  it("rejects a failed-run evidence reload that does not match its durable ID", async () => {
    const user = userEvent.setup();
    const staleRow = { ...row, event_id: "dddddddddddddddddddd", title: "错误证据快照中的事件" };
    const failedEvidence = { ...pipelineDone, phase: "failed" as const, trusted_snapshot_id: null, evidence_snapshot_id: "evidence-final", redacted_error: "publication_failed" as const, displayed_trusted_snapshot_id: "trusted-old", displayed_trusted: { ...pipelineDone.displayed_trusted!, snapshot_id: "trusted-old" } };
    vi.mocked(api.newsPipelineStatus).mockResolvedValueOnce(failedEvidence);
    vi.mocked(api.evidenceSummary)
      .mockResolvedValueOnce(summary)
      .mockResolvedValue({ ...summary, snapshot_id: "evidence-other" });
    vi.mocked(api.evidenceEvents)
      .mockResolvedValueOnce(listing)
      .mockResolvedValue({ ...listing, snapshot_id: "evidence-other", events: [staleRow] });
    render(<EvidenceCenter />);
    await screen.findByText(row.title);

    await user.click(screen.getByRole("button", { name: "运行核验" }));

    expect(
      await screen.findByText("证据快照加载失败，请确认本地后端可用后重试。"),
    ).toHaveAttribute("role", "alert");
    expect((await screen.findAllByText("可信资讯发布失败；继续显示上一份可信快照。")).length).toBeGreaterThan(0);
    expect(screen.getByText(row.title)).toBeInTheDocument();
    expect(screen.queryByText(staleRow.title)).not.toBeInTheDocument();
  });

  it("starts only one Evidence controller on two synchronous clicks", async () => {
    let resolveKickoff!: (value: typeof pipelineStarted) => void;
    const kickoff = vi.spyOn(api, "marketNewsRefresh").mockImplementation(() => new Promise((resolve) => { resolveKickoff = resolve; }));
    render(<EvidenceCenter />);
    await screen.findByText(row.title);
    const button = screen.getByRole("button", { name: "运行核验" });

    act(() => {
      button.click();
      button.click();
    });

    expect(kickoff).toHaveBeenCalledTimes(1);
    await act(async () => resolveKickoff(pipelineStarted));
  });

  it("derives correction rows from real transition data", async () => {
    const user = userEvent.setup(); render(<EvidenceCenter />); await screen.findByText("交易所公告：星河科技建设存储算力中心"); await user.click(screen.getByRole("tab", { name: "更正记录" }));
    const table = screen.getByRole("table"); expect(within(table).getByText("多源印证")).toBeInTheDocument(); expect(within(table).getByText("存在冲突")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看记录 交易所公告：星河科技建设存储算力中心" }));
    expect(await screen.findByRole("dialog", { name: "证据详情" })).toHaveTextContent("官方披露与转载金额不一致");
  });

  it("does not present initial status creation as a correction", async () => {
    const initial = { ...row, event_id: "aaaaaaaaaaaaaaaaaaaa", title: "初次核验事件", latest_transition: { ...transition, from_status: null, to_status: "verified" } };
    vi.mocked((api as any).evidenceEvents).mockResolvedValue({ ...listing, events: [row, initial], total: 2 });
    const user = userEvent.setup(); render(<EvidenceCenter />); await screen.findByText("初次核验事件"); await user.click(screen.getByRole("tab", { name: "更正记录" }));
    expect(screen.queryByText("初次核验事件")).not.toBeInTheDocument();
    expect(screen.getByText("交易所公告：星河科技建设存储算力中心")).toBeInTheDocument();
  });

  it("keeps the A1 source-health workspace unchanged", async () => {
    const user = userEvent.setup(); render(<EvidenceCenter />); await user.click(screen.getByRole("tab", { name: "数据源健康" }));
    expect(screen.getByRole("region", { name: "来源库直接清单" })).toHaveTextContent("完整数据源清单");
  });
});
