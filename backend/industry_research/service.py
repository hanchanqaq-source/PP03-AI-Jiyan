from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping

from evidence_verification.models import (
    EvidenceEvent as A2EvidenceEvent,
    EvidenceSnapshot,
    VerificationStatus as A2VerificationStatus,
)
from evidence_verification.storage import EvidenceStorage

from .models import (
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    CandidateExternalLineage,
    CandidateIndustryEvidenceEvent,
    ConflictingObservation,
    ConclusionStatus,
    DisplayedTrustedReport,
    EmptyReason,
    AvailabilityStatus,
    FreshnessStatus,
    IndustryChainNode,
    IndustryEvidenceEvent,
    IndustryMetricObservation,
    ReportCounts,
    SourceCoverage,
    SourceRunStatus,
    VerificationStatus,
    WireModel,
    _candidate_panel_with_canonical_a2_lineage,
    _conflicting_truth_key,
)
from .rules import (
    evaluate_storage_conclusion,
    observation_is_current,
    select_current_trusted_observations,
)
from .relationships import CompanyEvidenceBinding, project_company_relations
from .templates import REPORT_SECTION_IDS, get_industry_template


_NEWS_WINDOWS = (7, 30, 90)
_METRIC_LABELS = {
    "dram_price": "DRAM 价格",
    "nand_price": "NAND 价格",
    "hbm_demand": "HBM 需求",
    "inventory_level": "库存水平",
    "capacity_utilization": "产能利用率",
    "manufacturer_capex": "厂商资本开支",
    "server_demand": "服务器需求",
    "consumer_electronics_demand": "消费电子需求",
    "sector_fund_flow": "板块资金",
    "etf_share": "ETF 份额",
    "industry_valuation": "估值水平",
    "historical_valuation_percentile": "历史分位",
}


@dataclass(frozen=True, slots=True)
class NewsWindowProjection(WireModel):
    days: int
    trusted: tuple[IndustryEvidenceEvent, ...]
    unverified: tuple[CandidateIndustryEvidenceEvent, ...]
    conflicting: tuple[CandidateIndustryEvidenceEvent, ...]


@dataclass(frozen=True, slots=True)
class ReportSectionState(WireModel):
    section_id: str
    current_value: int | None
    empty_reason: EmptyReason | None
    missing_metric_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.section_id not in REPORT_SECTION_IDS:
            raise ValueError("unknown report section")
        if (self.current_value is None) is not (self.empty_reason is not None):
            raise ValueError("section requires either a value or an empty_reason")


@dataclass(frozen=True, slots=True)
class IndustryReportAssembly(WireModel):
    report: DisplayedTrustedReport
    candidate_evidence: CandidateEvidencePanel
    news_windows: tuple[NewsWindowProjection, ...]
    section_states: tuple[ReportSectionState, ...]


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _event_evidence(event: A2EvidenceEvent):
    return (
        event.primary_evidence
        + event.independent_evidence
        + event.syndicated_copies
        + event.contradicting_evidence
    )


def _company_evidence_bindings(snapshot: EvidenceSnapshot) -> Mapping[str, CompanyEvidenceBinding]:
    admitted: dict[str, CompanyEvidenceBinding] = {}
    rejected_ids: set[str] = set()
    for event in snapshot.events:
        if event.verification_status not in {
            A2VerificationStatus.VERIFIED,
            A2VerificationStatus.CORROBORATED,
        }:
            continue
        for evidence in _event_evidence(event):
            if (
                not evidence.is_official
                or not evidence.supports_claim
                or evidence.contradicts_claim
            ):
                continue
            as_of = evidence.published_at or event.evidence_as_of
            binding = CompanyEvidenceBinding(
                evidence_id=evidence.evidence_id,
                supports_fields=frozenset(evidence.supports_fields),
                as_of_date=as_of.date().isoformat(),
            )
            previous = admitted.get(binding.evidence_id)
            if previous is not None and previous != binding:
                rejected_ids.add(binding.evidence_id)
            else:
                admitted[binding.evidence_id] = binding
    for evidence_id in rejected_ids:
        admitted.pop(evidence_id, None)
    return admitted


def _event_roles(event: A2EvidenceEvent) -> tuple[str, ...]:
    category = event.category.strip().casefold()
    extra = category if category in {"catalyst", "risk", "reverse_signal"} else None
    return ("news",) if extra is None else ("news", extra)


def _trusted_event(industry_id: str, event: A2EvidenceEvent) -> IndustryEvidenceEvent:
    status = {
        A2VerificationStatus.VERIFIED: VerificationStatus.VERIFIED,
        A2VerificationStatus.CORROBORATED: VerificationStatus.CORROBORATED,
    }[event.verification_status]
    occurred = event.published_at or event.evidence_as_of
    evidence_ids = tuple(sorted({item.evidence_id for item in _event_evidence(event)}))
    return IndustryEvidenceEvent(
        industry_id=industry_id,
        event_id=event.event_id,
        status=status,
        occurred_at=occurred.isoformat(),
        evidence_ids=evidence_ids,
        roles=_event_roles(event),
    )


def _candidate_event(
    industry_id: str,
    event: A2EvidenceEvent,
    *,
    candidate_snapshot_id: str,
    raw_snapshot_id: str,
    evidence_snapshot_id: str,
) -> CandidateIndustryEvidenceEvent:
    status = {
        A2VerificationStatus.UNVERIFIED: VerificationStatus.UNVERIFIED,
        A2VerificationStatus.CONFLICTING: VerificationStatus.CONFLICTING,
    }[event.verification_status]
    occurred = event.published_at or event.evidence_as_of
    all_evidence = _event_evidence(event)
    supporting = tuple(sorted({
        item.evidence_id for item in all_evidence
        if item.supports_claim and not item.contradicts_claim
    }))
    contradicting = tuple(sorted({
        item.evidence_id
        for item in event.contradicting_evidence
        if item.contradicts_claim
    }))
    evidence_ids = tuple(sorted({item.evidence_id for item in all_evidence}))
    return CandidateIndustryEvidenceEvent(
        industry_id=industry_id,
        event_id=event.event_id,
        status=status,
        occurred_at=occurred.isoformat(),
        evidence_ids=evidence_ids,
        supporting_evidence_ids=supporting,
        contradicting_evidence_ids=contradicting,
        roles=_event_roles(event),
        candidate_snapshot_id=candidate_snapshot_id,
        raw_snapshot_id=raw_snapshot_id,
        evidence_snapshot_id=evidence_snapshot_id,
    )


def project_news_windows(
    *,
    industry_id: str,
    snapshot: EvidenceSnapshot,
    now: datetime,
) -> tuple[NewsWindowProjection, ...]:
    """Project A2 evidence without refreshing, publishing, or modifying it."""
    _aware(now, "now")
    if type(snapshot) is not EvidenceSnapshot:
        raise TypeError("snapshot must be the canonical A2 EvidenceSnapshot")
    relevant = tuple(
        event for event in snapshot.events
        if industry_id in {tag_id for tag_id, _label in event.related_tags}
        and (event.published_at or event.evidence_as_of) <= now
    )
    windows: list[NewsWindowProjection] = []
    for days in _NEWS_WINDOWS:
        cutoff = now - timedelta(days=days)
        selected = tuple(
            event for event in relevant
            if (event.published_at or event.evidence_as_of) >= cutoff
        )
        trusted = tuple(sorted(
            (_trusted_event(industry_id, event) for event in selected if event.verification_status in {
                A2VerificationStatus.VERIFIED,
                A2VerificationStatus.CORROBORATED,
            }),
            key=lambda item: (item.occurred_at, item.event_id),
            reverse=True,
        ))
        unverified = tuple(sorted(
            (_candidate_event(
                industry_id,
                event,
                candidate_snapshot_id=snapshot.snapshot_id,
                raw_snapshot_id=snapshot.raw_snapshot_id,
                evidence_snapshot_id=snapshot.snapshot_id,
            ) for event in selected
             if event.verification_status is A2VerificationStatus.UNVERIFIED),
            key=lambda item: (item.occurred_at, item.event_id),
            reverse=True,
        ))
        conflicting = tuple(sorted(
            (_candidate_event(
                industry_id,
                event,
                candidate_snapshot_id=snapshot.snapshot_id,
                raw_snapshot_id=snapshot.raw_snapshot_id,
                evidence_snapshot_id=snapshot.snapshot_id,
            ) for event in selected
             if event.verification_status is A2VerificationStatus.CONFLICTING),
            key=lambda item: (item.occurred_at, item.event_id),
            reverse=True,
        ))
        windows.append(NewsWindowProjection(days, trusted, unverified, conflicting))
    return tuple(windows)


def _empty_candidate(industry_id: str) -> CandidateEvidencePanel:
    return CandidateEvidencePanel(
        industry_id=industry_id,
        candidate_snapshot_id=None,
        counts=CandidateEvidenceCounts(0, 0, 0, 0),
        unverified=(),
        conflicting=(),
        unverified_events=(),
        conflicting_events=(),
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
    )


def _merge_candidates(
    metric_candidates: CandidateEvidencePanel,
    *,
    news_snapshot: EvidenceSnapshot | None,
    window: NewsWindowProjection,
) -> CandidateEvidencePanel:
    metric_batch_present = metric_candidates.candidate_snapshot_id is not None
    news_items_present = bool(window.unverified or window.conflicting)
    candidate_snapshot_id = metric_candidates.candidate_snapshot_id
    raw_snapshot_id = metric_candidates.raw_snapshot_id
    evidence_snapshot_id = metric_candidates.evidence_snapshot_id
    if news_items_present:
        if news_snapshot is None:
            raise ValueError("news candidates require canonical news snapshot lineage")
        news_lineage = (
            news_snapshot.snapshot_id,
            news_snapshot.raw_snapshot_id,
            news_snapshot.snapshot_id,
        )
        if not metric_batch_present:
            candidate_snapshot_id, raw_snapshot_id, evidence_snapshot_id = news_lineage
    unverified_events = metric_candidates.unverified_events + window.unverified
    conflicting_events = metric_candidates.conflicting_events + window.conflicting
    external_lineages = ()
    if news_items_present and news_lineage != (
        candidate_snapshot_id,
        raw_snapshot_id,
        evidence_snapshot_id,
    ):
        external_lineages = (CandidateExternalLineage("a2_news", *news_lineage),)
    constructor = (
        _candidate_panel_with_canonical_a2_lineage
        if external_lineages
        else CandidateEvidencePanel
    )
    return constructor(
        industry_id=metric_candidates.industry_id,
        candidate_snapshot_id=candidate_snapshot_id,
        counts=CandidateEvidenceCounts(
            unverified=len(metric_candidates.unverified),
            conflicting=len(metric_candidates.conflicting),
            unverified_events=len(unverified_events),
            conflicting_events=len(conflicting_events),
        ),
        unverified=metric_candidates.unverified,
        conflicting=metric_candidates.conflicting,
        unverified_events=unverified_events,
        conflicting_events=conflicting_events,
        raw_snapshot_id=raw_snapshot_id,
        evidence_snapshot_id=evidence_snapshot_id,
        external_lineages=external_lineages,
    )


def _section_states(
    *,
    cycle: tuple[IndustryMetricObservation, ...],
    chain: tuple[IndustryChainNode, ...],
    report: DisplayedTrustedReport,
) -> tuple[ReportSectionState, ...]:
    template = get_industry_template(report.industry_id)
    present = {row.metric_id for row in cycle if row.current_value is not None}
    missing = tuple(metric_id for metric_id in template.cycle_metric_ids if metric_id not in present)
    values: dict[str, tuple[int | None, EmptyReason | None, tuple[str, ...]]] = {
        "overview": (
            1 if report.overview.cycle_stage is not None or report.overview.outlook_direction is not None else None,
            None if report.overview.cycle_stage is not None or report.overview.outlook_direction is not None
            else EmptyReason.NO_RELIABLE_DATA,
            (),
        ),
        "cycle": (
            len(cycle) if not missing else None,
            None if not missing else EmptyReason.NO_RELIABLE_DATA,
            missing,
        ),
        "chain": (
            len(chain) if any(node.status is not ConclusionStatus.UNAVAILABLE for node in chain) else None,
            None if any(node.status is not ConclusionStatus.UNAVAILABLE for node in chain)
            else EmptyReason.NO_RELIABLE_DATA,
            (),
        ),
        "metrics": (
            sum(row.current_value is not None for row in report.metrics) or None,
            None if any(row.current_value is not None for row in report.metrics)
            else EmptyReason.NO_RELIABLE_DATA,
            (),
        ),
        "capital": (
            sum(row.current_value is not None for row in report.capital) or None,
            None if any(row.current_value is not None for row in report.capital)
            else EmptyReason.NO_RELIABLE_DATA,
            (),
        ),
        "companies": (len(report.companies) or None, None if report.companies else EmptyReason.NOT_DISCLOSED, ()),
        "funds": (len(report.funds) or None, None if report.funds else EmptyReason.NOT_DISCLOSED, ()),
        "news_risk": (len(report.news_risk) or None, None if report.news_risk else EmptyReason.NO_RELIABLE_DATA, ()),
    }
    return tuple(ReportSectionState(section_id, *values[section_id]) for section_id in REPORT_SECTION_IDS)


def _empty_axes(reason: EmptyReason) -> tuple[
    AvailabilityStatus, VerificationStatus, FreshnessStatus, SourceRunStatus
]:
    if reason in {
        EmptyReason.SOURCE_UNCONFIGURED,
        EmptyReason.USER_KEY_NOT_CONFIGURED,
        EmptyReason.LICENSE_REQUIRED,
    }:
        return (
            AvailabilityStatus.UNCONFIGURED,
            VerificationStatus.NOT_EVALUATED,
            FreshnessStatus.UNKNOWN,
            SourceRunStatus.NOT_CONFIGURED,
        )
    if reason in {EmptyReason.SOURCE_FAILED, EmptyReason.SOURCE_UNAVAILABLE}:
        return (
            AvailabilityStatus.UNAVAILABLE,
            VerificationStatus.NOT_EVALUATED,
            FreshnessStatus.UNKNOWN,
            SourceRunStatus.FAILED,
        )
    if reason is EmptyReason.VERIFYING:
        return (
            AvailabilityStatus.PARTIAL,
            VerificationStatus.UNVERIFIED,
            FreshnessStatus.UNKNOWN,
            SourceRunStatus.HEALTHY,
        )
    if reason is EmptyReason.CONFLICTING:
        return (
            AvailabilityStatus.PARTIAL,
            VerificationStatus.CONFLICTING,
            FreshnessStatus.FRESH,
            SourceRunStatus.HEALTHY,
        )
    if reason is EmptyReason.EXPIRED:
        return (
            AvailabilityStatus.UNAVAILABLE,
            VerificationStatus.NOT_EVALUATED,
            FreshnessStatus.EXPIRED,
            SourceRunStatus.HEALTHY,
        )
    return (
        AvailabilityStatus.UNAVAILABLE,
        VerificationStatus.NOT_EVALUATED,
        FreshnessStatus.UNKNOWN,
        SourceRunStatus.PARTIAL_FAILURE,
    )


def _placeholder(
    *,
    industry_id: str,
    metric_id: str,
    reason: EmptyReason,
    expires_at: str | None = None,
) -> IndustryMetricObservation:
    availability, verification, freshness, run_status = _empty_axes(reason)
    return IndustryMetricObservation(
        industry_id=industry_id,
        metric_id=metric_id,
        label=_METRIC_LABELS.get(metric_id, metric_id),
        current_value=None,
        unit=None,
        change=None,
        historical_position=None,
        availability_status=availability,
        verification_status=verification,
        freshness_status=freshness,
        source_run_status=run_status,
        empty_reason=reason,
        as_of_date=None,
        fetched_at=None,
        methodology="",
        judgment_basis=(),
        invalidating_conditions=(),
        evidence=(),
        independent_source_families=(),
        independent_content_sources=(),
        independent_origin_clusters=(),
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
        expires_at=expires_at,
    )


def _complete_metric_rows(
    *,
    industry_id: str,
    metric_ids: tuple[str, ...],
    current: Mapping[str, IndustryMetricObservation],
    candidates: CandidateEvidencePanel,
    expired: Mapping[str, IndustryMetricObservation],
) -> tuple[IndustryMetricObservation, ...]:
    unverified = {row.metric_id for row in candidates.unverified}
    conflicting = {row.metric_id for row in candidates.conflicting}
    rows: list[IndustryMetricObservation] = []
    for metric_id in metric_ids:
        if metric_id in current:
            rows.append(current[metric_id])
            continue
        if metric_id in conflicting:
            reason = EmptyReason.CONFLICTING
        elif metric_id in unverified:
            reason = EmptyReason.VERIFYING
        elif metric_id in expired:
            reason = EmptyReason.EXPIRED
        else:
            reason = EmptyReason.NO_RELIABLE_DATA
        rows.append(_placeholder(
            industry_id=industry_id,
            metric_id=metric_id,
            reason=reason,
            expires_at=expired[metric_id].expires_at if metric_id in expired else None,
        ))
    return tuple(rows)


def _has_structured_expiry_at_or_before(
    observation: IndustryMetricObservation,
    *,
    now: datetime,
) -> bool:
    if observation.expires_at is None:
        return False
    expiry = datetime.fromisoformat(observation.expires_at.replace("Z", "+00:00"))
    return expiry <= now


def _validate_canonical_contradictions(
    candidates: CandidateEvidencePanel,
    evidence_storage: EvidenceStorage | None,
) -> None:
    equal_truth = tuple(
        conflict
        for conflict in candidates.conflicting
        if len({_conflicting_truth_key(item) for item in conflict.source_values}) < 2
    )
    if not equal_truth:
        return
    if type(evidence_storage) is not EvidenceStorage:
        raise ValueError("equal-value conflict requires canonical A2 contradiction proof")
    snapshot = evidence_storage.load_current()
    if (
        snapshot is None
        or snapshot.snapshot_id != candidates.evidence_snapshot_id
        or snapshot.raw_snapshot_id != candidates.raw_snapshot_id
    ):
        raise ValueError("equal-value conflict requires canonical A2 contradiction proof")
    for conflict in equal_truth:
        source_ids = {item.evidence_id for item in conflict.source_values}
        proven = False
        for event in snapshot.events:
            if event.verification_status is not A2VerificationStatus.CONFLICTING:
                continue
            evidence = {item.evidence_id: item for item in _event_evidence(event)}
            if not source_ids.issubset(evidence):
                continue
            supporting = {
                evidence_id
                for evidence_id in source_ids
                if evidence[evidence_id].supports_claim
                and conflict.metric_id in evidence[evidence_id].supports_fields
                and not evidence[evidence_id].contradicts_claim
            }
            contradictory_ids = {
                item.evidence_id for item in event.contradicting_evidence
                if item.contradicts_claim
            }
            contradictory = source_ids & contradictory_ids
            if supporting and contradictory and supporting.isdisjoint(contradictory):
                proven = True
                break
        if not proven:
            raise ValueError("equal-value conflict requires canonical A2 contradiction proof")


def assemble_storage_report(
    *,
    trusted_snapshot_id: str,
    raw_snapshot_id: str,
    evidence_snapshot_id: str,
    generated_at: datetime,
    trusted_observations: Iterable[IndustryMetricObservation],
    expired_observations: Iterable[IndustryMetricObservation] = (),
    metric_candidates: CandidateEvidencePanel | None,
    candidate_evidence_storage: EvidenceStorage | None = None,
    news_snapshot: EvidenceSnapshot | None,
    now: datetime,
    company_candidates: Iterable[Mapping[str, object]] = (),
    company_evidence_snapshot: EvidenceSnapshot | None = None,
    source_coverage: SourceCoverage | None = None,
    demo: bool = False,
) -> IndustryReportAssembly:
    """Assemble fixed sections without refreshing, mutating, or writing evidence."""
    industry_id = "storage"
    _aware(generated_at, "generated_at")
    _aware(now, "now")
    if any(
        not isinstance(value, str) or not value.strip() or value != value.strip()
        for value in (raw_snapshot_id, evidence_snapshot_id)
    ):
        raise ValueError("report raw/evidence lineage must not be blank")
    template = get_industry_template(industry_id)
    trusted_rows = tuple(trusted_observations)
    if any(row.raw_snapshot_id != raw_snapshot_id for row in trusted_rows):
        raise ValueError("trusted raw lineage mismatch")
    if any(row.evidence_snapshot_id != evidence_snapshot_id for row in trusted_rows):
        raise ValueError("trusted evidence lineage mismatch")
    current = select_current_trusted_observations(
        industry_id=industry_id,
        observations=trusted_rows,
        now=now,
    )
    conclusion = evaluate_storage_conclusion(
        industry_id=industry_id,
        trusted_snapshot_id=trusted_snapshot_id,
        observations=current,
        now=now,
    )
    candidates = metric_candidates or _empty_candidate(industry_id)
    if type(candidates) is not CandidateEvidencePanel or candidates.industry_id != industry_id:
        raise ValueError("metric candidate panel industry_id mismatch")
    if metric_candidates is not None:
        if candidates.raw_snapshot_id != raw_snapshot_id:
            raise ValueError("candidate raw lineage mismatch")
        if candidates.evidence_snapshot_id != evidence_snapshot_id:
            raise ValueError("candidate evidence lineage mismatch")
        _validate_canonical_contradictions(candidates, candidate_evidence_storage)
    expired_rows = tuple(expired_observations)
    if any(type(row) is not IndustryMetricObservation or row.industry_id != industry_id for row in expired_rows):
        raise ValueError("expired observations must match the requested industry")
    if any(row.raw_snapshot_id != raw_snapshot_id for row in expired_rows):
        raise ValueError("expired raw lineage mismatch")
    if any(row.evidence_snapshot_id != evidence_snapshot_id for row in expired_rows):
        raise ValueError("expired evidence lineage mismatch")
    for row in expired_rows:
        if (
            not _has_structured_expiry_at_or_before(row, now=now)
            and row.freshness_status is FreshnessStatus.EXPIRED
        ):
            raise ValueError("expired observation requires structured expires_at at or before report time")
    if any(observation_is_current(row, now=now) for row in expired_rows):
        raise ValueError("expired observation is not expired at report time")
    auto_expired = tuple(
        row for row in trusted_rows if not observation_is_current(row, now=now)
    )
    if any(not _has_structured_expiry_at_or_before(row, now=now) for row in auto_expired):
        raise ValueError("expired observation requires structured expires_at at or before report time")
    expired_by_id = {row.metric_id: row for row in expired_rows}
    expired_by_id.update({
        row.metric_id: row
        for row in auto_expired
    })
    windows = (
        project_news_windows(industry_id=industry_id, snapshot=news_snapshot, now=now)
        if news_snapshot is not None
        else tuple(NewsWindowProjection(days, (), (), ()) for days in _NEWS_WINDOWS)
    )
    news_window = windows[-1]
    candidate_evidence = _merge_candidates(
        candidates,
        news_snapshot=news_snapshot,
        window=news_window,
    )
    chain = tuple(
        IndustryChainNode(
            industry_id=industry_id,
            node_id=node_id,
            label=node_id,
            observation_ids=(),
            evidence_ids=(),
            status=ConclusionStatus.UNAVAILABLE,
        )
        for node_id in template.chain_node_ids
    )
    current_by_id = {row.metric_id: row for row in current}
    cycle_rows = _complete_metric_rows(
        industry_id=industry_id,
        metric_ids=template.cycle_metric_ids,
        current=current_by_id,
        candidates=candidates,
        expired=expired_by_id,
    )
    coverage = source_coverage or SourceCoverage(
        unit="capability",
        total=len(cycle_rows),
        configured=sum(
            row.source_run_status is not SourceRunStatus.NOT_CONFIGURED
            for row in cycle_rows
        ),
        healthy=sum(
            row.source_run_status is SourceRunStatus.HEALTHY for row in cycle_rows
        ),
        partial_failure=sum(
            row.source_run_status is SourceRunStatus.PARTIAL_FAILURE
            for row in cycle_rows
        ),
        failed=sum(
            row.source_run_status is SourceRunStatus.FAILED for row in cycle_rows
        ),
        unconfigured=sum(
            row.source_run_status is SourceRunStatus.NOT_CONFIGURED
            for row in cycle_rows
        ),
    )
    metric_rows = _complete_metric_rows(
        industry_id=industry_id,
        metric_ids=template.core_metric_ids,
        current=current_by_id,
        candidates=candidates,
        expired=expired_by_id,
    )
    capital_rows = _complete_metric_rows(
        industry_id=industry_id,
        metric_ids=template.capital_metric_ids,
        current=current_by_id,
        candidates=candidates,
        expired=expired_by_id,
    )
    company_rows = tuple(company_candidates)
    if company_evidence_snapshot is None:
        companies = ()
    else:
        if (
            type(company_evidence_snapshot) is not EvidenceSnapshot
            or company_evidence_snapshot.raw_snapshot_id != raw_snapshot_id
            or company_evidence_snapshot.snapshot_id != evidence_snapshot_id
        ):
            raise ValueError("company evidence snapshot lineage mismatch")
        companies = project_company_relations(
            industry_id=industry_id,
            candidates=company_rows,
            allowed_chain_node_ids=template.chain_node_ids,
            allowed_metric_ids=(
                template.cycle_metric_ids
                + template.core_metric_ids
                + template.capital_metric_ids
            ),
            evidence_bindings=_company_evidence_bindings(company_evidence_snapshot),
        )
    report = DisplayedTrustedReport(
        industry_id=industry_id,
        template_status=template.status,
        trusted_snapshot_id=trusted_snapshot_id,
        displayed_trusted_snapshot_id=trusted_snapshot_id,
        raw_snapshot_id=raw_snapshot_id,
        evidence_snapshot_id=evidence_snapshot_id,
        generated_at=generated_at.isoformat(),
        demo=demo,
        source_coverage=coverage,
        counts=ReportCounts(
            verified=sum(row.verification_status is VerificationStatus.VERIFIED for row in current),
            corroborated=sum(row.verification_status is VerificationStatus.CORROBORATED for row in current),
        ),
        overview=conclusion,
        cycle=cycle_rows,
        chain=chain,
        metrics=metric_rows,
        capital=capital_rows,
        companies=companies,
        fund_selection=(),
        funds=(),
        news_risk=news_window.trusted,
    )
    return IndustryReportAssembly(
        report=report,
        candidate_evidence=candidate_evidence,
        news_windows=windows,
        section_states=_section_states(cycle=cycle_rows, chain=chain, report=report),
    )


class IndustryResearchService:
    """Production boundary for deterministic assembly; it performs no refresh or I/O."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        production: bool = True,
    ) -> None:
        self._now = now
        self.production = production

    def _clock(self) -> datetime:
        value = self._now()
        _aware(value, "service clock")
        return value

    def project_news_windows(
        self,
        *,
        industry_id: str,
        snapshot: EvidenceSnapshot,
    ) -> tuple[NewsWindowProjection, ...]:
        return project_news_windows(
            industry_id=industry_id,
            snapshot=snapshot,
            now=self._clock(),
        )

    def assemble_storage_report(
        self,
        *,
        trusted_snapshot_id: str,
        raw_snapshot_id: str,
        evidence_snapshot_id: str,
        generated_at: datetime,
        trusted_observations: Iterable[IndustryMetricObservation],
        expired_observations: Iterable[IndustryMetricObservation] = (),
        metric_candidates: CandidateEvidencePanel | None,
        candidate_evidence_storage: EvidenceStorage | None = None,
        news_snapshot: EvidenceSnapshot | None,
        company_candidates: Iterable[Mapping[str, object]] = (),
        company_evidence_snapshot: EvidenceSnapshot | None = None,
        source_coverage: SourceCoverage | None = None,
        demo: bool = False,
    ) -> IndustryReportAssembly:
        assembly = assemble_storage_report(
            trusted_snapshot_id=trusted_snapshot_id,
            raw_snapshot_id=raw_snapshot_id,
            evidence_snapshot_id=evidence_snapshot_id,
            generated_at=generated_at,
            trusted_observations=trusted_observations,
            expired_observations=expired_observations,
            metric_candidates=metric_candidates,
            candidate_evidence_storage=candidate_evidence_storage,
            news_snapshot=news_snapshot,
            company_candidates=company_candidates,
            company_evidence_snapshot=company_evidence_snapshot,
            now=self._clock(),
            source_coverage=source_coverage,
            demo=demo,
        )
        assembly.report.validate_for_mode(production=self.production)
        return assembly
