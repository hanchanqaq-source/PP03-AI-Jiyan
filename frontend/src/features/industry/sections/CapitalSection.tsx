import type { DataField, IndustryReportTemplate } from "../types";
import { FieldGrid, ReportSection } from "./shared";

export function CapitalSection({ template, liveCapital }: { template: IndustryReportTemplate; liveCapital?: DataField[] }) {
  return <ReportSection id="capital" index="05" title="资金与估值" eyebrow="Capital and valuation"><FieldGrid fields={liveCapital?.length ? liveCapital : template.capital} /></ReportSection>;
}
