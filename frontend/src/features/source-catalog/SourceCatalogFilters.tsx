import type { BillingModel, CatalogHealthStatus, CatalogStatus } from "./types";

export interface CatalogFiltersValue {
  search: string;
  billing: "all" | BillingModel;
  catalogStatus: "all" | CatalogStatus;
  healthStatus: "all" | CatalogHealthStatus;
}

export function SourceCatalogFilters({ value, onChange }: { value: CatalogFiltersValue; onChange: (next: CatalogFiltersValue) => void }) {
  const update = <K extends keyof CatalogFiltersValue>(key: K, next: CatalogFiltersValue[K]) => onChange({ ...value, [key]: next });
  return <div className="grid gap-3 border-b border-border/55 p-4 md:grid-cols-4">
    <label className="relative md:col-span-1"><span className="sr-only">来源目录搜索</span><input type="search" aria-label="来源目录搜索" value={value.search} onChange={(event) => update("search", event.target.value)} placeholder="家族、接入方式或能力" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground" /></label>
    <label className="text-xs text-muted-foreground">计费模式<select aria-label="按计费模式过滤" value={value.billing} onChange={(event) => update("billing", event.target.value as CatalogFiltersValue["billing"])} className="mt-1 w-full rounded-lg border border-border bg-background px-2 py-2 text-foreground"><option value="all">全部</option><option value="free_no_key">免费免密钥</option><option value="free_key">免费需密钥</option><option value="freemium">免费额度</option><option value="paid_api">付费 API</option><option value="enterprise_license">企业许可证</option><option value="internal_only">内部专用</option></select></label>
    <label className="text-xs text-muted-foreground">目录状态<select aria-label="按目录状态过滤" value={value.catalogStatus} onChange={(event) => update("catalogStatus", event.target.value as CatalogFiltersValue["catalogStatus"])} className="mt-1 w-full rounded-lg border border-border bg-background px-2 py-2 text-foreground"><option value="all">全部</option><option value="connected">已连接</option><option value="configured">已配置</option><option value="unconfigured">未配置</option><option value="catalog_only">仅目录</option><option value="license_required">需要许可证</option><option value="disabled">已停用</option></select></label>
    <label className="text-xs text-muted-foreground">健康状态<select aria-label="按健康状态过滤" value={value.healthStatus} onChange={(event) => update("healthStatus", event.target.value as CatalogFiltersValue["healthStatus"])} className="mt-1 w-full rounded-lg border border-border bg-background px-2 py-2 text-foreground"><option value="all">全部</option><option value="unexamined">尚未体检</option><option value="healthy">健康</option><option value="degraded">降级</option><option value="partial_degraded">部分降级</option><option value="failed">失败</option></select></label>
  </div>;
}
