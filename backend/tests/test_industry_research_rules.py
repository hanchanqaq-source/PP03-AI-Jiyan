from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import inspect

import pytest

from industry_research.models import (
    AvailabilityStatus,
    ConclusionStatus,
    EvidenceReference,
    FreshnessStatus,
    IndustryMetricObservation,
    MetricChange,
    SourceRunStatus,
    VerificationStatus,
)
from industry_research.rules import RULE_VERSION, evaluate_storage_conclusion


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def observation(
    metric_id: str,
    *,
    change: float | None = 1.0,
    status: VerificationStatus = VerificationStatus.VERIFIED,
    freshness: FreshnessStatus = FreshnessStatus.FRESH,
    invalidating_conditions: tuple[str, ...] | None = None,
) -> IndustryMetricObservation:
    evidence = EvidenceReference(
        evidence_id=f"ev-{metric_id}",
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
        change=None if change is None else MetricChange(change, "wow"),
        historical_position=None,
        availability_status=AvailabilityStatus.AVAILABLE,
        verification_status=status,
        freshness_status=freshness,
        source_run_status=SourceRunStatus.HEALTHY,
        empty_reason=None,
        as_of_date="2026-08-24",
        fetched_at="2026-08-25T07:00:00+00:00",
        methodology="Compare the current disclosed series with its prior weekly observation.",
        judgment_basis=(f"{metric_id} weekly change",),
        invalidating_conditions=invalidating_conditions or (
            "expires_at=2026-09-01T00:00:00+00:00",
        ),
        evidence=(evidence,),
        independent_source_families=(),
        independent_content_sources=(),
        independent_origin_clusters=(),
        raw_snapshot_id="raw-storage-1",
        evidence_snapshot_id="evidence-storage-1",
    )


def evaluate(*rows: IndustryMetricObservation):
    return evaluate_storage_conclusion(
        industry_id="storage",
        trusted_snapshot_id="trusted-storage-1",
        observations=rows,
        now=NOW,
    )


def test_missing_dram_or_nand_fails_closed_with_exact_trustworthy_empty_state() -> None:
    conclusion = evaluate(observation("dram_price"))

    assert conclusion.rule_version == RULE_VERSION
    assert conclusion.status is ConclusionStatus.PARTIAL
    assert conclusion.cycle_stage is None
    assert conclusion.outlook_direction is None
    assert conclusion.confidence_level is None
    assert conclusion.data_completeness.to_dict() == {
        "verified_metric_count": 1,
        "required_metric_count": 2,
        "ratio": 0.5,
    }
    assert "周期=暂无可靠数据" in conclusion.text
    assert "方向=暂无可靠数据" in conclusion.text


def test_price_direction_can_classify_cycle_but_cannot_alone_infer_outlook() -> None:
    conclusion = evaluate(
        observation("dram_price", change=2.0),
        observation("nand_price", change=1.0),
    )

    assert conclusion.status is ConclusionStatus.VERIFIED
    assert conclusion.cycle_stage == "expansion"
    assert conclusion.outlook_direction is None
    assert conclusion.confidence_level == "low"
    assert conclusion.basis_metric_ids == ("dram_price", "nand_price")
    assert conclusion.evidence_ids == ("ev-dram_price", "ev-nand_price")


def test_two_non_price_demand_signals_are_required_for_outlook() -> None:
    conclusion = evaluate(
        observation("dram_price", change=2.0),
        observation("nand_price", change=1.0),
        observation("manufacturer_capex", change=9.0),
        observation("hbm_demand", change=4.0),
        observation("server_demand", change=3.0),
    )

    assert conclusion.cycle_stage == "expansion"
    assert conclusion.outlook_direction == "improving"
    assert conclusion.confidence_level == "high"
    assert "manufacturer_capex" not in conclusion.basis_metric_ids
    assert conclusion.basis_metric_ids == (
        "dram_price",
        "nand_price",
        "hbm_demand",
        "server_demand",
    )


def test_expired_invalidation_condition_downgrades_the_conclusion() -> None:
    expired = observation(
        "nand_price",
        invalidating_conditions=("expires_at=2026-08-25T07:59:59+00:00",),
    )

    conclusion = evaluate(observation("dram_price"), expired)

    assert conclusion.status is ConclusionStatus.PARTIAL
    assert conclusion.cycle_stage is None
    assert conclusion.confidence_level is None
    assert conclusion.basis_metric_ids == ("dram_price",)
    assert conclusion.evidence_ids == ("ev-dram_price",)
    assert "ev-nand_price" not in conclusion.text


def test_deleting_evidence_input_recomputes_without_stale_free_text() -> None:
    complete = evaluate(observation("dram_price"), observation("nand_price"))
    downgraded = evaluate(observation("dram_price"))

    assert complete.cycle_stage == "expansion"
    assert downgraded.cycle_stage is None
    assert downgraded.evidence_ids == ("ev-dram_price",)
    assert "ev-nand_price" not in downgraded.text
    assert "周期=expansion" not in downgraded.text


def test_rules_reject_mixed_snapshot_lineage_instead_of_selecting_values() -> None:
    other_snapshot = replace(
        observation("nand_price"),
        raw_snapshot_id="raw-storage-2",
        evidence_snapshot_id="evidence-storage-2",
    )

    with pytest.raises(ValueError, match="one trusted snapshot"):
        evaluate(observation("dram_price"), other_snapshot)


def test_rules_are_pure_stable_and_do_not_accept_candidate_panels() -> None:
    rows = (observation("dram_price"), observation("nand_price"))

    assert evaluate(*rows) == evaluate(*rows)
    signature = inspect.signature(evaluate_storage_conclusion)
    assert "candidate" not in signature.parameters
