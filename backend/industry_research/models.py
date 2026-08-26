from __future__ import annotations

from dataclasses import InitVar, dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
import math
from typing import Any, Mapping


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNCONFIGURED = "unconfigured"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"
    NOT_EVALUATED = "not_evaluated"


class FreshnessStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class SourceRunStatus(str, Enum):
    HEALTHY = "healthy"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


class EmptyReason(str, Enum):
    SOURCE_UNCONFIGURED = "source_unconfigured"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_FAILED = "source_failed"
    VERIFYING = "verifying"
    NOT_APPLICABLE = "not_applicable"
    NOT_DISCLOSED = "not_disclosed"
    USER_KEY_NOT_CONFIGURED = "user_key_not_configured"
    LICENSE_REQUIRED = "license_required"
    EXPIRED = "expired"
    CONFLICTING = "conflicting"
    NO_RELIABLE_DATA = "no_reliable_data"
    INSUFFICIENT_HISTORY = "insufficient_history"


class FundResolutionEmptyReason(str, Enum):
    UNKNOWN = "unknown"
    NOT_DISCLOSED = "not_disclosed"
    SOURCE_UNAVAILABLE = "source_unavailable"


class ConclusionStatus(str, Enum):
    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class TemplateStatus(str, Enum):
    COMPLETE_LAYOUT = "complete_layout"
    PARTIAL_LAYOUT = "partial_layout"
    BUILDING = "building"


class RefreshPhase(str, Enum):
    IDLE = "idle"
    COLLECTING = "collecting"
    VERIFYING = "verifying"
    FAILED = "failed"
    TRUSTED_PUBLISHED = "trusted_published"


def _require_enum(value: object, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f"{field_name} must be {enum_type.__name__}")


def verification_display_label(status: VerificationStatus) -> str:
    return {
        VerificationStatus.VERIFIED: "已核验",
        VerificationStatus.CORROBORATED: "多源印证",
        VerificationStatus.UNVERIFIED: "待核验",
        VerificationStatus.CONFLICTING: "冲突",
        VerificationStatus.NOT_EVALUATED: "未核验",
    }[status]


def _wire_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _wire_value(getattr(value, item.name))
            for item in fields(value)
            if not item.name.startswith("_")
        }
    if isinstance(value, Mapping):
        return {str(key): _wire_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire_value(item) for item in value]
    return value


class WireModel:
    def to_dict(self) -> dict[str, Any]:
        return _wire_value(self)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class EvidenceReference(WireModel):
    evidence_id: str
    source_family_id: str
    content_source: str
    origin_cluster: str
    collector_source: str
    final_url: str
    is_official: bool
    is_official_attested: bool
    supports_claim: bool
    supports_fields: tuple[str, ...]
    contradicts_claim: bool
    as_of_date: str | None
    verified_at: str

    def __post_init__(self) -> None:
        required_fields = (
            "evidence_id",
            "source_family_id",
            "content_source",
            "origin_cluster",
            "collector_source",
            "final_url",
            "verified_at",
        )
        for field_name in required_fields:
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"evidence {field_name} must not be blank")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> EvidenceReference:
        values = dict(payload)
        values["supports_fields"] = tuple(values.get("supports_fields", ()))
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class MetricChange(WireModel):
    value: float
    basis: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _finite_real(self.value, "change.value"))
        if self.basis not in {"mom", "yoy", "wow"}:
            raise ValueError("change.basis must be mom, yoy, or wow")


def _finite_real(value: object, field_name: str) -> float:
    if type(value) not in {int, float}:
        raise TypeError(f"{field_name} must be a finite real number")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{field_name} must be a finite real number") from error
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be a finite real number")
    return 0.0 if normalized == 0.0 else normalized


def _validated_change_value(change: MetricChange) -> float:
    if type(change) is not MetricChange:
        raise TypeError("change must be MetricChange")
    if change.basis not in {"mom", "yoy", "wow"}:
        raise ValueError("change.basis must be mom, yoy, or wow")
    return _finite_real(change.value, "change.value")


@dataclass(frozen=True, slots=True)
class HistoricalPosition(WireModel):
    value: float
    window: str
    method: str


@dataclass(frozen=True, slots=True)
class IndustryMetricObservation(WireModel):
    industry_id: str
    metric_id: str
    label: str
    current_value: str | int | float | None
    unit: str | None
    change: MetricChange | None
    historical_position: HistoricalPosition | None
    availability_status: AvailabilityStatus
    verification_status: VerificationStatus
    freshness_status: FreshnessStatus
    source_run_status: SourceRunStatus
    empty_reason: EmptyReason | None
    as_of_date: str | None
    fetched_at: str | None
    methodology: str
    judgment_basis: tuple[str, ...]
    invalidating_conditions: tuple[str, ...]
    evidence: tuple[EvidenceReference, ...]
    independent_source_families: tuple[str, ...]
    independent_content_sources: tuple[str, ...]
    independent_origin_clusters: tuple[str, ...]
    raw_snapshot_id: str | None
    evidence_snapshot_id: str | None
    expires_at: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.availability_status, AvailabilityStatus, "availability_status")
        _require_enum(self.verification_status, VerificationStatus, "verification_status")
        _require_enum(self.freshness_status, FreshnessStatus, "freshness_status")
        _require_enum(self.source_run_status, SourceRunStatus, "source_run_status")
        if self.empty_reason is not None:
            _require_enum(self.empty_reason, EmptyReason, "empty_reason")
        if self.expires_at is not None:
            try:
                expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            except (AttributeError, ValueError) as error:
                raise ValueError("expires_at must be an ISO-8601 datetime") from error
            if expiry.tzinfo is None or expiry.utcoffset() is None:
                raise ValueError("expires_at must be timezone-aware")
        if self.current_value is None and self.empty_reason is None:
            raise ValueError("current_value=null requires empty_reason")
        if self.current_value is not None and self.empty_reason is not None:
            raise ValueError("non-empty current_value cannot have empty_reason")
        if self.verification_status is VerificationStatus.NOT_EVALUATED and self.empty_reason in {
            EmptyReason.SOURCE_UNCONFIGURED,
            EmptyReason.SOURCE_UNAVAILABLE,
        }:
            if self.raw_snapshot_id is not None or self.evidence_snapshot_id is not None:
                raise ValueError("not_evaluated source absence requires null lineage")
        if self.verification_status not in {
            VerificationStatus.VERIFIED,
            VerificationStatus.CORROBORATED,
        }:
            return
        if self.current_value is None:
            raise ValueError("trusted observation requires current_value")
        if not self.evidence:
            raise ValueError("trusted observation requires evidence")
        if any(item.contradicts_claim for item in self.evidence):
            raise ValueError("trusted observation cannot contain contradicting evidence")
        if not self.raw_snapshot_id:
            raise ValueError("trusted observation requires raw_snapshot_id")
        if not self.evidence_snapshot_id:
            raise ValueError("trusted observation requires evidence_snapshot_id")
        if not self.as_of_date:
            raise ValueError("trusted observation requires as_of_date")
        if not self.fetched_at:
            raise ValueError("trusted observation requires fetched_at")
        if not self.methodology.strip():
            raise ValueError("trusted observation requires methodology")
        if not self.judgment_basis:
            raise ValueError("trusted observation requires judgment_basis")
        if not self.invalidating_conditions:
            raise ValueError("trusted observation requires invalidating_conditions")
        if self.verification_status is VerificationStatus.VERIFIED:
            official_support = any(
                item.is_official
                and item.is_official_attested
                and item.supports_claim
                and self.metric_id in item.supports_fields
                and bool(item.source_family_id)
                and bool(item.content_source)
                and bool(item.final_url)
                and bool(item.as_of_date)
                and bool(item.verified_at)
                for item in self.evidence
            )
            if not official_support:
                raise ValueError("verified observation requires official_attestation with source and time")
            return
        independent_sets = {
            "independent_source_families": self.independent_source_families,
            "independent_content_sources": self.independent_content_sources,
            "independent_origin_clusters": self.independent_origin_clusters,
        }
        for name, values in independent_sets.items():
            if len(set(values)) < 2:
                raise ValueError(f"corroborated observation requires at least two {name}")
        supporting_evidence = [
            item for item in self.evidence if item.supports_claim and self.metric_id in item.supports_fields
        ]
        if len(supporting_evidence) < 2:
            raise ValueError("corroborated observation requires two supporting evidence records")
        evidence_sets = {
            "independent_source_families": {item.source_family_id for item in supporting_evidence},
            "independent_content_sources": {item.content_source for item in supporting_evidence},
            "independent_origin_clusters": {item.origin_cluster for item in supporting_evidence},
        }
        for name, declared_values in independent_sets.items():
            if set(declared_values) != evidence_sets[name]:
                raise ValueError(f"{name} do not match supporting evidence")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> IndustryMetricObservation:
        values = dict(payload)
        values["availability_status"] = AvailabilityStatus(str(values["availability_status"]))
        values["verification_status"] = VerificationStatus(str(values["verification_status"]))
        values["freshness_status"] = FreshnessStatus(str(values["freshness_status"]))
        values["source_run_status"] = SourceRunStatus(str(values["source_run_status"]))
        empty_reason = values.get("empty_reason")
        values["empty_reason"] = EmptyReason(str(empty_reason)) if empty_reason is not None else None
        change = values.get("change")
        values["change"] = MetricChange(**change) if isinstance(change, Mapping) else None
        historical = values.get("historical_position")
        values["historical_position"] = HistoricalPosition(**historical) if isinstance(historical, Mapping) else None
        values["judgment_basis"] = tuple(values.get("judgment_basis", ()))
        values["invalidating_conditions"] = tuple(values.get("invalidating_conditions", ()))
        values["evidence"] = tuple(
            EvidenceReference.from_dict(item) for item in values.get("evidence", ())  # type: ignore[union-attr]
        )
        for name in (
            "independent_source_families",
            "independent_content_sources",
            "independent_origin_clusters",
        ):
            values[name] = tuple(values.get(name, ()))
        values.setdefault("expires_at", None)
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class DataCompleteness(WireModel):
    verified_metric_count: int
    required_metric_count: int
    ratio: float | None

    def __post_init__(self) -> None:
        if self.verified_metric_count < 0 or self.required_metric_count < 0:
            raise ValueError("data completeness counts cannot be negative")
        expected = self.verified_metric_count / self.required_metric_count if self.required_metric_count else None
        if expected is None and self.ratio is not None:
            raise ValueError("data completeness ratio must be null when no metrics are required")
        if expected is not None and (self.ratio is None or abs(self.ratio - expected) > 1e-9):
            raise ValueError("data completeness ratio does not match counts")


def render_conclusion_text(
    *, conclusion_id: str, industry_id: str, rule_version: str, status: ConclusionStatus,
    cycle_stage: str | None, outlook_direction: str | None, confidence_level: str | None,
    data_completeness: DataCompleteness, basis_metric_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...], invalidating_conditions: tuple[str, ...],
) -> str:
    del conclusion_id, industry_id, basis_metric_ids, invalidating_conditions
    ratio = "暂无可靠数据" if data_completeness.ratio is None else f"{data_completeness.ratio:.2f}"
    evidence = ",".join(evidence_ids) if evidence_ids else "无"
    return (
        f"规则={rule_version}；状态={status.value}；周期={cycle_stage or '暂无可靠数据'}；"
        f"方向={outlook_direction or '暂无可靠数据'}；可信度={confidence_level or '暂无可靠数据'}；"
        f"完整度={ratio}；证据={evidence}"
    )


@dataclass(frozen=True, slots=True)
class IndustryConclusion(WireModel):
    conclusion_id: str
    industry_id: str
    rule_version: str
    status: ConclusionStatus
    cycle_stage: str | None
    outlook_direction: str | None
    confidence_level: str | None
    data_completeness: DataCompleteness
    text: str
    basis_metric_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    invalidating_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_enum(self.status, ConclusionStatus, "status")
        if self.cycle_stage not in {None, "recovery", "expansion", "peak", "contraction"}:
            raise ValueError("invalid cycle_stage")
        if self.outlook_direction not in {None, "improving", "stable", "weakening"}:
            raise ValueError("invalid outlook_direction")
        if self.confidence_level not in {None, "high", "medium", "low"}:
            raise ValueError("invalid confidence_level")
        if self.data_completeness.verified_metric_count < self.data_completeness.required_metric_count:
            if any((self.cycle_stage, self.outlook_direction, self.confidence_level)):
                raise ValueError("incomplete required metrics require null structured judgments")
        expected = render_conclusion_text(
            conclusion_id=self.conclusion_id, industry_id=self.industry_id, rule_version=self.rule_version,
            status=self.status, cycle_stage=self.cycle_stage, outlook_direction=self.outlook_direction,
            confidence_level=self.confidence_level, data_completeness=self.data_completeness,
            basis_metric_ids=self.basis_metric_ids, evidence_ids=self.evidence_ids,
            invalidating_conditions=self.invalidating_conditions,
        )
        if self.text != expected:
            raise ValueError("conclusion text must be the deterministic structured rendering")


@dataclass(frozen=True, slots=True)
class IndustryChainNode(WireModel):
    industry_id: str
    node_id: str
    label: str
    observation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    status: ConclusionStatus

    def __post_init__(self) -> None:
        _require_enum(self.status, ConclusionStatus, "status")


@dataclass(frozen=True, slots=True)
class IndustryCompanyRelation(WireModel):
    industry_id: str
    security_code: str
    company_name: str
    chain_node_id: str
    relation_type: str
    key_metric_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    as_of_date: str
    observation_only: bool

    def __post_init__(self) -> None:
        if not self.security_code.strip():
            raise ValueError("trusted company relation requires security_code")
        if self.relation_type not in {"official_disclosure", "public_classification"}:
            raise ValueError("invalid relation_type")
        if not self.evidence_ids:
            raise ValueError("trusted company relation requires evidence_ids")
        if not self.as_of_date:
            raise ValueError("trusted company relation requires as_of_date")
        if self.observation_only is not True:
            raise ValueError("company relation must be observation_only")


@dataclass(frozen=True, slots=True)
class FundSelectionScope(WireModel):
    selection_id: str
    fund_code: str
    selected_in_request: bool

    def __post_init__(self) -> None:
        if not self.fund_code.strip():
            raise ValueError("fund selection requires fund_code")
        if self.selected_in_request is not True:
            raise ValueError("fund selection must be explicitly selected_in_request")


@dataclass(frozen=True, slots=True)
class IndustryFundRelation(WireModel):
    industry_id: str
    fund_code: str
    relation_layer: str
    exposure_value: float | None
    exposure_unit: str | None
    disclosure_date: str | None
    evidence_ids: tuple[str, ...]
    status: VerificationStatus

    def __post_init__(self) -> None:
        _require_enum(self.status, VerificationStatus, "status")
        if not self.fund_code.strip():
            raise ValueError("trusted fund relation requires fund_code")
        if self.relation_layer not in {"official_allocation", "disclosed_lookthrough"}:
            raise ValueError("invalid relation_layer")
        if not self.evidence_ids:
            raise ValueError("trusted fund relation requires evidence_ids")
        if self.status not in {VerificationStatus.VERIFIED, VerificationStatus.CORROBORATED}:
            raise ValueError("trusted fund relation requires verified or corroborated status")
        if self.exposure_value is None and self.exposure_unit is not None:
            raise ValueError("empty exposure_value requires null exposure_unit")
        if self.exposure_value is not None and self.exposure_unit != "percent":
            raise ValueError("fund exposure_unit must be percent")


@dataclass(frozen=True, slots=True)
class IndustryFundRelationResolution(WireModel):
    selection_id: str
    fund_code: str
    relation: IndustryFundRelation | None
    empty_reason: FundResolutionEmptyReason | None

    def __post_init__(self) -> None:
        if self.empty_reason is not None:
            _require_enum(self.empty_reason, FundResolutionEmptyReason, "empty_reason")
        if self.relation is None and self.empty_reason is None:
            raise ValueError("missing fund relation requires empty_reason")
        if self.relation is not None and self.empty_reason is not None:
            raise ValueError("resolved fund relation cannot have empty_reason")
        if self.relation is not None and self.relation.fund_code != self.fund_code:
            raise ValueError("fund relation resolution code mismatch")


@dataclass(frozen=True, slots=True)
class IndustryEvidenceEvent(WireModel):
    industry_id: str
    event_id: str
    status: VerificationStatus
    occurred_at: str
    evidence_ids: tuple[str, ...]
    roles: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_enum(self.status, VerificationStatus, "status")
        if self.status not in {VerificationStatus.VERIFIED, VerificationStatus.CORROBORATED}:
            raise ValueError("IndustryEvidenceEvent requires trusted status")
        if not self.evidence_ids:
            raise ValueError("trusted event requires evidence_ids")


@dataclass(frozen=True, slots=True)
class CandidateIndustryEvidenceEvent(WireModel):
    industry_id: str
    event_id: str
    status: VerificationStatus
    occurred_at: str
    evidence_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    roles: tuple[str, ...]
    candidate_snapshot_id: str
    raw_snapshot_id: str
    evidence_snapshot_id: str

    def __post_init__(self) -> None:
        _require_enum(self.status, VerificationStatus, "status")
        if self.status not in {VerificationStatus.UNVERIFIED, VerificationStatus.CONFLICTING}:
            raise ValueError("candidate event requires unverified or conflicting status")
        if self.status is VerificationStatus.CONFLICTING:
            if not self.supporting_evidence_ids or not self.contradicting_evidence_ids:
                raise ValueError("conflicting event requires supporting and contradicting evidence_ids")
        for name, value in (
            ("candidate_snapshot_id", self.candidate_snapshot_id),
            ("raw_snapshot_id", self.raw_snapshot_id),
            ("evidence_snapshot_id", self.evidence_snapshot_id),
        ):
            if type(value) is not str or not value.strip() or value != value.strip():
                raise ValueError(f"candidate event {name} must not be blank")


@dataclass(frozen=True, slots=True)
class ConflictingSourceValue(WireModel):
    evidence_id: str
    source_family_id: str
    value: str | int | float
    unit: str | None
    as_of_date: str | None
    change: MetricChange | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("evidence_id", self.evidence_id),
            ("source_family_id", self.source_family_id),
        ):
            if type(value) is not str or not value.strip() or value != value.strip():
                raise ValueError(f"conflicting source {name} must not be blank")
        if type(self.value) in {int, float}:
            object.__setattr__(self, "value", _finite_real(self.value, "conflicting source value"))
        elif type(self.value) is not str:
            raise TypeError("conflicting source value must be a string or finite real number")
        if self.change is not None:
            _validated_change_value(self.change)


def _conflicting_truth_key(item: ConflictingSourceValue) -> tuple[object, ...]:
    value = item.value.strip().casefold() if type(item.value) is str else item.value
    return (
        value,
        item.unit.strip().casefold() if item.unit is not None else None,
        item.as_of_date,
        None if item.change is None else (_validated_change_value(item.change), item.change.basis),
    )


_CANONICAL_CONTRADICTION_PROOF = object()


@dataclass(frozen=True, slots=True)
class ConflictingObservation(WireModel):
    industry_id: str
    metric_id: str
    aggregate_value: None
    source_values: tuple[ConflictingSourceValue, ...]
    raw_snapshot_id: str | None = None
    evidence_snapshot_id: str | None = None
    _canonical_contradiction_proof: InitVar[object] = None

    def __post_init__(self, _canonical_contradiction_proof: object) -> None:
        if self.aggregate_value is not None:
            raise ValueError("conflicting observation aggregate_value must be null")
        if len(self.source_values) < 2:
            raise ValueError("conflicting observation requires per-source values")
        if any(type(item) is not ConflictingSourceValue for item in self.source_values):
            raise TypeError("conflicting observation requires ConflictingSourceValue rows")
        evidence_ids = [item.evidence_id for item in self.source_values]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("conflicting observation source evidence must be unique")
        for name, value in (
            ("raw_snapshot_id", self.raw_snapshot_id),
            ("evidence_snapshot_id", self.evidence_snapshot_id),
        ):
            if type(value) is not str or not value.strip() or value != value.strip():
                raise ValueError(f"conflicting observation {name} must not be blank")
        if (
            len({_conflicting_truth_key(item) for item in self.source_values}) < 2
            and _canonical_contradiction_proof is not _CANONICAL_CONTRADICTION_PROOF
        ):
            raise ValueError("conflicting observation requires meaningfully distinct truth values")


def _verified_conflicting_observation(
    *,
    industry_id: str,
    metric_id: str,
    source_values: tuple[ConflictingSourceValue, ...],
    raw_snapshot_id: str,
    evidence_snapshot_id: str,
) -> ConflictingObservation:
    """Construct only after canonical A2 storage proves support and contradiction."""
    return ConflictingObservation(
        industry_id=industry_id,
        metric_id=metric_id,
        aggregate_value=None,
        source_values=source_values,
        raw_snapshot_id=raw_snapshot_id,
        evidence_snapshot_id=evidence_snapshot_id,
        _canonical_contradiction_proof=_CANONICAL_CONTRADICTION_PROOF,
    )


@dataclass(frozen=True, slots=True)
class SourceCoverage(WireModel):
    unit: str
    total: int
    configured: int
    healthy: int
    partial_failure: int
    failed: int
    unconfigured: int

    def __post_init__(self) -> None:
        if self.unit != "capability":
            raise ValueError("source coverage unit must be capability")
        if self.total != self.configured + self.unconfigured:
            raise ValueError("source coverage configured + unconfigured must equal total")
        if self.configured != self.healthy + self.partial_failure + self.failed:
            raise ValueError("configured coverage must equal run status totals")


@dataclass(frozen=True, slots=True)
class ReportCounts(WireModel):
    verified: int
    corroborated: int

    def __post_init__(self) -> None:
        values = (self.verified, self.corroborated)
        if any(type(value) is not int for value in values):
            raise TypeError("report counts must be non-negative integers")
        if any(value < 0 for value in values):
            raise ValueError("report counts must be non-negative integers")


@dataclass(frozen=True, slots=True)
class DisplayedTrustedReport(WireModel):
    industry_id: str
    template_status: TemplateStatus
    trusted_snapshot_id: str | None
    displayed_trusted_snapshot_id: str | None
    raw_snapshot_id: str
    evidence_snapshot_id: str
    generated_at: str | None
    demo: bool
    source_coverage: SourceCoverage
    counts: ReportCounts
    overview: IndustryConclusion
    cycle: tuple[IndustryMetricObservation, ...]
    chain: tuple[IndustryChainNode, ...]
    metrics: tuple[IndustryMetricObservation, ...]
    capital: tuple[IndustryMetricObservation, ...]
    companies: tuple[IndustryCompanyRelation, ...]
    fund_selection: tuple[FundSelectionScope, ...]
    funds: tuple[IndustryFundRelationResolution, ...]
    news_risk: tuple[IndustryEvidenceEvent, ...]

    def __post_init__(self) -> None:
        _require_enum(self.template_status, TemplateStatus, "template_status")
        for name, value in (
            ("raw_snapshot_id", self.raw_snapshot_id),
            ("evidence_snapshot_id", self.evidence_snapshot_id),
        ):
            if type(value) is not str or not value.strip() or value != value.strip():
                raise ValueError(f"displayed report {name} must not be blank")
        if type(self.counts) is not ReportCounts:
            raise TypeError("report counts must be ReportCounts")
        observations = self.cycle + self.metrics + self.capital
        trusted_by_metric: dict[str, IndustryMetricObservation] = {}
        for observation in observations:
            if not isinstance(observation, IndustryMetricObservation):
                raise TypeError("trusted_observations must contain IndustryMetricObservation")
            if observation.industry_id != self.industry_id:
                raise ValueError("trusted observation industry_id mismatch")
            if observation.current_value is None:
                if (
                    observation.change is not None
                    or observation.historical_position is not None
                    or observation.as_of_date is not None
                    or observation.fetched_at is not None
                    or observation.evidence
                    or observation.raw_snapshot_id is not None
                    or observation.evidence_snapshot_id is not None
                ):
                    raise ValueError("trusted_observations empty rows must not retain value provenance")
                continue
            if observation.verification_status not in {
                VerificationStatus.VERIFIED, VerificationStatus.CORROBORATED,
            } or observation.freshness_status is FreshnessStatus.EXPIRED:
                raise ValueError("trusted_observations values require current verified/corroborated observations")
            previous = trusted_by_metric.get(observation.metric_id)
            if previous is not None and previous != observation:
                raise ValueError("duplicate report metric rows must be identical")
            trusted_by_metric[observation.metric_id] = observation
            if observation.raw_snapshot_id != self.raw_snapshot_id:
                raise ValueError("trusted observation raw lineage mismatch")
            if observation.evidence_snapshot_id != self.evidence_snapshot_id:
                raise ValueError("trusted observation evidence lineage mismatch")
        trusted = tuple(trusted_by_metric.values())
        expected_counts = ReportCounts(
            verified=sum(
                observation.verification_status is VerificationStatus.VERIFIED
                for observation in trusted
            ),
            corroborated=sum(
                observation.verification_status is VerificationStatus.CORROBORATED
                for observation in trusted
            ),
        )
        if self.counts != expected_counts:
            raise ValueError("trusted report counts do not match observations")
        if any(node.industry_id != self.industry_id for node in self.chain):
            raise ValueError("chain node industry_id mismatch")
        if any(company.industry_id != self.industry_id for company in self.companies):
            raise ValueError("company relation industry_id mismatch")
        if any(
            resolution.relation is not None and resolution.relation.industry_id != self.industry_id
            for resolution in self.funds
        ):
            raise ValueError("fund relation industry_id mismatch")
        for event in self.news_risk:
            if not isinstance(event, IndustryEvidenceEvent):
                raise TypeError("news_risk must contain IndustryEvidenceEvent")
            if event.industry_id != self.industry_id:
                raise ValueError("news event industry_id mismatch")
        if self.overview.industry_id != self.industry_id:
            raise ValueError("overview industry_id mismatch")
        from .templates import validate_metric_section_shape

        validate_metric_section_shape(
            industry_id=self.industry_id,
            cycle_metric_ids=tuple(row.metric_id for row in self.cycle),
            core_metric_ids=tuple(row.metric_id for row in self.metrics),
            capital_metric_ids=tuple(row.metric_id for row in self.capital),
        )

    def validate_for_mode(self, *, production: bool) -> None:
        if production and self.demo:
            raise ValueError("production rejects demo=true trusted reports")


@dataclass(frozen=True, slots=True)
class CandidateEvidenceCounts(WireModel):
    unverified: int
    conflicting: int
    unverified_events: int
    conflicting_events: int


@dataclass(frozen=True, slots=True)
class CandidateExternalLineage(WireModel):
    """A declared, canonical external candidate lineage carried on the wire."""

    kind: str
    candidate_snapshot_id: str
    raw_snapshot_id: str
    evidence_snapshot_id: str

    def __post_init__(self) -> None:
        if self.kind != "a2_news":
            raise ValueError("unsupported external candidate lineage kind")
        values = (
            self.candidate_snapshot_id,
            self.raw_snapshot_id,
            self.evidence_snapshot_id,
        )
        if any(type(value) is not str or not value.strip() or value != value.strip() for value in values):
            raise ValueError("external candidate lineage IDs must not be blank")
        if self.candidate_snapshot_id != self.evidence_snapshot_id:
            raise ValueError("canonical A2 candidate/evidence snapshots must match")


_CANONICAL_A2_LINEAGE_PROOF = object()


@dataclass(frozen=True, slots=True)
class CandidateEvidencePanel(WireModel):
    industry_id: str
    candidate_snapshot_id: str | None
    counts: CandidateEvidenceCounts
    unverified: tuple[IndustryMetricObservation, ...]
    conflicting: tuple[ConflictingObservation, ...]
    unverified_events: tuple[CandidateIndustryEvidenceEvent, ...]
    conflicting_events: tuple[CandidateIndustryEvidenceEvent, ...]
    raw_snapshot_id: str | None = None
    evidence_snapshot_id: str | None = None
    external_lineages: tuple[CandidateExternalLineage, ...] = ()
    _canonical_external_lineage_proof: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.counts, CandidateEvidenceCounts):
            raise TypeError("counts must be CandidateEvidenceCounts")
        expected_types = {
            "unverified": IndustryMetricObservation,
            "conflicting": ConflictingObservation,
            "unverified_events": CandidateIndustryEvidenceEvent,
            "conflicting_events": CandidateIndustryEvidenceEvent,
        }
        for field_name, expected_type in expected_types.items():
            items = getattr(self, field_name)
            if any(not isinstance(item, expected_type) for item in items):
                raise TypeError(f"{field_name} contains an invalid element type")
            if any(item.industry_id != self.industry_id for item in items):
                raise ValueError(f"{field_name} industry_id mismatch")
        if any(item.verification_status is not VerificationStatus.UNVERIFIED for item in self.unverified):
            raise ValueError("candidate unverified array accepts only unverified observations")
        if any(item.status is not VerificationStatus.UNVERIFIED for item in self.unverified_events):
            raise ValueError("unverified_events accepts only unverified events")
        if any(item.status is not VerificationStatus.CONFLICTING for item in self.conflicting_events):
            raise ValueError("conflicting_events accepts only conflicting events")
        if (self.raw_snapshot_id is None) != (self.evidence_snapshot_id is None):
            raise ValueError("candidate raw/evidence lineage must be provided together")
        if self.candidate_snapshot_id is not None and (
            type(self.candidate_snapshot_id) is not str
            or not self.candidate_snapshot_id.strip()
            or self.candidate_snapshot_id != self.candidate_snapshot_id.strip()
        ):
            raise ValueError("candidate_snapshot_id must not be blank")
        if self.unverified or self.conflicting:
            if not self.raw_snapshot_id or not self.evidence_snapshot_id:
                raise ValueError("metric candidates require raw/evidence lineage")
        if self.candidate_snapshot_id is None:
            if self.raw_snapshot_id is not None:
                raise ValueError("candidate lineage must be absent without candidate_snapshot_id")
        elif not self.raw_snapshot_id or not self.evidence_snapshot_id:
            raise ValueError("candidate snapshot requires raw/evidence lineage")
        if self.raw_snapshot_id is not None and (
            not self.raw_snapshot_id.strip()
            or self.raw_snapshot_id != self.raw_snapshot_id.strip()
            or not self.evidence_snapshot_id
            or not self.evidence_snapshot_id.strip()
            or self.evidence_snapshot_id != self.evidence_snapshot_id.strip()
        ):
            raise ValueError("candidate raw/evidence lineage must not be blank")
        if any(item.raw_snapshot_id != self.raw_snapshot_id for item in self.unverified):
            raise ValueError("candidate unverified raw lineage mismatch")
        if any(item.evidence_snapshot_id != self.evidence_snapshot_id for item in self.unverified):
            raise ValueError("candidate unverified evidence lineage mismatch")
        if any(item.raw_snapshot_id != self.raw_snapshot_id for item in self.conflicting):
            raise ValueError("candidate conflicting raw lineage mismatch")
        if any(item.evidence_snapshot_id != self.evidence_snapshot_id for item in self.conflicting):
            raise ValueError("candidate conflicting evidence lineage mismatch")
        if any(not isinstance(item, CandidateExternalLineage) for item in self.external_lineages):
            raise TypeError("external_lineages contains an invalid element type")
        external = {
            (item.candidate_snapshot_id, item.raw_snapshot_id, item.evidence_snapshot_id)
            for item in self.external_lineages
        }
        if len(external) != len(self.external_lineages):
            raise ValueError("duplicate external candidate lineage")
        if self.external_lineages and self._canonical_external_lineage_proof is not _CANONICAL_A2_LINEAGE_PROOF:
            raise ValueError("external candidate lineage lacks canonical A2 proof")
        events = self.unverified_events + self.conflicting_events
        panel_lineage = (
            self.candidate_snapshot_id,
            self.raw_snapshot_id,
            self.evidence_snapshot_id,
        )
        for item in events:
            item_lineage = (
                item.candidate_snapshot_id,
                item.raw_snapshot_id,
                item.evidence_snapshot_id,
            )
            if item_lineage != panel_lineage and item_lineage not in external:
                raise ValueError("candidate event lineage is neither panel-bound nor declared canonical A2 lineage")
        used_external = {
            (
                item.candidate_snapshot_id,
                item.raw_snapshot_id,
                item.evidence_snapshot_id,
            )
            for item in events
            if (
                item.candidate_snapshot_id,
                item.raw_snapshot_id,
                item.evidence_snapshot_id,
            ) != panel_lineage
        }
        if used_external != external:
            raise ValueError("declared external candidate lineage does not match events")
        actual = (len(self.unverified), len(self.conflicting), len(self.unverified_events), len(self.conflicting_events))
        expected = (self.counts.unverified, self.counts.conflicting, self.counts.unverified_events, self.counts.conflicting_events)
        if actual != expected:
            raise ValueError("candidate evidence counts do not match arrays")


def _candidate_panel_with_canonical_a2_lineage(**values: object) -> CandidateEvidencePanel:
    """Private construction boundary for a lineage verified by canonical A2 state."""
    return CandidateEvidencePanel(
        **values,
        _canonical_external_lineage_proof=_CANONICAL_A2_LINEAGE_PROOF,
    )


@dataclass(frozen=True, slots=True)
class RefreshRun(WireModel):
    industry_id: str
    run_id: str | None
    raw_snapshot_id: str | None
    evidence_snapshot_id: str | None
    candidate_snapshot_id: str | None
    phase: RefreshPhase
    error_code: str | None
    displayed_trusted_snapshot_id: str | None
    published_trusted_snapshot_id: str | None
    displayed_raw_snapshot_id: str | None = None
    displayed_evidence_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.phase, RefreshPhase, "phase")
        for name, value in (
            ("raw_snapshot_id", self.raw_snapshot_id),
            ("evidence_snapshot_id", self.evidence_snapshot_id),
            ("candidate_snapshot_id", self.candidate_snapshot_id),
            ("displayed_trusted_snapshot_id", self.displayed_trusted_snapshot_id),
            ("published_trusted_snapshot_id", self.published_trusted_snapshot_id),
            ("displayed_raw_snapshot_id", self.displayed_raw_snapshot_id),
            ("displayed_evidence_snapshot_id", self.displayed_evidence_snapshot_id),
        ):
            if value is not None and (
                type(value) is not str or not value.strip() or value != value.strip()
            ):
                raise ValueError(f"refresh {name} must not be blank")
        if (self.displayed_raw_snapshot_id is None) != (self.displayed_evidence_snapshot_id is None):
            raise ValueError("displayed raw/evidence lineage must be provided together")
        if self.candidate_snapshot_id is not None and (
            not self.raw_snapshot_id or not self.evidence_snapshot_id
        ):
            raise ValueError("refresh candidate snapshot requires raw/evidence lineage")
        if self.displayed_trusted_snapshot_id is None:
            if self.displayed_raw_snapshot_id is not None:
                raise ValueError("displayed lineage requires displayed_trusted_snapshot_id")
        elif not self.displayed_raw_snapshot_id or not self.displayed_evidence_snapshot_id:
            raise ValueError("displayed trusted snapshot requires displayed raw/evidence lineage")
        if self.phase is RefreshPhase.TRUSTED_PUBLISHED:
            if not self.published_trusted_snapshot_id:
                raise ValueError("trusted_published requires published_trusted_snapshot_id")
            if self.displayed_trusted_snapshot_id != self.published_trusted_snapshot_id:
                raise ValueError("published snapshot must be the displayed trusted snapshot")
            if not self.raw_snapshot_id or not self.evidence_snapshot_id:
                raise ValueError("trusted_published requires raw/evidence lineage")
            if (
                self.displayed_raw_snapshot_id != self.raw_snapshot_id
                or self.displayed_evidence_snapshot_id != self.evidence_snapshot_id
            ):
                raise ValueError("published displayed lineage must match current raw/evidence lineage")
        elif self.published_trusted_snapshot_id is not None:
            raise ValueError("published_trusted_snapshot_id must be null until trusted_published")
        if self.phase is RefreshPhase.FAILED and not self.error_code:
            raise ValueError("failed refresh requires error_code")


@dataclass(frozen=True, slots=True)
class IndustryReportResponse(WireModel):
    requested_industry_id: str
    displayed_industry_id: str | None
    displayed_trusted_report: DisplayedTrustedReport | None
    candidate_evidence: CandidateEvidencePanel | None
    refresh_run: RefreshRun

    def __post_init__(self) -> None:
        if not self.requested_industry_id.strip():
            raise ValueError("requested_industry_id must not be blank")
        if not isinstance(self.refresh_run, RefreshRun):
            raise TypeError("refresh_run must be RefreshRun")
        if self.refresh_run.industry_id != self.requested_industry_id:
            raise ValueError("refresh industry_id must match requested_industry_id")
        if self.refresh_run.candidate_snapshot_id is None:
            if self.candidate_evidence is not None:
                raise ValueError("candidate panel must be absent without a refresh candidate snapshot")
        else:
            if self.candidate_evidence is None:
                raise ValueError("candidate panel is required for the refresh candidate snapshot")
            if not isinstance(self.candidate_evidence, CandidateEvidencePanel):
                raise TypeError("candidate_evidence must be CandidateEvidencePanel")
            if self.candidate_evidence.industry_id != self.requested_industry_id:
                raise ValueError("candidate industry_id must match requested_industry_id")
            if (
                self.candidate_evidence.candidate_snapshot_id,
                self.candidate_evidence.raw_snapshot_id,
                self.candidate_evidence.evidence_snapshot_id,
            ) != (
                self.refresh_run.candidate_snapshot_id,
                self.refresh_run.raw_snapshot_id,
                self.refresh_run.evidence_snapshot_id,
            ):
                raise ValueError("candidate panel does not match refresh lineage")
        if self.refresh_run.displayed_trusted_snapshot_id is None:
            if self.displayed_industry_id is not None or self.displayed_trusted_report is not None:
                raise ValueError("displayed report must be absent without refresh displayed lineage")
            return
        if self.displayed_industry_id is None or self.displayed_trusted_report is None:
            raise ValueError("displayed report is required for refresh displayed lineage")
        if self.displayed_industry_id != self.requested_industry_id:
            raise ValueError("displayed_industry_id must match requested_industry_id")
        if not isinstance(self.displayed_trusted_report, DisplayedTrustedReport):
            raise TypeError("displayed_trusted_report must be DisplayedTrustedReport")
        if self.displayed_trusted_report.industry_id != self.displayed_industry_id:
            raise ValueError("displayed report industry_id mismatch")
        if (
            self.displayed_trusted_report.trusted_snapshot_id,
            self.displayed_trusted_report.displayed_trusted_snapshot_id,
            self.displayed_trusted_report.raw_snapshot_id,
            self.displayed_trusted_report.evidence_snapshot_id,
        ) != (
            self.refresh_run.displayed_trusted_snapshot_id,
            self.refresh_run.displayed_trusted_snapshot_id,
            self.refresh_run.displayed_raw_snapshot_id,
            self.refresh_run.displayed_evidence_snapshot_id,
        ):
            raise ValueError("displayed report does not match refresh lineage")
