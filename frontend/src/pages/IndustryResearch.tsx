import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, LoaderCircle } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { api, type IndustryResearchResponse, type IndustryWindowDays } from "@/lib/api";
import {
  CANDIDATE_REPORT_SECTIONS,
  CandidateOnlyIndustryReport,
  IndustryReport,
  IndustryReportAnchors,
} from "@/features/industry/IndustryReport";
import { REPORT_SECTIONS } from "@/features/industry/sections/shared";
import { INCOMPLETE_REPORT_MESSAGE } from "@/features/industry/templates";
import { SelectedTagBar } from "@/features/tags/SelectedTagBar";
import { TagSelector } from "@/features/tags/TagSelector";
import { usePageTags } from "@/features/tags/usePageTags";
import { useTagRequestCoordinator } from "@/features/tags/requestCoordinator";

const INDUSTRY_WINDOWS = new Set<IndustryWindowDays>([7, 30, 90]);

function navigationFromUrl(): { industryId: string | null; windowDays: IndustryWindowDays } {
  if (typeof window === "undefined") return { industryId: null, windowDays: 90 };
  const parameters = new URLSearchParams(window.location.search);
  const rawWindow = Number(parameters.get("window"));
  return {
    industryId: parameters.get("industry"),
    windowDays: INDUSTRY_WINDOWS.has(rawWindow as IndustryWindowDays)
      ? rawWindow as IndustryWindowDays : 90,
  };
}

function writeNavigation(
  industryId: string,
  windowDays: IndustryWindowDays,
  mode: "push" | "replace",
  preserveHash = false,
) {
  if (typeof window === "undefined") return;
  const parameters = new URLSearchParams(window.location.search);
  parameters.set("industry", industryId);
  parameters.set("window", String(windowDays));
  const url = `${window.location.pathname}?${parameters.toString()}${preserveHash ? window.location.hash : ""}`;
  const state = { industryResearch: { industryId, windowDays } };
  if (mode === "push") window.history.pushState(state, "", url);
  else window.history.replaceState(state, "", url);
}

export function IndustryResearch() {
  const tags = usePageTags("industry_research");
  const coordinator = useTagRequestCoordinator<IndustryResearchResponse>();
  const tagsRef = useRef(tags);
  tagsRef.current = tags;
  const reportTopRef = useRef<HTMLDivElement>(null);
  const bootstrappedRef = useRef(false);
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [response, setResponse] = useState<IndustryResearchResponse | null>(null);
  const [requested, setRequested] = useState<{ id: string; name: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState<IndustryWindowDays>(90);

  const completeUserNavigation = useCallback(() => {
    if (typeof window === "undefined") return;
    const hashId = window.location.hash.slice(1);
    if (REPORT_SECTIONS.some(([id]) => id === hashId)) {
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
    }
    window.requestAnimationFrame(() => {
      const reportTop = reportTopRef.current;
      if (typeof reportTop?.scrollIntoView === "function") reportTop.scrollIntoView({ block: "start" });
    });
  }, []);

  const clearForEmptySelection = useCallback(() => {
    coordinator.cancel();
    setResponse(null);
    setRequested(null);
    setLoading(false);
    setError(null);
    setWindowDays(90);
    completeUserNavigation();
  }, [completeUserNavigation, coordinator]);

  const loadReport = useCallback((
    industryId: string,
    industryName?: string,
    userInitiated = false,
    preActivated = false,
    targetWindow: IndustryWindowDays = 90,
    historyMode: "push" | "replace" | "none" = userInitiated ? "push" : "none",
  ) => {
    const name = industryName ?? tagsRef.current.tags.find((tag) => tag.id === industryId)?.name ?? industryId;
    if (preActivated) {
      coordinator.cancel();
      setResponse(null);
    }
    setRequested({ id: industryId, name });
    setWindowDays(targetWindow);
    setLoading(true);
    setError(null);
    void coordinator.run({
      pageKey: "industry_research",
      industryId,
      request: ({ signal }) => api.industryResearchReport(industryId, targetWindow, signal),
      commit: (value) => {
        if (value.requestedIndustryId !== industryId || (value.displayedIndustryId !== null && value.displayedIndustryId !== industryId)) {
          setError("行业报告身份校验失败；未显示可能串用的数据。");
          setLoading(false);
          return;
        }
        if (!preActivated && !tagsRef.current.activate(industryId)) {
          setLoading(false);
          return;
        }
        setResponse(value);
        setLoading(false);
        if (historyMode !== "none") {
          writeNavigation(industryId, targetWindow, historyMode, !userInitiated);
        }
        if (userInitiated) completeUserNavigation();
      },
    }).catch((failure) => {
      setLoading(false);
      setError(failure instanceof Error ? `行业报告读取失败：${failure.message}` : "行业报告读取失败；暂无可靠数据");
    });
  }, [completeUserNavigation, coordinator]);

  const removeTag = useCallback((id: string) => {
    const current = tagsRef.current;
    const wasActive = current.state.activeId === id;
    const saved = current.remove(id);
    if (saved === false) return false;
    if (!wasActive) return true;
    if (saved.activeId === "") clearForEmptySelection();
    else loadReport(saved.activeId, current.tags.find((tag) => tag.id === saved.activeId)?.name, true, true);
    return true;
  }, [clearForEmptySelection, loadReport]);

  const confirmTags = useCallback((ids: string[]) => {
    const current = tagsRef.current;
    const activeRemoved = current.state.activeId !== "" && !ids.includes(current.state.activeId);
    const saved = current.replace(ids);
    if (saved === false) return;
    setSelectorOpen(false);
    if (!activeRemoved) return;
    if (saved.activeId === "") clearForEmptySelection();
    else loadReport(saved.activeId, current.tags.find((tag) => tag.id === saved.activeId)?.name, true, true);
  }, [clearForEmptySelection, loadReport]);

  useEffect(() => {
    const active = tags.activeTag;
    if (!active || loading) return;
    if (!bootstrappedRef.current) {
      bootstrappedRef.current = true;
      const navigation = navigationFromUrl();
      const target = tags.tags.find((tag) => tag.id === navigation.industryId) ?? active;
      loadReport(target.id, target.name, false, false, navigation.windowDays, "replace");
      return;
    }
    if (requested === null || (response?.displayedIndustryId === requested.id && active.id !== requested.id)) {
      loadReport(active.id, active.name, false, false, windowDays, "replace");
    }
  }, [loadReport, loading, requested?.id, response?.displayedIndustryId, tags.activeTag, tags.tags, windowDays]);

  useEffect(() => {
    const restore = () => {
      const navigation = navigationFromUrl();
      const target = tagsRef.current.tags.find((tag) => tag.id === navigation.industryId);
      if (!target) return;
      loadReport(target.id, target.name, false, false, navigation.windowDays, "none");
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [loadReport]);

  const changeWindow = useCallback((nextWindow: IndustryWindowDays) => {
    const industryId = response?.displayedIndustryId ?? requested?.id ?? tagsRef.current.state.activeId;
    if (!industryId) return;
    const name = tagsRef.current.tags.find((tag) => tag.id === industryId)?.name;
    loadReport(industryId, name, false, false, nextWindow, "push");
  }, [loadReport, requested?.id, response?.displayedIndustryId]);

  const displayedName = response?.displayedIndustryId
    ? tags.tags.find((tag) => tag.id === response.displayedIndustryId)?.name ?? requested?.name ?? response.displayedIndustryId
    : requested?.name ?? tags.activeTag?.name ?? "当前行业";
  const report = response?.displayedTrustedReport ?? null;
  const candidateOnly = report === null ? response?.candidateEvidence ?? null : null;
  const anchorKey = report
    ? `${report.industryId}:${report.displayedTrustedSnapshotId ?? "none"}`
    : candidateOnly ? `${candidateOnly.industryId}:${candidateOnly.candidateSnapshotId}` : null;

  return (
    <div>
      <PageHeader title="行业研究" subtitle="连续产业研究报告；结论、依据、来源、更新时间和失效条件同时呈现" />
      <div className="sticky top-0 z-30 mb-5 rounded-xl border border-border/60 bg-background/95 px-2 backdrop-blur">
        <SelectedTagBar tags={tags.tags} activeId={tags.state.activeId} onActivate={(id) => loadReport(id, undefined, true)}
          onRemove={removeTag} onReorder={tags.reorder} onMove={tags.move} onAdd={() => setSelectorOpen(true)} />
        {!loading && report && <IndustryReportAnchors key={anchorKey ?? undefined} />}
        {!loading && candidateOnly && <IndustryReportAnchors key={anchorKey ?? undefined} sections={CANDIDATE_REPORT_SECTIONS} />}
      </div>
      {tags.errorMessage && !selectorOpen && <div role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4" />{tags.errorMessage}</div>}
      {error && <div role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4" />{error}</div>}
      {response && <div role="status" className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/70 bg-muted/10 px-4 py-3 text-xs"><span>最近一次刷新状态：{response.refreshRun.phase === "trusted_published" ? "可信快照已发布" : response.refreshRun.phase === "failed" ? "来源失败" : response.refreshRun.phase === "collecting" ? "正在采集" : response.refreshRun.phase === "verifying" ? "正在核验" : "空闲"}</span><span className="font-mono text-muted-foreground">run {response.refreshRun.runId ?? "暂无"}</span></div>}
      <div ref={reportTopRef} data-industry-report-top className="scroll-mt-40">
      {loading ? <div role="status" className="flex min-h-[360px] flex-col items-center justify-center rounded-2xl border border-border/70"><LoaderCircle className="h-6 w-6 animate-spin text-primary motion-reduce:animate-none" /><p className="mt-3 text-sm">正在读取{requested?.name ?? "当前行业"}的完整可信报告</p><p className="mt-1 text-xs leading-5 text-muted-foreground">原报告已隐藏，避免切换期间串用行业数据。</p></div>
        : report && response ? <IndustryReport report={report} candidate={response.candidateEvidence} industryName={displayedName} refreshRun={response.refreshRun} windowDays={windowDays} onWindowDaysChange={changeWindow} />
          : candidateOnly ? <CandidateOnlyIndustryReport candidate={candidateOnly} industryName={displayedName} windowDays={windowDays} onWindowDaysChange={changeWindow} />
          : response?.templateStatus === "building" ? (
        <div className="rounded-2xl border border-dashed border-border/70 px-6 py-20 text-center">
          <p className="text-lg font-semibold">{INCOMPLETE_REPORT_MESSAGE}</p>
          <p className="mt-2 text-sm text-muted-foreground">{displayedName}已进入共享标签库；当前状态为建设中；系统不会根据标签名称自动补造行业数据。</p>
        </div>
      ) : <div className="rounded-2xl border border-dashed border-border/70 px-6 py-20 text-center"><p className="text-lg font-semibold">暂无可靠数据</p><p className="mt-2 text-sm text-muted-foreground">{tags.state.ids.length === 0 ? "当前未选择行业标签；暂无可显示的可信快照。" : `当前已选择${tags.activeTag?.name ?? displayedName}，但暂无可信快照。`}</p></div>}
      </div>
      <TagSelector open={selectorOpen} selectedIds={tags.state.ids} customTags={tags.customTags}
        externalError={tags.errorMessage} onCreate={(name) => {
          const result = tags.create(name);
          loadReport(result.tag.id, result.tag.name, true, true);
          return result;
        }} onCancel={() => setSelectorOpen(false)}
        onConfirm={confirmTags} />
    </div>
  );
}
