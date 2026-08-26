import type {
  CandidateIndustryEvidence,
  DisplayedIndustryReport,
  IndustryMetric,
  IndustryWindowDays,
} from "@/lib/api";

export type { CandidateIndustryEvidence, DisplayedIndustryReport, IndustryMetric, IndustryWindowDays };

export interface IndustryReportTemplate {
  id: "semiconductor" | "storage" | "robotics";
  name: string;
  subtitle: string;
  cycleLabels: string[];
  chainLabels: string[];
  metricLabels: string[];
  capitalLabels: string[];
}

export interface IndustryReportProps {
  report: DisplayedIndustryReport;
  candidate: CandidateIndustryEvidence | null;
  industryName: string;
  windowDays: IndustryWindowDays;
  onWindowDaysChange: (days: IndustryWindowDays) => void;
}
