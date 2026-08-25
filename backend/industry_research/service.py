from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping

from evidence_verification.models import (
    EvidenceEvent as A2EvidenceEvent,
    EvidenceSnapshot,
    VerificationStatus as A2VerificationStatus,
)

from .models import (
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    CandidateIndustryEvidenceEvent,
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
)
from .rules import (
    evaluate_storage_conclusion,
    observation_is_current,
    select_current_trusted_observations,
)
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


def _candidate_event(industry_id: str, event: A2EvidenceEvent) -> CandidateIndustryEvidenceEvent:
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
            (_candidate_event(industry_id, event) for event in selected
             if event.verification_status is A2VerificationStatus.UNVERIFIED),
            key=lambda item: (item.occurred_at, item.event_id),
            reverse=True,
        ))
        conflicting = tuple(sorted(
            (_candidate_event(industry_id, event) for event in selected
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
    )


def _merge_candidates(
    metric_candidates: CandidateEvidencePanel,
    *,
    news_snapshot_id: str | None,
    window: NewsWindowProjection,
) -> CandidateEvidencePanel:
    candidate_snapshot_id = metric_candidates.candidate_snapshot_id
    if candidate_snapshot_id is None and (window.unverified or window.conflicting):
        candidate_snapshot_id = news_snapshot_id
    return CandidateEvidencePanel(
        industry_id=metric_candidates.industry_id,
        candidate_snapshot_id=candidate_snapshot_id,
        counts=CandidateEvidenceCounts(
            unverified=len(metric_candidates.unverified),
            conflicting=len(metric_candidates.conflicting),
            unverified_events=len(window.unverified),
            conflicting_events=len(window.conflicting),
        ),
        unverified=metric_candidates.unverified,
        conflicting=metric_candidates.conflicting,
        unverified_events=window.unverified,
        conflicting_events=window.conflicting,
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
    empty_reasons: Mapping[str, EmptyReason],
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
            reason = empty_reasons.get(metric_id, EmptyReason.NO_RELIABLE_DATA)
        rows.append(_placeholder(
            industry_id=industry_id,
            metric_id=metric_id,
            reason=reason,
            expires_at=expired[metric_id].expires_at if metric_id in expired else None,
        ))
    return tuple(rows)


def assemble_storage_report(
    *,
    trusted_snapshot_id: str,
    generated_at: datetime,
    trusted_observations: Iterable[IndustryMetricObservation],
    expired_observations: Iterable[IndustryMetricObservation] = (),
    metric_candidates: CandidateEvidencePanel | None,
    metric_empty_reasons: Mapping[str, EmptyReason] | None = None,
    news_snapshot: EvidenceSnapshot | None,
    now: datetime,
    source_coverage: SourceCoverage | None = None,
    demo: bool = False,
) -> IndustryReportAssembly:
    """Assemble all fixed sections from one trusted storage snapshot, without I/O."""
    industry_id = "storage"
    _aware(generated_at, "generated_at")
    _aware(now, "now")
    template = get_industry_template(industry_id)
    trusted_rows = tuple(trusted_observations)
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
    expired_rows = tuple(expired_observations)
    if any(type(row) is not IndustryMetricObservation or row.industry_id != industry_id for row in expired_rows):
        raise ValueError("expired observations must match the requested industry")
    expired_by_id = {row.metric_id: row for row in expired_rows}
    expired_by_id.update({
        row.metric_id: row
        for row in trusted_rows
        if not observation_is_current(row, now=now)
    })
    reasons = dict(metric_empty_reasons or {})
    allowed_report_metrics = set(
        template.cycle_metric_ids + template.core_metric_ids + template.capital_metric_ids
    )
    if any(metric_id not in allowed_report_metrics for metric_id in reasons):
        raise ValueError("empty reason metric_id is not in the report template")
    if any(type(reason) is not EmptyReason for reason in reasons.values()):
        raise TypeError("metric empty reasons must use EmptyReason")
    windows = (
        project_news_windows(industry_id=industry_id, snapshot=news_snapshot, now=now)
        if news_snapshot is not None
        else tuple(NewsWindowProjection(days, (), (), ()) for days in _NEWS_WINDOWS)
    )
    news_window = windows[-1]
    candidate_evidence = _merge_candidates(
        candidates,
        news_snapshot_id=news_snapshot.snapshot_id if news_snapshot is not None else None,
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
        empty_reasons=reasons,
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
        empty_reasons=reasons,
    )
    capital_rows = _complete_metric_rows(
        industry_id=industry_id,
        metric_ids=template.capital_metric_ids,
        current=current_by_id,
        candidates=candidates,
        expired=expired_by_id,
        empty_reasons=reasons,
    )
    report = DisplayedTrustedReport(
        industry_id=industry_id,
        template_status=template.status,
        trusted_snapshot_id=trusted_snapshot_id,
        displayed_trusted_snapshot_id=trusted_snapshot_id,
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
        companies=(),
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
        generated_at: datetime,
        trusted_observations: Iterable[IndustryMetricObservation],
        expired_observations: Iterable[IndustryMetricObservation] = (),
        metric_candidates: CandidateEvidencePanel | None,
        metric_empty_reasons: Mapping[str, EmptyReason] | None = None,
        news_snapshot: EvidenceSnapshot | None,
        source_coverage: SourceCoverage | None = None,
        demo: bool = False,
    ) -> IndustryReportAssembly:
        assembly = assemble_storage_report(
            trusted_snapshot_id=trusted_snapshot_id,
            generated_at=generated_at,
            trusted_observations=trusted_observations,
            expired_observations=expired_observations,
            metric_candidates=metric_candidates,
            metric_empty_reasons=metric_empty_reasons,
            news_snapshot=news_snapshot,
            now=self._clock(),
            source_coverage=source_coverage,
            demo=demo,
        )
        assembly.report.validate_for_mode(production=self.production)
        return assembly
