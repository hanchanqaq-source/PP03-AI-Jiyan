export type VerificationStatus = "verified" | "corroborated" | "unverified" | "conflicting" | "corrected" | "disproved";
export type FieldVerificationStatus = "verified" | "corroborated" | "unverified" | "conflicting";
export type EvidenceHistoryDays = 1 | 3 | 7 | 30 | 90;

export interface EvidenceTransition {
  from_status: VerificationStatus | null;
  to_status: VerificationStatus;
  changed_at: string;
  reason: string;
}

export interface EvidenceField {
  field_name: string;
  raw_value: string;
  normalized_value: string;
  verification_status: FieldVerificationStatus;
  evidence_ids: string[];
  reason: string;
}

export interface EvidenceItem {
  evidence_id: string;
  content_source: string;
  collector_source: string;
  canonical_url: string;
  published_at: string | null;
  source_role: "primary" | "independent" | "syndicated";
  origin_cluster: string;
  supports_claim: boolean;
  supports_fields: string[];
  contradicts_claim: boolean;
  is_official: boolean;
  title: string;
  excerpt: string;
}

export interface EvidenceEventSummary {
  event_id: string;
  title: string;
  published_at: string | null;
  verified_at: string;
  evidence_as_of: string;
  category: string;
  related_tags: Array<{ id: string; name: string }>;
  core_claim: string;
  verification_status: VerificationStatus;
  verification_reason: string;
  verified_key_fields: EvidenceField[];
  pending_key_field_count: number;
  conflicting_key_field_count: number;
  primary_evidence_count: number;
  independent_evidence_count: number;
  syndicated_copy_count: number;
  contradicting_evidence_count: number;
  holding_relevance: string;
  status_change_count: number;
  latest_transition: EvidenceTransition | null;
}

export interface EvidenceEventDetail extends EvidenceEventSummary {
  summary: string;
  key_fields: EvidenceField[];
  primary_evidence: EvidenceItem[];
  independent_evidence: EvidenceItem[];
  syndicated_copies: EvidenceItem[];
  contradicting_evidence: EvidenceItem[];
  status_history: EvidenceTransition[];
}

export interface EvidenceSummaryData {
  loaded: boolean;
  snapshot_id: string | null;
  generated_at: string | null;
  counts: Record<VerificationStatus, number> | null;
  field_counts: Record<FieldVerificationStatus, number> | null;
  admitted_count: number | null;
  isolated_count: number | null;
  last_refresh: Record<string, unknown> | null;
}

export interface EvidenceEventList {
  events: EvidenceEventSummary[];
  snapshot_id: string | null;
  generated_at: string | null;
  total: number;
  filters: {
    verification_status: VerificationStatus | null;
    tag_id: string | null;
    category: string | null;
    days: number;
    holding_relevance: string | null;
  };
}

export interface EvidenceEventQuery {
  verification_status?: VerificationStatus;
  tag_id?: string;
  category?: string;
  days?: 1 | 3 | 7 | 30;
  holding_relevance?: string;
}

export interface EvidenceRecoveryProvenance {
  source: string;
  status: "cache_recovered" | "public_refetched";
  recovered_at: string;
  source_snapshot_id: string;
}

export interface EvidenceArchiveLineage {
  evidence_snapshot_id: string;
  raw_snapshot_id: string;
  generated_at: string;
  content_digest: string;
  raw_input_digest: string;
  recovery?: EvidenceRecoveryProvenance;
  legacy_v1?: true;
  legacy_projection_digest?: string;
  legacy_unverifiable?: true;
}

export interface EvidenceArchiveEvent extends EvidenceEventDetail {
  schema_version: 3;
  evidence_snapshot_id: string;
  raw_snapshot_id: string;
  snapshot_generated_at: string;
  archived_at: string;
  last_updated_at: string;
  snapshot_history: EvidenceArchiveLineage[];
  content_digest: string;
  raw_input_digest: string;
}

export interface EvidenceArchiveDiagnostics {
  scanned_files: number;
  skipped_files: number;
  scanned_rows: number;
  skipped_corrupt_rows: number;
  duplicate_rows: number;
}

export interface EvidenceArchiveProvenance {
  event_id: string;
  evidence_snapshot_id: string;
  raw_snapshot_id: string;
  snapshot_history: EvidenceArchiveLineage[];
  recovery: EvidenceRecoveryProvenance[];
}

export interface EvidenceArchiveQuery {
  days: EvidenceHistoryDays;
  verification_status?: VerificationStatus;
  limit: number;
  cursor?: string;
}

export interface EvidenceArchivePage {
  limit: number;
  returned: number;
  has_more: boolean;
  next_cursor: string | null;
}

export interface EvidenceArchiveList {
  events: EvidenceArchiveEvent[];
  total: number;
  filters: {
    days: EvidenceHistoryDays;
    verification_status: VerificationStatus | null;
  };
  diagnostics: EvidenceArchiveDiagnostics;
  provenance: EvidenceArchiveProvenance[];
  page: EvidenceArchivePage;
}
