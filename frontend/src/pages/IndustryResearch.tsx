import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, LoaderCircle } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { api, type IndustryResearchResponse, type IndustryWindowDays } from "@/lib/api";
import { IndustryReport, IndustryReportAnchors } from "@/features/industry/IndustryReport";
import { INCOMPLETE_REPORT_MESSAGE } from "@/features/industry/templates";
import { SelectedTagBar } from "@/features/tags/SelectedTagBar";
import { TagSelector } from "@/features/tags/TagSelector";
import { usePageTags } from "@/features/tags/usePageTags";
import { useTagRequestCoordinator } from "@/features/tags/requestCoordinator";

export function IndustryResearch() {
  const tags = usePageTags("industry_research");
  const coordinator = useTagRequestCoordinator<IndustryResearchResponse>();
  const tagsRef = useRef(tags);
  tagsRef.current = tags;
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [response, setResponse] = useState<IndustryResearchResponse | null>(null);
  const [requested, setRequested] = useState<{ id: string; name: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState<IndustryWindowDays>(90);

  const loadReport = useCallback((industryId: string, industryName?: string) => {
    const name = industryName ?? tagsRef.current.tags.find((tag) => tag.id === industryId)?.name ?? industryId;
    setRequested({ id: industryId, name });
    setWindowDays(90);
    setLoading(true);
    setError(null);
    void coordinator.run({
      pageKey: "industry_research",
      industryId,
      request: ({ signal }) => api.industryResearchReport(industryId, 90, signal),
      commit: (value) => {
        if (value.requestedIndustryId !== industryId || (value.displayedIndustryId !== null && value.displayedIndustryId !== industryId)) {
          setError("行业报告身份校验失败；未显示可能串用的数据。");
          setLoading(false);
          return;
        }
        if (!tagsRef.current.activate(industryId)) {
          setLoading(false);
          return;
        }
        setResponse(value);
        setLoading(false);
      },
    }).catch((failure) => {
      setLoading(false);
      setError(failure instanceof Error ? `行业报告读取失败：${failure.message}` : "行业报告读取失败；暂无可靠数据");
    });
  }, [coordinator]);

  useEffect(() => {
    const active = tags.activeTag;
    if (!active || loading) return;
    if (requested === null || (response?.displayedIndustryId === requested.id && active.id !== requested.id)) {
      loadReport(active.id, active.name);
    }
  }, [loadReport, loading, requested?.id, response?.displayedIndustryId, tags.activeTag]);

  const displayedName = response?.displayedIndustryId
    ? tags.tags.find((tag) => tag.id === response.displayedIndustryId)?.name ?? requested?.name ?? response.displayedIndustryId
    : requested?.name ?? tags.activeTag?.name ?? "当前行业";
  const report = response?.displayedTrustedReport ?? null;

  return (
    <div>
      <PageHeader title="行业研究" subtitle="连续产业研究报告；结论、依据、来源、更新时间和失效条件同时呈现" />
      <div className="sticky top-0 z-30 mb-5 rounded-xl border border-border/60 bg-background/95 px-2 backdrop-blur">
        <SelectedTagBar tags={tags.tags} activeId={tags.state.activeId} onActivate={(id) => loadReport(id)}
          onRemove={tags.remove} onReorder={tags.reorder} onMove={tags.move} onAdd={() => setSelectorOpen(true)} />
        {report && <IndustryReportAnchors />}
      </div>
      {tags.errorMessage && !selectorOpen && <div role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4" />{tags.errorMessage}</div>}
      {error && <div role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4" />{error}</div>}
      {response && <div role="status" className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/70 bg-muted/10 px-4 py-3 text-xs"><span>最近一次刷新状态：{response.refreshRun.phase === "trusted_published" ? "可信快照已发布" : response.refreshRun.phase === "failed" ? "来源失败" : response.refreshRun.phase === "collecting" ? "正在采集" : response.refreshRun.phase === "verifying" ? "正在核验" : "空闲"}</span><span className="font-mono text-muted-foreground">run {response.refreshRun.runId ?? "—"}</span></div>}
      {loading ? <div role="status" className="flex min-h-[360px] flex-col items-center justify-center rounded-2xl border border-border/70"><LoaderCircle className="h-6 w-6 animate-spin text-primary motion-reduce:animate-none" /><p className="mt-3 text-sm">正在读取{requested?.name ?? "当前行业"}的完整可信报告</p><p className="mt-1 text-xs text-muted-foreground">原报告已隐藏，避免切换期间串用行业数据。</p></div>
        : report && response ? <IndustryReport report={report} candidate={response.candidateEvidence} industryName={displayedName} refreshRun={response.refreshRun} windowDays={windowDays} onWindowDaysChange={setWindowDays} />
          : response?.templateStatus === "building" ? (
        <div className="rounded-2xl border border-dashed border-border/70 px-6 py-20 text-center">
          <p className="text-lg font-semibold">{INCOMPLETE_REPORT_MESSAGE}</p>
          <p className="mt-2 text-sm text-muted-foreground">{displayedName}已进入共享标签库；当前状态为建设中。</p>
        </div>
      ) : !error && <div className="rounded-2xl border border-dashed border-border/70 px-6 py-20 text-center"><p className="text-lg font-semibold">暂无可靠数据</p><p className="mt-2 text-sm text-muted-foreground">当前行业尚无可显示的可信快照。</p></div>}
      <TagSelector open={selectorOpen} selectedIds={tags.state.ids} customTags={tags.customTags}
        externalError={tags.errorMessage} onCreate={tags.create} onCancel={() => setSelectorOpen(false)}
        onConfirm={(ids) => { if (tags.replace(ids)) setSelectorOpen(false); }} />
    </div>
  );
}
