from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class PipelinePhase(str, Enum):
    QUEUED = "queued"
    FETCHING = "fetching"
    RAW_SAVED = "raw_saved"
    VERIFYING = "verifying"
    EVIDENCE_SAVED = "evidence_saved"
    TRUSTED_PUBLISHED = "trusted_published"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class PipelineCounts:
    raw_event_count: int = 0
    verified_count: int = 0
    corroborated_count: int = 0
    pending_count: int = 0
    conflicting_count: int = 0
    corrected_count: int = 0
    disproved_count: int = 0
    failed_source_count: int = 0


@dataclass(frozen=True, slots=True)
class PipelineAuthority:
    token: str
    generation: int
    expires_at: datetime
    diagnostic_name: str


@dataclass(frozen=True, slots=True)
class PipelineRun:
    run_id: str
    raw_snapshot_id: str
    evidence_snapshot_id: str | None
    trusted_snapshot_id: str | None
    phase: PipelinePhase
    counts: PipelineCounts
    created_at: datetime
    updated_at: datetime
    redacted_error: str | None
    displayed_trusted_snapshot_id: str | None
    owner_id: str | None = None
    lease_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RawSnapshot:
    raw_snapshot_id: str
    collected_at: datetime
    items: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class TrustedSnapshot:
    raw_snapshot_id: str
    published_at: datetime
    events: tuple[dict[str, Any], ...]
