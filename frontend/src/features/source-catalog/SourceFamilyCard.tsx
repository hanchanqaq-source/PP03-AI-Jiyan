import { ChevronDown, ChevronUp, ExternalLink, Settings2 } from "lucide-react";
import { useState } from "react";
import type { AdapterView, CatalogHealthStatus, CatalogStatus, SourceFamilyView } from "./types";
import { SourceConfigurationDrawer } from "./SourceConfigurationDrawer";

const catalogLabels: Record<CatalogStatus, string> = { connected: "已连接", configured: "已配置", unconfigured: "未配置", catalog_only: "仅目录", license_required: "需要许可证", disabled: "已停用" };
const healthLabels: Record<CatalogHealthStatus, string> = { unexamined: "尚未体检", healthy: "健康", degraded: "降级", partial_degraded: "部分降级", failed: "失败" };
const billingLabels: Record<AdapterView["billing_model"], string> = { free_no_key: "免费免密钥", free_key: "免费需密钥", freemium: "免费额度", paid_api: "付费 API", enterprise_license: "企业许可证", internal_only: "内部专用" };

function PublicLink({ label, href }: { label: string; href: string | null | undefined }) {
  if (!href || !/^https?:\/\//i.test(href)) return null;
  return <a href={href} target="_blank" rel="noreferrer" className="inline-flex max-w-full items-center gap-1 break-all text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><ExternalLink className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{label}</a>;
}

function AdapterRow({ adapter, onConfigure }: { adapter: AdapterView; onConfigure: () => void }) {
  return <section className="rounded-lg border border-border/55 bg-background/45 p-3 text-xs">
    <div className="flex flex-wrap items-start justify-between gap-2"><div><h4 className="font-semibold text-foreground">{adapter.adapter_name}</h4><p className="mt-1 text-muted-foreground">接入 ID：{adapter.adapter_id} · 优先级：{adapter.current_provider_priority}</p></div><div className="flex flex-wrap gap-1"><span className="rounded border border-border px-2 py-0.5">目录：{catalogLabels[adapter.catalog_status]}</span><span className="rounded border border-border px-2 py-0.5">体检：{healthLabels[adapter.health_status]}</span></div></div>
    <p className="mt-2 text-muted-foreground">{billingLabels[adapter.billing_model]} · {adapter.enabled ? "已启用" : "未启用"} · {adapter.usage_note}</p>
    <button type="button" onClick={onConfigure} aria-label={`配置数据源 ${adapter.adapter_name}`} className="mt-3 inline-flex items-center gap-1 rounded-lg border border-primary/40 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><Settings2 className="h-3.5 w-3.5" aria-hidden="true" />配置与用量</button>
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1"><PublicLink label="配置公开地址" href={adapter.configured_reference} />{adapter.capabilities.flatMap((capability) => capability.observed_final_reference ? [<PublicLink key={capability.capability_id} label={`观测公开地址（${capability.capability_name}）`} href={capability.observed_final_reference} />] : [])}</div>
    <div className="mt-3 space-y-2 border-t border-border/45 pt-2">{adapter.capabilities.map((capability) => <div key={capability.capability_id} className="flex flex-wrap items-center justify-between gap-2"><p><span className="font-medium text-foreground">{capability.capability_name}</span> <span className="text-muted-foreground">· 能力 ID：{capability.capability_id}</span></p><span className="rounded border border-border px-2 py-0.5">体检：{healthLabels[capability.health_status]}</span></div>)}</div>
  </section>;
}

export function SourceFamilyCard({ family }: { family: SourceFamilyView }) {
  const [expanded, setExpanded] = useState(false);
  const [selectedAdapter, setSelectedAdapter] = useState<AdapterView | null>(null);
  return <><article aria-label={`来源家族 ${family.source_family_name}`} className="rounded-xl border border-border/60 bg-muted/10 p-4">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-foreground">{family.source_family_name}</h3><p className="mt-1 text-xs text-muted-foreground">家族 ID：{family.source_family_id} · {family.region} / {family.market}</p></div><div className="flex flex-wrap gap-1 text-xs"><span className="rounded border border-border px-2 py-1">目录：{catalogLabels[family.catalog_status]}</span><span className="rounded border border-border px-2 py-1">体检：{healthLabels[family.health_status]}</span></div></div>
    <p className="mt-3 text-xs text-muted-foreground">{family.independent_evidence_eligible ? "可作为独立证据来源" : "访问路径不构成独立证据来源"} · {family.adapters.length} 个接入方式</p>
    <button onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} aria-label={`${expanded ? "收起" : "展开"}接入方式 ${family.source_family_name}`} className="mt-3 inline-flex items-center gap-1 rounded-lg border border-primary/40 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">{expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}{expanded ? "收起接入方式" : "展开接入方式"}</button>
    {expanded && <div className="mt-3 space-y-2 border-t border-border/50 pt-3">{family.adapters.map((adapter) => <AdapterRow key={adapter.adapter_id} adapter={adapter} onConfigure={() => setSelectedAdapter(adapter)} />)}</div>}
  </article><SourceConfigurationDrawer open={selectedAdapter !== null} adapter={selectedAdapter} onClose={() => setSelectedAdapter(null)} /></>;
}
