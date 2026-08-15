export type TruthStatus = "verified" | "unavailable" | "development";

export interface DataField {
  label: string;
  value: string | number | null;
  unit?: string;
  status: TruthStatus;
  source: string;
  updatedAt: string | null;
  note?: string;
}

export interface ChainNode {
  name: string;
  description: string;
  representatives: string[];
  status: TruthStatus;
}

export interface IndustryNewsItem {
  title: string;
  source: string;
  time: string;
  url: string;
}

export interface IndustryReportTemplate {
  id: "semiconductor" | "storage" | "robotics";
  name: string;
  subtitle: string;
  summary: DataField[];
  judgement: string;
  basis: string[];
  confidence: string;
  invalidatingConditions: string[];
  cycle: DataField[];
  chain: ChainNode[];
  metrics: DataField[];
  capital: DataField[];
  companies: Array<{
    name: string;
    chainPosition: string;
    business: string;
    relevance: string;
    event: string;
    risk: string;
    fundExposure: string;
    status: TruthStatus;
  }>;
  funds: Array<{
    code: string;
    name: string;
    reason: string;
    advantage: string;
    risk: string;
    overlap: string;
    invalidatingCondition: string;
    status: TruthStatus;
  }>;
  catalysts: string[];
  risks: string[];
  reverseSignals: string[];
}
