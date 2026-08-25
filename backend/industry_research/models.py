from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
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
        return {field.name: _wire_value(getattr(value, field.name)) for field in fields(value)}
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
        if self.basis not in {"mom", "yoy", "wow"}:
            raise ValueError("change.basis must be mom, yoy, or wow")


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

    def __post_init__(self) -> None:
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
    empty_reason: EmptyReason | None

    def __post_init__(self) -> None:
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

    def __post_init__(self) -> None:
        if self.status not in {VerificationStatus.UNVERIFIED, VerificationStatus.CONFLICTING}:
            raise ValueError("candidate event requires unverified or conflicting status")
        if self.status is VerificationStatus.CONFLICTING:
            if not self.supporting_evidence_ids or not self.contradicting_evidence_ids:
                raise ValueError("conflicting event requires supporting and contradicting evidence_ids")


@dataclass(frozen=True, slots=True)
class ConflictingSourceValue(WireModel):
    evidence_id: str
    source_family_id: str
    value: str | int | float
    unit: str | None
    as_of_date: str | None


@dataclass(frozen=True, slots=True)
class ConflictingObservation(WireModel):
    industry_id: str
    metric_id: str
    aggregate_value: None
    source_values: tuple[ConflictingSourceValue, ...]

    def __post_init__(self) -> None:
        if self.aggregate_value is not None:
            raise ValueError("conflicting observation aggregate_value must be null")
        if len(self.source_values) < 2:
            raise ValueError("conflicting observation requires per-source values")
        evidence_ids = [item.evidence_id for item in self.source_values]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("conflicting observation source evidence must be unique")


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


@dataclass(frozen=True, slots=True)
class DisplayedTrustedReport(WireModel):
    industry_id: str
    template_status: TemplateStatus
    trusted_snapshot_id: str | None
    displayed_trusted_snapshot_id: str | None
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
        observations = self.cycle + self.metrics + self.capital
        for observation in observations:
            if not isinstance(observation, IndustryMetricObservation):
                raise TypeError("trusted_observations must contain IndustryMetricObservation")
            if observation.industry_id != self.industry_id:
                raise ValueError("trusted observation industry_id mismatch")
            if observation.verification_status not in {
                VerificationStatus.VERIFIED, VerificationStatus.CORROBORATED,
            } or observation.freshness_status is FreshnessStatus.EXPIRED:
                raise ValueError("trusted_observations accept only current verified/corroborated values")
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
class CandidateEvidencePanel(WireModel):
    industry_id: str
    candidate_snapshot_id: str | None
    counts: CandidateEvidenceCounts
    unverified: tuple[IndustryMetricObservation, ...]
    conflicting: tuple[ConflictingObservation, ...]
    unverified_events: tuple[CandidateIndustryEvidenceEvent, ...]
    conflicting_events: tuple[CandidateIndustryEvidenceEvent, ...]

    def __post_init__(self) -> None:
        if any(item.verification_status is not VerificationStatus.UNVERIFIED for item in self.unverified):
            raise ValueError("candidate unverified array accepts only unverified observations")
        if any(item.status is not VerificationStatus.UNVERIFIED for item in self.unverified_events):
            raise ValueError("unverified_events accepts only unverified events")
        if any(item.status is not VerificationStatus.CONFLICTING for item in self.conflicting_events):
            raise ValueError("conflicting_events accepts only conflicting events")
        actual = (len(self.unverified), len(self.conflicting), len(self.unverified_events), len(self.conflicting_events))
        expected = (self.counts.unverified, self.counts.conflicting, self.counts.unverified_events, self.counts.conflicting_events)
        if actual != expected:
            raise ValueError("candidate evidence counts do not match arrays")


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

    def __post_init__(self) -> None:
        if self.phase is RefreshPhase.TRUSTED_PUBLISHED:
            if not self.published_trusted_snapshot_id:
                raise ValueError("trusted_published requires published_trusted_snapshot_id")
            if self.displayed_trusted_snapshot_id != self.published_trusted_snapshot_id:
                raise ValueError("published snapshot must be the displayed trusted snapshot")
            if not self.raw_snapshot_id or not self.evidence_snapshot_id:
                raise ValueError("trusted_published requires raw/evidence lineage")
        elif self.published_trusted_snapshot_id is not None:
            raise ValueError("published_trusted_snapshot_id must be null until trusted_published")
        if self.phase is RefreshPhase.FAILED and not self.error_code:
            raise ValueError("failed refresh requires error_code")
