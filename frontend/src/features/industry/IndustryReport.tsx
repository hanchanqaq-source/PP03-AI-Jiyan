import type { DataField, IndustryNewsItem, IndustryReportTemplate } from "./types";
import { CapitalSection } from "./sections/CapitalSection";
import { ChainSection } from "./sections/ChainSection";
import { CompanySection } from "./sections/CompanySection";
import { CycleSection } from "./sections/CycleSection";
import { FundSection } from "./sections/FundSection";
import { MetricsSection } from "./sections/MetricsSection";
import { NewsRiskSection } from "./sections/NewsRiskSection";
import { REPORT_SECTIONS } from "./sections/shared";
import { SummarySection } from "./sections/SummarySection";

export function IndustryReport({ template, liveNews, liveCapital }: {
  template: IndustryReportTemplate;
  liveNews?: IndustryNewsItem[];
  liveCapital?: DataField[];
}) {
  return (
    <article aria-label={`${template.name}行业研究报告`} className="overflow-hidden rounded-2xl border border-border/70 bg-background/70 shadow-xl">
      <div className="border-b border-border/60 bg-[radial-gradient(circle_at_top_right,rgba(255,90,31,0.15),transparent_42%)] px-5 py-8 sm:px-8">
        <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-primary">PP03 Industry dossier</p>
        <h1 className="mt-3 text-3xl font-black tracking-tight sm:text-4xl">{template.name}<span className="ml-2 font-normal text-muted-foreground">产业研究</span></h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">{template.subtitle}</p>
      </div>
      <nav aria-label="行业报告内部导航" className="sticky top-0 z-10 flex gap-1 overflow-x-auto border-b border-border/60 bg-background/95 px-4 py-2 backdrop-blur">
        {REPORT_SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`} className="shrink-0 rounded-lg px-3 py-1.5 text-xs text-muted-foreground hover:bg-primary/10 hover:text-primary">{label}</a>)}
      </nav>
      <SummarySection template={template} />
      <CycleSection template={template} />
      <ChainSection template={template} />
      <MetricsSection template={template} />
      <CapitalSection template={template} liveCapital={liveCapital} />
      <CompanySection template={template} />
      <FundSection template={template} />
      <NewsRiskSection template={template} news={liveNews} />
    </article>
  );
}
