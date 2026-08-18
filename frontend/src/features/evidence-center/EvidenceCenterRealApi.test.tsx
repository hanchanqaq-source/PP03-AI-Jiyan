import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { EvidenceCenter } from "./EvidenceCenterReal";

vi.mock("@/features/source-health/SourceHealthSummary", () => ({ SourceHealthSummary: ({ onOpenDetails }: { onOpenDetails: () => void }) => <button onClick={onOpenDetails}>A1 健康摘要</button> }));
vi.mock("@/features/source-health/SourceHealthWorkspace", () => ({ SourceHealthWorkspace: () => <section aria-label="来源库直接清单">完整数据源清单</section> }));

const transition = { from_status: "corroborated", to_status: "conflicting", changed_at: "2026-08-18T08:30:00+00:00", reason: "官方披露与转载金额不一致" };
const row = {
  event_id: "0123456789abcdef0123", title: "交易所公告：星河科技建设存储算力中心", published_at: "2026-08-18T07:00:00+00:00", verified_at: "2026-08-18T08:30:00+00:00", evidence_as_of: "2026-08-18T08:30:00+00:00", category: "company", related_tags: [{ id: "semiconductor", name: "半导体" }], core_claim: "公司公告建设存储算力中心", verification_status: "verified", verification_reason: "已有明确官方证据", verified_key_fields: [], pending_key_field_count: 1, conflicting_key_field_count: 0, primary_evidence_count: 1, independent_evidence_count: 1, syndicated_copy_count: 2, contradicting_evidence_count: 0, holding_relevance: "none", status_change_count: 1, latest_transition: transition,
};
const detail = {
  ...row, summary: "交易所公告确认星河科技建设存储算力中心。", key_fields: [{ field_name: "money", raw_value: "12亿元", normalized_value: "CNY:1200000000", verification_status: "unverified", evidence_ids: [], reason: "金额尚待核验" }],
  primary_evidence: [{ evidence_id: "official-1", content_source: "交易所公告", collector_source: "现有资讯源", canonical_url: "https://www.sse.com.cn/disclosure/a", published_at: "2026-08-18T07:00:00+00:00", source_role: "primary", origin_cluster: "publisher:sse.com.cn", supports_claim: true, supports_fields: ["money"], contradicts_claim: false, is_official: true, title: "官方公告", excerpt: "公开证据摘要" }],
  independent_evidence: [{ evidence_id: "independent-1", content_source: "独立来源", collector_source: "现有资讯源二", canonical_url: "https://example.org/report", published_at: "2026-08-18T07:10:00+00:00", source_role: "independent", origin_cluster: "publisher:example.org", supports_claim: true, supports_fields: [], contradicts_claim: false, is_official: false, title: "独立报道", excerpt: "一致摘要" }],
  syndicated_copies: [{ evidence_id: "copy-1", content_source: "转载来源", collector_source: "现有资讯源三", canonical_url: "https://example.net/copy", published_at: "2026-08-18T07:20:00+00:00", source_role: "syndicated", origin_cluster: "syndication:abc", supports_claim: true, supports_fields: [], contradicts_claim: false, is_official: false, title: "转载", excerpt: "同稿" }],
  contradicting_evidence: [], status_history: [transition],
};
const summary = { loaded: true, snapshot_id: "acceptance-snapshot", generated_at: "2026-08-18T08:30:00+00:00", counts: { verified: 3, corroborated: 3, unverified: 1, conflicting: 1, corrected: 0, disproved: 0 }, field_counts: { verified: 2, corroborated: 0, unverified: 1, conflicting: 0 }, admitted_count: 6, isolated_count: 2, last_refresh: { status: "completed", attempted_at: "2026-08-18T08:30:00+00:00" } };
const listing = { events: [row], snapshot_id: summary.snapshot_id, generated_at: summary.generated_at, total: 1, filters: { verification_status: null, tag_id: null, category: null, days: 7, holding_relevance: null } };

describe("EvidenceCenter real verification workspace", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/evidence-center");
    vi.restoreAllMocks();
    vi.spyOn(api as any, "evidenceSummary").mockResolvedValue(summary);
    vi.spyOn(api as any, "evidenceEvents").mockResolvedValue(listing);
    vi.spyOn(api as any, "evidenceEvent").mockResolvedValue(detail);
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
    await waitFor(() => expect((api as any).evidenceEvents).toHaveBeenLastCalledWith({ days: 7, verification_status: "unverified" }));
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

  it("auto-opens query detail and runs a real refresh", async () => {
    const user = userEvent.setup(); window.history.replaceState({}, "", "/evidence-center?event_id=0123456789abcdef0123"); render(<EvidenceCenter />);
    expect(await screen.findByRole("dialog", { name: "证据详情" })).toHaveTextContent("交易所公告：星河科技建设存储算力中心");
    await user.click(screen.getByRole("button", { name: "关闭证据详情" })); await user.click(screen.getByRole("button", { name: "运行核验" }));
    expect(await screen.findByText("核验刷新完成，已载入最新成功快照。")).toBeInTheDocument();
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
