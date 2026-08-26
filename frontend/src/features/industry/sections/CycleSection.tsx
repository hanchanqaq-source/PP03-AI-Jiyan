import type { IndustryMetric } from "@/lib/api";
import { MetricRows, ReportSection } from "./shared";

export function CycleSection({ metrics }: { metrics: IndustryMetric[] }) {
  return <ReportSection id="cycle" index="02" title="当前周期" eyebrow="Cycle position"><MetricRows metrics={metrics} /></ReportSection>;
}
