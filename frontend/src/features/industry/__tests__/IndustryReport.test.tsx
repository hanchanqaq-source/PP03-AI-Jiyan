/// <reference types="vite/client" />
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToString } from "react-dom/server";
import { vi } from "vitest";
import { decodeIndustryResearchResponse, type IndustryResearchResponse } from "@/lib/api";
import { EvidenceDrawer } from "../EvidenceDrawer";
import { IndustryReport, IndustryReportAnchors } from "../IndustryReport";
import { formatEmptyReason } from "../sections/shared";
import { industryResponseWire, researchResponse } from "./fixtures";

const industryUiSources = import.meta.glob(["../**/*.tsx", "../../../pages/IndustryResearch.tsx"], {
  eager: true, import: "default", query: "?raw",
}) as Record<string, string>;

function reportView(response: IndustryResearchResponse = researchResponse(), days: 7 | 30 | 90 = 7, now?: () => Date) {
  return <IndustryReport report={response.displayedTrustedReport!} candidate={response.candidateEvidence}
    refreshRun={response.refreshRun} industryName={response.requestedIndustryId === "storage" ? "存储" : response.requestedIndustryId}
    windowDays={days as 7 | 30 | 90} onWindowDaysChange={() => undefined} now={now} />;
}

describe("continuous evidence-bound industry report", () => {
  it.each([
    ["source_unconfigured", "来源未配置"],
    ["source_unavailable", "来源不可用"],
    ["source_failed", "来源当前失败"],
    ["verifying", "数据正在核验"],
    ["not_applicable", "当前行业不适用"],
    ["not_disclosed", "暂无最新披露"],
    ["user_key_not_configured", "用户未配置 Key"],
    ["license_required", "需要许可证"],
    ["expired", "数据已失效"],
    ["conflicting", "存在冲突"],
    ["no_reliable_data", "暂无可靠数据"],
    ["insufficient_history", "历史样本不足"],
    ["future_unknown_reason", "暂无可靠数据"],
  ] as const)("maps the Task 7 empty reason %s without collapsing its meaning", (reason, expected) => {
    const metric = {
      ...researchResponse().displayedTrustedReport!.cycle[0],
      currentValue: null,
      emptyReason: reason,
    };
    expect(formatEmptyReason(metric)).toBe(expected);
  });

  it("decodes complete differentiated layouts before rendering them", () => {
    const storage = researchResponse("storage");
    const semiconductor = researchResponse("semiconductor");
    const robotics = researchResponse("robotics");
    expect(storage.displayedTrustedReport?.cycle).toHaveLength(8);
    expect(storage.displayedTrustedReport?.chain).toHaveLength(5);
    expect(storage.displayedTrustedReport?.capital).toHaveLength(4);
    expect(storage.displayedTrustedReport?.counts).toEqual({ verified: 2, corroborated: 1 });
    expect(storage.displayedTrustedReport?.overview.text).toBe("规则=storage-cycle-v1；状态=verified；周期=recovery；方向=improving；可信度=medium；完整度=1.00；证据=E-dram_price-official,E-nand_price-official");
    const hbm = storage.displayedTrustedReport?.cycle.find((item) => item.metricId === "hbm_demand");
    expect(hbm?.independentSourceFamilies).toHaveLength(2);
    expect(hbm?.independentContentSources).toHaveLength(2);
    expect(hbm?.independentOriginClusters).toHaveLength(2);
    expect(hbm?.evidence.map((item) => item.supportsFields)).toEqual([["hbm_demand"], ["hbm_demand"]]);
    expect(storage.displayedTrustedReport?.cycle.map((item) => item.metricId)).toEqual(expect.arrayContaining(["dram_price", "nand_price", "hbm_demand"]));
    for (const response of [semiconductor, robotics]) {
      expect(response.displayedTrustedReport?.cycle).toHaveLength(8);
      expect(response.displayedTrustedReport?.capital).toHaveLength(4);
      expect(JSON.stringify(response)).not.toMatch(/dram_price|nand_price|hbm_demand|inventory_level/);
    }
    expect(semiconductor.displayedTrustedReport?.chain.map((item) => item.label)).toContain("晶圆制造");
    expect(robotics.displayedTrustedReport?.chain.map((item) => item.label)).toContain("核心零部件");
  });

  it("renders all eight sections in the fixed reading order on one report spine", () => {
    render(reportView(researchResponse(), 90));
    const article = screen.getByRole("article", { name: "存储行业研究报告" });
    const ids = Array.from(article.querySelectorAll(":scope > section")).map((node) => node.id);
    expect(ids).toEqual(["overview", "cycle", "chain", "metrics", "capital", "companies", "funds", "news-risk"]);
    expect(article).toHaveAttribute("data-industry-id", "storage");
    expect(article.querySelectorAll(".grid.lg\\:grid-cols-3")).toHaveLength(0);
  });

  it("shows every truth field, corroboration independence, and accurate null history", () => {
    render(reportView(researchResponse(), 90));
    for (const label of ["当前值", "数据来源", "数据更新时间", "数据口径", "判断依据", "失效条件", "对应证据"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText("来源未配置").length).toBeGreaterThan(0);
    const hbm = screen.getAllByText("HBM 需求")[0].closest("article")!;
    expect(hbm).toHaveTextContent("2 个独立来源族");
    expect(hbm).toHaveTextContent("2 个独立内容来源");
    expect(hbm).toHaveTextContent("2 个独立起源集群");
    expect(hbm).toHaveTextContent("共同支持字段：hbm_demand");
    expect(hbm).toHaveTextContent("历史位置暂无可靠数据");
    expect(hbm).not.toHaveTextContent("历史样本不足");
  });

  it("keeps candidate and full conflict evidence outside the trusted overview", () => {
    render(reportView(researchResponse(), 90));
    const overview = screen.getByRole("region", { name: "行业总览" });
    expect(overview).toHaveTextContent("dram_price");
    expect(overview).not.toHaveTextContent("storage_candidate_metric");
    expect(screen.getByText("待核验候选指标")).toBeInTheDocument();
    const conflict = screen.getByText("storage_conflict · 冲突值并列").closest("article")!;
    expect(conflict).toHaveTextContent("family-a · 上升");
    expect(conflict).toHaveTextContent("E-CONFLICT-A");
    expect(conflict).toHaveTextContent("2026-08-24");
    expect(conflict).toHaveTextContent("+2.1% MOM");
    expect(conflict).toHaveTextContent("family-b · 下降");
    expect(conflict).toHaveTextContent("-1.8% MOM");
    expect(screen.queryByText(/冲突综合值/)).not.toBeInTheDocument();
  });

  it("uses report generatedAt as the common clock, excludes future events, and lets no candidate shift trusted thresholds", () => {
    const wire = industryResponseWire();
    wire.candidate_evidence.unverified_events.push({
      ...wire.candidate_evidence.unverified_events[0],
      event_id: "STORAGE-CANDIDATE-FUTURE",
      occurred_at: "2026-09-20T00:00:00+00:00",
      evidence_ids: ["E-CANDIDATE-FUTURE"], supporting_evidence_ids: ["E-CANDIDATE-FUTURE"],
    });
    wire.candidate_evidence.counts.unverified_events += 1;
    const response = decodeIndustryResearchResponse(wire, new Date("2026-08-25T12:00:00+00:00"));
    render(reportView(response, 7));
    expect(screen.getByText("STORAGE-NEWS-7")).toBeInTheDocument();
    expect(screen.getByText("STORAGE-CANDIDATE-RECENT")).toBeInTheDocument();
    expect(screen.queryByText("STORAGE-NEWS-FUTURE")).not.toBeInTheDocument();
    expect(screen.queryByText("STORAGE-CANDIDATE-FUTURE")).not.toBeInTheDocument();
    expect(screen.queryByText("STORAGE-NEWS-30")).not.toBeInTheDocument();
  });

  it("shows zero seven-day events when all evidence is stale instead of moving the clock backwards", () => {
    const wire = industryResponseWire();
    wire.displayed_trusted_report.news_risk = wire.displayed_trusted_report.news_risk.map((item: any) => ({ ...item, occurred_at: "2026-06-01T00:00:00+00:00" }));
    wire.candidate_evidence.unverified_events = wire.candidate_evidence.unverified_events.map((item: any) => ({ ...item, occurred_at: "2026-06-01T00:00:00+00:00" }));
    wire.candidate_evidence.conflicting_events = wire.candidate_evidence.conflicting_events.map((item: any) => ({ ...item, occurred_at: "2026-06-01T00:00:00+00:00" }));
    const response = decodeIndustryResearchResponse(wire, new Date("2026-08-25T12:00:00+00:00"));
    render(reportView(response, 7));
    expect(screen.getByText("当前窗口没有已准入新闻、催化、风险或反向信号。")).toBeInTheDocument();
    expect(screen.getByText("暂无待核验事件")).toBeInTheDocument();
  });

  it("uses one injected fallback clock when generatedAt is absent", () => {
    const wire = industryResponseWire("storage", { generatedAt: null });
    const response = decodeIndustryResearchResponse(wire, new Date("2026-08-25T12:00:00+00:00"));
    const clock = vi.fn(() => new Date("2026-08-25T00:00:00+00:00"));
    render(reportView(response, 7, clock));
    expect(clock).toHaveBeenCalledTimes(1);
    expect(screen.getByText("STORAGE-NEWS-7")).toBeInTheDocument();
    expect(screen.queryByText("STORAGE-NEWS-FUTURE")).not.toBeInTheDocument();
  });

  it.each(["collecting", "verifying", "failed"] as const)("marks the displayed report as the old trusted snapshot during %s", (phase) => {
    const response = researchResponse("storage", { phase, demo: true });
    render(reportView(response, 90));
    expect(screen.getByText(/当前显示的旧可信快照/)).toHaveTextContent("TRUSTED-STORAGE-1");
    expect(screen.getByText(/隔离演示快照/)).toHaveTextContent("TRUSTED-STORAGE-1");
  });

  it("shows candidate-news evidence IDs and complete company relation/source fields", () => {
    render(reportView(researchResponse(), 90));
    const candidateEvent = screen.getByText("STORAGE-CANDIDATE-RECENT").closest("li")!;
    expect(candidateEvent).toHaveTextContent("E-CANDIDATE-RECENT");
    const company = screen.getAllByText("隔离演示公司")[0].closest("tr")!;
    expect(company).toHaveTextContent("与当前结论的关系");
    expect(company).toHaveTextContent("dram_price");
    expect(company).toHaveTextContent("数据来源");
    expect(company).toHaveTextContent("来源名称未随关系投影返回");
    expect(company).not.toHaveTextContent("数据来源来源未配置");
    expect(company).toHaveTextContent("E-COMPANY-1");
    const mobile = screen.getByTestId("company-mobile-DEMO-SEC-001");
    expect(mobile.querySelectorAll("[data-mobile-field-group]")).toHaveLength(3);
  });

  it("gives every overview basis metric a keyboard evidence trail and fails closed when association is absent", async () => {
    const user = userEvent.setup();
    const response = researchResponse();
    const { rerender } = render(reportView(response, 90));
    const overview = screen.getByRole("region", { name: "行业总览" });
    const dramTrigger = within(overview).getByRole("button", { name: "查看 DRAM 价格总览证据" });
    expect(within(overview).getByRole("button", { name: "查看 NAND 价格总览证据" })).toBeInTheDocument();

    dramTrigger.focus();
    await user.keyboard("{Enter}");
    const drawer = screen.getByRole("dialog", { name: "DRAM 价格证据" });
    expect(drawer).toHaveTextContent("source-official.example");
    expect(drawer).toHaveTextContent("2026-08-24");
    expect(drawer).toHaveTextContent("同口径公开快照；隔离测试值");
    expect(drawer).toHaveTextContent("dram_price 已有准入证据支持");
    expect(drawer).toHaveTextContent("已核验");
    expect(drawer).toHaveTextContent("dram_price 来源撤回或口径变化");
    expect(drawer).toHaveTextContent("E-dram_price-official");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(dramTrigger).toHaveFocus());

    const incomplete = researchResponse();
    incomplete.displayedTrustedReport!.overview.basisMetricIds = ["missing_metric"];
    rerender(reportView(incomplete, 90));
    const missing = within(screen.getByRole("region", { name: "行业总览" })).getByText("missing_metric").closest("li")!;
    expect(missing).toHaveTextContent("暂无可靠数据：未关联可信指标证据");
    expect(within(missing).queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders conflict event time, Chinese roles, and parallel evidence inside its local window", () => {
    render(reportView(researchResponse(), 30));
    const conflict = screen.getByText("STORAGE-CONFLICT-30").closest("li")!;
    expect(conflict).toHaveTextContent("2026-08-05T00:00:00+00:00");
    expect(conflict).toHaveTextContent("风险");
    expect(conflict).toHaveTextContent("支持证据 E-CONFLICT-A");
    expect(conflict).toHaveTextContent("反驳证据 E-CONFLICT-B");
  });

  it("marks companies as observations and does not turn them into recommendations", () => {
    render(reportView(researchResponse(), 90));
    expect(screen.getByText("观察对象，不构成推荐")).toBeInTheDocument();
    expect(screen.queryByText(/建议买入|建议卖出|基金排行榜/)).not.toBeInTheDocument();
  });

  it("keeps all industry auxiliary text at twelve pixels or larger", () => {
    const undersizedHelperClass = `text-[${10}px]`;
    const productionSources = Object.entries(industryUiSources).filter(([path]) => !path.includes("/__tests__/"));
    expect(productionSources.filter(([, source]) => source.includes(undersizedHelperClass)).map(([path]) => path)).toEqual([]);
    const { container } = render(reportView(researchResponse(), 90));
    const undersized = Array.from(container.querySelectorAll<HTMLElement>("[class]"))
      .filter((node) => (node.getAttribute("class") ?? "").split(/\s+/u).includes(undersizedHelperClass));
    expect(undersized).toHaveLength(0);
    expect(screen.getByText(/DRAM、NAND 与 HBM/)).toHaveClass("text-sm");
  });
});

describe("industry report navigation and evidence dismissal", () => {
  it("renders anchors without accessing window during SSR", () => {
    const currentWindow = globalThis.window;
    vi.stubGlobal("window", undefined);
    try {
      expect(() => renderToString(<IndustryReportAnchors />)).not.toThrow();
    } finally {
      vi.stubGlobal("window", currentWindow);
    }
  });

  it("updates aria-current from intersection and browser hash navigation, then cleans observers", () => {
    let callback!: IntersectionObserverCallback;
    const disconnect = vi.fn();
    const observe = vi.fn();
    vi.stubGlobal("IntersectionObserver", class {
      constructor(next: IntersectionObserverCallback) { callback = next; }
      observe = observe; disconnect = disconnect; unobserve = vi.fn(); takeRecords = () => []; root = null; rootMargin = ""; thresholds = [];
    });
    const view = render(<><IndustryReportAnchors /><section id="overview" /><section id="metrics" /><section id="cycle" /></>);
    act(() => callback([{ isIntersecting: true, intersectionRatio: 1, target: document.getElementById("metrics")! } as unknown as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(screen.getByRole("link", { name: "核心数据" })).toHaveAttribute("aria-current", "location");
    act(() => { history.pushState(null, "", "#cycle"); window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(screen.getByRole("link", { name: "周期" })).toHaveAttribute("aria-current", "location");
    view.unmount();
    expect(disconnect).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("restores trigger focus for overlay, Escape, and close-button dismissal", async () => {
    const user = userEvent.setup();
    const metric = researchResponse().displayedTrustedReport!.cycle[0];
    const { container } = render(<EvidenceDrawer metric={metric} />);
    const trigger = screen.getByRole("button", { name: "查看 DRAM 价格证据" });

    await user.click(trigger);
    fireEvent.mouseDown(container.querySelector(".fixed.inset-0")!);
    await act(async () => { await new Promise((done) => requestAnimationFrame(done)); });
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.keyboard("{Escape}");
    await act(async () => { await new Promise((done) => requestAnimationFrame(done)); });
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "关闭证据" }));
    await act(async () => { await new Promise((done) => requestAnimationFrame(done)); });
    expect(trigger).toHaveFocus();
  });
});
