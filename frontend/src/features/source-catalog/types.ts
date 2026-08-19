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
