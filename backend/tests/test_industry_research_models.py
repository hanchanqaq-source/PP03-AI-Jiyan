from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

import pytest

from industry_research.models import (
    AvailabilityStatus,
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    CandidateIndustryEvidenceEvent,
    ConclusionStatus,
    ConflictingObservation,
    ConflictingSourceValue,
    DataCompleteness,
    DisplayedTrustedReport,
    EmptyReason,
    EvidenceReference,
    FreshnessStatus,
    FundResolutionEmptyReason,
    IndustryChainNode,
    IndustryCompanyRelation,
    IndustryConclusion,
    IndustryEvidenceEvent,
    IndustryFundRelation,
    IndustryFundRelationResolution,
    IndustryMetricObservation,
    IndustryReportResponse,
    RefreshPhase,
    RefreshRun,
    ReportCounts,
    SourceCoverage,
    SourceRunStatus,
    TemplateStatus,
    VerificationStatus,
    render_conclusion_text,
    verification_display_label,
)


def _official_evidence(**changes: object) -> EvidenceReference:
    values: dict[str, object] = {
        "evidence_id": "ev-official-1",
        "source_family_id": "sec",
        "content_source": "sec.gov",
        "origin_cluster": "sec-filing-2026-q2",
        "collector_source": "sec_edgar",
        "final_url": "https://www.sec.gov/Archives/example",
        "is_official": True,
        "is_official_attested": True,
        "supports_claim": True,
        "supports_fields": ("dram_price",),
        "contradicts_claim": False,
        "as_of_date": "2026-06-30",
        "verified_at": "2026-08-25T09:00:00+08:00",
    }
    values.update(changes)
    return EvidenceReference(**values)


def _verified_observation(**changes: object) -> IndustryMetricObservation:
    values: dict[str, object] = {
        "industry_id": "storage",
        "metric_id": "dram_price",
        "label": "DRAM 价格",
        "current_value": 101.5,
        "unit": "index",
        "change": None,
        "historical_position": None,
        "availability_status": AvailabilityStatus.AVAILABLE,
        "verification_status": VerificationStatus.VERIFIED,
        "freshness_status": FreshnessStatus.FRESH,
        "source_run_status": SourceRunStatus.HEALTHY,
        "empty_reason": None,
        "as_of_date": "2026-06-30",
        "fetched_at": "2026-08-25T08:55:00+08:00",
        "methodology": "Official quarterly value, no aggregation",
        "judgment_basis": ("Official filing supports dram_price",),
        "invalidating_conditions": ("A correction supersedes this filing",),
        "evidence": (_official_evidence(),),
        "independent_source_families": ("sec",),
        "independent_content_sources": ("sec.gov",),
        "independent_origin_clusters": ("sec-filing-2026-q2",),
        "raw_snapshot_id": "raw-storage-1",
        "evidence_snapshot_id": "evidence-storage-1",
    }
    values.update(changes)
    return IndustryMetricObservation(**values)


def _corroborated_observation(**changes: object) -> IndustryMetricObservation:
    second = _official_evidence(
        evidence_id="ev-independent-2",
        source_family_id="industry_media",
        content_source="publisher.example",
        origin_cluster="publisher-report-2026-q2",
        collector_source="news_api",
        final_url="https://publisher.example/report",
        is_official=False,
        is_official_attested=False,
    )
    values: dict[str, object] = {
        "verification_status": VerificationStatus.CORROBORATED,
        "evidence": (_official_evidence(), second),
        "independent_source_families": ("sec", "industry_media"),
        "independent_content_sources": ("sec.gov", "publisher.example"),
        "independent_origin_clusters": ("sec-filing-2026-q2", "publisher-report-2026-q2"),
    }
    values.update(changes)
    return _verified_observation(**values)


def _empty_observation() -> IndustryMetricObservation:
    return IndustryMetricObservation(
        industry_id="storage",
        metric_id="hbm_demand",
        label="HBM 需求",
        current_value=None,
        unit=None,
        change=None,
        historical_position=None,
        availability_status=AvailabilityStatus.UNCONFIGURED,
        verification_status=VerificationStatus.NOT_EVALUATED,
        freshness_status=FreshnessStatus.UNKNOWN,
        source_run_status=SourceRunStatus.NOT_CONFIGURED,
        empty_reason=EmptyReason.SOURCE_UNCONFIGURED,
        as_of_date=None,
        fetched_at=None,
        methodology="No verified adapter is configured",
        judgment_basis=("Source is not configured",),
        invalidating_conditions=("A source is configured and verified",),
        evidence=(),
        independent_source_families=(),
        independent_content_sources=(),
        independent_origin_clusters=(),
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
    )


def _conclusion() -> IndustryConclusion:
    completeness = DataCompleteness(
        verified_metric_count=1,
        required_metric_count=1,
        ratio=1.0,
    )
    values = {
        "conclusion_id": "conclusion-storage-1",
        "industry_id": "storage",
        "rule_version": "storage-cycle-v1",
        "status": ConclusionStatus.VERIFIED,
        "cycle_stage": "recovery",
        "outlook_direction": "improving",
        "confidence_level": "high",
        "data_completeness": completeness,
        "basis_metric_ids": ("dram_price",),
        "evidence_ids": ("ev-official-1",),
        "invalidating_conditions": ("Required metric expires",),
    }
    return IndustryConclusion(
        **values,
        text=render_conclusion_text(**values),
    )


def _report(*, cycle: tuple[IndustryMetricObservation, ...] = ()) -> DisplayedTrustedReport:
    return DisplayedTrustedReport(
        industry_id="storage",
        template_status=TemplateStatus.COMPLETE_LAYOUT,
        trusted_snapshot_id="trusted-storage-1",
        displayed_trusted_snapshot_id="trusted-storage-1",
        generated_at="2026-08-25T09:05:00+08:00",
        demo=False,
        source_coverage=SourceCoverage(
            unit="capability",
            total=1,
            configured=1,
            healthy=1,
            partial_failure=0,
            failed=0,
            unconfigured=0,
        ),
        counts=ReportCounts(verified=1, corroborated=0),
        overview=_conclusion(),
        cycle=cycle,
        chain=(
            IndustryChainNode(
                industry_id="storage",
                node_id="memory_design_manufacturing",
                label="存储设计与制造",
                observation_ids=tuple(item.metric_id for item in cycle),
                evidence_ids=("ev-official-1",) if cycle else (),
                status=ConclusionStatus.VERIFIED if cycle else ConclusionStatus.UNAVAILABLE,
            ),
        ),
        metrics=(),
        capital=(),
        companies=(),
        fund_selection=(),
        funds=(),
        news_risk=(),
    )


def _candidate_panel(industry_id: str = "storage") -> CandidateEvidencePanel:
    return CandidateEvidencePanel(
        industry_id=industry_id,
        candidate_snapshot_id=None,
        counts=CandidateEvidenceCounts(0, 0, 0, 0),
        unverified=(),
        conflicting=(),
        unverified_events=(),
        conflicting_events=(),
    )


def _idle_refresh(industry_id: str = "storage") -> RefreshRun:
    return RefreshRun(
        industry_id=industry_id,
        run_id=None,
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
        candidate_snapshot_id=None,
        phase=RefreshPhase.IDLE,
        error_code=None,
        displayed_trusted_snapshot_id=None,
        published_trusted_snapshot_id=None,
    )


def test_source_unconfigured_round_trips_as_empty_not_evaluated_with_null_lineage() -> None:
    # Break caught: a serializer that aliases an empty reason or fabricates lineage.
    observation = _empty_observation()

    payload = json.loads(json.dumps(observation.to_dict(), ensure_ascii=False))
    restored = IndustryMetricObservation.from_dict(payload)

    assert restored == observation
    assert payload["verification_status"] == "not_evaluated"
    assert payload["empty_reason"] == "source_unconfigured"
    assert payload["raw_snapshot_id"] is None
    assert payload["evidence_snapshot_id"] is None
    assert verification_display_label(VerificationStatus.UNVERIFIED) == "待核验"


@pytest.mark.parametrize(
    "field_name",
    (
        "evidence_id",
        "source_family_id",
        "content_source",
        "origin_cluster",
        "collector_source",
        "final_url",
        "verified_at",
    ),
)
def test_evidence_reference_rejects_blank_required_identity_fields(field_name: str) -> None:
    # Break caught: blank identities are counted as independent evidence provenance.
    with pytest.raises(ValueError, match=field_name):
        _official_evidence(**{field_name: "  "})


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"evidence": (replace(_official_evidence(), is_official_attested=False),)}, "official_attestation"),
        ({"evidence": ()}, "evidence"),
        ({"as_of_date": None}, "as_of_date"),
        ({"methodology": ""}, "methodology"),
        ({"invalidating_conditions": ()}, "invalidating_conditions"),
        ({"raw_snapshot_id": None}, "raw_snapshot_id"),
        ({"evidence_snapshot_id": None}, "evidence_snapshot_id"),
    ],
)
def test_verified_observation_rejects_missing_truth_contract_fields(
    changes: dict[str, object], reason: str
) -> None:
    # Break caught: verified admission that accepts an incomplete official fact.
    with pytest.raises(ValueError, match=reason):
        _verified_observation(**changes)


@pytest.mark.parametrize(
    ("field_name", "raw_value"),
    (
        ("verification_status", "verified"),
        ("freshness_status", "expired"),
    ),
)
def test_observation_rejects_raw_string_enum_values(field_name: str, raw_value: str) -> None:
    # Break caught: str/Enum equality passes membership while identity branches are bypassed.
    with pytest.raises(TypeError, match=field_name):
        _verified_observation(**{field_name: raw_value})


def test_corroborated_requires_three_independently_counted_sets() -> None:
    # Break caught: one ambiguous count or duplicate publisher upgrades a fact.
    second = _official_evidence(
        evidence_id="ev-independent-2",
        source_family_id="industry_media",
        content_source="publisher.example",
        origin_cluster="same-origin",
        collector_source="news_api",
        final_url="https://publisher.example/report",
        is_official=False,
        is_official_attested=False,
    )

    with pytest.raises(ValueError, match="independent_origin_clusters"):
        _verified_observation(
            verification_status=VerificationStatus.CORROBORATED,
            evidence=(_official_evidence(origin_cluster="same-origin"), second),
            independent_source_families=("sec", "industry_media"),
            independent_content_sources=("sec.gov", "publisher.example"),
            independent_origin_clusters=("same-origin",),
        )


def test_corroborated_rejects_declared_independence_not_backed_by_evidence() -> None:
    # Break caught: callers fabricate independent-set arrays around duplicated evidence.
    duplicate_origin = _official_evidence(
        evidence_id="ev-duplicate-2",
        source_family_id="sec",
        content_source="sec.gov",
        origin_cluster="sec-filing-2026-q2",
    )

    with pytest.raises(ValueError, match="do not match supporting evidence"):
        _verified_observation(
            verification_status=VerificationStatus.CORROBORATED,
            evidence=(_official_evidence(), duplicate_origin),
            independent_source_families=("sec", "invented-family"),
            independent_content_sources=("sec.gov", "invented.example"),
            independent_origin_clusters=("sec-filing-2026-q2", "invented-origin"),
        )


@pytest.mark.parametrize("factory", [_verified_observation, _corroborated_observation])
def test_trusted_observation_rejects_any_contradicting_evidence(
    factory: Callable[..., IndustryMetricObservation],
) -> None:
    # Break caught: positive support masks a valid contradiction and enters trusted truth.
    observation = factory()
    contradicting = replace(observation.evidence[0], contradicts_claim=True)

    with pytest.raises(ValueError, match="contradicting evidence"):
        replace(observation, evidence=(contradicting, *observation.evidence[1:]))


def test_conflict_preserves_each_source_value_and_rejects_aggregate() -> None:
    # Break caught: conflict values are averaged or one source is discarded.
    source_values = (
        ConflictingSourceValue("ev-1", "family-1", 100, "index", "2026-08-20"),
        ConflictingSourceValue("ev-2", "family-2", 112, "index", "2026-08-20"),
    )
    conflict = ConflictingObservation("storage", "dram_price", None, source_values)

    assert [item.value for item in conflict.source_values] == [100, 112]
    assert conflict.aggregate_value is None
    with pytest.raises(ValueError, match="aggregate_value"):
        ConflictingObservation("storage", "dram_price", 106, source_values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("verification_status", "freshness_status"),
    [
        (VerificationStatus.UNVERIFIED, FreshnessStatus.FRESH),
        (VerificationStatus.CONFLICTING, FreshnessStatus.FRESH),
        (VerificationStatus.VERIFIED, FreshnessStatus.EXPIRED),
        (VerificationStatus.NOT_EVALUATED, FreshnessStatus.UNKNOWN),
    ],
)
def test_untrusted_or_expired_observation_cannot_enter_trusted_report(
    verification_status: VerificationStatus, freshness_status: FreshnessStatus
) -> None:
    # Break caught: candidate or expired data enters the trusted report arrays.
    candidate = _empty_observation() if verification_status is VerificationStatus.NOT_EVALUATED else _verified_observation(
        verification_status=verification_status,
        freshness_status=freshness_status,
        current_value=None if verification_status is VerificationStatus.CONFLICTING else 101.5,
        empty_reason=EmptyReason.CONFLICTING if verification_status is VerificationStatus.CONFLICTING else None,
    )
    with pytest.raises(ValueError, match="trusted_observations"):
        _report(cycle=(candidate,))


def test_trusted_report_rejects_cross_industry_observations() -> None:
    # Break caught: a trusted robotics value is displayed under the storage report.
    wrong_industry = _verified_observation(industry_id="robotics")

    with pytest.raises(ValueError, match="industry_id mismatch"):
        _report(cycle=(wrong_industry,))


def test_candidate_events_are_type_isolated_from_trusted_news() -> None:
    # Break caught: pending/conflicting news is accepted by news_risk.
    pending = CandidateIndustryEvidenceEvent(
        industry_id="storage",
        event_id="event-pending",
        status=VerificationStatus.UNVERIFIED,
        occurred_at="2026-08-24T10:00:00+08:00",
        evidence_ids=("ev-pending",),
        supporting_evidence_ids=("ev-pending",),
        contradicting_evidence_ids=(),
        roles=("news",),
    )
    conflict = CandidateIndustryEvidenceEvent(
        industry_id="storage",
        event_id="event-conflict",
        status=VerificationStatus.CONFLICTING,
        occurred_at="2026-08-24T11:00:00+08:00",
        evidence_ids=("ev-support", "ev-contradict"),
        supporting_evidence_ids=("ev-support",),
        contradicting_evidence_ids=("ev-contradict",),
        roles=("risk",),
    )
    panel = CandidateEvidencePanel(
        industry_id="storage",
        candidate_snapshot_id="candidate-1",
        counts=CandidateEvidenceCounts(
            unverified=0,
            conflicting=0,
            unverified_events=1,
            conflicting_events=1,
        ),
        unverified=(),
        conflicting=(),
        unverified_events=(pending,),
        conflicting_events=(conflict,),
    )

    assert panel.unverified_events == (pending,)
    assert panel.conflicting_events == (conflict,)
    with pytest.raises((TypeError, ValueError), match="news_risk|IndustryEvidenceEvent"):
        replace(_report(), news_risk=(pending,))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "counts"),
    (
        ("unverified", CandidateEvidenceCounts(1, 0, 0, 0)),
        ("conflicting", CandidateEvidenceCounts(0, 1, 0, 0)),
        ("unverified_events", CandidateEvidenceCounts(0, 0, 1, 0)),
        ("conflicting_events", CandidateEvidenceCounts(0, 0, 0, 1)),
    ),
)
def test_candidate_panel_rejects_wrong_concrete_element_types(
    field_name: str, counts: CandidateEvidenceCounts
) -> None:
    # Break caught: arbitrary objects enter candidate arrays or raise incidental AttributeError.
    with pytest.raises(TypeError, match=field_name):
        replace(_candidate_panel(), counts=counts, **{field_name: (object(),)})


@pytest.mark.parametrize(
    ("field_name", "entry", "counts"),
    (
        (
            "unverified",
            _verified_observation(
                industry_id="robotics",
                verification_status=VerificationStatus.UNVERIFIED,
            ),
            CandidateEvidenceCounts(1, 0, 0, 0),
        ),
        (
            "conflicting",
            ConflictingObservation(
                "robotics",
                "orders",
                None,
                (
                    ConflictingSourceValue("ev-r1", "family-r1", 1, None, None),
                    ConflictingSourceValue("ev-r2", "family-r2", 2, None, None),
                ),
            ),
            CandidateEvidenceCounts(0, 1, 0, 0),
        ),
        (
            "unverified_events",
            CandidateIndustryEvidenceEvent(
                "robotics",
                "event-r1",
                VerificationStatus.UNVERIFIED,
                "2026-08-25T00:00:00+08:00",
                ("ev-r1",),
                ("ev-r1",),
                (),
                ("news",),
            ),
            CandidateEvidenceCounts(0, 0, 1, 0),
        ),
        (
            "conflicting_events",
            CandidateIndustryEvidenceEvent(
                "robotics",
                "event-r2",
                VerificationStatus.CONFLICTING,
                "2026-08-25T00:00:00+08:00",
                ("ev-r1", "ev-r2"),
                ("ev-r1",),
                ("ev-r2",),
                ("risk",),
            ),
            CandidateEvidenceCounts(0, 0, 0, 1),
        ),
    ),
)
def test_candidate_panel_rejects_cross_industry_elements(
    field_name: str, entry: object, counts: CandidateEvidenceCounts
) -> None:
    # Break caught: robotics candidates are returned under a storage panel.
    with pytest.raises(ValueError, match="industry_id mismatch"):
        replace(_candidate_panel(), counts=counts, **{field_name: (entry,)})


def test_refresh_publish_lineage_is_explicit_on_success_and_failure() -> None:
    # Break caught: a failed refresh masquerades as a new trusted publication.
    success = RefreshRun(
        industry_id="storage",
        run_id="run-2",
        raw_snapshot_id="raw-2",
        evidence_snapshot_id="evidence-2",
        candidate_snapshot_id="candidate-2",
        phase=RefreshPhase.TRUSTED_PUBLISHED,
        error_code=None,
        displayed_trusted_snapshot_id="trusted-2",
        published_trusted_snapshot_id="trusted-2",
    )
    failure = RefreshRun(
        industry_id="storage",
        run_id="run-3",
        raw_snapshot_id="raw-3",
        evidence_snapshot_id="evidence-3",
        candidate_snapshot_id="candidate-3",
        phase=RefreshPhase.FAILED,
        error_code="source_failed",
        displayed_trusted_snapshot_id="trusted-2",
        published_trusted_snapshot_id=None,
    )

    assert success.published_trusted_snapshot_id == "trusted-2"
    assert failure.published_trusted_snapshot_id is None
    assert failure.displayed_trusted_snapshot_id == "trusted-2"
    with pytest.raises(ValueError, match="published_trusted_snapshot_id"):
        replace(failure, published_trusted_snapshot_id="trusted-3")


def test_industry_report_response_enforces_composite_industry_and_null_display_contract() -> None:
    # Break caught: a response can mix requested/displayed/report/candidate/refresh industries.
    candidate = _candidate_panel()
    refresh = _idle_refresh()
    ready = IndustryReportResponse(
        requested_industry_id="storage",
        displayed_industry_id="storage",
        displayed_trusted_report=_report(),
        candidate_evidence=candidate,
        refresh_run=refresh,
    )
    empty = IndustryReportResponse(
        requested_industry_id="storage",
        displayed_industry_id=None,
        displayed_trusted_report=None,
        candidate_evidence=candidate,
        refresh_run=refresh,
    )

    assert ready.to_dict()["displayed_industry_id"] == "storage"
    assert empty.to_dict()["displayed_trusted_report"] is None
    invalid_values = (
        {"displayed_industry_id": None, "displayed_trusted_report": _report()},
        {"displayed_industry_id": "storage", "displayed_trusted_report": None},
        {"displayed_industry_id": "robotics", "displayed_trusted_report": _report()},
        {"candidate_evidence": _candidate_panel("robotics")},
        {"refresh_run": _idle_refresh("robotics")},
    )
    for changes in invalid_values:
        with pytest.raises(ValueError, match="industry|displayed"):
            replace(ready, **changes)


def test_structured_conclusion_is_authoritative_and_text_cannot_add_cycle_judgment() -> None:
    # Break caught: free text asserts a cycle stage absent from structured fields.
    conclusion = _conclusion()
    assert conclusion.cycle_stage == "recovery"
    assert conclusion.outlook_direction == "improving"
    assert conclusion.confidence_level == "high"
    assert conclusion.data_completeness.ratio == 1.0

    with pytest.raises(ValueError, match="deterministic"):
        replace(conclusion, text=conclusion.text + "；行业已进入峰值周期")


def test_incomplete_required_metrics_force_structured_judgments_to_null() -> None:
    # Break caught: missing required metrics still yield a cycle judgment.
    completeness = DataCompleteness(verified_metric_count=1, required_metric_count=2, ratio=0.5)
    values = {
        "conclusion_id": "conclusion-incomplete",
        "industry_id": "storage",
        "rule_version": "storage-cycle-v1",
        "status": ConclusionStatus.PARTIAL,
        "cycle_stage": None,
        "outlook_direction": None,
        "confidence_level": None,
        "data_completeness": completeness,
        "basis_metric_ids": ("dram_price",),
        "evidence_ids": ("ev-official-1",),
        "invalidating_conditions": ("Missing metric becomes available",),
    }
    conclusion = IndustryConclusion(**values, text=render_conclusion_text(**values))
    assert conclusion.cycle_stage is None
    assert conclusion.outlook_direction is None
    assert conclusion.confidence_level is None

    with pytest.raises(ValueError, match="incomplete"):
        replace(conclusion, cycle_stage="expansion")


def test_relation_objects_fail_closed_without_codes_or_disclosure_evidence() -> None:
    # Break caught: a company/fund name alone is treated as a trusted relation.
    with pytest.raises(ValueError, match="security_code"):
        IndustryCompanyRelation(
            industry_id="storage",
            security_code="",
            company_name="Example Memory",
            chain_node_id="memory_design_manufacturing",
            relation_type="official_disclosure",
            key_metric_ids=("dram_price",),
            evidence_ids=("ev-1",),
            as_of_date="2026-06-30",
            observation_only=True,
        )
    with pytest.raises(ValueError, match="evidence_ids"):
        IndustryFundRelation(
            industry_id="storage",
            fund_code="000001",
            relation_layer="disclosed_lookthrough",
            exposure_value=12.3,
            exposure_unit="percent",
            disclosure_date="2026-06-30",
            evidence_ids=(),
            status=VerificationStatus.VERIFIED,
        )

    resolution = IndustryFundRelationResolution(
        selection_id="selection-1",
        fund_code="000001",
        relation=None,
        empty_reason=FundResolutionEmptyReason.NOT_DISCLOSED,
    )
    assert resolution.empty_reason is FundResolutionEmptyReason.NOT_DISCLOSED


def test_fund_resolution_uses_only_its_three_allowed_empty_reasons() -> None:
    # Break caught: metric/provider empty reasons leak into fund relation resolution.
    assert {reason.value for reason in FundResolutionEmptyReason} == {
        "unknown",
        "not_disclosed",
        "source_unavailable",
    }
    resolution = IndustryFundRelationResolution(
        selection_id="selection-unknown",
        fund_code="000001",
        relation=None,
        empty_reason=FundResolutionEmptyReason.UNKNOWN,
    )
    assert resolution.empty_reason is FundResolutionEmptyReason.UNKNOWN
    with pytest.raises(TypeError, match="FundResolutionEmptyReason"):
        replace(resolution, empty_reason=EmptyReason.SOURCE_FAILED)


def test_demo_fixture_is_rejected_in_production_mode() -> None:
    # Break caught: isolated demonstration data is returned as production truth.
    report = replace(_report(), demo=True)
    report.validate_for_mode(production=False)
    with pytest.raises(ValueError, match="demo"):
        report.validate_for_mode(production=True)


def test_wire_keys_remain_snake_case_for_nested_report() -> None:
    # Break caught: a serializer emits mixed camelCase/snake_case payloads.
    payload = _report(cycle=(_verified_observation(),)).to_dict()

    def keys(value: object) -> list[str]:
        if isinstance(value, dict):
            return [str(key) for key in value] + [item for child in value.values() for item in keys(child)]
        if isinstance(value, list):
            return [item for child in value for item in keys(child)]
        return []

    assert all(not any(character.isupper() for character in key) for key in keys(payload))
    assert payload["displayed_trusted_snapshot_id"] == "trusted-storage-1"
