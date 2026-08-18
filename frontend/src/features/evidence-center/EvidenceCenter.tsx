import { useEffect, useMemo, useRef, useState } from "react";
import { Database, ExternalLink, ShieldCheck, X } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { cn } from "@/lib/utils";
import { SourceHealthSummary } from "@/features/source-health/SourceHealthSummary";
import { SourceHealthWorkspace } from "@/features/source-health/SourceHealthWorkspace";
import { correctionFixtures, evidenceFixtures, type EvidenceFixture, type VerificationStatus } from "./fixtures";

type Tab = "verification" | "health" | "corrections";
type Filter = "全部" | VerificationStatus;
type Sort = "latest" | "impact" | "holdings";

const filters: Filter[] = ["全部", "已核验", "多源印证", "待核验", "存在冲突", "已证伪"];
const tabs: Array<{ value: Tab; label: string }> = [{ value: "verification", label: "资讯核验" }, { value: "health", label: "数据源健康" }, { value: "corrections", label: "更正记录" }];
const statusTone: Record<VerificationStatus, string> = { "已核验": "border-primary/50 bg-primary/10 text-primary", "多源印证": "border-sky-400/50 bg-sky-400/10 text-sky-300", "待核验": "border-slate-400/45 bg-slate-400/10 text-slate-300", "存在冲突": "border-orange-400/55 bg-orange-400/10 text-orange-300", "已证伪": "border-destructive/55 bg-destructive/10 text-destructive", "已更正": "border-primary/50 bg-primary/10 text-primary" };
const holdingsPriority = { "直接关联": 0, "行业关联": 1, "待核验相关": 2 } as const;

function PrototypeNotice({ message }: { message: string | null }) {
  return message ? <p role="status" className="rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-xs text-primary">{message}</p> : null;
}

function StatusBadge({ status }: { status: VerificationStatus }) {
  return <span className={cn("rounded-full border px-2 py-0.5 text-[11px] font-semibold", statusTone[status])}>{status}</span>;
}

function EvidenceDrawer({ event, focusHistory, onClose, onPrototypeSource }: { event: EvidenceFixture | null; focusHistory: boolean; onClose: () => void; onPrototypeSource: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const historyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!event) return;
    if (focusHistory) historyRef.current?.focus(); else closeRef.current?.focus();
    const handler = (keyboardEvent: KeyboardEvent) => { if (keyboardEvent.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [event, focusHistory, onClose]);
  if (!event) return null;
  return <div className="fixed inset-0 z-50 bg-black/65" onMouseDown={(mouseEvent) => { if (mouseEvent.currentTarget === mouseEvent.target) onClose(); }}>
    <aside role="dialog" aria-modal="true" aria-label="证据详情" className="ml-auto flex h-full w-full max-w-2xl flex-col border-l border-primary/25 bg-background/95 shadow-2xl backdrop-blur-xl">
      <header className="flex items-start justify-between border-b border-border/60 px-5 py-4"><div><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">前端演示 Fixture</p><h2 className="mt-1 text-xl font-bold">{event.title}</h2><div className="mt-2 flex items-center gap-2"><StatusBadge status={event.status} /><span className="text-xs text-muted-foreground">最后核验：{event.verifiedAt}</span></div></div><button ref={closeRef} onClick={onClose} aria-label="关闭证据详情" className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-5 w-5" /></button></header>
      <div className="flex-1 space-y-5 overflow-y-auto p-5 text-sm">
        <section><h3 className="font-semibold text-foreground">核心主张</h3><p className="mt-2 rounded-lg border border-border/55 bg-muted/15 p-3">{event.claim}</p><p className="mt-2 text-xs text-muted-foreground">状态：{event.status}。核心主张与金额、比例、数量、日期分别核验。</p></section>
        <section><h3 className="font-semibold">关键字段</h3><div className="mt-2 space-y-2">{event.fields.map((field) => <div key={field.label} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/50 bg-background/45 px-3 py-2 text-xs"><span>{field.label}：{field.value}</span><StatusBadge status={field.status} /></div>)}</div></section>
        <section><h3 className="font-semibold">一手证据</h3><ul className="mt-2 space-y-1 text-xs text-muted-foreground">{event.primaryEvidence.map((row) => <li key={row}>• {row}</li>)}</ul></section>
        <section><h3 className="font-semibold">独立来源链</h3><ul className="mt-2 space-y-1 text-xs text-muted-foreground">{event.independentChains.map((row) => <li key={row}>• {row}</li>)}</ul></section>
        <section className="rounded-lg border border-border/55 bg-muted/15 p-3"><h3 className="font-semibold">转载链</h3><p className="mt-1 text-xs">共 {event.reprintCount} 个转载来源</p><p className="mt-1 text-xs text-muted-foreground">这些来源来自同一原始稿件，不重复计算为独立证据。</p></section>
        <section><h3 className="font-semibold">冲突与更正</h3><p className="mt-2 text-xs text-muted-foreground">{event.conflictNote}</p></section>
        <section ref={historyRef} tabIndex={-1} className="rounded-lg border border-primary/20 bg-primary/5 p-3"><h3 className="font-semibold">状态历史</h3><ol className="mt-2 space-y-1 text-xs text-muted-foreground">{event.history.map((row) => <li key={row}>{row}</li>)}</ol></section>
      </div>
      <footer className="flex gap-2 border-t border-border/60 p-4"><button onClick={onPrototypeSource} className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-2 text-xs font-medium hover:border-primary/45"><ExternalLink className="h-3.5 w-3.5" />打开原始证据</button><button onClick={onClose} className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground">关闭</button></footer>
    </aside>
  </div>;
}

export function EvidenceCenter() {
  const [tab, setTab] = useState<Tab>("verification");
  const [filter, setFilter] = useState<Filter>("全部");
  const [sort, setSort] = useState<Sort>("latest");
  const [selected, setSelected] = useState<EvidenceFixture | null>(null);
  const [focusHistory, setFocusHistory] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [explanationOpen, setExplanationOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => { const eventId = new URLSearchParams(window.location.search).get("event_id"); const match = evidenceFixtures.find((event) => event.eventId === eventId); if (match) setSelected(match); }, []);
  const visibleEvents = useMemo(() => evidenceFixtures.filter((event) => filter === "全部" || event.status === filter).sort((left, right) => sort === "impact" ? right.impact - left.impact : sort === "holdings" ? holdingsPriority[left.holdingsRelation] - holdingsPriority[right.holdingsRelation] : right.verifiedAt.localeCompare(left.verifiedAt)), [filter, sort]);
  const openEvent = (event: EvidenceFixture, trigger?: HTMLButtonElement | null, history = false) => { triggerRef.current = trigger || null; setFocusHistory(history); setSelected(event); };
  const closeEvent = () => { setSelected(null); setFocusHistory(false); triggerRef.current?.focus(); };
  const sourcePrototype = () => setNotice("当前为页面原型，尚未绑定正式原始文件。");

  return <div className="pb-8">
    <PageHeader title="证据中心" subtitle="查看资讯的核验状态、一手证据、独立来源与更正记录" actions={<><button aria-label="数据说明" onClick={() => setExplanationOpen((value) => !value)} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:border-primary/45 hover:text-foreground"><Database className="h-4 w-4" />数据说明</button><button aria-label="运行核验" onClick={() => setNotice("真实性核验能力将在 A1.1-W1 接入")} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground"><ShieldCheck className="h-4 w-4" />运行核验</button></>} />
    {explanationOpen && <section role="dialog" aria-label="数据说明" className="mb-4 rounded-xl border border-primary/25 bg-primary/5 p-4 text-xs text-muted-foreground"><p className="font-semibold text-foreground">资讯核验与数据源健康是两套不同机制。</p><p className="mt-2">数据源健康只表示来源当前是否可访问、可解析和足够新鲜。</p><p className="mt-1">资讯核验表示事件的核心主张是否有一手证据或独立来源支持。</p><p className="mt-1">AI 翻译、AI 摘要和来源数量不会自动提高核验等级。</p><p className="mt-1">本轮只做页面原型，实际核验能力将在 A1.1-W1 实现。</p></section>}
    <PrototypeNotice message={notice} />
    <section className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5" aria-label="前端演示 Fixture 核验概览">{[["核验覆盖率", "73%", "本周 100 条核心主张中，73 条已完成核验", "完成核验的比例，不表示 73% 是真的"], ["已核验", "41", "有明确官方或一手证据"], ["多源印证", "22", "至少两个独立来源链支持"], ["待核验", "8", "尚未取得足够证据"], ["冲突 / 更正", "2", "存在来源冲突或官方更正"]].map(([label, value, description, note]) => <article key={label} className="rounded-xl border border-border/60 bg-gradient-to-b from-slate-950/55 to-background/45 p-3"><p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-primary">前端演示 Fixture</p><p className="mt-1 text-xs text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-bold">{value}</p><p className="mt-1 text-xs text-muted-foreground">{description}</p>{note && <p className="mt-2 text-[11px] text-warning">{note}</p>}</article>)}</section>
    <div role="tablist" aria-label="证据中心内容" className="mt-5 flex flex-wrap gap-1 border-b border-border/55">{tabs.map((item) => <button key={item.value} role="tab" aria-selected={tab === item.value} onClick={() => setTab(item.value)} className={cn("rounded-t-lg px-3 py-2 text-sm", tab === item.value ? "bg-primary/15 font-semibold text-primary" : "text-muted-foreground hover:bg-muted/50")}>{item.label}</button>)}</div>
    {tab === "verification" && <div className="mt-4 grid gap-5 xl:grid-cols-[minmax(0,7fr)_minmax(280px,3fr)]"><main className="min-w-0"><div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/55 bg-muted/10 p-3"><span className="mr-1 text-xs text-muted-foreground">筛选：</span>{filters.map((value) => <button key={value} onClick={() => setFilter(value)} aria-pressed={filter === value} className={cn("rounded-lg px-2.5 py-1 text-xs", filter === value ? "bg-primary/15 font-semibold text-primary" : "text-muted-foreground hover:bg-muted/50")}>{value}</button>)}<label className="ml-auto text-xs text-muted-foreground">排序方式<select aria-label="排序方式" value={sort} onChange={(event) => setSort(event.target.value as Sort)} className="ml-2 rounded-lg border border-border bg-background px-2 py-1.5 text-foreground"><option value="latest">最新核验</option><option value="impact">影响重要度</option><option value="holdings">持仓关联</option></select></label></div><div className="mt-3 space-y-3">{visibleEvents.map((event) => <article key={event.eventId} className="rounded-xl border border-border/60 bg-background/45 p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><StatusBadge status={event.status} /><h2 className="mt-2 font-semibold">{event.title}</h2><p className="mt-1 text-xs text-muted-foreground">发布时间：{event.publishedAt} · 前端演示 Fixture</p></div><p className="text-xs text-muted-foreground">持仓关联：{event.holdingsRelation}</p></div><p className="mt-3 text-sm">核心主张：{event.claim}</p><div className="mt-3 flex flex-wrap gap-2 text-xs">{event.fields.map((field) => <span key={field.label} className="rounded border border-border/50 px-2 py-1">{field.label}：{field.value} · {field.status}</span>)}</div><div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-border/45 pt-3"><p className="text-xs text-muted-foreground">一手证据：{event.primaryCount} · 独立来源：{event.independentCount} · 转载来源：{event.reprintCount} · 冲突来源：{event.conflictCount}</p><div className="flex gap-2"><button onClick={(buttonEvent) => openEvent(event, buttonEvent.currentTarget)} aria-label="查看证据" className="rounded-lg border border-primary/45 px-3 py-1.5 text-xs font-medium text-primary">查看证据</button><button onClick={sourcePrototype} className="rounded-lg border border-border px-3 py-1.5 text-xs">打开原始来源</button></div></div></article>)}</div></main><aside className="space-y-3"><section className="rounded-xl border border-border/60 bg-muted/10 p-4"><h2 className="font-semibold">今日重点核验</h2><ol className="mt-3 space-y-2 text-xs text-muted-foreground"><li>1. 算力中心建设公告</li><li>2. 存储产品价格变化</li><li>3. 机器人产业政策</li><li>4. 基金定期报告</li><li>5. 公司财务更正</li></ol></section><section className="rounded-xl border border-border/60 bg-muted/10 p-4"><h2 className="font-semibold">我的持仓相关</h2><p className="mt-2 text-xs text-muted-foreground">今天有 3 条已核验事件与你的持仓方向相关</p><p className="mt-2 text-xs text-warning">前端演示数据，不读取用户真实持仓。</p><p className="mt-2 text-xs">直接关联：1 · 行业关联：2 · 待核验相关：1</p></section><section className="rounded-xl border border-border/60 bg-muted/10 p-4"><h2 className="font-semibold">数据源健康</h2><p className="mt-2 text-xs text-muted-foreground">基金与行情状态 · 资讯来源状态 · 最后体检时间 · 评级置信度</p><SourceHealthSummary onOpenDetails={() => setTab("health")} /><button onClick={() => setTab("health")} className="mt-3 rounded-lg border border-border px-3 py-1.5 text-xs">查看健康详情</button></section><section className="rounded-xl border border-border/60 bg-muted/10 p-4"><h2 className="font-semibold">更正与冲突提醒</h2><p className="mt-2 text-xs text-muted-foreground">过去 7 天 · 官方更正：1 · 存在冲突：1 · 已证伪：0</p><button onClick={() => setTab("corrections")} className="mt-3 rounded-lg border border-border px-3 py-1.5 text-xs">查看更正记录</button></section></aside></div>}
    {tab === "health" && <section className="mt-4"><div className="rounded-xl border border-border/60 bg-background/45 p-4"><h2 className="font-semibold">数据源健康</h2><p className="mt-2 text-sm text-muted-foreground">数据源健康表示来源能否访问、能否解析、是否新鲜；内容核验表示具体消息是否存在证据。前者不等于后者。</p></div><SourceHealthWorkspace /></section>}
    {tab === "corrections" && <section className="mt-4 rounded-xl border border-border/60 bg-background/45 p-4"><h2 className="font-semibold">更正记录</h2><p className="mt-1 text-xs text-warning">以下均为前端演示 Fixture，不代表在线核验记录。</p><div className="mt-4 overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-muted-foreground"><tr><th className="pb-2">事件标题</th><th>原始状态</th><th>当前状态</th><th>更正类型</th><th>更正时间</th><th>更正来源</th><th /></tr></thead><tbody>{correctionFixtures.map((row) => <tr key={`${row.eventId}-${row.to}`} className="border-t border-border/45"><td className="py-3 pr-3">{row.title}</td><td>{row.from}</td><td>{row.to}</td><td>{row.type}</td><td>{row.at}</td><td>{row.source}</td><td><button onClick={(buttonEvent) => { const event = evidenceFixtures.find((fixture) => fixture.eventId === row.eventId); if (event) openEvent(event, buttonEvent.currentTarget, true); }} aria-label={`查看记录 ${row.title}`} className="rounded-lg border border-primary/45 px-2 py-1 text-primary">查看记录</button></td></tr>)}</tbody></table></div></section>}
    <EvidenceDrawer event={selected} focusHistory={focusHistory} onClose={closeEvent} onPrototypeSource={sourcePrototype} />
  </div>;
}
