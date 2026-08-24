import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import type { NewsPipelineStatusData } from "@/features/market-news/types";
import type { EvidenceArchiveEvent, EvidenceArchiveList, EvidenceEventDetail, EvidenceEventList, EvidenceEventSummary, EvidenceSummaryData, EvidenceTransition, EvidenceHistoryDays, VerificationStatus } from "./types";
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
const archivedTransitions: EvidenceTransition[] = [
  { from_status: null, to_status: "verified", changed_at: "2026-07-20T08:00:00+00:00", reason: "首次官方核验" },
  { from_status: "verified", to_status: "corrected", changed_at: "2026-08-01T08:00:00+00:00", reason: "官方公告更正项目日期" },
];
const archiveRecovery = {
  source: "evidence_history" as const,
  status: "cache_recovered" as const,
  recovered_at: "2026-08-19T08:00:00+00:00",
  source_snapshot_id: "archive-source-snapshot",
};
const archiveLineage = {
  evidence_snapshot_id: "archive-evidence-snapshot",
  raw_snapshot_id: "archive-raw-snapshot",
  generated_at: "2026-08-01T08:00:00+00:00",
  content_digest: "a".repeat(64),
  raw_input_digest: "b".repeat(64),
  recovery: archiveRecovery,
};
const archiveQueryVersion = "1".repeat(64);
const archivedRow: EvidenceArchiveEvent = {
  ...detail,
  event_id: "aaaaaaaaaaaaaaaaaaaa",
  title: "三十天历史：官方公告更正项目日期",
  published_at: "2026-07-20T07:00:00+00:00",
  verified_at: "2026-08-01T08:00:00+00:00",
  evidence_as_of: "2026-08-01T08:00:00+00:00",
  verification_status: "corrected",
  verification_reason: "官方公告更正项目日期",
  status_history: archivedTransitions,
  primary_evidence: detail.primary_evidence.map((item) => ({ ...item, published_at: "2026-07-20T07:00:00+00:00" })),
  independent_evidence: detail.independent_evidence.map((item) => ({ ...item, published_at: "2026-07-20T07:10:00+00:00" })),
  syndicated_copies: detail.syndicated_copies.map((item) => ({ ...item, published_at: "2026-07-20T07:20:00+00:00" })),
  schema_version: 3,
  evidence_snapshot_id: archiveLineage.evidence_snapshot_id,
  raw_snapshot_id: archiveLineage.raw_snapshot_id,
  snapshot_generated_at: archiveLineage.generated_at,
  archived_at: "2026-08-19T08:00:00+00:00",
  last_updated_at: archiveLineage.generated_at,
  snapshot_history: [archiveLineage],
  content_digest: archiveLineage.content_digest,
  raw_input_digest: archiveLineage.raw_input_digest,
};
const archiveListing: EvidenceArchiveList = {
  events: [archivedRow],
  total: 1,
  filters: { days: 7, verification_status: null },
  diagnostics: { scanned_files: 2, skipped_files: 0, scanned_rows: 1, skipped_corrupt_rows: 0, duplicate_rows: 0 },
  provenance: [{
    event_id: archivedRow.event_id,
    evidence_snapshot_id: archivedRow.evidence_snapshot_id,
    raw_snapshot_id: archivedRow.raw_snapshot_id,
    snapshot_history: archivedRow.snapshot_history,
    recovery: [archiveRecovery],
  }],
  page: { limit: 100, returned: 1, has_more: false, next_cursor: null, query_version: archiveQueryVersion } as any,
} as EvidenceArchiveList;

function archiveHttpResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function restoreArchiveClientWithResponse(value: unknown) {
  vi.mocked(api.newsArchive).mockRestore();
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(archiveHttpResponse(value));
}

function archiveInvalidResponse(): { message: string; status: number } {
  return { message: "证据历史响应无效", status: 502 };
}

function archiveWireListing(): any {
  const payload = structuredClone(archiveListing) as any;
  for (const key of [
    "verified_key_fields", "pending_key_field_count", "conflicting_key_field_count",
    "primary_evidence_count", "independent_evidence_count", "syndicated_copy_count",
    "contradicting_evidence_count", "status_change_count", "latest_transition",
  ]) delete payload.events[0][key];
  return payload;
}

function syncArchiveProvenance(payload: any): void {
  const event = payload.events[0];
  payload.provenance[0] = {
    event_id: event.event_id,
    evidence_snapshot_id: event.evidence_snapshot_id,
    raw_snapshot_id: event.raw_snapshot_id,
    snapshot_history: structuredClone(event.snapshot_history),
    recovery: event.snapshot_history.flatMap((lineage: any) => lineage.recovery ? [structuredClone(lineage.recovery)] : []),
  };
}

function setArchiveClock(payload: any, timestamp: string): void {
  payload.events[0].published_at = null;
  payload.events[0].verified_at = timestamp;
  payload.events[0].evidence_as_of = timestamp;
  payload.events[0].status_history = [{ from_status: null, to_status: payload.events[0].verification_status, changed_at: timestamp, reason: payload.events[0].verification_reason }];
  for (const collection of ["primary_evidence", "independent_evidence", "syndicated_copies", "contradicting_evidence"]) {
    payload.events[0][collection].forEach((item: any) => { item.published_at = null; });
  }
  payload.events[0].snapshot_generated_at = timestamp;
  payload.events[0].archived_at = timestamp;
  payload.events[0].last_updated_at = timestamp;
  payload.events[0].snapshot_history = [{
    ...payload.events[0].snapshot_history[0],
    generated_at: timestamp,
  }];
  delete payload.events[0].snapshot_history[0].recovery;
  syncArchiveProvenance(payload);
}
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
    if (typeof (api as any).newsArchive !== "function") (api as any).newsArchive = vi.fn();
    vi.spyOn(api as any, "newsArchive").mockResolvedValue(archiveListing);
  });

  it("loads seven-day archive history separately from the current snapshot summary", async () => {
    render(<EvidenceCenter />);

    const history = await screen.findByRole("region", { name: "证据历史" });
    expect(within(history).getByText(archivedRow.title)).toBeInTheDocument();
    expect(within(history).getByText("历史记录：1")).toBeInTheDocument();
    expect(within(history).getByText(/当前核验概览与历史记录分别计数/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "真实核验概览" })).getByText("已核验").nextElementSibling).toHaveTextContent("3");
    expect((api as any).newsArchive).toHaveBeenCalledWith({ days: 7, limit: 100 }, expect.any(AbortSignal));
  });

  it("offers all five exact history windows and queries the selected window", async () => {
    const user = userEvent.setup();
    vi.mocked((api as any).newsArchive).mockImplementation((query: { days: EvidenceHistoryDays }) => Promise.resolve({
      ...archiveListing,
      filters: { days: query.days, verification_status: null },
      events: [{ ...archivedRow, title: `${query.days}天历史事件` }],
    }));
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    const range = within(history).getByRole("group", { name: "历史时间范围" });
    for (const days of [1, 3, 7, 30, 90] as const) {
      expect(within(range).getByRole("button", { name: `${days}天` })).toBeInTheDocument();
    }

    for (const days of [1, 3, 30, 90] as const) {
      await user.click(within(range).getByRole("button", { name: `${days}天` }));
      expect(await within(history).findByText(`${days}天历史事件`)).toBeInTheDocument();
      expect((api as any).newsArchive).toHaveBeenLastCalledWith({ days, limit: 100 }, expect.any(AbortSignal));
      expect(within(range).getByRole("button", { name: `${days}天` })).toHaveAttribute("aria-pressed", "true");
    }
  });

  it("queries archive statuses without relabeling corrected conflicting or disproved rows as trusted", async () => {
    const user = userEvent.setup();
    vi.mocked((api as any).newsArchive).mockImplementation((query: { days: EvidenceHistoryDays; verification_status?: VerificationStatus }) => {
      const selected = query.verification_status ?? "corrected";
      const transition: EvidenceTransition = {
        from_status: "verified",
        to_status: selected,
        changed_at: "2026-08-02T08:00:00+00:00",
        reason: `历史状态 ${selected}`,
      };
      return Promise.resolve({
        ...archiveListing,
        filters: { days: query.days, verification_status: query.verification_status ?? null },
        events: [{
          ...archivedRow,
          title: `历史状态 ${selected}`,
          verification_status: selected,
          verification_reason: transition.reason,
          status_history: [{ ...archivedTransitions[0] }, transition],
        }],
      });
    });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    const statuses = within(history).getByRole("group", { name: "历史核验状态" });

    for (const [label, status] of [["已更正", "corrected"], ["存在冲突", "conflicting"], ["已证伪", "disproved"]] as const) {
      await user.click(within(statuses).getByRole("button", { name: `历史筛选：${label}` }));
      const title = await within(history).findByRole("heading", { name: `历史状态 ${status}` });
      expect((api as any).newsArchive).toHaveBeenLastCalledWith(
        { days: 7, verification_status: status, limit: 100 },
        expect.any(AbortSignal),
      );
      expect(within(title.closest("article")!).getByText(label)).toBeInTheDocument();
      expect(within(history).queryByText("可信准入")).not.toBeInTheDocument();
    }
  });

  it("shows 30-day and 90-day rows returned by the archive", async () => {
    const user = userEvent.setup();
    vi.mocked((api as any).newsArchive).mockImplementation((query: { days: EvidenceHistoryDays }) => Promise.resolve({
      ...archiveListing,
      filters: { days: query.days, verification_status: null },
      events: [{ ...archivedRow, title: query.days === 90 ? "第六十天归档事件" : "第二十天归档事件" }],
    }));
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await user.click(within(history).getByRole("button", { name: "30天" }));
    expect(await within(history).findByText("第二十天归档事件")).toBeInTheDocument();
    await user.click(within(history).getByRole("button", { name: "90天" }));
    expect(await within(history).findByText("第六十天归档事件")).toBeInTheDocument();
  });

  it("keeps committed history visible while a new archive window is loading", async () => {
    const user = userEvent.setup();
    let resolveThirty!: (value: EvidenceArchiveList) => void;
    vi.mocked((api as any).newsArchive)
      .mockResolvedValueOnce(archiveListing)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveThirty = resolve; }));
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await within(history).findByText(archivedRow.title);

    await user.click(within(history).getByRole("button", { name: "30天" }));
    expect(history).toHaveAttribute("aria-busy", "true");
    expect(within(history).getByText(archivedRow.title)).toBeInTheDocument();
    expect(within(history).getByText("正在载入证据历史…")).toBeInTheDocument();

    await act(async () => resolveThirty({ ...archiveListing, filters: { days: 30, verification_status: null } }));
  });

  it("retains the committed archive selection and rows when a history query fails", async () => {
    const user = userEvent.setup();
    vi.mocked((api as any).newsArchive)
      .mockResolvedValueOnce(archiveListing)
      .mockRejectedValueOnce(new Error("private backend path"));
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await within(history).findByText(archivedRow.title);

    await user.click(within(history).getByRole("button", { name: "90天" }));

    expect(await within(history).findByRole("alert")).toHaveTextContent("证据历史加载失败，请稍后重试。");
    expect(within(history).getByText(archivedRow.title)).toBeInTheDocument();
    expect(within(history).getByRole("button", { name: "7天" })).toHaveAttribute("aria-pressed", "true");
    expect(within(history).getByRole("button", { name: "90天" })).toHaveAttribute("aria-pressed", "false");
    expect(within(history).getByRole("alert")).toHaveTextContent("继续显示上次成功查询");
    expect(history).not.toHaveTextContent("private backend path");
  });

  it("shows an explicit archive no-results state without changing current counts", async () => {
    vi.mocked((api as any).newsArchive).mockResolvedValue({
      ...archiveListing,
      events: [], total: 0, provenance: [],
      page: { ...archiveListing.page, returned: 0 },
    });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    expect(within(history).getByText("所选时间和状态下暂无历史证据。")).toBeInTheDocument();
    expect(within(history).getByText("历史记录：0")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "真实核验概览" })).getByText("已核验").nextElementSibling).toHaveTextContent("3");
  });

  it("ignores a superseded history response and commits only the newest selection", async () => {
    const user = userEvent.setup();
    let resolveThirty!: (value: EvidenceArchiveList) => void;
    let thirtySignal!: AbortSignal;
    const stale = { ...archivedRow, title: "迟到的三十天历史" };
    const fresh = { ...archivedRow, event_id: "bbbbbbbbbbbbbbbbbbbb", title: "最新九十天历史" };
    vi.mocked((api as any).newsArchive)
      .mockResolvedValueOnce(archiveListing)
      .mockImplementationOnce((_query: unknown, signal: AbortSignal) => {
        thirtySignal = signal;
        return new Promise((resolve) => { resolveThirty = resolve; });
      })
      .mockResolvedValueOnce({ ...archiveListing, events: [fresh], filters: { days: 90, verification_status: null } });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await within(history).findByText(archivedRow.title);
    await user.click(within(history).getByRole("button", { name: "30天" }));
    await user.click(within(history).getByRole("button", { name: "90天" }));
    expect(thirtySignal.aborted).toBe(true);
    expect(await within(history).findByText(fresh.title)).toBeInTheDocument();

    await act(async () => resolveThirty({ ...archiveListing, events: [stale], filters: { days: 30, verification_status: null } }));
    expect(within(history).queryByText(stale.title)).not.toBeInTheDocument();
    expect(within(history).getByText(fresh.title)).toBeInTheDocument();
  });

  it("combines rapid history controls from the latest pending intent", async () => {
    const user = userEvent.setup();
    let resolveThirty!: (value: EvidenceArchiveList) => void;
    vi.mocked((api as any).newsArchive)
      .mockResolvedValueOnce(archiveListing)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveThirty = resolve; }))
      .mockResolvedValueOnce({
        ...archiveListing,
        filters: { days: 30, verification_status: "disproved" },
        events: [{ ...archivedRow, verification_status: "disproved", title: "三十天已证伪事件" }],
      });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await within(history).findByText(archivedRow.title);

    await user.click(within(history).getByRole("button", { name: "30天" }));
    await user.click(within(history).getByRole("button", { name: "历史筛选：已证伪" }));

    await waitFor(() => expect((api as any).newsArchive).toHaveBeenLastCalledWith(
      { days: 30, verification_status: "disproved", limit: 100 },
      expect.any(AbortSignal),
    ));
    expect(await within(history).findByText("三十天已证伪事件")).toBeInTheDocument();
    await act(async () => resolveThirty({ ...archiveListing, filters: { days: 30, verification_status: null } }));
  });

  it("loads every bounded archive page without changing the full history total", async () => {
    const user = userEvent.setup();
    const second = {
      ...archivedRow,
      event_id: "legacy-event-2",
      evidence_snapshot_id: "legacy snapshot 2",
      raw_snapshot_id: "raw snapshot 2",
      title: "第二页历史事件",
    };
    vi.mocked((api as any).newsArchive)
      .mockResolvedValueOnce({
        ...archiveListing,
        total: 2,
        page: { ...archiveListing.page, has_more: true, next_cursor: "opaque-page-2" },
      })
      .mockResolvedValueOnce({
        ...archiveListing,
        events: [second],
        total: 2,
        provenance: [],
        page: { ...archiveListing.page },
      });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await within(history).findByText(archivedRow.title);

    expect(within(history).getByText("历史记录：2")).toBeInTheDocument();
    expect(within(history).getByText("已加载：1")).toBeInTheDocument();
    await user.click(within(history).getByRole("button", { name: "加载更多历史证据" }));

    expect((api as any).newsArchive).toHaveBeenLastCalledWith(
      { days: 7, limit: 100, cursor: "opaque-page-2" },
      expect.any(AbortSignal),
    );
    expect(await within(history).findByText(second.title)).toBeInTheDocument();
    expect(within(history).getByText("已加载：2")).toBeInTheDocument();
    expect(within(history).getByText(archivedRow.title)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "真实核验概览" })).getByText("已核验").nextElementSibling).toHaveTextContent("3");
  });

  it.each([
    ["total drift", 3, 2, archiveQueryVersion],
    ["query-version drift", 2, 2, "2".repeat(64)],
    ["terminal cumulative count mismatch", 3, 3, archiveQueryVersion],
  ])("preserves the first archive page and requires a reload on %s", async (_name, firstTotal, secondTotal, secondVersion) => {
    const user = userEvent.setup();
    const second = { ...archivedRow, event_id: `second-${_name}`, title: `第二页-${_name}` };
    vi.mocked((api as any).newsArchive)
      .mockResolvedValueOnce({
        ...archiveListing,
        total: firstTotal,
        page: { ...archiveListing.page, has_more: true, next_cursor: "snapshot-page-2" },
      })
      .mockResolvedValueOnce({
        ...archiveListing,
        events: [second],
        total: secondTotal,
        page: { ...archiveListing.page, query_version: secondVersion },
      });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await within(history).findByText(archivedRow.title);

    await user.click(within(history).getByRole("button", { name: "加载更多历史证据" }));

    expect(await within(history).findByRole("alert")).toHaveTextContent("证据历史已变化");
    expect(within(history).getByText(archivedRow.title)).toBeInTheDocument();
    expect(within(history).queryByText(second.title)).not.toBeInTheDocument();
    expect(within(history).getByText("已加载：1")).toBeInTheDocument();
    expect(within(history).getByRole("button", { name: "重新加载历史证据" })).toBeInTheDocument();
    expect(within(history).queryByRole("button", { name: "加载更多历史证据" })).not.toBeInTheDocument();
  });

  it("keeps loaded facts on a stale 409 and can restart from the first page", async () => {
    const user = userEvent.setup();
    const refreshed = { ...archivedRow, event_id: "refreshed-event", title: "重新首载后的历史事件" };
    vi.mocked((api as any).newsArchive)
      .mockResolvedValueOnce({
        ...archiveListing,
        total: 2,
        page: { ...archiveListing.page, has_more: true, next_cursor: "stale-page-2" },
      })
      .mockRejectedValueOnce(new ApiError("证据历史请求失败", 409))
      .mockResolvedValueOnce({
        ...archiveListing,
        events: [refreshed],
        total: 1,
        page: { ...archiveListing.page, query_version: "3".repeat(64) },
      });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await within(history).findByText(archivedRow.title);

    await user.click(within(history).getByRole("button", { name: "加载更多历史证据" }));

    expect(await within(history).findByRole("alert")).toHaveTextContent("证据历史已变化");
    expect(within(history).getByText(archivedRow.title)).toBeInTheDocument();
    expect(within(history).queryByRole("button", { name: "加载更多历史证据" })).not.toBeInTheDocument();
    await user.click(within(history).getByRole("button", { name: "重新加载历史证据" }));
    expect(await within(history).findByText(refreshed.title)).toBeInTheDocument();
    expect(within(history).queryByText(archivedRow.title)).not.toBeInTheDocument();
    expect((api as any).newsArchive).toHaveBeenLastCalledWith(
      { days: 7, limit: 100 },
      expect.any(AbortSignal),
    );
  });

  it("never appends a stale load-more page after the filters change", async () => {
    const user = userEvent.setup();
    let resolveOldPage!: (value: EvidenceArchiveList) => void;
    let oldPageSignal!: AbortSignal;
    const stale = { ...archivedRow, event_id: "legacy-stale-page", title: "旧筛选迟到页" };
    const fresh = { ...archivedRow, event_id: "legacy-fresh-page", title: "三十天新首屏" };
    vi.mocked((api as any).newsArchive)
      .mockResolvedValueOnce({
        ...archiveListing,
        total: 2,
        page: { ...archiveListing.page, has_more: true, next_cursor: "old-page" },
      })
      .mockImplementationOnce((_query: unknown, signal: AbortSignal) => {
        oldPageSignal = signal;
        return new Promise((resolve) => { resolveOldPage = resolve; });
      })
      .mockResolvedValueOnce({
        ...archiveListing,
        events: [fresh],
        filters: { days: 30, verification_status: null },
        page: { ...archiveListing.page },
      });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await within(history).findByText(archivedRow.title);

    await user.click(within(history).getByRole("button", { name: "加载更多历史证据" }));
    await user.click(within(history).getByRole("button", { name: "30天" }));
    expect(oldPageSignal.aborted).toBe(true);
    expect(await within(history).findByText(fresh.title)).toBeInTheDocument();

    await act(async () => resolveOldPage({
      ...archiveListing,
      events: [stale],
      total: 2,
      page: { ...archiveListing.page },
    }));
    expect(within(history).queryByText(stale.title)).not.toBeInTheDocument();
    expect(within(history).getByText(fresh.title)).toBeInTheDocument();
  });

  it("opens archived detail from the returned row and restores focus", async () => {
    const user = userEvent.setup();
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    const trigger = await within(history).findByRole("button", { name: `查看历史证据 ${archivedRow.title}` });
    await user.click(trigger);

    const drawer = await screen.findByRole("dialog", { name: "证据详情" });
    expect(drawer).toHaveTextContent("官方公告更正项目日期");
    expect(drawer).toHaveTextContent("公开证据摘要");
    expect(api.evidenceEvent).not.toHaveBeenCalledWith(archivedRow.event_id);
    await user.click(within(drawer).getByRole("button", { name: "关闭证据详情" }));
    expect(trigger).toHaveFocus();
  });

  it("shows an explicit no-link state for a backend-legal empty canonical URL", async () => {
    const user = userEvent.setup();
    const withoutUrl = {
      ...archivedRow,
      primary_evidence: [{ ...archivedRow.primary_evidence[0], canonical_url: "" }],
    };
    vi.mocked((api as any).newsArchive).mockResolvedValueOnce({ ...archiveListing, events: [withoutUrl] });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await user.click(await within(history).findByRole("button", { name: `查看历史证据 ${withoutUrl.title}` }));

    const drawer = await screen.findByRole("dialog", { name: "证据详情" });
    expect(within(drawer).getByText("未提供公开链接")).toBeInTheDocument();
    expect(within(drawer).queryByRole("link", { name: /官方公告/ })).not.toBeInTheDocument();
  });

  it("aborts a current detail request before opening an archived row and fences its late promise", async () => {
    const user = userEvent.setup();
    let resolveCurrent!: (value: EvidenceEventDetail) => void;
    let currentSignal: AbortSignal | undefined;
    vi.mocked(api.evidenceEvent).mockImplementationOnce((_eventId, signal) => {
      currentSignal = signal;
      return new Promise((resolve) => { resolveCurrent = resolve; });
    });
    render(<EvidenceCenter />);
    await screen.findByText(row.title);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await user.click(screen.getByRole("button", { name: "查看证据" }));
    await screen.findByRole("dialog", { name: "证据详情" });

    fireEvent.click(await within(history).findByRole("button", { name: `查看历史证据 ${archivedRow.title}` }));
    expect(currentSignal?.aborted).toBe(true);
    expect(screen.getByRole("dialog", { name: "证据详情" })).toHaveTextContent(archivedRow.title);

    await act(async () => resolveCurrent(detail));
    expect(screen.getByRole("dialog", { name: "证据详情" })).toHaveTextContent(archivedRow.title);
    expect(screen.getByRole("dialog", { name: "证据详情" })).not.toHaveTextContent(detail.title);
  });

  it("fences a late URL auto-open detail after the drawer closes", async () => {
    window.history.replaceState({}, "", `/evidence-center?event_id=${row.event_id}`);
    let resolveUrl!: (value: EvidenceEventDetail) => void;
    let urlSignal: AbortSignal | undefined;
    vi.mocked(api.evidenceEvent).mockImplementationOnce((_eventId, signal) => {
      urlSignal = signal;
      return new Promise((resolve) => { resolveUrl = resolve; });
    });
    const user = userEvent.setup();
    render(<EvidenceCenter />);
    const drawer = await screen.findByRole("dialog", { name: "证据详情" });

    await user.click(within(drawer).getByRole("button", { name: "关闭证据详情" }));
    expect(urlSignal?.aborted).toBe(true);
    await act(async () => resolveUrl(detail));

    expect(screen.queryByRole("dialog", { name: "证据详情" })).not.toBeInTheDocument();
  });

  it("aborts and fences URL detail work on unmount", async () => {
    window.history.replaceState({}, "", `/evidence-center?event_id=${row.event_id}`);
    let urlSignal: AbortSignal | undefined;
    vi.mocked(api.evidenceEvent).mockImplementationOnce((_eventId, signal) => {
      urlSignal = signal;
      return new Promise(() => undefined);
    });
    const view = render(<EvidenceCenter />);
    await waitFor(() => expect(api.evidenceEvent).toHaveBeenCalled());

    view.unmount();

    expect(urlSignal?.aborted).toBe(true);
  });

  it("renders bounded archive lineage and recovery provenance as audit facts", async () => {
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    const card = (await within(history).findByText(archivedRow.title)).closest("article")!;
    expect(card).toHaveTextContent("快照沿革：1");
    expect(card).toHaveTextContent("证据快照 archive-");
    expect(card).toHaveTextContent("原始快照 archive-");
    expect(card).toHaveTextContent("缓存恢复");
    expect(card).not.toHaveTextContent(archiveLineage.content_digest);
    expect(card).not.toHaveTextContent(archiveLineage.raw_input_digest);
  });

  it("shortens archive IDs by Unicode scalar and visibly escapes hidden controls", async () => {
    const selected = {
      ...archivedRow,
      evidence_snapshot_id: `${"😀".repeat(9)}\u0000tail`,
      raw_snapshot_id: `raw\u202E${"界".repeat(9)}`,
    };
    vi.mocked((api as any).newsArchive).mockResolvedValueOnce({ ...archiveListing, events: [selected] });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    const card = (await within(history).findByText(selected.title)).closest("article")!;

    expect(card).toHaveTextContent(`证据快照 ${"😀".repeat(8)}…`);
    expect(card).toHaveTextContent("原始快照 raw\\u{202e}界界界界…");
    expect(card.textContent).not.toContain("\u202E");
  });

  it("renders every archive lineage and recovery fact through a bounded accessible disclosure", async () => {
    const user = userEvent.setup();
    const lineages = Array.from({ length: 21 }, (_, index) => ({
      evidence_snapshot_id: index === 0 ? "evidence\u0000root" : `evidence-${String(index).padStart(2, "0")}`,
      raw_snapshot_id: index === 0 ? "raw\u202Eroot" : `raw-${String(index).padStart(2, "0")}`,
      generated_at: `2026-08-01T08:00:${String(index).padStart(2, "0")}.00000${index % 10}+00:00`,
      content_digest: `${(index % 16).toString(16)}`.repeat(64),
      raw_input_digest: `${((index + 1) % 16).toString(16)}`.repeat(64),
      ...(index === 0 || index === 20 ? { recovery: {
        source: index === 0 ? "evidence_history" as const : "legacy_snapshot" as const,
        status: "cache_recovered" as const,
        recovered_at: `2026-08-19T08:00:${String(index).padStart(2, "0")}+00:00`,
        source_snapshot_id: index === 0 ? "source\u0007snapshot" : `source-${index}`,
      } } : {}),
    }));
    const selected = {
      ...archivedRow,
      evidence_snapshot_id: lineages[20].evidence_snapshot_id,
      raw_snapshot_id: lineages[20].raw_snapshot_id,
      snapshot_generated_at: lineages[20].generated_at,
      last_updated_at: lineages[20].generated_at,
      raw_input_digest: lineages[20].raw_input_digest,
      snapshot_history: lineages,
    } as EvidenceArchiveEvent;
    vi.mocked((api as any).newsArchive).mockResolvedValueOnce({ ...archiveListing, events: [selected] });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    await user.click(await within(history).findByRole("button", { name: `查看历史证据 ${selected.title}` }));
    const drawer = await screen.findByRole("dialog", { name: "证据详情" });

    const auditHeading = within(drawer).getByRole("heading", { name: "快照沿革与恢复来源" });
    expect(auditHeading).toBeInTheDocument();
    expect(within(drawer).getByText("已显示 20 / 共 21 条")).toBeInTheDocument();
    expect(drawer).toHaveTextContent("evidence\\u{0000}root");
    expect(drawer).toHaveTextContent("raw\\u{202e}root");
    expect(drawer).toHaveTextContent("source\\u{0007}snapshot");
    expect(drawer).toHaveTextContent("evidence_history");
    expect(drawer).toHaveTextContent("cache_recovered");
    expect(drawer).not.toHaveTextContent(lineages[0].content_digest);
    expect(drawer).not.toHaveTextContent(lineages[0].raw_input_digest);
    expect(drawer).not.toHaveTextContent("evidence-20");

    const more = within(drawer).getByRole("button", { name: "显示更多快照沿革" });
    await user.click(more);
    expect(within(drawer).getByText("已显示 21 / 共 21 条")).toBeInTheDocument();
    expect(drawer).toHaveTextContent("evidence-20");
    expect(within(drawer).queryByRole("button", { name: "显示更多快照沿革" })).not.toBeInTheDocument();
    expect(auditHeading).toHaveFocus();
  });

  it("neutralizes hidden archive controls in every visible audit string while preserving real newlines", async () => {
    const user = userEvent.setup();
    const selected = {
      ...archivedRow,
      title: "归档\u0000标题\u202E\n第二行",
      core_claim: "主张\u0007内容",
      verification_reason: "理由\u2066文本",
      primary_evidence: [{
        ...archivedRow.primary_evidence[0],
        title: "证据\u200B标题",
        content_source: "来源\u0001名称",
        collector_source: "采集\u0002来源",
        origin_cluster: "链\u202D路",
        excerpt: "第一行\n第二行\u0003",
      }],
    };
    vi.mocked((api as any).newsArchive).mockResolvedValueOnce({ ...archiveListing, events: [selected] });
    render(<EvidenceCenter />);
    const history = await screen.findByRole("region", { name: "证据历史" });
    const trigger = await within(history).findByRole("button", { name: /查看历史证据 归档\\u\{0000\}标题/ });
    expect(history.textContent).not.toMatch(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B\u202D\u202E\u2066]/u);
    await user.click(trigger);
    const drawer = await screen.findByRole("dialog", { name: "证据详情" });

    expect(drawer.textContent).not.toMatch(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B\u202D\u202E\u2066]/u);
    expect(drawer).toHaveTextContent("主张\\u{0007}内容");
    expect(drawer).toHaveTextContent("理由\\u{2066}文本");
    expect(drawer).toHaveTextContent("来源\\u{0001}名称");
    expect(drawer).toHaveTextContent("第一行 第二行\\u{0003}");
    expect(selected.primary_evidence[0].excerpt).toContain("\n");
  });

  it("uses the exact archive query and forwards its AbortSignal through the real client", async () => {
    const payload: EvidenceArchiveList = {
      ...archiveListing,
      events: [],
      total: 0,
      filters: { days: 90, verification_status: "disproved" },
      provenance: [],
      page: { ...archiveListing.page, returned: 0 },
    } as EvidenceArchiveList;
    const fetchMock = restoreArchiveClientWithResponse({ data: payload });
    const signal = new AbortController().signal;

    await expect(api.newsArchive({ days: 90, verification_status: "disproved", limit: 100 }, signal)).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/news/archive?days=90&verification_status=disproved&limit=100",
      expect.objectContaining({ method: "GET", signal }),
    );
  });

  it("normalizes omitted optional archive recovery provenance to an empty list", async () => {
    const payload = archiveWireListing();
    delete payload.events[0].snapshot_history[0].recovery;
    delete payload.provenance[0].snapshot_history[0].recovery;
    delete payload.provenance[0].recovery;
    restoreArchiveClientWithResponse({ data: payload });

    const result = await api.newsArchive({ days: 7, limit: 100 });

    expect(result.provenance[0].recovery).toEqual([]);
  });

  it("accepts a merged archive whose event digest differs from its current input lineage", async () => {
    const payload = archiveWireListing();
    const earlier = {
      ...structuredClone(payload.events[0].snapshot_history[0]),
      evidence_snapshot_id: "earlier-evidence-snapshot",
      raw_snapshot_id: "earlier-raw-snapshot",
      generated_at: "2026-07-20T08:00:00+00:00",
      content_digest: "c".repeat(64),
      raw_input_digest: "d".repeat(64),
    };
    delete earlier.recovery;
    payload.events[0].snapshot_history.unshift(earlier);
    payload.events[0].content_digest = "e".repeat(64);
    payload.provenance[0].snapshot_history = structuredClone(payload.events[0].snapshot_history);
    restoreArchiveClientWithResponse({ data: payload });

    const result = await api.newsArchive({ days: 7, limit: 100 });

    expect(result.events[0].content_digest).toBe("e".repeat(64));
    expect(result.events[0].snapshot_history[result.events[0].snapshot_history.length - 1]?.content_digest).toBe("a".repeat(64));
  });

  it("accepts a same-time status-priority current lineage that is not the array tail", async () => {
    const payload = archiveWireListing();
    const arrayTail = {
      ...structuredClone(payload.events[0].snapshot_history[0]),
      evidence_snapshot_id: "z-evidence-snapshot",
      raw_snapshot_id: "z-raw-snapshot",
      content_digest: "c".repeat(64),
      raw_input_digest: "d".repeat(64),
    };
    delete arrayTail.recovery;
    payload.events[0].snapshot_history.push(arrayTail);
    payload.provenance[0].snapshot_history = structuredClone(payload.events[0].snapshot_history);
    restoreArchiveClientWithResponse({ data: payload });

    const result = await api.newsArchive({ days: 7, limit: 100 });

    expect(result.events[0].verification_status).toBe("corrected");
    expect(result.events[0].evidence_snapshot_id).toBe("archive-evidence-snapshot");
    expect(result.events[0].snapshot_history[result.events[0].snapshot_history.length - 1]?.evidence_snapshot_id).toBe("z-evidence-snapshot");
  });

  it("uses Python Unicode code-point order for same-time lineage identities", async () => {
    const payload = archiveWireListing();
    const first = {
      ...structuredClone(payload.events[0].snapshot_history[0]),
      evidence_snapshot_id: "Z-snapshot",
      raw_snapshot_id: "Z-raw",
      content_digest: "c".repeat(64),
      raw_input_digest: "d".repeat(64),
    };
    const current = {
      ...structuredClone(payload.events[0].snapshot_history[0]),
      evidence_snapshot_id: "a-snapshot",
      raw_snapshot_id: "a-raw",
    };
    delete first.recovery;
    payload.events[0].snapshot_history = [first, current];
    payload.events[0].evidence_snapshot_id = current.evidence_snapshot_id;
    payload.events[0].raw_snapshot_id = current.raw_snapshot_id;
    payload.provenance[0].evidence_snapshot_id = current.evidence_snapshot_id;
    payload.provenance[0].raw_snapshot_id = current.raw_snapshot_id;
    payload.provenance[0].snapshot_history = structuredClone(payload.events[0].snapshot_history);
    restoreArchiveClientWithResponse({ data: payload });

    const result = await api.newsArchive({ days: 7, limit: 100 });

    expect(result.events[0].snapshot_history.map((item) => item.evidence_snapshot_id)).toEqual([
      "Z-snapshot", "a-snapshot",
    ]);
  });

  it("accepts a backend-budgeted archive page below the three MiB pagination limit", async () => {
    const payload = archiveWireListing();
    const generatedAt = payload.events[0].snapshot_generated_at;
    const lineages = Array.from({ length: 2_600 }, (_, index) => {
      const ordinal = String(index).padStart(4, "0");
      return {
        evidence_snapshot_id: `e-${ordinal}-${"e".repeat(120)}`.slice(0, 128),
        raw_snapshot_id: `r-${ordinal}-${"r".repeat(120)}`.slice(0, 128),
        generated_at: generatedAt,
        content_digest: "a".repeat(64),
        raw_input_digest: "b".repeat(64),
      };
    });
    const current = lineages[lineages.length - 1];
    payload.events[0].snapshot_history = lineages;
    payload.events[0].evidence_snapshot_id = current.evidence_snapshot_id;
    payload.events[0].raw_snapshot_id = current.raw_snapshot_id;
    payload.events[0].raw_input_digest = current.raw_input_digest;
    payload.provenance[0] = {
      event_id: payload.events[0].event_id,
      evidence_snapshot_id: current.evidence_snapshot_id,
      raw_snapshot_id: current.raw_snapshot_id,
      snapshot_history: structuredClone(lineages),
      recovery: [],
    };
    const body = { data: payload };
    const encodedBytes = new TextEncoder().encode(JSON.stringify(body)).byteLength;
    expect(encodedBytes).toBeGreaterThan(1_500_000);
    expect(encodedBytes).toBeLessThan(3 * 1_048_576);
    restoreArchiveClientWithResponse(body);

    await expect(api.newsArchive({ days: 7, limit: 100 })).resolves.toMatchObject({
      total: 1,
      page: { returned: 1, has_more: false },
    });
  });

  it("accepts backend-legal opaque archive IDs and an omitted public URL", async () => {
    const payload = archiveWireListing();
    payload.events[0].event_id = "legacy-event-1";
    payload.events[0].evidence_snapshot_id = "legacy snapshot 1";
    payload.events[0].raw_snapshot_id = "raw snapshot 1";
    payload.events[0].snapshot_history[0].evidence_snapshot_id = "legacy snapshot 1";
    payload.events[0].snapshot_history[0].raw_snapshot_id = "raw snapshot 1";
    payload.events[0].primary_evidence[0].canonical_url = "";
    payload.provenance[0].event_id = "legacy-event-1";
    payload.provenance[0].evidence_snapshot_id = "legacy snapshot 1";
    payload.provenance[0].raw_snapshot_id = "raw snapshot 1";
    payload.provenance[0].snapshot_history = structuredClone(payload.events[0].snapshot_history);
    restoreArchiveClientWithResponse({ data: payload });

    const result = await api.newsArchive({ days: 7, limit: 100 });

    expect(result.events[0].event_id).toBe("legacy-event-1");
    expect(result.events[0].evidence_snapshot_id).toBe("legacy snapshot 1");
    expect(result.events[0].primary_evidence[0].canonical_url).toBe("");
  });

  it("accepts the exact backend archive projection text limits", async () => {
    const payload = archiveWireListing();
    payload.events[0].title = "t".repeat(500);
    payload.events[0].summary = "s".repeat(1_200);
    payload.events[0].core_claim = "c".repeat(1_200);
    payload.events[0].primary_evidence[0].title = "e".repeat(500);
    payload.events[0].primary_evidence[0].excerpt = "x".repeat(1_200);
    payload.events[0].verification_reason = "r".repeat(8_192);
    payload.events[0].status_history[payload.events[0].status_history.length - 1].reason = payload.events[0].verification_reason;
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).resolves.toMatchObject({ total: 1 });
  });

  it("counts archive text and authority IDs by Unicode scalar instead of UTF-16 code units", async () => {
    const payload = archiveWireListing();
    const reason = "🧭".repeat(8_192);
    payload.events[0].title = "😀".repeat(500);
    payload.events[0].summary = "📝".repeat(1_200);
    payload.events[0].core_claim = "🧩".repeat(1_200);
    payload.events[0].verification_reason = reason;
    payload.events[0].status_history[payload.events[0].status_history.length - 1].reason = reason;
    payload.events[0].primary_evidence[0].title = "📄".repeat(500);
    payload.events[0].primary_evidence[0].excerpt = "🔎".repeat(1_200);
    payload.events[0].event_id = "🆔".repeat(128);
    payload.events[0].evidence_snapshot_id = "📸".repeat(128);
    payload.events[0].raw_snapshot_id = "🧪".repeat(128);
    payload.events[0].snapshot_history[0].evidence_snapshot_id = payload.events[0].evidence_snapshot_id;
    payload.events[0].snapshot_history[0].raw_snapshot_id = payload.events[0].raw_snapshot_id;
    payload.events[0].snapshot_history[0].recovery.source_snapshot_id = "🗄️".repeat(64);
    syncArchiveProvenance(payload);
    restoreArchiveClientWithResponse({ data: payload });

    const result = await api.newsArchive({ days: 7, limit: 100 });

    expect(Array.from(result.events[0].title)).toHaveLength(500);
    expect(Array.from(result.events[0].summary)).toHaveLength(1_200);
    expect(Array.from(result.events[0].verification_reason)).toHaveLength(8_192);
    expect(Array.from(result.events[0].event_id)).toHaveLength(128);
  });

  it("accepts backend-legal newlines and controls without mutating the shaped archive value", async () => {
    const payload = archiveWireListing();
    payload.events[0].summary = "首行\n次行\u0000审计";
    payload.events[0].primary_evidence[0].excerpt = "公开摘录\n第二行\u0007";
    restoreArchiveClientWithResponse({ data: payload });

    const result = await api.newsArchive({ days: 7, limit: 100 });

    expect(result.events[0].summary).toBe("首行\n次行\u0000审计");
    expect(result.events[0].primary_evidence[0].excerpt).toBe("公开摘录\n第二行\u0007");
  });

  it("rejects isolated UTF-16 surrogates even when their code-unit length is in bounds", async () => {
    const payload = archiveWireListing();
    payload.events[0].title = "broken-\uD800";
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it("accepts an exact 8192-scalar public URL and rejects the next scalar", async () => {
    const exact = `https://example.com/${"a".repeat(8_192 - "https://example.com/".length)}`;
    expect(Array.from(exact)).toHaveLength(8_192);
    const accepted = archiveWireListing();
    accepted.events[0].primary_evidence[0].canonical_url = exact;
    const fetchMock = restoreArchiveClientWithResponse({ data: accepted });
    await expect(api.newsArchive({ days: 7, limit: 100 })).resolves.toMatchObject({ total: 1 });

    const rejected = archiveWireListing();
    rejected.events[0].primary_evidence[0].canonical_url = `${exact}a`;
    fetchMock.mockResolvedValueOnce(archiveHttpResponse({ data: rejected }));
    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it.each([
    ["title", (payload: any) => { payload.events[0].title = "😀".repeat(501); }],
    ["summary", (payload: any) => { payload.events[0].summary = "📝".repeat(1_201); }],
    ["core claim", (payload: any) => { payload.events[0].core_claim = "🧩".repeat(1_201); }],
    ["excerpt", (payload: any) => { payload.events[0].primary_evidence[0].excerpt = "🔎".repeat(1_201); }],
    ["generic text", (payload: any) => { payload.events[0].category = "🗂️".repeat(4_097); }],
    ["authority id", (payload: any) => { payload.events[0].event_id = "🆔".repeat(129); }],
  ])("rejects one Unicode scalar beyond the backend field boundary: %s", async (_name, mutate) => {
    const payload = archiveWireListing();
    mutate(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it("orders lineage and status timestamps at full Python microsecond precision", async () => {
    const payload = archiveWireListing();
    const first = {
      ...structuredClone(payload.events[0].snapshot_history[0]),
      evidence_snapshot_id: "z-first",
      raw_snapshot_id: "z-raw",
      generated_at: "2026-08-01T08:00:00.000001+00:00",
      content_digest: "c".repeat(64),
      raw_input_digest: "d".repeat(64),
    };
    delete first.recovery;
    const current = {
      ...structuredClone(payload.events[0].snapshot_history[0]),
      evidence_snapshot_id: "a-current",
      raw_snapshot_id: "a-raw",
      generated_at: "2026-08-01T08:00:00.000002+00:00",
    };
    payload.events[0].snapshot_history = [first, current];
    payload.events[0].evidence_snapshot_id = current.evidence_snapshot_id;
    payload.events[0].raw_snapshot_id = current.raw_snapshot_id;
    payload.events[0].snapshot_generated_at = current.generated_at;
    payload.events[0].last_updated_at = current.generated_at;
    payload.events[0].raw_input_digest = current.raw_input_digest;
    payload.events[0].verified_at = current.generated_at;
    payload.events[0].evidence_as_of = current.generated_at;
    payload.events[0].status_history = [
      { from_status: null, to_status: "verified", changed_at: first.generated_at, reason: "first" },
      { from_status: "verified", to_status: "corrected", changed_at: current.generated_at, reason: payload.events[0].verification_reason },
    ];
    syncArchiveProvenance(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).resolves.toMatchObject({ total: 1 });
  });

  it("rejects reverse microsecond order that Date.parse aliases to one millisecond", async () => {
    const payload = archiveWireListing();
    payload.events[0].status_history = [
      { from_status: null, to_status: "verified", changed_at: "2026-08-01T08:00:00.000002+00:00", reason: "first" },
      { from_status: "verified", to_status: "corrected", changed_at: "2026-08-01T08:00:00.000001+00:00", reason: payload.events[0].verification_reason },
    ];
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it.each([
    "0001-01-01T00:00:00+00:00",
    "2024-02-29T23:59:59.999999+00:00",
  ])("accepts a real Gregorian UTC boundary timestamp: %s", async (timestamp) => {
    const payload = archiveWireListing();
    setArchiveClock(payload, timestamp);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).resolves.toMatchObject({ total: 1 });
  });

  it.each([
    "2026-02-29T08:00:00+00:00",
    "2026-08-01T08:00:00Z",
    "0000-01-01T00:00:00+00:00",
    "10000-01-01T00:00:00+00:00",
    "9999-12-31T23:59:59.999999+00:00",
  ])("rejects a noncanonical or impossible archive UTC timestamp: %s", async (timestamp) => {
    const payload = archiveWireListing();
    setArchiveClock(payload, timestamp);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it("rejects an event fact one microsecond beyond the backend snapshot five-minute skew", async () => {
    const payload = archiveWireListing();
    payload.events[0].snapshot_generated_at = "2026-08-01T08:00:00+00:00";
    payload.events[0].last_updated_at = payload.events[0].snapshot_generated_at;
    payload.events[0].snapshot_history[0].generated_at = payload.events[0].snapshot_generated_at;
    payload.events[0].verified_at = "2026-08-01T08:05:00.000001+00:00";
    payload.events[0].evidence_as_of = "2026-08-01T08:00:00+00:00";
    payload.events[0].status_history = [{
      from_status: null,
      to_status: payload.events[0].verification_status,
      changed_at: payload.events[0].verified_at,
      reason: payload.events[0].verification_reason,
    }];
    syncArchiveProvenance(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it("uses structural lineage identity so backend-legal NUL IDs cannot collide", async () => {
    const payload = archiveWireListing();
    const first = {
      ...structuredClone(payload.events[0].snapshot_history[0]),
      evidence_snapshot_id: "a",
      raw_snapshot_id: "b\u0000c",
      content_digest: "c".repeat(64),
      raw_input_digest: "d".repeat(64),
    };
    delete first.recovery;
    const current = {
      ...structuredClone(payload.events[0].snapshot_history[0]),
      evidence_snapshot_id: "a\u0000b",
      raw_snapshot_id: "c",
    };
    payload.events[0].snapshot_history = [first, current];
    payload.events[0].evidence_snapshot_id = current.evidence_snapshot_id;
    payload.events[0].raw_snapshot_id = current.raw_snapshot_id;
    payload.events[0].raw_input_digest = current.raw_input_digest;
    syncArchiveProvenance(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).resolves.toMatchObject({ total: 1 });
  });

  it("accepts a later null status transition as a backend reappearance root", async () => {
    const payload = archiveWireListing();
    payload.events[0].status_history[1].from_status = null;
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).resolves.toMatchObject({ total: 1 });
  });

  it("accepts nonblank status reasons with backend-legal surrounding whitespace", async () => {
    const payload = archiveWireListing();
    payload.events[0].verification_reason = "  official correction  ";
    payload.events[0].status_history[1].reason = payload.events[0].verification_reason;
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).resolves.toMatchObject({ total: 1 });
  });

  it.each([
    ["empty history", (payload: any) => { payload.events[0].status_history = []; }],
    ["first transition has a previous status", (payload: any) => { payload.events[0].status_history[0].from_status = "unverified"; }],
    ["non-null discontinuity", (payload: any) => { payload.events[0].status_history[1].from_status = "disproved"; }],
    ["self transition", (payload: any) => { payload.events[0].status_history[1].to_status = "verified"; payload.events[0].verification_status = "verified"; }],
    ["final status reason mismatch", (payload: any) => { payload.events[0].verification_reason = "different"; }],
    ["Python-whitespace-only reason", (payload: any) => { payload.events[0].verification_reason = "\u0085"; payload.events[0].status_history[1].reason = "\u0085"; }],
  ])("rejects an archive status-history domain violation: %s", async (_name, mutate) => {
    const payload = archiveWireListing();
    mutate(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it.each([
    ["duplicate canonical URL", (payload: any) => { payload.events[0].independent_evidence[0].canonical_url = payload.events[0].primary_evidence[0].canonical_url; }],
    ["duplicate key-field reference", (payload: any) => { payload.events[0].key_fields[0].evidence_ids = ["official-1", "official-1"]; }],
    ["missing key-field reference", (payload: any) => { payload.events[0].key_fields[0].evidence_ids = ["missing-proof"]; }],
  ])("rejects an archive evidence-domain violation: %s", async (_name, mutate) => {
    const payload = archiveWireListing();
    mutate(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it("does not invent supports-field or collection-role constraints absent from the backend archive", async () => {
    const payload = archiveWireListing();
    payload.events[0].primary_evidence[0].supports_fields = ["historical-field-without-key-row"];
    payload.events[0].primary_evidence[0].source_role = "independent";
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).resolves.toMatchObject({ total: 1 });
  });

  it.each([
    ["event title 501", (payload: any) => { payload.events[0].title = "t".repeat(501); }],
    ["event summary 1201", (payload: any) => { payload.events[0].summary = "s".repeat(1_201); }],
    ["event core claim 1201", (payload: any) => { payload.events[0].core_claim = "c".repeat(1_201); }],
    ["evidence title 501", (payload: any) => { payload.events[0].primary_evidence[0].title = "e".repeat(501); }],
    ["evidence excerpt 1201", (payload: any) => { payload.events[0].primary_evidence[0].excerpt = "x".repeat(1_201); }],
    ["generic text 8193", (payload: any) => { payload.events[0].verification_reason = "r".repeat(8_193); }],
  ])("rejects archive text beyond its field contract: %s", async (_name, mutate) => {
    const payload = archiveWireListing();
    mutate(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it("rejects a filtered archive page containing a differently classified row", async () => {
    const payload = archiveWireListing();
    payload.filters.verification_status = "disproved";
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, verification_status: "disproved", limit: 100 }))
      .rejects.toMatchObject(archiveInvalidResponse());
  });

  it.each([
    ["unknown page field", (payload: any) => { payload.page.private_cursor = "secret"; }],
    ["returned count mismatch", (payload: any) => { payload.page.returned = 0; }],
    ["has-more without cursor", (payload: any) => { payload.page.has_more = true; }],
    ["total smaller than page", (payload: any) => { payload.total = 0; }],
  ])("rejects contradictory archive pagination: %s", async (_name, mutate) => {
    const payload = archiveWireListing();
    mutate(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it("requires the backend archive authority query version on every paged response", async () => {
    const payload = archiveWireListing();
    delete payload.page.query_version;
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it.each([
    ["not a digest", "version-1"],
    ["uppercase digest", "A".repeat(64)],
    ["short digest", "a".repeat(63)],
  ])("rejects an invalid archive query version: %s", async (_name, queryVersion) => {
    const payload = archiveWireListing();
    payload.page.query_version = queryVersion;
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it.each([
    ["zero progress", (payload: any) => {
      payload.events = [];
      payload.provenance = [];
      payload.total = 2;
      payload.page.returned = 0;
      payload.page.has_more = true;
      payload.page.next_cursor = "next-page";
    }],
    ["same next cursor", (payload: any) => {
      payload.total = 2;
      payload.page.has_more = true;
      payload.page.next_cursor = "current-page";
    }],
  ])("rejects an archive cursor page with %s", async (_name, mutate) => {
    const payload = archiveWireListing();
    mutate(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100, cursor: "current-page" })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it("redacts a non-success archive error while preserving its status", async () => {
    vi.mocked(api.newsArchive).mockRestore();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      detail: "C:\\Users\\private\\token.txt?api_key=secret",
    }), { status: 503, headers: { "Content-Type": "application/json" } }));

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toEqual(
      new ApiError("证据历史请求失败", 503),
    );
  });

  it("preserves AbortError for a cancelled archive transport", async () => {
    vi.mocked(api.newsArchive).mockRestore();
    const aborted = new DOMException("cancelled", "AbortError");
    vi.spyOn(globalThis, "fetch").mockRejectedValue(aborted);

    await expect(api.newsArchive({ days: 7, limit: 100 }, new AbortController().signal)).rejects.toBe(aborted);
  });

  it.each([
    ["unknown response key", (payload: any) => { payload.private_holdings = []; }],
    ["unknown event key such as full article body", (payload: any) => { payload.events[0].article_body = "private full text"; }],
    ["unknown verification status", (payload: any) => { payload.events[0].verification_status = "trusted"; }],
    ["overlong text", (payload: any) => { payload.events[0].title = "x".repeat(8_193); }],
    ["excessive nested array", (payload: any) => { payload.events[0].related_tags = Array.from({ length: 5_001 }, (_, index) => ({ id: `tag-${index}`, name: "标签" })); }],
    ["mismatched total", (payload: any) => { payload.total = 2; }],
    ["mismatched echoed filters", (payload: any) => { payload.filters.days = 30; }],
    ["mismatched provenance", (payload: any) => { payload.provenance[0].raw_snapshot_id = "other-raw-snapshot"; }],
    ["invalid digest", (payload: any) => { payload.events[0].content_digest = "not-a-digest"; }],
  ])("rejects hostile archive shaping: %s", async (_name, mutate) => {
    const payload = archiveWireListing();
    mutate(payload);
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it.each([
    "http://127.0.0.1/private",
    "https://user:password@example.com/report",
    "https://example.com/report?access_token=secret",
  ])("rejects an unsafe archived evidence URL: %s", async (unsafeUrl) => {
    const payload = archiveWireListing();
    payload.events[0].primary_evidence[0].canonical_url = unsafeUrl;
    restoreArchiveClientWithResponse({ data: payload });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toMatchObject(archiveInvalidResponse());
  });

  it("rejects an archive response with the wrong envelope", async () => {
    restoreArchiveClientWithResponse({ error: { detail: "private backend path" } });

    await expect(api.newsArchive({ days: 7, limit: 100 })).rejects.toEqual(new ApiError("证据历史响应无效", 502));
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

  it("does not let a slow archive refresh block trusted-published polling", async () => {
    const user = userEvent.setup();
    const evidenceSaved: NewsPipelineStatusData = {
      ...pipelineDone,
      phase: "evidence_saved",
      trusted_snapshot_id: null,
      displayed_trusted_snapshot_id: "trusted-old",
      displayed_trusted: { ...pipelineDone.displayed_trusted!, snapshot_id: "trusted-old" },
    };
    vi.mocked(api.newsPipelineStatus)
      .mockResolvedValueOnce(evidenceSaved)
      .mockResolvedValueOnce(pipelineDone);
    vi.mocked(api.newsArchive)
      .mockResolvedValueOnce(archiveListing)
      .mockImplementationOnce(() => new Promise(() => undefined));
    render(<EvidenceCenter />);
    await screen.findByText(row.title);
    await screen.findByRole("region", { name: "证据历史" });

    await user.click(screen.getByRole("button", { name: "运行核验" }));

    await waitFor(() => expect(api.newsPipelineStatus).toHaveBeenCalledTimes(2), { timeout: 1_400 });
    expect(await screen.findByText("核验刷新完成，已载入最新成功快照。")).toBeInTheDocument();
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
