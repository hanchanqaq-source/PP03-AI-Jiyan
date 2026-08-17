from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SourceGroup = Literal["fund", "quote", "industry", "news"]
ProbeStatus = Literal["success", "partial", "failure"]
HealthLabel = Literal["healthy", "usable", "degraded", "failed"]
RatingConfidence = Literal["initial", "growing", "stable"]
RepairValue = Literal[
    "none",
    "immediate_fix",
    "worth_fixing",
    "observe",
    "replace_candidate",
    "disable_candidate",
]

ErrorType = Literal[
    "none",
    "timeout",
    "dns",
    "tls",
    "connection",
    "http",
    "redirect",
    "rate_limit",
    "authentication",
    "parse",
    "empty_payload",
    "schema_changed",
    "stale_data",
    "unknown",
]


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    source_name: str
    group: SourceGroup
    capability: str
    source_reference: str
    priority: int
    critical: bool
    requires_api_key: bool
    probe_kind: str
    probe_args: dict[str, Any] = field(default_factory=dict)
    freshness_max_age_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProbeObservation:
    source_id: str
    source_name: str
    group: SourceGroup
    capability: str
    started_at: str
    finished_at: str
    latency_ms: int
    probe_status: ProbeStatus
    error_type: ErrorType
    error_message_redacted: str
    http_status: int | None
    returned_items: int
    data_as_of_date: str | None
    freshness_seconds: int | None
    field_completeness_pct: float | None
    used_cache: bool
    cache_status: str
    fallback_available: bool
    redirected: bool
    final_reference: str | None
    rating_score: float = 0.0
    rating: HealthLabel = "failed"
    rating_confidence: RatingConfidence = "initial"
    repair_value: RepairValue = "none"
    repair_reason: str = ""
    consecutive_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
