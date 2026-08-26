import type { CandidateIndustryEvidence, IndustryTrustedEvent, IndustryWindowDays } from "@/lib/api";
import { IndustryTruthBadge } from "../IndustryTruthBadge";
import { EmptyEvidence, ReportSection } from "./shared";

function roleLabel(roles: readonly string[]): string {
  const labels: Record<string, string> = { news: "新闻", catalyst: "催化", risk: "风险", reverse_signal: "反向验证" };
  return roles.map((role) => labels[role]).join(" · ");
}

export function NewsRiskSection({ trusted, candidate, windowDays, onWindowDaysChange }: {
  trusted: IndustryTrustedEvent[]; candidate: CandidateIndustryEvidence | null;
  windowDays: IndustryWindowDays; onWindowDaysChange: (days: IndustryWindowDays) => void;
}) {
  return <ReportSection id="news-risk" index="08" title="新闻、催化与风险" eyebrow="Catalysts and risks">
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">窗口仅过滤当前已加载证据，不触发 Live 刷新。</p><div className="flex rounded-lg border border-border p-1">{([7, 30, 90] as const).map((days) => <button key={days} type="button" aria-label={`最近 ${days} 天`} aria-pressed={windowDays === days} onClick={() => onWindowDaysChange(days)} className={`min-h-11 rounded-md px-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${windowDays === days ? "bg-primary/15 text-primary" : "text-muted-foreground"}`}>{days} 天</button>)}</div></div>
    <div className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
      <div><h3 className="text-sm font-semibold">可信事件</h3>{trusted.length ? <div className="mt-3 divide-y divide-border/60 border-y border-border/60">{trusted.map((event) => <article key={event.eventId} data-industry-id={event.industryId} className="py-4"><div className="flex flex-wrap items-center gap-2"><IndustryTruthBadge label={event.status === "verified" ? "已核验" : "多源印证"} tone="success" /><span className="text-xs text-muted-foreground">{roleLabel(event.roles)}</span></div><h4 className="mt-2 font-semibold">{event.eventId}</h4><p className="mt-1 text-xs text-muted-foreground">{event.occurredAt} · {event.evidenceIds.join(" · ")}</p></article>)}</div> : <div className="mt-3"><EmptyEvidence>当前窗口没有已准入新闻、催化、风险或反向信号。</EmptyEvidence></div>}</div>
      <aside className="space-y-5 border-l border-border/70 pl-5"><div><div className="flex items-center gap-2"><h3 className="text-sm font-semibold">待核验</h3><IndustryTruthBadge label="未进入当前结论" tone="warning" /></div>{candidate?.unverifiedEvents.length ? <ul className="mt-3 space-y-3">{candidate.unverifiedEvents.map((event) => <li key={event.eventId}><p className="font-semibold">{event.eventId}</p><p className="text-xs text-muted-foreground">{event.occurredAt} · {roleLabel(event.roles)}</p><p className="mt-1 font-mono text-[10px] text-muted-foreground">证据 {event.evidenceIds.join(" · ") || "暂无可靠数据"}</p></li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">暂无待核验事件</p>}</div>
      <div role={candidate?.conflictingEvents.length ? "alert" : undefined}><div className="flex items-center gap-2"><h3 className="text-sm font-semibold">冲突</h3><IndustryTruthBadge label="支持与反驳并列" tone="danger" /></div>{candidate?.conflictingEvents.length ? <ul className="mt-3 space-y-3">{candidate.conflictingEvents.map((event) => <li key={event.eventId}><p className="font-semibold">{event.eventId}</p><p className="text-xs text-muted-foreground">支持 {event.supportingEvidenceIds.join(" · ")} / 反驳 {event.contradictingEvidenceIds.join(" · ")}</p></li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">暂无冲突事件</p>}</div></aside>
    </div>
  </ReportSection>;
}
