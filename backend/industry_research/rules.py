from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Iterable

from .models import (
    ConclusionStatus,
    DataCompleteness,
    FreshnessStatus,
    IndustryConclusion,
    IndustryMetricObservation,
    VerificationStatus,
    _validated_change_value,
    render_conclusion_text,
)
from .templates import get_industry_template


RULE_VERSION = "storage-cycle-v1"
_REQUIRED_CYCLE_METRICS = ("dram_price", "nand_price")
_OUTLOOK_METRICS = (
    "hbm_demand",
    "inventory_level",
    "capacity_utilization",
    "server_demand",
    "consumer_electronics_demand",
)


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def observation_is_current(observation: IndustryMetricObservation, *, now: datetime) -> bool:
    """Evaluate the structured expiry field; never parse methodology prose."""
    _aware(now, "now")
    if observation.freshness_status is FreshnessStatus.EXPIRED:
        return False
    if observation.expires_at is None:
        return True
    expiry = datetime.fromisoformat(observation.expires_at.replace("Z", "+00:00"))
    return expiry > now


def select_current_trusted_observations(
    *,
    industry_id: str,
    observations: Iterable[IndustryMetricObservation],
    now: datetime,
) -> tuple[IndustryMetricObservation, ...]:
    """Validate one trusted lineage and return current observations in template order."""
    _aware(now, "now")
    template = get_industry_template(industry_id)
    allowed = set(template.cycle_metric_ids)
    order = {metric_id: index for index, metric_id in enumerate(template.cycle_metric_ids)}
    rows = tuple(observations)
    seen: set[str] = set()
    lineages: set[tuple[str | None, str | None]] = set()
    for row in rows:
        if type(row) is not IndustryMetricObservation:
            raise TypeError("observations must contain IndustryMetricObservation")
        if row.industry_id != industry_id:
            raise ValueError("trusted observation industry_id mismatch")
        if row.metric_id not in allowed:
            raise ValueError("trusted observation metric_id is not in the industry template")
        if row.metric_id in seen:
            raise ValueError("one trusted snapshot cannot contain duplicate metric IDs")
        seen.add(row.metric_id)
        if row.verification_status not in {
            VerificationStatus.VERIFIED,
            VerificationStatus.CORROBORATED,
        }:
            raise ValueError("rules accept only verified/corroborated observations")
        if row.change is not None:
            _validated_change_value(row.change)
        lineages.add((row.raw_snapshot_id, row.evidence_snapshot_id))
    if len(lineages) > 1:
        raise ValueError("rules consume observations from one trusted snapshot")
    return tuple(sorted(
        (row for row in rows if observation_is_current(row, now=now)),
        key=lambda row: (order[row.metric_id], row.metric_id),
    ))


def _cycle_stage(rows: dict[str, IndustryMetricObservation]) -> str | None:
    changes = [rows[metric_id].change for metric_id in _REQUIRED_CYCLE_METRICS]
    if any(change is None for change in changes):
        return None
    values = tuple(change.value for change in changes if change is not None)
    if all(value > 0 for value in values):
        return "expansion"
    if all(value < 0 for value in values):
        return "contraction"
    return None


def _outlook_rows(rows: dict[str, IndustryMetricObservation]) -> tuple[tuple[IndustryMetricObservation, ...], str | None]:
    selected = tuple(
        rows[metric_id]
        for metric_id in _OUTLOOK_METRICS
        if metric_id in rows and rows[metric_id].change is not None
    )
    if len(selected) < 2:
        return (), None
    score = sum(
        -row.change.value if row.metric_id == "inventory_level" else row.change.value
        for row in selected
        if row.change is not None
    )
    if score > 0:
        return selected, "improving"
    if score < 0:
        return selected, "weakening"
    return selected, "stable"


def _conclusion_id(
    *,
    industry_id: str,
    trusted_snapshot_id: str,
    basis_metric_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    cycle_stage: str | None,
    outlook_direction: str | None,
) -> str:
    payload = json.dumps(
        {
            "industry_id": industry_id,
            "trusted_snapshot_id": trusted_snapshot_id,
            "rule_version": RULE_VERSION,
            "basis_metric_ids": basis_metric_ids,
            "evidence_ids": evidence_ids,
            "cycle_stage": cycle_stage,
            "outlook_direction": outlook_direction,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{industry_id}-{RULE_VERSION}-{digest}"


def evaluate_storage_conclusion(
    *,
    industry_id: str,
    trusted_snapshot_id: str,
    observations: Iterable[IndustryMetricObservation],
    now: datetime,
) -> IndustryConclusion:
    """Pure deterministic storage rule; candidate evidence is intentionally absent."""
    if not isinstance(trusted_snapshot_id, str) or not trusted_snapshot_id.strip():
        raise ValueError("trusted_snapshot_id must not be blank")
    current = select_current_trusted_observations(
        industry_id=industry_id,
        observations=observations,
        now=now,
    )
    rows = {row.metric_id: row for row in current}
    required_rows = tuple(rows[item] for item in _REQUIRED_CYCLE_METRICS if item in rows)
    completeness = DataCompleteness(
        verified_metric_count=len(required_rows),
        required_metric_count=len(_REQUIRED_CYCLE_METRICS),
        ratio=len(required_rows) / len(_REQUIRED_CYCLE_METRICS),
    )
    complete = len(required_rows) == len(_REQUIRED_CYCLE_METRICS)
    cycle_stage = _cycle_stage(rows) if complete else None
    outlook_rows, outlook_direction = _outlook_rows(rows) if complete else ((), None)
    basis_rows = required_rows + outlook_rows
    basis_metric_ids = tuple(row.metric_id for row in basis_rows)
    evidence_ids = tuple(sorted({
        evidence.evidence_id
        for row in basis_rows
        for evidence in row.evidence
    }))
    invalidating_conditions = tuple(sorted({
        condition
        for row in basis_rows
        for condition in row.invalidating_conditions
    }))
    if not complete:
        status = ConclusionStatus.PARTIAL if current else ConclusionStatus.UNAVAILABLE
        confidence_level = None
    else:
        status = (
            ConclusionStatus.CORROBORATED
            if any(row.verification_status is VerificationStatus.CORROBORATED for row in basis_rows)
            else ConclusionStatus.VERIFIED
        )
        if outlook_direction is None:
            confidence_level = "low"
        elif status is ConclusionStatus.VERIFIED and len(outlook_rows) >= 2:
            confidence_level = "high"
        else:
            confidence_level = "medium"
    conclusion_id = _conclusion_id(
        industry_id=industry_id,
        trusted_snapshot_id=trusted_snapshot_id,
        basis_metric_ids=basis_metric_ids,
        evidence_ids=evidence_ids,
        cycle_stage=cycle_stage,
        outlook_direction=outlook_direction,
    )
    values = dict(
        conclusion_id=conclusion_id,
        industry_id=industry_id,
        rule_version=RULE_VERSION,
        status=status,
        cycle_stage=cycle_stage,
        outlook_direction=outlook_direction,
        confidence_level=confidence_level,
        data_completeness=completeness,
        basis_metric_ids=basis_metric_ids,
        evidence_ids=evidence_ids,
        invalidating_conditions=invalidating_conditions,
    )
    return IndustryConclusion(
        **values,
        text=render_conclusion_text(**values),
    )
