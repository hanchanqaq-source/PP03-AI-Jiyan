import { useEffect, useState } from "react";
import { Building2, Clock3, ExternalLink, Languages, Layers3, Link2, Newspaper } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MarketNewsEvent } from "./types";

const RELATION = {
  direct_holding: { label: "直接持仓", className: "border-primary/45 bg-primary/12 text-primary" },
  industry_relation: { label: "产业关联", className: "border-sky-400/35 bg-sky-400/10 text-sky-300" },
  watch_tag: { label: "关注标签", className: "border-violet-400/35 bg-violet-400/10 text-violet-300" },
  none: { label: "普通资讯", className: "border-border bg-muted/30 text-muted-foreground" },
} as const;

const CATEGORY = { policy: "政策", industry: "产业", company: "公司", fund_notice: "基金公告", deep_content: "深度内容" } as const;
const IMPACT = { positive: "偏正面", negative: "偏负面", neutral: "中性", unclear: "影响不明确" } as const;
const CONFIDENCE = { high: "高置信度", medium: "中置信度", low: "低置信度", unavailable: "置信度不可用" } as const;
const STATUS: Record<string, string> = { realtime: "实时抓取", cache: "缓存数据", stale: "过期缓存", partial: "来源失败", source_failure: "来源失败" };

function shortDateTime(value: string | null) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date).replace("/", "-");
}

export function EventCard({ event, onOpenDetails, onViewEvidence }: { event: MarketNewsEvent; onOpenDetails: (event: MarketNewsEvent) => void; onViewEvidence?: (event: MarketNewsEvent) => void }) {
  const relation = RELATION[event.relation_level];
  const originalUrl = event.original_links[0] || event.sources[0]?.original_url || "";
  const hasTranslation = event.translation_status === "translated" && Boolean(event.translated_title_zh);
  const [language, setLanguage] = useState<"zh" | "original">(hasTranslation ? "zh" : "original");
  useEffect(() => {
    setLanguage(hasTranslation ? "zh" : "original");
  }, [event.event_id, event.translated_at, hasTranslation]);
  const showChinese = hasTranslation && language === "zh";
  const displayTitle = showChinese ? event.translated_title_zh || event.title : event.title;
  const displaySummary = showChinese ? event.translated_summary_zh || event.summary : event.summary;
  const hasPrototypeVerification = event.verification_fixture === "frontend_demo" && Boolean(event.verification_status);
  return (
    <article className="group relative border-b border-border/55 py-5 pl-7 pr-1 last:border-b-0" aria-label={`${displayTitle}事件卡`}>
      <span className="absolute left-0 top-7 h-2.5 w-2.5 rounded-full border-2 border-background bg-primary shadow-[0_0_14px_hsl(var(--primary)/0.55)]" />
      <span className="absolute bottom-0 left-[4px] top-9 w-px bg-gradient-to-b from-primary/45 to-border/20 group-last:hidden" />

      <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
        <span className={cn("rounded-full border px-2 py-0.5 font-medium", relation.className)}>{relation.label}</span>
        <span className="rounded-full border border-border/60 bg-muted/25 px-2 py-0.5">{CATEGORY[event.category]}</span>
        {hasPrototypeVerification && <><span className="rounded-full border border-primary/45 bg-primary/10 px-2 py-0.5 font-medium text-primary">{event.verification_status}</span><span className="rounded-full border border-primary/30 px-2 py-0.5 text-primary">前端演示 Fixture</span></>}
        <span>{STATUS[event.data_status] || "缓存数据"}</span>
        {hasTranslation && <button onClick={() => setLanguage(showChinese ? "original" : "zh")} aria-label={showChinese ? "查看原文" : "中文"} className="inline-flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 hover:border-primary/45 hover:text-primary"><Languages className="h-3 w-3" />{showChinese ? "查看原文" : "中文"}</button>}
        <span className="ml-auto inline-flex items-center gap-1 font-mono"><Clock3 className="h-3 w-3" />{shortDateTime(event.published_at_latest)}</span>
      </div>

      <h3 className="mt-2.5 text-[17px] font-semibold leading-6 tracking-tight transition-colors group-hover:text-primary">{displayTitle}</h3>
      <p className={cn("mt-2 text-sm leading-6", event.summary_status === "ai_unavailable" ? "text-warning" : "text-muted-foreground")}>{displaySummary}</p>
      {event.translation_status === "unavailable" && <p className="mt-1 text-xs text-warning">中文翻译暂不可用</p>}

      <div className="mt-3 flex flex-wrap gap-1.5">
        {event.related_tags.map((tag) => <span key={tag.id} className="rounded-md border border-border/60 bg-black/15 px-2 py-1 text-[11px] text-muted-foreground">{tag.name}</span>)}
      </div>

      <div className="mt-3 grid gap-2 text-xs text-muted-foreground lg:grid-cols-2">
        <p className="flex items-start gap-1.5"><Link2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" /><span>与你的关系：<strong className="font-medium text-foreground">{relation.label}</strong></span></p>
        <p className="flex items-start gap-1.5"><Newspaper className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span>{event.source_count} 个公开来源 · 最新 {shortDateTime(event.published_at_latest)}</span></p>
        <p className="flex items-start gap-1.5"><Layers3 className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span>关联基金：{event.related_funds.length ? event.related_funds.map((fund) => `${fund.fund_name}（${fund.fund_code}）`).join("、") : "暂无持仓证据"}</span></p>
        <p className="flex items-start gap-1.5"><Building2 className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span>关联公司：{event.related_companies.length ? event.related_companies.map((company) => `${company.stock_name}（${company.stock_code}）`).join("、") : "暂无直接公司证据"}</span></p>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full border border-border/70 px-2.5 py-1">{IMPACT[event.impact_tendency]}</span>
        <span className="rounded-full border border-border/70 px-2.5 py-1 text-muted-foreground">{CONFIDENCE[event.confidence]}</span>
        <div className="ml-auto flex items-center gap-2">
          {hasPrototypeVerification && onViewEvidence && <button onClick={() => onViewEvidence(event)} aria-label="查看证据" className="rounded-lg border border-primary/45 px-3 py-1.5 font-medium text-primary hover:bg-primary/10">查看证据</button>}
          <button onClick={() => onOpenDetails(event)} aria-label={`查看事件详情 ${displayTitle}`} className="rounded-lg border border-border px-3 py-1.5 font-medium text-foreground hover:border-primary/45 hover:text-primary">查看事件详情</button>
          {originalUrl && <a href={originalUrl} target="_blank" rel="noreferrer" aria-label={`打开原始来源 ${displayTitle}`} className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 font-semibold text-primary-foreground hover:bg-primary/90">打开原始来源<ExternalLink className="h-3 w-3" /></a>}
        </div>
      </div>
    </article>
  );
}
