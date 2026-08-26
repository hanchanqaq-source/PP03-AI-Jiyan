import { useEffect, useRef, useState } from "react";
import { SearchCheck } from "lucide-react";
import { api, type IndustryFundProjection, type IndustryFundResolution } from "@/lib/api";
import { IndustryTruthBadge } from "./IndustryTruthBadge";

function relationRow(item: IndustryFundResolution, pending: Set<string>) {
  const relation = item.relation;
  return <article key={item.selectionId} className="border-b border-border/50 py-3 last:border-0">
    <div className="flex flex-wrap items-center justify-between gap-2"><p className="font-mono text-sm font-semibold">{item.fundCode}</p>{relation && <IndustryTruthBadge label={relation.status === "verified" ? "已核验" : "多源印证"} tone="success" />}</div>
    {relation ? <dl className="mt-2 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2"><div><dt>数据日期</dt><dd className="mt-1 text-foreground">{relation.disclosureDate ?? "尚未披露"}</dd></div><div><dt>证据层级</dt><dd className="mt-1 text-foreground">{relation.relationLayer === "official_allocation" ? "官方行业配置" : "披露持仓穿透"}</dd></div><div><dt>行业暴露</dt><dd className="mt-1 text-foreground">{relation.exposureValue == null ? "待穿透" : `${relation.exposureValue}${relation.exposureUnit === "percent" ? "%" : ""}`}</dd></div><div><dt>对应证据</dt><dd className="mt-1 font-mono text-foreground">{relation.evidenceIds.join(" · ")}</dd></div></dl> : <p className="mt-2 text-xs text-muted-foreground">{item.emptyReason === "not_disclosed" ? "尚未披露" : item.emptyReason === "source_unavailable" ? "来源暂不可用" : "关系未知"}</p>}
    {pending.has(item.selectionId) && <p className="mt-2 text-xs text-warning">待穿透，不等同于当前行业暴露</p>}
  </article>;
}

export function FundRelationResolver({ industryId }: { industryId: string }) {
  const [input, setInput] = useState("");
  const [projection, setProjection] = useState<IndustryFundProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setInput(""); setProjection(null); setError(null); setLoading(false);
    return () => controllerRef.current?.abort();
  }, [industryId]);

  const submit = async () => {
    const codes = [...new Set(input.split(/[\s,，;；]+/u).map((code) => code.trim()).filter(Boolean))];
    if (codes.length === 0 || codes.some((code) => !/^\d{6}$/.test(code))) {
      setProjection(null); setError("请输入 6 位基金代码；多个代码可用逗号分隔"); return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true); setError(null); setProjection(null);
    try {
      const result = await api.resolveIndustryFundRelations(industryId, codes, controller.signal);
      if (!controller.signal.aborted && controllerRef.current === controller) setProjection(result);
    } catch (failure) {
      if (!(failure instanceof DOMException && failure.name === "AbortError") && !controller.signal.aborted) {
        setError("基金关系来源暂不可用；暂无可靠数据");
      }
    } finally {
      if (controllerRef.current === controller) { controllerRef.current = null; setLoading(false); }
    }
  };

  const pending = new Set(projection?.pendingLookthroughSelectionIds ?? []);
  const official = projection?.resolutions.filter((item) => item.relation?.relationLayer === "official_allocation") ?? [];
  const lookthrough = projection?.resolutions.filter((item) => item.relation?.relationLayer === "disclosed_lookthrough") ?? [];
  const unresolved = projection?.resolutions.filter((item) => item.relation === null) ?? [];
  return <div>
    <div className="flex flex-col gap-3 rounded-xl border border-border/70 bg-muted/10 p-4 sm:flex-row sm:items-end"><label className="min-w-0 flex-1 text-xs text-muted-foreground">基金代码<input aria-label="基金代码" value={input} onChange={(event) => setInput(event.target.value)} placeholder="例如：000001, 000002" inputMode="numeric" autoComplete="off" className="mt-2 min-h-11 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary" /></label><button type="button" onClick={submit} disabled={loading} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><SearchCheck className="h-4 w-4" />{loading ? "正在解析" : "解析本次选择"}</button></div>
    {error && <div role="alert" className="mt-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"><p>{error}</p><p className="mt-1 font-medium">暂无可靠数据</p></div>}
    {!projection && !error && !loading && <div className="mt-4 rounded-xl border border-dashed border-border/70 p-5 text-center"><p className="font-medium">还没有基金持仓</p><p className="mt-1 text-xs text-muted-foreground">仅在你显式输入基金代码并提交后解析本次关系。</p></div>}
    {projection && <div className="mt-5 grid gap-5 lg:grid-cols-2"><section aria-labelledby="official-funds"><h3 id="official-funds" className="text-sm font-semibold">官方行业配置</h3><div className="mt-2 rounded-xl border border-border/70 px-4">{official.length ? official.map((item) => relationRow(item, pending)) : <p className="py-4 text-xs text-muted-foreground">暂无可靠数据</p>}</div></section><section aria-labelledby="lookthrough-funds"><h3 id="lookthrough-funds" className="text-sm font-semibold">披露持仓穿透</h3><div className="mt-2 rounded-xl border border-border/70 px-4">{lookthrough.length ? lookthrough.map((item) => relationRow(item, pending)) : <p className="py-4 text-xs text-muted-foreground">暂无可靠数据</p>}</div></section>{unresolved.length > 0 && <section className="lg:col-span-2"><h3 className="text-sm font-semibold">未解析关系</h3><div className="mt-2 rounded-xl border border-border/70 px-4">{unresolved.map((item) => relationRow(item, pending))}</div></section>}</div>}
  </div>;
}
