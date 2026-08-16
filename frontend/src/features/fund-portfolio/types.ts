export type FundDataStatus = "official" | "disclosed" | "estimated" | "user_entered" | "stale" | "unavailable" | "error";

export interface DataMeta {
  source_name: string;
  source_reference: string;
  data_type: string;
  as_of_date: string | null;
  fetched_at: string;
  status: FundDataStatus | string;
  is_cached: boolean;
  is_stale: boolean;
  provider: string;
  fallback_used: boolean;
  message: string;
  original_status: string | null;
}

export interface DataSection<T> { data: T | null; meta: DataMeta }

export interface FundSearchResult {
  code: string;
  name: string;
  fund_type: string | null;
  latest_nav: number | null;
  latest_nav_date: string | null;
  manager_names: string[];
  management_company: string | null;
}

export interface FundProfile {
  code: string;
  name: string;
  full_name: string | null;
  fund_type: string | null;
  established_date: string | null;
  scale: number | null;
  scale_unit: string | null;
  scale_date: string | null;
  manager_names: string[];
  management_company: string | null;
  custodian: string | null;
  risk_level: string | null;
  risk_note: string | null;
}

export interface NavPoint {
  date: string;
  unit_nav: number;
  cumulative_nav: number | null;
  daily_change_pct: number | null;
}

export interface LatestNav { unit_nav: number; cumulative_nav: number | null; nav_date: string }
export interface NavHistory { points: NavPoint[]; latest: LatestNav }

export interface FundPerformance {
  returns: Record<"1m" | "3m" | "6m" | "1y" | "3y", number | null>;
  since_inception: number | null;
  max_drawdown: number | null;
  annualized_volatility: number | null;
}

export interface DisclosedStock {
  stock_code: string;
  stock_name: string;
  weight_pct: number;
  shares_10k: number | null;
  market_value_10k: number | null;
}

export interface DisclosedHoldings {
  report_period: string;
  disclosure_date: string;
  public_date: string | null;
  is_top_ten: boolean;
  top10_coverage_pct: number;
  holdings: DisclosedStock[];
}

export interface ExposureItem { name: string; weight_pct: number }
export interface SystemTagExposure { id: string; name: string; weight_pct: number }
export interface FundIndustryExposure {
  primary: ExposureItem[];
  secondary: ExposureItem[];
  broad: ExposureItem[];
  system_tags: SystemTagExposure[];
  identified_coverage_pct: number;
  unidentified_disclosed_pct: number;
  undisclosed_stock_pct: number;
  non_stock_pct: number;
  calculation_basis: string;
  industry_classification_source: string;
}

export interface IntradayEstimate {
  status: "estimated" | "unavailable";
  estimated_nav?: number;
  estimated_change_pct?: number;
  estimated_at?: string;
  disclosure_date?: string;
  holdings_coverage_pct?: number;
  quote_coverage_pct?: number;
  confidence?: string;
  formula?: string;
  message: string;
}

export interface FundAnalysis {
  code: string;
  profile: DataSection<FundProfile>;
  latest_nav: DataSection<LatestNav>;
  nav_history: DataSection<NavHistory>;
  performance: FundPerformance;
  holdings: DataSection<DisclosedHoldings>;
  industry_exposure: DataSection<FundIndustryExposure>;
  intraday_estimate: DataSection<IntradayEstimate>;
  data_quality: Record<string, DataMeta>;
}

export interface FundHolding {
  schema_version: 3;
  code: string;
  input_mode: "amount_pnl" | "shares_cost";
  amount_snapshot: number | null;
  cumulative_pnl_snapshot: number | null;
  snapshot_at: string | null;
  shares: number | null;
  shares_source: "user" | "inferred" | null;
  basis_nav: number | null;
  basis_nav_date: string | null;
  shares_inference_note: string | null;
  avg_unit_cost: number | null;
  avg_cost: number | null;
  buy_date: string;
  notes: string;
  custom_tag_ids: string[];
  verification_status: "verified" | "manual_unverified";
  manual_name: string | null;
  cost_confirmation_required: boolean;
  created_at: string;
  updated_at: string;
  legacy_name?: string | null;
  legacy_amount?: number | null;
  legacy_cost?: number | null;
}

export interface FundHoldingInput {
  code: string;
  input_mode: "amount_pnl" | "shares_cost";
  amount_snapshot: number | null;
  cumulative_pnl_snapshot: number | null;
  shares: number | null;
  avg_unit_cost: number | null;
  avg_cost: number | null;
  buy_date: string;
  notes: string;
  custom_tag_ids: string[];
  verification_status: "verified" | "manual_unverified";
  manual_name: string | null;
  replace: boolean;
}

export interface FundMigration {
  from_schema: number;
  migrated_at: string;
  backup_file: string;
  cost_confirmation_required_count: number;
}

export interface FundPortfolioData {
  schema_version: number;
  holdings: FundHolding[];
  total_cost: number;
  updated: string | null;
  migration: FundMigration | null;
  data_status: string;
}

export interface PositionMetrics {
  user_amount_snapshot: number | null;
  user_cumulative_pnl_snapshot: number | null;
  snapshot_at: string | null;
  official_market_value: number | null;
  intraday_market_value: number | null;
  position_value: number | null;
  position_value_basis: "official_nav_from_inferred_shares" | "official_nav_from_user_shares" | "user_amount_snapshot" | "unavailable";
  reference_total_cost: number | null;
  today_estimated_profit_loss: number | null;
  intraday_change_pct: number | null;
  total_cost: number | null;
  market_value: number | null;
  profit_loss: number | null;
  return_rate: number | null;
}

export interface PortfolioHoldingAnalysis {
  code: string;
  name: string;
  fund_type: string | null;
  user_holding: FundHolding;
  position: PositionMetrics;
  weight_pct: number | null;
  analysis: FundAnalysis | null;
}

export interface PortfolioOverview {
  fund_count: number;
  total_cost: number;
  market_value: number;
  total_holding_value: number;
  profit_loss: number | null;
  return_rate: number | null;
  intraday_change_pct: number | null;
  intraday_estimated_profit_loss: number | null;
  intraday_message: string;
  nav_dates: string[];
  latest_nav_date: string | null;
  inconsistent_nav_dates: boolean;
  cost_incomplete: boolean;
  pnl_complete: boolean;
  estimable_count: number;
  official_only_count: number;
  updated_at: string;
}

export interface OverlapFund {
  fund_code: string;
  fund_name: string;
  weight_pct: number;
  portfolio_exposure_pct: number;
}

export interface OverlapStock {
  stock_code: string;
  stock_name: string;
  funds: OverlapFund[];
  portfolio_exposure_pct: number;
  calculation_basis: string;
}

export interface PortfolioIndustryConcentration {
  exposure: ExposureItem[];
  identified_coverage_pct: number;
  unknown_pct: number;
  calculation_basis: string;
}

export interface FundPortfolioAnalysisData {
  overview: PortfolioOverview;
  holdings: PortfolioHoldingAnalysis[];
  overlap: OverlapStock[];
  industry_concentration: PortfolioIndustryConcentration;
  risk_flags: string[];
}
