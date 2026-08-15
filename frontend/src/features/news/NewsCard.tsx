import { ExternalLink, Link2, Newspaper } from "lucide-react";
import type { NormalizedNewsEvent } from "./normalize";

export function NewsCard({ event }: { event: NormalizedNewsEvent }) {
  return (
    <article className="group border-b border-border/60 px-1 py-5 last:border-0">
      <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
        <span className="font-mono">{event.publishedAt || "时间未知"}</span>
        <span>·</span>
        <span>{event.sources.join(" · ")}</span>
        {event.categories.map((category) => <span key={category} className="rounded-full bg-muted/60 px-2 py-0.5">{category}</span>)}
      </div>
      <h3 className="mt-2 text-base font-semibold leading-6 group-hover:text-primary">{event.title}</h3>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">{event.summary}</p>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
        <span className="flex items-center gap-1 text-muted-foreground"><Newspaper className="h-3.5 w-3.5" />影响行业：{event.impactedIndustries.join("、")}</span>
        <span className="rounded-full border border-border px-2 py-0.5 text-muted-foreground">{event.sentiment}</span>
        {event.holdingRelated && <span className="flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-primary"><Link2 className="h-3 w-3" />与持仓标签相关</span>}
        <a href={event.url} target="_blank" rel="noreferrer" aria-label="查看原始来源"
          className="ml-auto inline-flex items-center gap-1 text-primary hover:underline">
          查看原始来源 <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </article>
  );
}
