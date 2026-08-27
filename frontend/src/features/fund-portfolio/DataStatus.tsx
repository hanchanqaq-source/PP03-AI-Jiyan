import type { DataMeta } from "./types";

const LABELS: Record<string, string> = {
  official: "正式净值",
  disclosed: "公开披露",
  estimated: "估算数据",
  user_entered: "用户输入",
  stale: "过期缓存",
  unavailable: "暂不可用",
  error: "获取失败",
};

const TONES: Record<string, string> = {
  official: "border-success/35 bg-success/10 text-success",
  disclosed: "border-primary/35 bg-primary/10 text-primary",
  estimated: "border-warning/35 bg-warning/10 text-warning",
  user_entered: "border-border bg-muted/50 text-foreground",
  stale: "border-warning/35 bg-warning/10 text-warning",
  unavailable: "border-border bg-muted/40 text-muted-foreground",
  error: "border-destructive/35 bg-destructive/10 text-destructive",
};

export function DataStatus({ meta, compact = false }: { meta?: DataMeta | null; compact?: boolean }) {
  const status = meta?.status || "unavailable";
  const label = LABELS[status] || status;
  const cache = meta?.is_stale ? " · 已过期" : meta?.is_cached ? " · 缓存" : "";
  const fallback = meta?.fallback_used ? " · 备用源" : "";
  return (
    <span title={meta?.message || undefined} className={`inline-flex items-center rounded-full border px-2 py-0.5 ${compact ? "text-[10px]" : "text-xs"} ${TONES[status] || TONES.unavailable}`}>
      {label}{cache}{fallback}
    </span>
  );
}
