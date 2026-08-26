import { AlertTriangle, CheckCircle2, CircleDashed, Layers3, ServerCrash } from "lucide-react";
import type { IndustryMetric, IndustryVerificationStatus } from "@/lib/api";

const verificationLabel: Record<IndustryVerificationStatus, string> = {
  verified: "已核验", corroborated: "多源印证", unverified: "待核验",
  conflicting: "发生冲突", not_evaluated: "尚未核验",
};

const availabilityLabel = { available: "数据可用", partial: "部分可用", unavailable: "暂无可靠数据", unconfigured: "来源未配置" } as const;
const freshnessLabel = { fresh: "数据新鲜", stale: "旧可信数据", expired: "已失效", unknown: "时效未知" } as const;
const sourceLabel = { healthy: "来源正常", partial_failure: "部分来源失败", failed: "来源失败", not_configured: "来源未配置" } as const;

type EvidenceTone = "verified" | "corroborated" | "unverified" | "conflict" | "unavailable";

export function IndustryTruthBadge({ label, tone = "unavailable" }: { label: string; tone?: EvidenceTone }) {
  const Icon = tone === "verified" ? CheckCircle2 : tone === "corroborated" ? Layers3
    : tone === "conflict" ? ServerCrash : tone === "unverified" ? AlertTriangle : CircleDashed;
  const cls = tone === "verified" ? "border-primary/30 bg-primary/10 text-primary"
    : tone === "corroborated" ? "border-purple-400/40 bg-purple-500/10 text-purple-400"
      : tone === "conflict" ? "border-destructive/40 bg-destructive/10 text-destructive"
        : tone === "unverified" ? "border-warning/40 bg-warning/10 text-warning"
          : "border-border bg-muted/30 text-muted-foreground";
  return <span className={`inline-flex min-h-6 items-center gap-1 rounded-full border px-2 text-xs font-medium leading-5 ${cls}`}><Icon className="h-3 w-3" aria-hidden="true" />{label}</span>;
}

export function TruthAxes({ metric }: { metric: IndustryMetric }) {
  const verificationTone: EvidenceTone = metric.verificationStatus === "verified" ? "verified"
    : metric.verificationStatus === "corroborated" ? "corroborated"
      : metric.verificationStatus === "conflicting" ? "conflict"
        : metric.verificationStatus === "unverified" ? "unverified" : "unavailable";
  return (
    <div className="flex flex-wrap gap-1.5" aria-label={`${metric.label}真实性状态`}>
      <IndustryTruthBadge label={availabilityLabel[metric.availabilityStatus]} tone={metric.availabilityStatus === "partial" ? "unverified" : "unavailable"} />
      <IndustryTruthBadge label={verificationLabel[metric.verificationStatus]} tone={verificationTone} />
      <IndustryTruthBadge label={freshnessLabel[metric.freshnessStatus]} tone={metric.freshnessStatus === "expired" ? "conflict" : metric.freshnessStatus === "stale" ? "unverified" : "unavailable"} />
      <IndustryTruthBadge label={sourceLabel[metric.sourceRunStatus]} tone={metric.sourceRunStatus === "failed" ? "conflict" : metric.sourceRunStatus === "partial_failure" ? "unverified" : "unavailable"} />
    </div>
  );
}

export function VerificationBadge({ status }: { status: IndustryVerificationStatus | "partial" | "unavailable" }) {
  const label = status === "partial" ? "部分可信" : status === "unavailable" ? "暂无可靠数据" : verificationLabel[status];
  const tone: EvidenceTone = status === "verified" ? "verified" : status === "corroborated" ? "corroborated"
    : status === "conflicting" ? "conflict" : status === "unverified" || status === "partial" ? "unverified" : "unavailable";
  return <IndustryTruthBadge label={label} tone={tone} />;
}

export function MultiSourceMark({ count }: { count: number }) {
  return <span className="inline-flex items-center gap-1 text-xs leading-5 text-muted-foreground"><Layers3 className="h-3 w-3" aria-hidden="true" />{count} 个独立来源族</span>;
}
