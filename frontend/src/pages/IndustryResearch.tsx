import { useEffect, useMemo, useState } from "react";
import { AlertCircle } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { api, type RadarData } from "@/lib/api";
import { IndustryReport } from "@/features/industry/IndustryReport";
import { getIndustryTemplate, INCOMPLETE_REPORT_MESSAGE } from "@/features/industry/templates";
import type { IndustryNewsItem } from "@/features/industry/types";
import { normalizeRadar } from "@/features/news/normalize";
import { SelectedTagBar } from "@/features/tags/SelectedTagBar";
import { TagSelector } from "@/features/tags/TagSelector";
import { usePageTags } from "@/features/tags/usePageTags";

export function IndustryResearch() {
  const tags = usePageTags("industry_research");
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [radar, setRadar] = useState<RadarData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const template = tags.activeTag ? getIndustryTemplate(tags.activeTag.id) : undefined;

  useEffect(() => {
    api.radar().then(setRadar).catch(() => setError("行业资讯加载失败；报告骨架仍可阅读。"));
  }, []);

  const liveNews = useMemo<IndustryNewsItem[]>(() => {
    if (!radar || !tags.activeTag) return [];
    return normalizeRadar(radar, { tagId: tags.activeTag.id, days: 7 }).slice(0, 8).map((event) => ({
      title: event.title, source: event.sources.join(" · "), time: event.publishedAt, url: event.url,
    }));
  }, [radar, tags.activeTag]);

  return (
    <div>
      <PageHeader title="行业研究" subtitle="连续产业研究报告；结论、依据、来源、更新时间和失效条件同时呈现" />
      <div className="sticky top-0 z-20 mb-5 bg-background/95 backdrop-blur">
        <SelectedTagBar tags={tags.tags} activeId={tags.state.activeId} onActivate={tags.activate}
          onRemove={tags.remove} onReorder={tags.reorder} onMove={tags.move} onAdd={() => setSelectorOpen(true)} />
      </div>
      {tags.errorMessage && <div role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4" />{tags.errorMessage}</div>}
      {error && <div className="mb-4 flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning"><AlertCircle className="h-4 w-4" />{error}</div>}
      {template ? <IndustryReport template={template} liveNews={liveNews} /> : (
        <div className="rounded-2xl border border-dashed border-border/70 px-6 py-20 text-center">
          <p className="text-lg font-semibold">{INCOMPLETE_REPORT_MESSAGE}</p>
          <p className="mt-2 text-sm text-muted-foreground">{tags.activeTag?.name || "当前标签"}已进入共享标签库，但不会由 AI 自动补造行业数据。</p>
        </div>
      )}
      <TagSelector open={selectorOpen} selectedIds={tags.state.ids} customTags={tags.customTags} onCreate={tags.create} onCancel={() => setSelectorOpen(false)}
        onConfirm={(ids) => { tags.replace(ids); setSelectorOpen(false); }} />
    </div>
  );
}
