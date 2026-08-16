import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import type {
  ExposureConstituent,
  ExposureItem,
  FundIndustryExposure,
  IndustryChainTag,
  LookthroughExposure,
  OfficialIndustryAllocation,
  PortfolioIndustryConcentration,
  SystemTagExposure,
} from "./types";

type Layer = "primary" | "secondary" | "detail";
type ExpandedBucket = "other" | "unknown" | null;

const layerLabels: Record<Layer, string> = {
  primary: "一级行业",
  secondary: "二级行业",
  detail: "细分行业",
};

export interface OfficialAllocationGroup {
  fundCode?: string;
  fundName?: string;
  allocation: OfficialIndustryAllocation | null;
}

interface Props {
  title: string;
  exposure: FundIndustryExposure | PortfolioIndustryConcentration | null;
  officialAllocations?: OfficialAllocationGroup[];
  compact?: boolean;
}

function pct(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(2)}%`;
}

function isFundExposure(exposure: Props["exposure"]): exposure is FundIndustryExposure {
  return Boolean(exposure && "lookthrough" in exposure);
}

function portfolioLookthrough(exposure: PortfolioIndustryConcentration): LookthroughExposure {
  const primary = exposure.primary || exposure.exposure || [];
  const secondary = exposure.secondary || [];
  const detail = exposure.detail || [];
  return {
    status: primary.length || secondary.length || detail.length ? "disclosed" : "unavailable",
    message: exposure.calculation_basis,
    primary,
    secondary,
    detail,
    identified_coverage_pct: exposure.identified_coverage_pct || 0,
    other_pct: exposure.other_pct || 0,
    unknown_pct: exposure.unknown_pct || 0,
    undisclosed_stock_pct: exposure.undisclosed_stock_pct || 0,
    non_stock_pct: exposure.non_stock_pct || 0,
    disclosure_date: null,
    source_name: "各基金股票行业穿透结果",
    source_reference: "",
    classification_standard: "各基金已披露分类标准",
    calculation_basis: exposure.calculation_basis,
  };
}

function SourceLink({ name, href }: { name: string; href: string }) {
  if (!name) return <span>来源暂无可靠数据</span>;
  if (!href) return <span>{name}</span>;
  return <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">{name}<ExternalLink className="h-3 w-3" /></a>;
}

function Constituents({ rows }: { rows: ExposureConstituent[] }) {
  if (!rows.length) return <p className="px-3 py-2 text-xs text-muted-foreground">暂无可展开构成</p>;
  return <div className="divide-y divide-border/40 rounded-lg border border-border/50 bg-slate-950/35">{rows.map((row, index) => (
    <div key={`${row.stock_code}-${row.stock_name}-${index}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs">
      <span><span className="font-medium">{row.stock_name}</span>{row.stock_code && <span className="ml-2 font-mono text-muted-foreground">{row.stock_code}</span>}</span>
      <span className="text-right"><span className="font-semibold tabular-nums">{pct(row.weight_pct)}</span><span className="ml-2 text-muted-foreground">{row.reason}</span></span>
    </div>
  ))}</div>;
}

export function IndustryExposurePanel({ title, exposure, officialAllocations, compact = false }: Props) {
  const [layer, setLayer] = useState<Layer>("primary");
  const [expandedBucket, setExpandedBucket] = useState<ExpandedBucket>(null);
  const fundExposure = isFundExposure(exposure) ? exposure : null;
  const lookthrough = fundExposure?.lookthrough || (exposure ? portfolioLookthrough(exposure as PortfolioIndustryConcentration) : null);
  const rows: ExposureItem[] = lookthrough?.[layer] || [];
  const tags: Array<IndustryChainTag | SystemTagExposure> = fundExposure?.industry_chain_tags || (exposure && "industry_chain_tags" in exposure ? exposure.industry_chain_tags || [] : []);
  const officialGroups = useMemo<OfficialAllocationGroup[]>(() => {
    if (officialAllocations) return officialAllocations.filter((group) => group.allocation);
    return fundExposure?.official_allocation ? [{ allocation: fundExposure.official_allocation }] : [];
  }, [fundExposure, officialAllocations]);
  const disclosedCoverage = lookthrough?.disclosed_coverage_pct
    ?? ((lookthrough?.identified_coverage_pct || 0) + (lookthrough?.unknown_pct || 0));
  const lowCoverage = disclosedCoverage > 0 && disclosedCoverage < 50;

  return (
    <GlassCard className={`overflow-hidden bg-gradient-to-br from-slate-900/85 via-slate-950/75 to-blue-950/35 ${compact ? "p-4" : "p-5"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="font-semibold">{title}</h2><p className="mt-1 text-xs text-muted-foreground">行业事实来自公开持仓与公开行业分类，比例保持基金净值原始口径</p></div>
        {lookthrough && <span className="rounded-full border border-blue-400/20 bg-blue-400/5 px-2.5 py-1 text-[11px] text-blue-100/75">持仓披露 {lookthrough.disclosure_date || "日期待补充"}</span>}
      </div>

      <section className="mt-4 rounded-xl border border-primary/20 bg-slate-950/35 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h3 className="text-sm font-semibold">重仓股穿透后的行业暴露</h3><p className="mt-1 text-[11px] text-muted-foreground">公开前十大持仓原始占净值比例，不对未披露资产归一化</p></div>
          <div className="flex gap-1" aria-label="行业层级">{(Object.keys(layerLabels) as Layer[]).map((value) => (
            <button key={value} aria-label={layerLabels[value]} aria-pressed={layer === value} onClick={() => setLayer(value)} className={`rounded-md px-2.5 py-1.5 text-xs ${layer === value ? "bg-primary text-primary-foreground" : "border border-border/60 text-muted-foreground hover:border-primary/40 hover:text-foreground"}`}>{layerLabels[value]}</button>
          ))}</div>
        </div>

        {lowCoverage && <p className="mt-3 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">行业暴露仅基于公开持仓估算，未披露部分未归一化。</p>}
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
          <span>已识别覆盖率 {pct(lookthrough?.identified_coverage_pct)}</span>
          <span>未知 {pct(lookthrough?.unknown_pct)}</span>
          <span>未披露股票资产 {pct(lookthrough?.undisclosed_stock_pct)}</span>
          <span>非股票资产 {pct(lookthrough?.non_stock_pct)}</span>
        </div>

        <div data-testid="lookthrough-rows" className="mt-4 space-y-3">
          {lookthrough?.status === "disclosed" && rows.length ? rows.map((item) => (
            <div key={item.name}>
              <div className="mb-1 flex justify-between text-xs"><span>{item.name}</span><span className="font-semibold tabular-nums">{pct(item.weight_pct)}</span></div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-800"><div className="h-full rounded-full bg-gradient-to-r from-primary to-blue-400" style={{ width: `${Math.min(100, Math.max(0, item.weight_pct))}%` }} /></div>
            </div>
          )) : <p className="rounded-lg border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">暂无可靠数据</p>}
        </div>

        <div className="mt-4 space-y-2">
          <button aria-expanded={expandedBucket === "other"} aria-label={`其他 ${pct(lookthrough?.other_pct)}`} onClick={() => setExpandedBucket((value) => value === "other" ? null : "other")} className="flex w-full items-center justify-between rounded-lg border border-border/60 px-3 py-2 text-xs text-muted-foreground hover:border-primary/35 hover:text-foreground"><span>其他（已分类但当前层级名称缺失）</span><span className="inline-flex items-center gap-2 tabular-nums">{pct(lookthrough?.other_pct)}{expandedBucket === "other" ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}</span></button>
          {expandedBucket === "other" && <Constituents rows={fundExposure?.other_constituents || []} />}
          <button aria-expanded={expandedBucket === "unknown"} aria-label={`未知 ${pct(lookthrough?.unknown_pct)}`} onClick={() => setExpandedBucket((value) => value === "unknown" ? null : "unknown")} className="flex w-full items-center justify-between rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning"><span>未知（缺少分类或无法识别）</span><span className="inline-flex items-center gap-2 tabular-nums">{pct(lookthrough?.unknown_pct)}{expandedBucket === "unknown" ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}</span></button>
          {expandedBucket === "unknown" && <Constituents rows={fundExposure?.unknown_constituents || []} />}
        </div>

        <div className="mt-4 border-t border-border/40 pt-3 text-[11px] leading-5 text-muted-foreground">
          <p>{lookthrough?.calculation_basis || "穿透计算依据暂不可用"}</p>
          <p className="mt-1 flex flex-wrap gap-x-3"><span>分类标准：{lookthrough?.classification_standard || "暂无可靠数据"}</span><SourceLink name={lookthrough?.source_name || ""} href={lookthrough?.source_reference || ""} /></p>
        </div>
      </section>

      <section className="mt-3 rounded-xl border border-border/60 bg-slate-950/25 p-4">
        <h3 className="text-sm font-semibold">官方行业配置</h3>
        <p className="mt-1 text-[11px] text-muted-foreground">基金官方原始大类仅作补充和兜底，不参与重仓股穿透比例</p>
        {officialGroups.length ? <div className="mt-3 space-y-3">{officialGroups.map((group, groupIndex) => {
          const allocation = group.allocation!;
          return <div key={`${group.fundCode || "single"}-${groupIndex}`} className="rounded-lg border border-border/50 p-3">
            {(group.fundName || group.fundCode) && <p className="mb-2 text-xs font-medium">{group.fundName || group.fundCode}<span className="ml-2 font-mono text-muted-foreground">{group.fundCode}</span></p>}
            <div className="space-y-2">{allocation.exposure.map((item) => <div key={item.name} className="flex items-center justify-between gap-3 text-xs"><span>{item.display_name}</span><span className="font-semibold tabular-nums">{pct(item.weight_pct)}</span></div>)}</div>
            <p className="mt-2 flex flex-wrap gap-x-3 text-[11px] text-muted-foreground"><span>配置日期 {allocation.as_of_date || "—"}</span><SourceLink name={allocation.source_name} href={allocation.source_reference} /></p>
          </div>;
        })}</div> : <p className="mt-3 rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted-foreground">官方行业配置暂无可靠数据</p>}
      </section>

      <section className="mt-3 rounded-xl border border-border/60 bg-slate-950/25 p-4">
        <h3 className="text-sm font-semibold">产业链 / 主题标签</h3>
        <p className="mt-1 text-[11px] text-muted-foreground">基金名称未参与行业事实判断</p>
        <div className="mt-3 flex flex-wrap gap-2">{tags.length ? tags.map((tag) => <span key={tag.id} className="rounded-full border border-primary/30 bg-primary/10 px-2.5 py-1 text-xs text-primary">{tag.name} {pct(tag.weight_pct)}</span>) : <span className="text-xs text-muted-foreground">暂无可靠数据</span>}</div>
      </section>
    </GlassCard>
  );
}
