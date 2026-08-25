from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from data_sources.models import ProviderValue
from evidence_verification.models import (
    EvidenceEvent,
    EvidenceItem,
    EvidenceSnapshot,
    SourceRole,
    VerificationStatus as A2VerificationStatus,
)
from evidence_verification.storage import EvidenceStorage
from industry_research.admission import (
    EvidenceDecision,
    RawMetricObservation,
    SourceIdentity,
    admit_metric_observations,
)
from industry_research.models import (
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    ConflictingObservation,
    ConflictingSourceValue,
    EmptyReason,
    MetricChange,
    VerificationStatus,
)
from industry_research.rules import evaluate_storage_conclusion
from industry_research.service import assemble_storage_report
from industry_research.storage import IndustryResearchStorage
from industry_research.templates import get_industry_template
from news_intelligence.models import NewsSourceItem


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def _provider_row(
    metric_id: str,
    *,
    event_id: str,
    evidence_id: str,
    change: MetricChange | None,
    expires_at: datetime,
) -> tuple[RawMetricObservation, EvidenceEvent]:
    source = NewsSourceItem(
        source_name="SEC",
        source_url="https://www.sec.gov/Archives/edgar/data/1/",
        original_url=f"https://www.sec.gov/Archives/edgar/data/1/{metric_id}.htm",
        published_at=NOW,
        fetched_at=NOW,
        title=f"{metric_id} official disclosure",
        summary=f"Official disclosure supporting {metric_id}.",
        language="en",
        region="US",
        track_key="storage",
        track_name="存储",
        category="industry",
        normalized_title=f"{metric_id} official disclosure",
        tokens=frozenset({metric_id, "official"}),
        anchors=frozenset({metric_id}),
        related_tags=(("storage", "存储"),),
        text_related_tags=(("storage", "存储"),),
        source_domain="www.sec.gov",
        data_status="current_snapshot",
    )
    identity = SourceIdentity(source_family_id=f"official-{metric_id}", source=source)
    provider = ProviderValue(
        value=100.0,
        source_family_id=identity.source_family_id,
        adapter_id=f"adapter-{metric_id}",
        capability_id="industry_price_snapshot",
        as_of_date=date(2026, 8, 24),
        fetched_at=NOW,
        data_status="candidate_snapshot",
        license="verified_public_current_snapshot",
        priority=100,
        difference_from_primary=Decimal("0"),
        unit="index",
        frequency="current_snapshot",
        source_metadata={"product": metric_id},
    )
    raw = RawMetricObservation(
        industry_id="storage",
        metric_id=metric_id,
        label=metric_id,
        provider_value=provider,
        identity=identity,
        decision=EvidenceDecision(event_id=event_id, evidence_id=evidence_id),
        expires_at=expires_at,
        methodology="Verified same-series weekly comparison.",
        judgment_basis=("Provider supplied a structured weekly change.",),
        invalidating_conditions=("Official correction or expiry invalidates this observation.",),
        change=change,
    )
    item = EvidenceItem(
        evidence_id=evidence_id,
        content_source=identity.content_source,
        collector_source=identity.collector_source,
        canonical_url=identity.final_url,
        published_at=NOW,
        source_role=SourceRole.PRIMARY,
        origin_cluster=identity.origin_cluster,
        supports_claim=True,
        supports_fields=(metric_id,),
        contradicts_claim=False,
        is_official=True,
        title=f"{metric_id} evidence",
        excerpt="Official structured observation.",
    )
    event = EvidenceEvent(
        event_id=event_id,
        title=f"{metric_id} evidence",
        summary="Official structured observation.",
        category="industry",
        related_tags=(("storage", "存储"),),
        published_at=NOW,
        core_claim=metric_id,
        verification_status=A2VerificationStatus.VERIFIED,
        verification_reason="official attestation",
        verified_at=NOW,
        evidence_as_of=NOW,
        primary_evidence=(item,),
    )
    return raw, event


def _admit_provider_rows(
    tmp_path,
    *,
    with_changes: bool,
    expires_at: datetime,
):
    dram, dram_event = _provider_row(
        "dram_price",
        event_id="dram-event",
        evidence_id="dram-evidence",
        change=MetricChange(2.0, "wow") if with_changes else None,
        expires_at=expires_at,
    )
    nand, nand_event = _provider_row(
        "nand_price",
        event_id="nand-event",
        evidence_id="nand-evidence",
        change=MetricChange(1.0, "wow") if with_changes else None,
        expires_at=expires_at,
    )
    evidence = EvidenceSnapshot(
        snapshot_id="evidence-storage-1",
        raw_snapshot_id="raw-storage-1",
        generated_at=NOW,
        events=(dram_event, nand_event),
    )
    evidence_storage = EvidenceStorage(tmp_path / "canonical-a2")
    evidence_storage.publish(evidence)
    return admit_metric_observations(
        industry_id="storage",
        raw_snapshot_id="raw-storage-1",
        evidence_snapshot_id="evidence-storage-1",
        evidence_storage=evidence_storage,
        candidate_snapshot_id="candidate-storage-1",
        observations=(dram, nand),
        now=NOW,
    )


def test_provider_a2_admission_preserves_structured_change_and_expiry_for_rules(tmp_path) -> None:
    expiry = NOW + timedelta(days=1)
    projection = _admit_provider_rows(tmp_path, with_changes=True, expires_at=expiry)

    assert tuple(row.change.to_dict() for row in projection.trusted) == (
        {"value": 2.0, "basis": "wow"},
        {"value": 1.0, "basis": "wow"},
    )
    assert {row.expires_at for row in projection.trusted} == {expiry.isoformat()}
    assert projection.candidate.raw_snapshot_id == "raw-storage-1"
    assert projection.candidate.evidence_snapshot_id == "evidence-storage-1"
    current = evaluate_storage_conclusion(
        industry_id="storage",
        trusted_snapshot_id="trusted-storage-1",
        observations=projection.trusted,
        now=NOW,
    )
    expired = evaluate_storage_conclusion(
        industry_id="storage",
        trusted_snapshot_id="trusted-storage-1",
        observations=projection.trusted,
        now=NOW + timedelta(days=2),
    )

    assert current.cycle_stage == "expansion"
    assert expired.cycle_stage is None
    assert expired.evidence_ids == ()
    assert "dram-evidence" not in expired.text
    assert "nand-evidence" not in expired.text

    assembly = assemble_storage_report(
        trusted_snapshot_id="trusted-storage-1",
        raw_snapshot_id="raw-storage-1",
        evidence_snapshot_id="evidence-storage-1",
        generated_at=NOW,
        trusted_observations=projection.trusted,
        metric_candidates=projection.candidate,
        news_snapshot=None,
        now=NOW,
    )
    report_storage = IndustryResearchStorage(root=tmp_path / "current-industry")
    publication = report_storage.publish(
        assembly.report,
        expected_industry_id="storage",
        expected_raw_snapshot_id="raw-storage-1",
        expected_evidence_snapshot_id="evidence-storage-1",
    )
    loaded = report_storage.load_current("storage")

    assert publication.error_code is None
    assert loaded == assembly.report
    assert loaded is not None
    assert {row.expires_at for row in loaded.cycle[:2]} == {expiry.isoformat()}
    assert tuple(row.change.to_dict() for row in loaded.cycle[:2]) == (
        {"value": 2.0, "basis": "wow"},
        {"value": 1.0, "basis": "wow"},
    )


def test_provider_a2_admission_without_structured_change_never_infers_direction(tmp_path) -> None:
    projection = _admit_provider_rows(
        tmp_path,
        with_changes=False,
        expires_at=NOW + timedelta(days=1),
    )

    conclusion = evaluate_storage_conclusion(
        industry_id="storage",
        trusted_snapshot_id="trusted-storage-1",
        observations=projection.trusted,
        now=NOW,
    )

    assert all(row.change is None for row in projection.trusted)
    assert conclusion.cycle_stage is None
    assert conclusion.outlook_direction is None


def test_full_placeholder_rows_and_empty_reasons_survive_storage_round_trip(tmp_path) -> None:
    projection = _admit_provider_rows(
        tmp_path,
        with_changes=True,
        expires_at=NOW + timedelta(days=1),
    )
    unverified = replace(
        projection.trusted[0],
        metric_id="capacity_utilization",
        label="capacity_utilization",
        verification_status=VerificationStatus.UNVERIFIED,
    )
    conflict = ConflictingObservation(
        industry_id="storage",
        metric_id="manufacturer_capex",
        aggregate_value=None,
        source_values=(
            ConflictingSourceValue("capex-a", "family-a", 1.0, "index", "2026-08-24"),
            ConflictingSourceValue("capex-b", "family-b", -1.0, "index", "2026-08-24"),
        ),
        raw_snapshot_id="raw-storage-1",
        evidence_snapshot_id="evidence-storage-1",
    )
    candidates = CandidateEvidencePanel(
        industry_id="storage",
        candidate_snapshot_id="candidate-storage-1",
        counts=CandidateEvidenceCounts(1, 1, 0, 0),
        unverified=(unverified,),
        conflicting=(conflict,),
        unverified_events=(),
        conflicting_events=(),
        raw_snapshot_id="raw-storage-1",
        evidence_snapshot_id="evidence-storage-1",
    )
    assembly = assemble_storage_report(
        trusted_snapshot_id="trusted-storage-1",
        raw_snapshot_id="raw-storage-1",
        evidence_snapshot_id="evidence-storage-1",
        generated_at=NOW + timedelta(days=2),
        trusted_observations=projection.trusted,
        metric_candidates=candidates,
        news_snapshot=None,
        now=NOW + timedelta(days=2),
    )
    report = assembly.report
    template = get_industry_template("storage")

    assert tuple(row.metric_id for row in report.cycle) == template.cycle_metric_ids
    assert tuple(row.metric_id for row in report.metrics) == template.core_metric_ids
    assert tuple(row.metric_id for row in report.capital) == template.capital_metric_ids
    cycle = {row.metric_id: row for row in report.cycle}
    assert cycle["dram_price"].empty_reason is EmptyReason.EXPIRED
    assert cycle["nand_price"].empty_reason is EmptyReason.EXPIRED
    assert cycle["capacity_utilization"].empty_reason is EmptyReason.VERIFYING
    assert cycle["manufacturer_capex"].empty_reason is EmptyReason.CONFLICTING
    assert cycle["hbm_demand"].empty_reason is EmptyReason.NO_RELIABLE_DATA
    assert cycle["inventory_level"].empty_reason is EmptyReason.NO_RELIABLE_DATA
    assert report.source_coverage.to_dict() == {
        "unit": "capability",
        "total": 8,
        "configured": 8,
        "healthy": 4,
        "partial_failure": 4,
        "failed": 0,
        "unconfigured": 0,
    }
    for row in report.cycle + report.metrics + report.capital:
        assert row.current_value is None
        assert row.change is None
        assert row.historical_position is None
        assert row.as_of_date is None
        assert row.fetched_at is None
        assert row.evidence == ()
        assert row.raw_snapshot_id is None
        assert row.evidence_snapshot_id is None

    storage = IndustryResearchStorage(root=tmp_path / "industry")
    result = storage.publish(
        report,
        expected_industry_id="storage",
        expected_raw_snapshot_id="raw-storage-1",
        expected_evidence_snapshot_id="evidence-storage-1",
    )
    loaded = storage.load_current("storage")

    assert result.error_code is None
    assert loaded == report
    assert loaded is not None
    document = loaded.to_dict()
    assert len(document["cycle"]) == 8
    assert all("empty_reason" in row and row["current_value"] is None for row in document["cycle"])
    assert {document["cycle"][0]["expires_at"], document["cycle"][1]["expires_at"]} == {
        (NOW + timedelta(days=1)).isoformat()
    }
