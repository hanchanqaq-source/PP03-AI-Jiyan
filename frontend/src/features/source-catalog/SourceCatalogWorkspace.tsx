import { Loader2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { SourceCatalogFilters, type CatalogFiltersValue } from "./SourceCatalogFilters";
import { SourceFamilyCard } from "./SourceFamilyCard";
import type { DataSourceCatalogResponse, SourceFamilyView } from "./types";

const initialFilters: CatalogFiltersValue = { search: "", billing: "all", catalogStatus: "all", healthStatus: "all" };

function matches(family: SourceFamilyView, filters: CatalogFiltersValue): boolean {
  const phrase = filters.search.trim().toLocaleLowerCase("zh-CN");
  const textMatches = !phrase || [family.source_family_name, family.source_family_id, ...family.adapters.flatMap((adapter) => [adapter.adapter_name, adapter.adapter_id, ...adapter.capabilities.flatMap((capability) => [capability.capability_name, capability.capability_id])])].some((value) => value.toLocaleLowerCase("zh-CN").includes(phrase));
  const statusMatches = filters.healthStatus === "all" || family.health_status === filters.healthStatus || family.adapters.some((adapter) => adapter.health_status === filters.healthStatus || adapter.capabilities.some((capability) => capability.health_status === filters.healthStatus));
  const adapters = family.adapters.filter((adapter) => (filters.billing === "all" || adapter.billing_model === filters.billing) && (filters.catalogStatus === "all" || adapter.catalog_status === filters.catalogStatus));
  const selectMatches = (filters.billing === "all" && filters.catalogStatus === "all" || adapters.length > 0) && statusMatches;
  return textMatches && selectMatches;
}

export function SourceCatalogWorkspace({ onCatalogUnavailable }: { onCatalogUnavailable?: () => void }) {
  const [catalog, setCatalog] = useState<DataSourceCatalogResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [filters, setFilters] = useState(initialFilters);
  const unavailableRef = useRef(onCatalogUnavailable);
  useEffect(() => { unavailableRef.current = onCatalogUnavailable; }, [onCatalogUnavailable]);
  useEffect(() => { let active = true; api.dataSourceCatalog().then((value) => { if (active) { setCatalog(value); setError(false); } }).catch(() => { if (active) { setError(true); unavailableRef.current?.(); } }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, []);
  const families = useMemo(() => catalog?.families.filter((family) => matches(family, filters)) || [], [catalog, filters]);
  return <section className="rounded-xl border border-border/60 bg-background/45" aria-label="来源目录">
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border/55 p-4"><div><h2 className="font-semibold">来源目录</h2><p className="mt-1 text-xs text-muted-foreground">目录身份独立于体检观测；展开家族后查看接入方式与能力。</p></div>{catalog && <div className="flex flex-wrap gap-2 text-xs"><span className="rounded border border-border px-2 py-1">{catalog.registration.news_sources} 个资讯来源</span><span className="rounded border border-border px-2 py-1">已观测 {catalog.observed.sources} 项</span></div>}</header>
    <SourceCatalogFilters value={filters} onChange={setFilters} />
    <div className="space-y-3 p-4">{catalog?.portfolio_relation.status === "unavailable_no_holdings" && <p className="rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">暂无基金持仓，无法建立关联</p>}{loading && <p className="inline-flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取来源目录…</p>}{error && <p role="alert" className="text-sm text-warning">来源目录读取失败，未显示任何推测来源。</p>}{catalog && !loading && !error && <><p className="text-xs text-muted-foreground">{families.length} 个来源家族</p>{families.length ? families.map((family) => <SourceFamilyCard key={family.source_family_id} family={family} />) : <p className="rounded-lg border border-border/55 p-4 text-sm text-muted-foreground">当前筛选没有匹配的来源家族。</p>}</>}</div>
  </section>;
}
