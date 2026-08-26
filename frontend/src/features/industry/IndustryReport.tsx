import { useEffect, useMemo, useState } from "react";
import type { CandidateIndustryEvidence, IndustryReportProps, IndustryWindowDays } from "./types";
import { CapitalSection } from "./sections/CapitalSection";
import { ChainSection } from "./sections/ChainSection";
import { CompanySection } from "./sections/CompanySection";
import { CycleSection } from "./sections/CycleSection";
import { FundSection } from "./sections/FundSection";
import { MetricsSection } from "./sections/MetricsSection";
import { NewsRiskSection } from "./sections/NewsRiskSection";
import { REPORT_SECTIONS } from "./sections/shared";
import { SummarySection } from "./sections/SummarySection";
import { getIndustryTemplate } from "./templates";

export const CANDIDATE_REPORT_SECTIONS = [
  ["metrics", "核心数据"], ["news-risk", "新闻与风险"],
] as const;

function filterEvents<T extends { occurredAt: string }>(items: T[], days: number, referenceMs: number): T[] {
  const threshold = referenceMs - days * 86_400_000;
  return items.filter((item) => {
    const occurredMs = Date.parse(item.occurredAt);
    return Number.isFinite(occurredMs) && occurredMs >= threshold && occurredMs <= referenceMs;
  });
}

export function IndustryReport({ report, candidate, industryName, refreshRun, windowDays, onWindowDaysChange, now }: IndustryReportProps) {
  const template = getIndustryTemplate(report.industryId);
  const referenceMs = useMemo(() => {
    const generatedMs = report.generatedAt ? Date.parse(report.generatedAt) : Number.NaN;
    return Number.isFinite(generatedMs) ? generatedMs : (now?.() ?? new Date()).getTime();
  }, [now, report.generatedAt]);
  const filtered = useMemo(() => {
    const filteredCandidate: CandidateIndustryEvidence | null = candidate ? {
      ...candidate,
      unverifiedEvents: filterEvents(candidate.unverifiedEvents, windowDays, referenceMs),
      conflictingEvents: filterEvents(candidate.conflictingEvents, windowDays, referenceMs),
    } : null;
    return { trusted: filterEvents(report.newsRisk, windowDays, referenceMs), candidate: filteredCandidate };
  }, [candidate, referenceMs, report.newsRisk, windowDays]);
  const snapshotId = report.displayedTrustedSnapshotId ?? "暂无可靠数据";
  const showsOldSnapshot = refreshRun.phase === "collecting" || refreshRun.phase === "verifying" || refreshRun.phase === "failed";
  return (
    <article aria-label={`${industryName}行业研究报告`} data-industry-id={report.industryId} className="overflow-hidden rounded-2xl border border-border/70 bg-background/70 shadow-xl">
      {showsOldSnapshot && <div role={refreshRun.phase === "failed" ? "alert" : "status"} className="border-b border-warning/40 bg-warning/5 px-5 py-3 text-sm text-warning sm:px-8">{refreshRun.phase === "failed" ? "来源失败；" : "刷新仍在进行；"}当前显示的旧可信快照 <span className="font-mono">{snapshotId}</span>，不会标记为最新。</div>}
      {report.demo && <div className="border-b border-warning/40 bg-warning/5 px-5 py-3 text-sm text-warning sm:px-8"><strong className="font-mono">{`隔离演示快照 ${snapshotId}`}</strong>：不代表真实市场、基金或公司数据。</div>}
      <div className="border-b border-border/60 bg-[radial-gradient(circle_at_top_right,rgba(255,90,31,0.15),transparent_42%)] px-5 py-8 sm:px-8">
        <p className="font-mono text-xs uppercase leading-5 tracking-[0.3em] text-primary">PP03 Industry dossier</p>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-3xl font-black tracking-tight sm:text-4xl">{industryName}<span className="ml-2 font-normal text-muted-foreground">行业研究</span></h1><p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">{template?.subtitle ?? "只展示已准入证据；缺失即为空。"}</p></div><dl className="text-right text-xs leading-5 text-muted-foreground"><dt>当前可信快照</dt><dd className="mt-1 font-mono text-foreground">{report.displayedTrustedSnapshotId ?? "暂无可靠数据"}</dd><dt className="mt-2">数据更新时间</dt><dd className="mt-1 font-mono text-foreground">{report.generatedAt ?? "暂无可靠数据"}</dd></dl></div>
        <dl className="mt-6 grid gap-px overflow-hidden rounded-xl border border-border/60 bg-border/60 sm:grid-cols-2 xl:grid-cols-5"><div className="bg-background/80 p-3"><dt className="text-xs leading-5 text-muted-foreground">来源覆盖</dt><dd className="mt-1 font-mono text-sm">{report.sourceCoverage.healthy}/{report.sourceCoverage.total}</dd></div><div className="bg-background/80 p-3"><dt className="text-xs leading-5 text-muted-foreground">已核验</dt><dd className="mt-1 font-mono text-sm">{report.counts.verified}</dd></div><div className="bg-background/80 p-3"><dt className="text-xs leading-5 text-muted-foreground">多源印证</dt><dd className="mt-1 font-mono text-sm">{report.counts.corroborated}</dd></div><div className="bg-background/80 p-3"><dt className="text-xs leading-5 text-muted-foreground">待核验（指标+事件合计）</dt><dd className="mt-1 font-mono text-sm">{(candidate?.counts.unverified ?? 0) + (candidate?.counts.unverifiedEvents ?? 0)}</dd></div><div className="bg-background/80 p-3"><dt className="text-xs leading-5 text-muted-foreground">冲突（指标+事件合计）</dt><dd className="mt-1 font-mono text-sm">{(candidate?.counts.conflicting ?? 0) + (candidate?.counts.conflictingEvents ?? 0)}</dd></div></dl>
      </div>
      <SummarySection report={report} />
      <CycleSection metrics={report.cycle} />
      <ChainSection nodes={report.chain} />
      <MetricsSection metrics={report.metrics} candidate={candidate} />
      <CapitalSection metrics={report.capital} />
      <CompanySection companies={report.companies} />
      <FundSection industryId={report.industryId} />
      <NewsRiskSection trusted={filtered.trusted} candidate={filtered.candidate} windowDays={windowDays} onWindowDaysChange={onWindowDaysChange} />
    </article>
  );
}

export function CandidateOnlyIndustryReport({ candidate, industryName, windowDays, onWindowDaysChange, now }: {
  candidate: CandidateIndustryEvidence;
  industryName: string;
  windowDays: IndustryWindowDays;
  onWindowDaysChange: (days: IndustryWindowDays) => void;
  now?: () => Date;
}) {
  const referenceMs = useMemo(() => (now?.() ?? new Date()).getTime(), [candidate.candidateSnapshotId, now]);
  const filteredCandidate = useMemo<CandidateIndustryEvidence>(() => ({
    ...candidate,
    unverifiedEvents: filterEvents(candidate.unverifiedEvents, windowDays, referenceMs),
    conflictingEvents: filterEvents(candidate.conflictingEvents, windowDays, referenceMs),
  }), [candidate, referenceMs, windowDays]);
  return (
    <article aria-label={`${industryName}候选证据报告`} data-industry-id={candidate.industryId} className="overflow-hidden rounded-2xl border border-border/70 bg-background/70 shadow-xl">
      <div role="status" className="border-b border-warning/40 bg-warning/5 px-5 py-4 text-sm leading-6 text-warning sm:px-8">
        <strong>暂无可信快照。</strong> 以下仅展示待核验与冲突证据，不进入可信结论，也不补造行业数据。
      </div>
      <header className="border-b border-border/60 px-5 py-8 sm:px-8">
        <p className="font-mono text-xs uppercase leading-5 tracking-[0.3em] text-primary">PP03 Candidate evidence</p>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div><h1 className="text-3xl font-black tracking-tight sm:text-4xl">{industryName}<span className="ml-2 font-normal text-muted-foreground">候选证据</span></h1><p className="mt-3 text-sm leading-6 text-muted-foreground">可信发布准入尚未完成；候选内容与可信结论保持隔离。</p></div>
          <dl className="text-right text-xs leading-5 text-muted-foreground"><dt>候选证据快照</dt><dd className="mt-1 font-mono text-foreground">{candidate.candidateSnapshotId}</dd></dl>
        </div>
        <dl className="mt-6 grid gap-px overflow-hidden rounded-xl border border-border/60 bg-border/60 sm:grid-cols-2">
          <div className="bg-background/80 p-3"><dt className="text-xs leading-5 text-muted-foreground">待核验（指标+事件合计）</dt><dd className="mt-1 font-mono text-sm">{candidate.counts.unverified + candidate.counts.unverifiedEvents}</dd></div>
          <div className="bg-background/80 p-3"><dt className="text-xs leading-5 text-muted-foreground">冲突（指标+事件合计）</dt><dd className="mt-1 font-mono text-sm">{candidate.counts.conflicting + candidate.counts.conflictingEvents}</dd></div>
        </dl>
      </header>
      <MetricsSection metrics={[]} candidate={filteredCandidate} index="01" />
      <NewsRiskSection trusted={[]} candidate={filteredCandidate} windowDays={windowDays} onWindowDaysChange={onWindowDaysChange} index="02" />
    </article>
  );
}

export function IndustryReportAnchors({ sections = REPORT_SECTIONS }: {
  sections?: readonly (readonly [string, string])[];
}) {
  const availableSectionIds = useMemo(() => new Set(sections.map(([id]) => id)), [sections]);
  const defaultSectionId = sections[0]?.[0] ?? "overview";
  const [active, setActive] = useState(() => {
    if (typeof window === "undefined") return defaultSectionId;
    const hashId = window.location.hash.slice(1);
    return availableSectionIds.has(hashId) ? hashId : defaultSectionId;
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    const syncHash = () => {
      const id = window.location.hash.slice(1);
      setActive(availableSectionIds.has(id) ? id : defaultSectionId);
    };
    window.addEventListener("hashchange", syncHash);
    if (!("IntersectionObserver" in window)) return () => window.removeEventListener("hashchange", syncHash);
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting)
        .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
      if (visible && availableSectionIds.has(visible.target.id)) {
        const id = visible.target.id;
        setActive(id);
        if (window.location.hash !== `#${id}`) {
          window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}#${id}`);
        }
      }
    }, { rootMargin: "-18% 0px -68%", threshold: [0, 0.25, 0.5, 1] });
    sections.forEach(([id]) => {
      const section = document.getElementById(id);
      if (section) observer.observe(section);
    });
    return () => {
      window.removeEventListener("hashchange", syncHash);
      observer.disconnect();
    };
  }, [availableSectionIds, defaultSectionId, sections]);
  return <nav aria-label="行业报告内部导航" className="flex gap-1 overflow-x-auto border-t border-border/60 py-2"><span className="sr-only" aria-live="polite">当前章节：{active}</span>{sections.map(([id, label]) => <a key={id} href={`#${id}`} aria-current={active === id ? "location" : undefined} onClick={() => setActive(id)} className={`min-h-11 shrink-0 rounded-lg px-3 py-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${active === id ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-primary/10 hover:text-primary"}`}>{label}</a>)}</nav>;
}
