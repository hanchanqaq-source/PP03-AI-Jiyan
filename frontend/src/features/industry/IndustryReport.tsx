import { useMemo, useState } from "react";
import type { CandidateIndustryEvidence, IndustryReportProps } from "./types";
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

const sectionIds: Set<string> = new Set(REPORT_SECTIONS.map(([id]) => id));

function filterEvents<T extends { occurredAt: string }>(items: T[], days: number, latestMs: number): T[] {
  const threshold = latestMs - days * 86_400_000;
  return items.filter((item) => Date.parse(item.occurredAt) >= threshold);
}

export function IndustryReport({ report, candidate, industryName, windowDays, onWindowDaysChange }: IndustryReportProps) {
  const template = getIndustryTemplate(report.industryId);
  const filtered = useMemo(() => {
    const all = [...report.newsRisk, ...(candidate?.unverifiedEvents ?? []), ...(candidate?.conflictingEvents ?? [])];
    const latestMs = Math.max(...all.map((item) => Date.parse(item.occurredAt)), 0);
    const filteredCandidate: CandidateIndustryEvidence | null = candidate ? {
      ...candidate,
      unverifiedEvents: filterEvents(candidate.unverifiedEvents, windowDays, latestMs),
      conflictingEvents: filterEvents(candidate.conflictingEvents, windowDays, latestMs),
    } : null;
    return { trusted: filterEvents(report.newsRisk, windowDays, latestMs), candidate: filteredCandidate };
  }, [candidate, report.newsRisk, windowDays]);
  return (
    <article aria-label={`${industryName}行业研究报告`} data-industry-id={report.industryId} className="overflow-hidden rounded-2xl border border-border/70 bg-background/70 shadow-xl">
      <div className="border-b border-border/60 bg-[radial-gradient(circle_at_top_right,rgba(255,90,31,0.15),transparent_42%)] px-5 py-8 sm:px-8">
        <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-primary">PP03 Industry dossier</p>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-3xl font-black tracking-tight sm:text-4xl">{industryName}<span className="ml-2 font-normal text-muted-foreground">行业研究</span></h1><p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">{template?.subtitle ?? "只展示已准入证据；缺失即为空。"}</p></div><dl className="text-right text-[10px] text-muted-foreground"><dt>当前可信快照</dt><dd className="mt-1 font-mono text-foreground">{report.displayedTrustedSnapshotId ?? "暂无可靠数据"}</dd><dt className="mt-2">数据更新时间</dt><dd className="mt-1 font-mono text-foreground">{report.generatedAt ?? "暂无可靠数据"}</dd></dl></div>
        <dl className="mt-6 grid gap-px overflow-hidden rounded-xl border border-border/60 bg-border/60 sm:grid-cols-2 xl:grid-cols-5"><div className="bg-background/80 p-3"><dt className="text-[10px] text-muted-foreground">来源覆盖</dt><dd className="mt-1 font-mono text-sm">{report.sourceCoverage.healthy}/{report.sourceCoverage.total}</dd></div><div className="bg-background/80 p-3"><dt className="text-[10px] text-muted-foreground">已核验</dt><dd className="mt-1 font-mono text-sm">{report.counts.verified}</dd></div><div className="bg-background/80 p-3"><dt className="text-[10px] text-muted-foreground">多源印证</dt><dd className="mt-1 font-mono text-sm">{report.counts.corroborated}</dd></div><div className="bg-background/80 p-3"><dt className="text-[10px] text-muted-foreground">待核验</dt><dd className="mt-1 font-mono text-sm">{candidate?.counts.unverified ?? 0}</dd></div><div className="bg-background/80 p-3"><dt className="text-[10px] text-muted-foreground">冲突</dt><dd className="mt-1 font-mono text-sm">{candidate?.counts.conflicting ?? 0}</dd></div></dl>
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

export function IndustryReportAnchors() {
  const [active, setActive] = useState(() => sectionIds.has(window.location.hash.slice(1)) ? window.location.hash.slice(1) : "overview");
  return <nav aria-label="行业报告内部导航" className="flex gap-1 overflow-x-auto border-t border-border/60 py-2"><span className="sr-only" aria-live="polite">当前章节：{active}</span>{REPORT_SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`} aria-current={active === id ? "location" : undefined} onClick={() => setActive(id)} className={`min-h-11 shrink-0 rounded-lg px-3 py-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${active === id ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-primary/10 hover:text-primary"}`}>{label}</a>)}</nav>;
}
