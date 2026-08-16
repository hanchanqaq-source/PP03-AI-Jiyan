import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Bookmark, Newspaper, PieChart, Radar, Wallet } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { api, type FundPortfolioData, type RadarData } from "@/lib/api";
import { getTag } from "@/features/tags/catalog";
import { loadPageTagState } from "@/features/tags/preferences";
import { normalizeRadar } from "@/features/news/normalize";

const EMPTY_PORTFOLIO: FundPortfolioData = {
  schema_version: 2, holdings: [], total_cost: 0, updated: null, migration: null, data_status: "ok",
};

export function ResearchHome() {
  const [portfolio, setPortfolio] = useState<FundPortfolioData>(EMPTY_PORTFOLIO);
  const [radar, setRadar] = useState<RadarData | null>(null);
  const marketTags = useMemo(() => loadPageTagState("market_news"), []);
  const industryTags = useMemo(() => loadPageTagState("industry_research"), []);

  useEffect(() => {
    api.fundPortfolio().then(setPortfolio).catch(() => {});
    api.radar().then(setRadar).catch(() => {});
  }, []);

  const holdingTagIds = Array.from(new Set(portfolio.holdings.flatMap((holding) => holding.custom_tag_ids)));
  const news = radar ? normalizeRadar(radar, {
    tagId: marketTags.activeId, days: 3, holdingTagIds,
  }).filter((event) => event.holdingRelated).slice(0, 4) : [];
  const followed = Array.from(new Set([...marketTags.ids, ...industryTags.ids]));

  const blocks = [
    { title: "今日持仓变化", icon: Wallet, content: "暂无可靠数据", note: "尚未接入可靠盘中估算源" },
    { title: "持仓总览", icon: PieChart, content: `¥${portfolio.total_cost.toLocaleString("zh-CN")}`, note: `${portfolio.holdings.length} 只本地持仓基金` },
    { title: "重要行业变化", icon: Radar, content: radar?.generated_at ? `资讯更新于 ${radar.generated_at}` : "暂无可靠数据", note: "按关注标签跟踪，不预测涨跌" },
  ];

  return (
    <div>
      <PageHeader title="投研首页" subtitle="今天发生了什么、为什么与我有关、接下来继续关注什么" />
      <div className="mb-5 grid gap-3 lg:grid-cols-3">{blocks.map(({ title, icon: Icon, content, note }) => <GlassCard key={title}><div className="flex items-center gap-2 text-xs text-muted-foreground"><Icon className="h-4 w-4 text-primary" /><h2>{title}</h2></div><p className="mt-3 text-xl font-bold">{content}</p><p className="mt-2 text-xs text-muted-foreground">{note}</p></GlassCard>)}</div>
      <div className="grid gap-5 lg:grid-cols-[1.15fr_0.85fr]">
        <GlassCard glow><div className="mb-3 flex items-center gap-2"><Newspaper className="h-4 w-4 text-primary" /><h2 className="font-semibold">与持仓相关的重要资讯</h2></div>{news.length ? <div className="space-y-3">{news.map((event) => <a key={event.id} href={event.url} target="_blank" rel="noreferrer" className="block border-b border-border/40 pb-3 text-sm hover:text-primary"><p>{event.title}</p><p className="mt-1 text-xs text-muted-foreground">{event.sources.join(" · ")} · {event.publishedAt}</p></a>)}</div> : <p className="py-8 text-center text-sm text-muted-foreground">暂无可靠数据</p>}</GlassCard>
        <div className="space-y-5">
          <GlassCard><div className="mb-3 flex items-center gap-2"><Bookmark className="h-4 w-4 text-primary" /><h2 className="font-semibold">我的关注标签</h2></div><div className="flex flex-wrap gap-2">{followed.map((id) => <span key={id} className="rounded-full border border-primary/30 bg-primary/10 px-2.5 py-1 text-xs text-primary">{getTag(id)?.name || id}</span>)}</div></GlassCard>
          <GlassCard><div className="mb-3 flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-warning" /><h2 className="font-semibold">主要风险提醒</h2></div><p className="text-sm leading-6 text-muted-foreground">基金净值、盘中估算、基金持仓披露和行业资金数据尚未形成完整可靠链路。页面不会用 AI 填补这些缺口。</p></GlassCard>
          <GlassCard><h2 className="font-semibold">最近查看的行业</h2><p className="mt-3 text-sm">{getTag(industryTags.activeId)?.name || "暂无记录"}</p><p className="mt-1 text-xs text-muted-foreground">记录来自行业研究页面的本地标签偏好</p></GlassCard>
        </div>
      </div>
    </div>
  );
}
