from __future__ import annotations

from datetime import datetime, timedelta, timezone

from evidence_verification.models import (
    EvidenceEvent,
    EvidenceItem,
    EvidenceSnapshot,
    SourceRole,
    VerificationStatus as A2VerificationStatus,
)
from industry_research.models import (
    AvailabilityStatus,
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    EmptyReason,
    EvidenceReference,
    FreshnessStatus,
    IndustryMetricObservation,
    MetricChange,
    SourceRunStatus,
    VerificationStatus,
)
from industry_research.service import (
    IndustryResearchService,
    assemble_storage_report,
    project_news_windows,
)
from industry_research.templates import REPORT_SECTION_IDS


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def trusted_observation(metric_id: str, change: float = 1.0) -> IndustryMetricObservation:
    evidence = EvidenceReference(
        evidence_id=f"metric-{metric_id}",
        source_family_id=f"family-{metric_id}",
        content_source=f"source-{metric_id}",
        origin_cluster=f"origin-{metric_id}",
        collector_source=f"collector-{metric_id}",
        final_url=f"https://example.com/{metric_id}",
        is_official=True,
        is_official_attested=True,
        supports_claim=True,
        supports_fields=(metric_id,),
        contradicts_claim=False,
        as_of_date="2026-08-24",
        verified_at="2026-08-25T07:00:00+00:00",
    )
    return IndustryMetricObservation(
        industry_id="storage",
        metric_id=metric_id,
        label=metric_id,
        current_value=100.0,
        unit="index",
        change=MetricChange(change, "wow"),
        historical_position=None,
        availability_status=AvailabilityStatus.AVAILABLE,
        verification_status=VerificationStatus.VERIFIED,
        freshness_status=FreshnessStatus.FRESH,
        source_run_status=SourceRunStatus.HEALTHY,
        empty_reason=None,
        as_of_date="2026-08-24",
        fetched_at="2026-08-25T07:00:00+00:00",
        methodology="Weekly disclosed-series comparison.",
        judgment_basis=(f"{metric_id} weekly change",),
        invalidating_conditions=("expires_at=2026-09-01T00:00:00+00:00",),
        evidence=(evidence,),
        independent_source_families=(),
        independent_content_sources=(),
        independent_origin_clusters=(),
        raw_snapshot_id="raw-storage-1",
        evidence_snapshot_id="evidence-storage-1",
    )


def evidence_item(event_id: str, suffix: str, *, contradicts: bool = False) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"{event_id}-{suffix}",
        content_source=f"publisher-{suffix}",
        collector_source=f"collector-{suffix}",
        canonical_url=f"https://example.com/{event_id}/{suffix}",
        published_at=NOW,
        source_role=SourceRole.PRIMARY if suffix == "a" else SourceRole.INDEPENDENT,
        origin_cluster=f"origin-{suffix}",
        supports_claim=not contradicts,
        supports_fields=("core_claim",) if not contradicts else (),
        contradicts_claim=contradicts,
        is_official=suffix == "a",
    )


def event(event_id: str, status: A2VerificationStatus, days_ago: int) -> EvidenceEvent:
    primary = evidence_item(event_id, "a")
    independent = (evidence_item(event_id, "b"),) if status is A2VerificationStatus.CORROBORATED else ()
    contradicting = (
        (evidence_item(event_id, "c", contradicts=True),)
        if status is A2VerificationStatus.CONFLICTING else ()
    )
    occurred = NOW - timedelta(days=days_ago)
    return EvidenceEvent(
        event_id=event_id,
        title=event_id,
        summary=event_id,
        category="industry",
        related_tags=(("storage", "存储"),),
        published_at=occurred,
        core_claim=event_id,
        verification_status=status,
        verification_reason="fixture",
        verified_at=NOW,
        evidence_as_of=occurred,
        primary_evidence=(primary,),
        independent_evidence=independent,
        contradicting_evidence=contradicting,
    )


def news_snapshot() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id="news-evidence-1",
        raw_snapshot_id="news-raw-1",
        generated_at=NOW,
        events=(
            event("trusted-3d", A2VerificationStatus.VERIFIED, 3),
            event("corroborated-20d", A2VerificationStatus.CORROBORATED, 20),
            event("pending-2d", A2VerificationStatus.UNVERIFIED, 2),
            event("conflict-40d", A2VerificationStatus.CONFLICTING, 40),
            event("outside-100d", A2VerificationStatus.VERIFIED, 100),
        ),
    )


def empty_candidate() -> CandidateEvidencePanel:
    return CandidateEvidencePanel(
        industry_id="storage",
        candidate_snapshot_id="candidate-storage-1",
        counts=CandidateEvidenceCounts(0, 0, 0, 0),
        unverified=(),
        conflicting=(),
        unverified_events=(),
        conflicting_events=(),
    )


def test_news_projection_is_read_only_stable_and_uses_real_7_30_90_day_cutoffs() -> None:
    snapshot = news_snapshot()

    first = project_news_windows(industry_id="storage", snapshot=snapshot, now=NOW)
    second = project_news_windows(industry_id="storage", snapshot=snapshot, now=NOW)

    assert first == second
    assert snapshot == news_snapshot()
    assert tuple(window.days for window in first) == (7, 30, 90)
    assert tuple(item.event_id for item in first[0].trusted) == ("trusted-3d",)
    assert tuple(item.event_id for item in first[1].trusted) == (
        "trusted-3d",
        "corroborated-20d",
    )
    assert tuple(item.event_id for item in first[2].conflicting) == ("conflict-40d",)
    assert all("outside-100d" not in repr(window) for window in first)


def test_pending_and_conflicting_news_are_candidate_only_and_cannot_change_conclusion() -> None:
    rows = (trusted_observation("dram_price"), trusted_observation("nand_price"))
    with_candidates = assemble_storage_report(
        trusted_snapshot_id="trusted-storage-1",
        generated_at=NOW,
        trusted_observations=rows,
        metric_candidates=empty_candidate(),
        news_snapshot=news_snapshot(),
        now=NOW,
    )
    trusted_only_snapshot = EvidenceSnapshot(
        snapshot_id="news-evidence-2",
        raw_snapshot_id="news-raw-2",
        generated_at=NOW,
        events=(event("trusted-3d", A2VerificationStatus.VERIFIED, 3),),
    )
    without_candidates = assemble_storage_report(
        trusted_snapshot_id="trusted-storage-1",
        generated_at=NOW,
        trusted_observations=rows,
        metric_candidates=empty_candidate(),
        news_snapshot=trusted_only_snapshot,
        now=NOW,
    )

    assert with_candidates.report.overview == without_candidates.report.overview
    assert tuple(item.event_id for item in with_candidates.candidate_evidence.unverified_events) == (
        "pending-2d",
    )
    assert tuple(item.event_id for item in with_candidates.candidate_evidence.conflicting_events) == (
        "conflict-40d",
    )
    assert all(
        item.event_id not in {"pending-2d", "conflict-40d"}
        for item in with_candidates.report.news_risk
    )


def test_report_assembly_keeps_all_eight_sections_and_explicit_empty_reasons() -> None:
    assembly = assemble_storage_report(
        trusted_snapshot_id="trusted-storage-1",
        generated_at=NOW,
        trusted_observations=(trusted_observation("dram_price"),),
        metric_candidates=empty_candidate(),
        news_snapshot=None,
        now=NOW,
    )

    report_document = assembly.report.to_dict()
    assert tuple(state.section_id for state in assembly.section_states) == REPORT_SECTION_IDS
    assert all(section_id in report_document for section_id in REPORT_SECTION_IDS)
    cycle = next(state for state in assembly.section_states if state.section_id == "cycle")
    assert cycle.empty_reason is EmptyReason.NO_RELIABLE_DATA
    assert "nand_price" in cycle.missing_metric_ids
    assert next(state for state in assembly.section_states if state.section_id == "companies").empty_reason is EmptyReason.NOT_DISCLOSED
    assert report_document["overview"]["cycle_stage"] is None
    assert "暂无可靠数据" in report_document["overview"]["text"]


def test_assembly_uses_only_one_validated_snapshot_and_orders_metrics_by_template() -> None:
    rows = (
        trusted_observation("nand_price"),
        trusted_observation("dram_price"),
    )

    assembly = assemble_storage_report(
        trusted_snapshot_id="trusted-storage-1",
        generated_at=NOW,
        trusted_observations=rows,
        metric_candidates=empty_candidate(),
        news_snapshot=None,
        now=NOW,
    )

    assert tuple(item.metric_id for item in assembly.report.cycle[:2]) == (
        "dram_price",
        "nand_price",
    )
    assert len(assembly.report.cycle) == 8
    assert all(item.current_value is None for item in assembly.report.cycle[2:])
    assert assembly.report.trusted_snapshot_id == "trusted-storage-1"
    assert assembly.report.displayed_trusted_snapshot_id == "trusted-storage-1"
    assert assembly.report == assemble_storage_report(
        trusted_snapshot_id="trusted-storage-1",
        generated_at=NOW,
        trusted_observations=rows,
        metric_candidates=empty_candidate(),
        news_snapshot=None,
        now=NOW,
    ).report


def test_public_service_facade_is_deterministic_and_rejects_demo_in_production() -> None:
    service = IndustryResearchService(now=lambda: NOW, production=True)
    arguments = dict(
        trusted_snapshot_id="trusted-storage-1",
        generated_at=NOW,
        trusted_observations=(
            trusted_observation("dram_price"),
            trusted_observation("nand_price"),
        ),
        metric_candidates=empty_candidate(),
        news_snapshot=news_snapshot(),
    )

    assert service.assemble_storage_report(**arguments) == service.assemble_storage_report(**arguments)
    try:
        service.assemble_storage_report(**arguments, demo=True)
    except ValueError as error:
        assert str(error) == "production rejects demo=true trusted reports"
    else:
        raise AssertionError("production service accepted demo=true")
