import type {
  CandidateIndustryEvidence,
  DisplayedIndustryReport,
  IndustryMetric,
  IndustryRefreshRun,
  IndustryWindowDays,
} from "@/lib/api";

export type { CandidateIndustryEvidence, DisplayedIndustryReport, IndustryMetric, IndustryRefreshRun, IndustryWindowDays };

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
  refreshRun: IndustryRefreshRun;
  windowDays: IndustryWindowDays;
  onWindowDaysChange: (days: IndustryWindowDays) => void;
  now?: () => Date;
}
