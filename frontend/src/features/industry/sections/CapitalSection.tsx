import type { IndustryMetric } from "@/lib/api";
import { MetricRows, ReportSection } from "./shared";

export function CapitalSection({ metrics }: { metrics: IndustryMetric[] }) {
  return <ReportSection id="capital" index="05" title="资金与估值" eyebrow="Capital and valuation"><p className="mb-5 text-sm text-muted-foreground">板块资金、ETF 份额、估值水平与历史分位不以行情涨跌替代。</p><MetricRows metrics={metrics} /></ReportSection>;
}
