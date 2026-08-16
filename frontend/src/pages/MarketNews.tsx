import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Loader2, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { api, ApiError, type FundPortfolioData, type RadarData } from "@/lib/api";
import { NewsCard } from "@/features/news/NewsCard";
import { normalizeRadar, type NewsCategory } from "@/features/news/normalize";
import { SelectedTagBar } from "@/features/tags/SelectedTagBar";
import { TagSelector } from "@/features/tags/TagSelector";
import { usePageTags } from "@/features/tags/usePageTags";
import { cn } from "@/lib/utils";

const CATEGORIES: Array<"全部" | NewsCategory> = ["全部", "全球", "国内", "政策", "产业", "公司", "基金相关"];
const WINDOWS = [{ days: 1, label: "今天" }, { days: 3, label: "3天" }, { days: 7, label: "7天" }, { days: 30, label: "30天" }] as const;

export function MarketNews() {
  const tags = usePageTags("market_news");
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [radar, setRadar] = useState<RadarData | null>(null);
  const [portfolio, setPortfolio] = useState<FundPortfolioData | null>(null);
  const [category, setCategory] = useState<"全部" | NewsCategory>("全部");
  const [days, setDays] = useState<1 | 3 | 7 | 30>(7);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    api.radar().then(setRadar).catch((reason) => setError(reason instanceof ApiError ? reason.message : "市场资讯加载失败"));
    api.fundPortfolio().then(setPortfolio).catch(() => {});
  }, []);

  const holdingTagIds = useMemo(() => Array.from(new Set(portfolio?.holdings.flatMap((holding) => holding.custom_tag_ids) || [])), [portfolio]);
  const events = useMemo(() => radar && tags.activeTag ? normalizeRadar(radar, {
    tagId: tags.activeTag.id, days, category, holdingTagIds,
  }) : [], [category, days, holdingTagIds, radar, tags.activeTag]);

  const refresh = async () => {
    setRefreshing(true); setError(null);
    try { setRadar(await api.radarRefresh()); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "资讯刷新失败"); }
    finally { setRefreshing(false); }
  };

  return (
    <div>
      <PageHeader title="市场资讯" subtitle="按行业与产业链标签切换，保留原始来源与更新时间"
        actions={<button onClick={refresh} disabled={refreshing} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50">{refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}{refreshing ? "抓取中" : "刷新资讯"}</button>} />

      <div className="sticky top-0 z-20 mb-4 bg-background/95 backdrop-blur">
        <SelectedTagBar tags={tags.tags} activeId={tags.state.activeId} onActivate={tags.activate}
          onRemove={tags.remove} onReorder={tags.reorder} onAdd={() => setSelectorOpen(true)} />
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/50 py-3">
          <div className="flex flex-wrap gap-1">{CATEGORIES.map((item) => <button key={item} onClick={() => setCategory(item)} aria-pressed={category === item}
            className={cn("rounded-lg px-2.5 py-1 text-xs", category === item ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/50")}>{item}</button>)}</div>
          <div className="flex gap-1">{WINDOWS.map((item) => <button key={item.days} onClick={() => setDays(item.days)} aria-pressed={days === item.days}
            className={cn("rounded-lg px-2.5 py-1 text-xs", days === item.days ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted/50")}>{item.label}</button>)}</div>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{tags.activeTag?.name || "未选择标签"} · {events.length} 个去重事件</span>
        <span>{radar?.generated_at ? `${radar.stats.total_sources} 个公开源 · 更新于 ${radar.generated_at}` : "资讯尚未抓取"}</span>
      </div>
      {error && <div className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="h-4 w-4" />{error}</div>}
      <div className="rounded-2xl border border-border/70 bg-background/60 px-5">
        {!radar && !error ? <p className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取本地资讯缓存</p>
          : events.length ? events.map((event) => <NewsCard key={event.id} event={event} />)
            : <div className="py-14 text-center"><p className="font-medium">当前筛选没有可靠资讯</p><p className="mt-2 text-xs text-muted-foreground">可扩大时间范围、切换分类或刷新公开 RSS 源；不会用 AI 生成新闻。</p></div>}
      </div>

      <TagSelector open={selectorOpen} selectedIds={tags.state.ids} onCancel={() => setSelectorOpen(false)}
        onConfirm={(ids) => { tags.replace(ids); setSelectorOpen(false); }} />
    </div>
  );
}
