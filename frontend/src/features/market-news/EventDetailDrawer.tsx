import { useEffect, useRef } from "react";
import { ExternalLink, X } from "lucide-react";
import type { MarketNewsEvent } from "./types";

function dateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "时间未知";
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
  return parts.replace("/", "-");
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="rounded-2xl border border-border/60 bg-black/10 p-4"><h3 className="mb-3 text-sm font-semibold tracking-wide">{title}</h3>{children}</section>;
}

export function EventDetailDrawer({ open, event, onClose }: { open: boolean; event: MarketNewsEvent | null; onClose: () => void }) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open || !event) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButtonRef.current?.focus();
    const handleKey = (keyboardEvent: KeyboardEvent) => {
      if (keyboardEvent.key === "Escape") {
        keyboardEvent.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      previousFocusRef.current?.focus();
    };
  }, [event, onClose, open]);

  if (!open || !event) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/70" onMouseDown={(mouseEvent) => { if (mouseEvent.currentTarget === mouseEvent.target) onClose(); }}>
      <aside role="dialog" aria-modal="true" aria-label={`${event.title}事件详情`} className="ml-auto flex h-full w-full max-w-3xl flex-col border-l border-primary/25 bg-background/95 shadow-2xl backdrop-blur-xl">
        <header className="border-b border-border/60 px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div><p className="font-mono text-[10px] uppercase tracking-[0.18em] text-primary">Event intelligence</p><h2 className="mt-1 text-xl font-bold leading-7">{event.title}</h2><p className="mt-2 text-xs text-muted-foreground">最早：{dateTime(event.published_at_first)} · 最新：{dateTime(event.published_at_latest)}</p></div>
            <button ref={closeButtonRef} onClick={onClose} aria-label="关闭事件详情" className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-5 w-5" /></button>
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          <Section title="事件摘要"><p className="text-sm leading-6 text-muted-foreground">{event.summary}</p></Section>
          <Section title={`全部公开来源（${event.source_count}）`}>
            <div className="space-y-2">{event.sources.map((source) => <div key={`${source.source_name}-${source.original_url}`} className="rounded-xl border border-border/50 p-3 text-xs">
              <div className="flex flex-wrap items-center gap-2 text-muted-foreground"><strong className="text-foreground">{source.source_name}</strong><span>{dateTime(source.published_at)}</span><span>{source.data_status === "stale" ? "过期缓存" : "公开来源"}</span></div>
              <p className="mt-2 font-medium">{source.title}</p><p className="mt-1 line-clamp-2 text-muted-foreground">{source.summary_or_excerpt || "来源未提供摘要"}</p>
              <a href={source.original_url} target="_blank" rel="noreferrer" aria-label={`打开原始来源 ${source.source_name}`} className="mt-2 inline-flex items-center gap-1 text-primary hover:underline">打开原始来源<ExternalLink className="h-3 w-3" /></a>
            </div>)}</div>
          </Section>

          <Section title="关联对象">
            <div className="grid gap-3 text-xs sm:grid-cols-2">
              <div><p className="text-muted-foreground">相关基金</p>{event.related_funds.length ? event.related_funds.map((fund) => <p key={fund.fund_code} className="mt-1 font-medium">{fund.fund_name}（{fund.fund_code}）</p>) : <p className="mt-1">暂无持仓证据</p>}</div>
              <div><p className="text-muted-foreground">相关公司</p>{event.related_companies.length ? event.related_companies.map((company) => <p key={company.stock_code} className="mt-1 font-medium">{company.stock_name}（{company.stock_code}）</p>) : <p className="mt-1">暂无直接公司证据</p>}</div>
              <div className="sm:col-span-2"><p className="text-muted-foreground">相关行业和产业链</p><p className="mt-1">{event.related_tags.map((tag) => tag.name).join("、") || "暂无可靠标签"}</p></div>
            </div>
          </Section>

          <Section title="关联证据链">
            {event.relation_evidence.length ? <div className="space-y-2">{event.relation_evidence.map((evidence, index) => <div key={`${evidence.matched_kind}-${evidence.matched_value}-${index}`} className="rounded-xl border border-border/50 p-3 text-xs leading-5">
              {evidence.fund_name && <p>基金：{evidence.fund_name}（{evidence.fund_code}）</p>}
              {evidence.stock_name && <p>重仓公司：{evidence.stock_name}（{evidence.stock_code}）</p>}
              {evidence.holding_disclosure_date && <p>持仓披露日期：{evidence.holding_disclosure_date}</p>}
              {evidence.industry_classification && <p>行业分类：{evidence.industry_classification}</p>}
              {evidence.classification_standard && <p>分类标准：{evidence.classification_standard}</p>}
              <p>匹配依据：{evidence.matched_value}</p>
              {evidence.source_name && <p className="text-muted-foreground">依据来源：{evidence.source_name}</p>}
              {evidence.source_reference && <a href={evidence.source_reference} target="_blank" rel="noreferrer" className="text-primary hover:underline">查看关联依据</a>}
            </div>)}</div> : <p className="text-sm text-muted-foreground">当前仅有普通资讯信号，没有持仓证据链。</p>}
          </Section>

          <Section title="影响判断与证据缺口">
            <div className="space-y-2 text-xs"><p>影响倾向：{event.impact_tendency === "unclear" ? "影响不明确" : event.impact_tendency}</p><p>判断置信度：{event.confidence === "unavailable" ? "暂不可用" : event.confidence}</p>{event.impact_basis.map((basis) => <p key={basis}>{basis}</p>)}{event.missing_information.map((item) => <p key={item} className="text-warning">{item}</p>)}</div>
          </Section>
        </div>
        <footer className="flex justify-end border-t border-border/60 px-5 py-4"><button onClick={onClose} className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground">关闭</button></footer>
      </aside>
    </div>
  );
}
