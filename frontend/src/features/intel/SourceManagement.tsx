import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";

import { api, ApiError, type RadarSource, type RadarSourceDefinition, type RadarSourceListing, type RadarSourceProbe } from "@/lib/api";
import { cn } from "@/lib/utils";

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : "操作失败";
}

function HealthBadge({ source }: { source: RadarSource }) {
  const status = source.health?.status ?? "unknown";
  const label = status === "ok" ? "正常" : status === "failed" ? "失败" : "未检测";
  return <span className={cn(
    "rounded-full px-2 py-0.5 text-[10px]",
    status === "ok" ? "bg-emerald-500/15 text-emerald-400" : status === "failed" ? "bg-red-500/15 text-red-400" : "bg-muted text-muted-foreground",
  )}>{label}</span>;
}

export function SourceManagement() {
  const [listing, setListing] = useState<RadarSourceListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [definition, setDefinition] = useState<RadarSourceDefinition>({ source_type: "rss", name: "", url: "", hint: "", region: "GLOBAL" });
  const [newProbe, setNewProbe] = useState<RadarSourceProbe | null>(null);
  const [newError, setNewError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const next = await api.radarSources();
      setListing(next);
      setDefinition((current) => ({ ...current, hint: current.hint || next.industries[0]?.key || "" }));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);
  const readOnly = listing?.store_status === "corrupt";
  const canTestNew = Boolean(definition.name.trim() && definition.url.trim() && definition.hint)
    && (definition.source_type === "rss" || Boolean(definition.api_adapter));
  const sourceByTrack = useMemo(() => {
    const groups = new Map<string, RadarSource[]>();
    for (const source of listing?.sources ?? []) groups.set(source.hint, [...(groups.get(source.hint) ?? []), source]);
    return groups;
  }, [listing]);

  function updateDefinition(patch: Partial<RadarSourceDefinition>) {
    setDefinition((current) => ({ ...current, ...patch }));
    setNewProbe(null);
    setNewError(null);
  }

  async function testNewSource() {
    setBusy("new-test");
    setNewError(null);
    try {
      const result = await api.testRadarSourceDefinition(definition);
      setNewProbe(result);
      if (!result.ok) setNewError(result.error_message || "连接测试失败");
    } catch (cause) {
      setNewProbe(null);
      setNewError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  async function saveSource() {
    if (!newProbe?.ok || readOnly) return;
    setBusy("new-save");
    setNewError(null);
    try {
      await api.addRadarSource(definition);
      setDefinition({ source_type: "rss", name: "", url: "", hint: listing?.industries[0]?.key || "", region: "GLOBAL" });
      setNewProbe(null);
      await reload();
    } catch (cause) {
      setNewError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  async function mutate(key: string, operation: () => Promise<unknown>) {
    if (readOnly) return;
    setBusy(key);
    setError(null);
    try {
      await operation();
      setDeleteConfirm(null);
      await reload();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  if (!listing && !error) return <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在加载来源配置…</div>;

  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p className="text-sm text-muted-foreground">内置源保持在仓库中；你的新增、停用和健康状态只保存在本机数据目录。</p>
        {listing && <p className="mt-1 text-xs text-muted-foreground/70">共 {listing.summary.total} 个 · 已启用 {listing.summary.enabled} · 自定义 {listing.summary.custom}</p>}
      </div>
      <button type="button" onClick={() => void reload()} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted/50"><RefreshCw className="h-3.5 w-3.5" />重新读取</button>
    </div>

    {(error || listing?.store_error) && <div role="alert" className="flex gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span>{listing?.store_error || error}</span></div>}

    <section className="rounded-xl border border-border/70 bg-background/25 p-4">
      <div className="mb-3 flex items-center gap-2"><Plus className="h-4 w-4 text-primary" /><h4 className="font-medium">添加资讯来源</h4></div>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="grid gap-1 text-xs text-muted-foreground">来源类型
          <select aria-label="来源类型" value={definition.source_type} onChange={(event) => updateDefinition({ source_type: event.target.value as "rss" | "api", api_adapter: undefined })} className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"><option value="rss">RSS / Atom</option><option value="api">API（显式适配器）</option></select>
        </label>
        <label className="grid gap-1 text-xs text-muted-foreground">来源名称
          <input aria-label="来源名称" value={definition.name} onChange={(event) => updateDefinition({ name: event.target.value })} className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground" placeholder="例如：机构研究 RSS" />
        </label>
        <label className="grid gap-1 text-xs text-muted-foreground md:col-span-2">来源 URL
          <input aria-label="来源 URL" value={definition.url} onChange={(event) => updateDefinition({ url: event.target.value })} className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground" placeholder="https://example.com/feed.xml" />
        </label>
        <label className="grid gap-1 text-xs text-muted-foreground">归属赛道
          <select aria-label="归属赛道" value={definition.hint} onChange={(event) => updateDefinition({ hint: event.target.value })} className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground">{(listing?.industries ?? []).map((industry) => <option key={industry.key} value={industry.key}>{industry.name}</option>)}</select>
        </label>
        <label className="grid gap-1 text-xs text-muted-foreground">地区标签
          <input aria-label="地区标签" value={definition.region ?? ""} onChange={(event) => updateDefinition({ region: event.target.value })} className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground" placeholder="GLOBAL" />
        </label>
        {definition.source_type === "api" && <label className="grid gap-1 text-xs text-muted-foreground md:col-span-2">API 适配器
          <select aria-label="API 适配器" value={definition.api_adapter ?? ""} onChange={(event) => updateDefinition({ api_adapter: event.target.value || undefined })} className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"><option value="">请选择已实现的适配器</option>{(listing?.api_adapters ?? []).map((adapter) => <option key={adapter} value={adapter}>{adapter}</option>)}</select>
          {(listing?.api_adapters.length ?? 0) === 0 && <span className="text-amber-400">当前没有已实现的 API 适配器；不会尝试猜测任意 JSON 结构。</span>}
        </label>}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" disabled={!canTestNew || readOnly || busy !== null} onClick={() => void testNewSource()} className="rounded-lg border border-primary/40 px-3 py-1.5 text-xs text-primary disabled:cursor-not-allowed disabled:opacity-40">{busy === "new-test" ? "正在测试…" : "测试连接"}</button>
        <button type="button" disabled={!newProbe?.ok || readOnly || busy !== null} onClick={() => void saveSource()} className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-40">{busy === "new-save" ? "正在保存…" : "保存来源"}</button>
        {newProbe?.ok && <span className="inline-flex items-center gap-1 text-xs text-emerald-400"><CheckCircle2 className="h-3.5 w-3.5" />连接成功 · {newProbe.feed_format?.toUpperCase()} · {newProbe.item_count ?? 0} 条可解析条目</span>}
        {newError && <span role="alert" className="text-xs text-red-400">{newError}</span>}
      </div>
    </section>

    <section className="space-y-4">
      {(listing?.industries ?? []).map((industry) => {
        const rows = sourceByTrack.get(industry.key) ?? [];
        if (rows.length === 0) return null;
        return <div key={industry.key}>
          <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{industry.name} · {rows.length}</h4>
          <div className="space-y-2">{rows.map((source) => <article key={source.id} className="rounded-xl border border-border/60 bg-background/20 p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{source.name}</span><span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{source.built_in ? "内置" : "自定义"}</span><span className={cn("rounded-full px-2 py-0.5 text-[10px]", source.enabled ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground")}>{source.enabled ? "已启用" : "已停用"}</span><HealthBadge source={source} /></div>
                <span className="mt-1 block max-w-3xl truncate text-xs text-muted-foreground">{source.display_url}</span>
                {source.health?.error_message && <p className="mt-1 text-xs text-red-400">{source.health.error_message}</p>}
              </div>
              <div className="flex flex-wrap gap-1.5">
                <button type="button" aria-label={`测试 ${source.name}`} disabled={readOnly || busy !== null} onClick={() => void mutate(`test-${source.id}`, () => api.testRadarSource(source.id))} className="rounded-lg border border-border px-2.5 py-1 text-xs disabled:opacity-40">测试</button>
                <button type="button" aria-label={`${source.enabled ? "停用" : "启用"} ${source.name}`} disabled={readOnly || busy !== null} onClick={() => void mutate(`toggle-${source.id}`, () => api.setRadarSourceEnabled(source.id, !source.enabled))} className="rounded-lg border border-border px-2.5 py-1 text-xs disabled:opacity-40">{source.enabled ? "停用" : "启用"}</button>
                {!source.built_in && (deleteConfirm === source.id ? <button type="button" aria-label={`确认删除 ${source.name}`} disabled={readOnly || busy !== null} onClick={() => void mutate(`delete-${source.id}`, () => api.deleteRadarSource(source.id))} className="rounded-lg border border-red-500/50 px-2.5 py-1 text-xs text-red-400 disabled:opacity-40">确认删除</button> : <button type="button" aria-label={`删除 ${source.name}`} disabled={readOnly || busy !== null} onClick={() => setDeleteConfirm(source.id)} className="rounded-lg border border-border px-2.5 py-1 text-xs text-muted-foreground disabled:opacity-40"><Trash2 className="h-3.5 w-3.5" /></button>)}
              </div>
            </div>
          </article>)}</div>
        </div>;
      })}
    </section>
  </div>;
}
