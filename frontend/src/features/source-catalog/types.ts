export type BillingModel = "free_no_key" | "free_key" | "freemium" | "paid_api" | "enterprise_license" | "internal_only";
export type CatalogStatus = "connected" | "configured" | "unconfigured" | "catalog_only" | "license_required" | "disabled";
export type CatalogHealthStatus = "unexamined" | "healthy" | "degraded" | "partial_degraded" | "failed";

export interface CapabilityView {
  capability_id: string;
  capability_name: string;
  data_category: string;
  freshness_max_age_seconds: number | null;
  probe_enabled: boolean;
  unit_policy: string;
  frequency_policy: string;
  primary_families: string[];
  fallback_families: string[];
  cross_check_families: string[];
  probe_status?: string | null;
  health_status: CatalogHealthStatus;
  observed_final_reference?: string | null;
  last_success_at?: string | null;
  latency_ms?: number | null;
  error_type?: string | null;
  error_message_redacted?: string;
}

export interface AdapterView {
  adapter_id: string;
  adapter_name: string;
  source_family_id: string;
  provider_type: string;
  source_roles: string[];
  capability_ids: string[];
  billing_model: BillingModel;
  auth_type: string;
  credential_env_names: string[];
  default_enabled: boolean;
  license_note: string;
  usage_note: string;
  data_delay: string;
  quota_policy: string;
  cost_policy: string;
  configured_reference: string;
  current_provider_priority: number;
  catalog_status: CatalogStatus;
  enabled: boolean;
  health_status: CatalogHealthStatus;
  capabilities: CapabilityView[];
}

export interface SourceFamilyView {
  source_family_id: string;
  source_family_name: string;
  region: string;
  market: string;
  source_roles: string[];
  independent_evidence_eligible: boolean;
  commercial_use_status: string;
  catalog_status: CatalogStatus;
  health_status: CatalogHealthStatus;
  adapters: AdapterView[];
}

export interface DataSourceCatalogResponse {
  registration: { families: number; adapters: number; capabilities: number; news_sources: number; fingerprint: string };
  observed: { sources: number; families: number; adapters: number; capabilities: number };
  portfolio_relation: { status: "unavailable_no_holdings" | string };
  families: SourceFamilyView[];
  capabilities: CapabilityView[];
}

export interface CredentialState {
  configured: boolean;
  status: string;
  last_validated_at: string | null;
  credential_source: string;
}

export interface AdapterConfigurationView {
  adapter_id: string;
  billing_model: BillingModel;
  catalog_status: CatalogStatus;
  enabled: boolean;
  usage_mode: string | null;
  daily_budget: string | null;
  monthly_budget: string | null;
  per_request_budget: string | null;
  daily_request_limit: number | null;
  monthly_request_limit: number | null;
  credential: CredentialState;
}

export interface DataSourceConfigurationResponse {
  free_only: boolean;
  adapters: AdapterConfigurationView[];
}

export interface AdapterUsageView {
  adapter_id: string;
  day: string;
  month: string;
  usage_status: "observed" | "unobserved";
  daily_cost: string | null;
  monthly_cost: string | null;
  daily_request_count: number | null;
  monthly_request_count: number | null;
  daily_units: string | null;
  monthly_units: string | null;
  status_counts: Record<string, number>;
  open_reservations: number | null;
}

export interface DataSourceUsageResponse {
  as_of: string;
  timezone: string;
  usage_status: "observed" | "unobserved";
  adapters: AdapterUsageView[];
}

export interface AdapterCostView {
  adapter_id: string;
  billing_model: BillingModel;
  enabled: boolean;
  credential_configured: boolean;
  status: string;
  usage_status: "observed" | "unobserved";
  day: string;
  month: string;
  daily_budget: string | null;
  monthly_budget: string | null;
  per_request_budget: string | null;
  daily_cost: string | null;
  monthly_cost: string | null;
  daily_remaining: string | null;
  monthly_remaining: string | null;
  open_reservations: number | null;
}

export interface DataSourceCostResponse {
  as_of: string;
  timezone: string;
  free_only: boolean;
  usage_status: "observed" | "unobserved";
  adapters: AdapterCostView[];
}

export interface AdapterConfigUpdate {
  usage_mode?: string;
  daily_budget?: string;
  monthly_budget?: string;
  per_request_budget?: string;
  daily_request_limit?: number;
  monthly_request_limit?: number;
}

export interface AdapterConfigMutationResponse {
  adapter_id: string;
  config: Record<string, string | number | boolean | null>;
}

export interface AdapterActionResponse {
  adapter_id: string;
  action?: "enable" | "disable";
  status: string;
  enabled?: boolean;
  connected: boolean;
  health_failure?: boolean;
  last_validated_at?: string | null;
}

export interface BudgetGateState {
  valid: boolean;
  saved: boolean;
}
