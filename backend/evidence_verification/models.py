from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"
    CORRECTED = "corrected"
    DISPROVED = "disproved"


class FieldVerificationStatus(str, Enum):
    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"


class SourceRole(str, Enum):
    PRIMARY = "primary"
    INDEPENDENT = "independent"
    SYNDICATED = "syndicated"


@dataclass(frozen=True, slots=True)
class KeyField:
    field_name: str
    raw_value: str
    normalized_value: str
    verification_status: FieldVerificationStatus = FieldVerificationStatus.UNVERIFIED
    evidence_ids: tuple[str, ...] = ()
    reason: str = "证据不足，字段保持待核验"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    content_source: str
    collector_source: str
    canonical_url: str
    published_at: datetime | None
    source_role: SourceRole
    origin_cluster: str
    supports_claim: bool = False
    supports_fields: tuple[str, ...] = ()
    contradicts_claim: bool = False
    is_official: bool = False
    title: str = ""
    excerpt: str = ""


@dataclass(frozen=True, slots=True)
class StatusTransition:
    from_status: VerificationStatus | None
    to_status: VerificationStatus
    changed_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    event_id: str
    title: str
    summary: str
    category: str
    related_tags: tuple[tuple[str, str], ...]
    published_at: datetime | None
    core_claim: str
    verification_status: VerificationStatus
    verification_reason: str
    verified_at: datetime
    evidence_as_of: datetime
    key_fields: tuple[KeyField, ...] = ()
    primary_evidence: tuple[EvidenceItem, ...] = ()
    independent_evidence: tuple[EvidenceItem, ...] = ()
    syndicated_copies: tuple[EvidenceItem, ...] = ()
    contradicting_evidence: tuple[EvidenceItem, ...] = ()
    status_history: tuple[StatusTransition, ...] = ()
    holding_relevance: str = "none"


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    snapshot_id: str
    generated_at: datetime
    events: tuple[EvidenceEvent, ...]
    raw_snapshot_id: str | None = None
    recovery_metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.raw_snapshot_id:
            return
        metadata = dict(self.recovery_metadata)
        metadata["legacy_identity"] = True
        object.__setattr__(self, "raw_snapshot_id", self.snapshot_id)
        object.__setattr__(self, "recovery_metadata", metadata)


@dataclass(frozen=True, slots=True)
class MatchResult:
    key_fields: tuple[KeyField, ...]
    primary_evidence: tuple[EvidenceItem, ...]
    independent_evidence: tuple[EvidenceItem, ...]
    syndicated_copies: tuple[EvidenceItem, ...]
    contradicting_evidence: tuple[EvidenceItem, ...]
    official_support: bool
    independent_support_count: int
    has_conflict: bool
    official_correction: bool
    official_denial: bool
