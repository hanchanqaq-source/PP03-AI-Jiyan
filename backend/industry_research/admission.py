from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Iterable

from data_sources.models import ProviderValue
from evidence_verification.models import (
    EvidenceItem,
    EvidenceSnapshot,
    VerificationStatus as A2VerificationStatus,
)
from evidence_verification.storage import EvidenceStorage
from evidence_verification.source_identity import (
    canonicalize_public_url,
    identify_evidence,
    official_content_source,
)
from news_intelligence.models import NewsSourceItem

from .models import (
    AvailabilityStatus,
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    ConflictingObservation,
    ConflictingSourceValue,
    EvidenceReference,
    FreshnessStatus,
    IndustryMetricObservation,
    SourceRunStatus,
    VerificationStatus,
)
from .templates import get_industry_template


_SOURCE_FAMILY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    source_family_id: str
    source: NewsSourceItem = field(repr=False, compare=False)
    content_source: str = field(init=False)
    origin_cluster: str = field(init=False)
    collector_source: str = field(init=False)
    final_url: str = field(init=False)
    is_official: bool = field(init=False)
    is_official_attested: bool = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source_family_id, str) or not _SOURCE_FAMILY_ID.fullmatch(self.source_family_id):
            raise ValueError("source_identity source_family_id must be a canonical identifier")
        if not isinstance(self.source, NewsSourceItem):
            raise TypeError("source_identity source must be NewsSourceItem")
        canonical_original = canonicalize_public_url(self.source.original_url)
        if canonical_original is None:
            raise ValueError("A2 source identity rejected the original URL")
        evidence = identify_evidence(self.source)
        if evidence.canonical_url != canonical_original:
            raise ValueError("A2 source identity did not preserve the final URL")
        is_official = official_content_source(evidence.canonical_url) is not None
        if evidence.is_official and not is_official:
            raise ValueError("official attestation requires an official final URL")
        object.__setattr__(self, "content_source", evidence.content_source)
        object.__setattr__(self, "origin_cluster", evidence.origin_cluster)
        object.__setattr__(self, "collector_source", evidence.collector_source)
        object.__setattr__(self, "final_url", evidence.canonical_url)
        object.__setattr__(self, "is_official", is_official)
        object.__setattr__(self, "is_official_attested", evidence.is_official)

@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    event_id: str
    evidence_id: str

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.evidence_id.strip():
            raise ValueError("evidence decision IDs must not be blank")


@dataclass(frozen=True, slots=True)
class RawMetricObservation:
    industry_id: str
    metric_id: str
    label: str
    provider_value: ProviderValue
    identity: SourceIdentity
    decision: EvidenceDecision
    expires_at: datetime
    methodology: str
    judgment_basis: tuple[str, ...]
    invalidating_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.industry_id.strip() or not self.metric_id.strip() or not self.label.strip():
            raise ValueError("raw observation identity must not be blank")
        if not isinstance(self.provider_value, ProviderValue):
            raise TypeError("provider_value must be ProviderValue")
        if self.provider_value.source_family_id != self.identity.source_family_id:
            raise ValueError("provider and evidence source_family_id mismatch")
        if self.provider_value.source_metadata.get("product") != self.metric_id:
            raise ValueError("provider product metadata does not match metric_id")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if not self.methodology.strip() or not self.judgment_basis or not self.invalidating_conditions:
            raise ValueError("raw observation requires methodology, basis and invalidating conditions")


@dataclass(frozen=True, slots=True)
class AdmissionProjection:
    industry_id: str
    raw: tuple[RawMetricObservation, ...]
    trusted: tuple[IndustryMetricObservation, ...]
    candidate: CandidateEvidencePanel
    expired: tuple[IndustryMetricObservation, ...]


@dataclass(frozen=True, slots=True)
class _ResolvedEvidence:
    item: EvidenceItem
    event_status: A2VerificationStatus
    verified_at: datetime
    contradicts_claim: bool


def _evidence_index(snapshot: EvidenceSnapshot) -> dict[tuple[str, str], _ResolvedEvidence]:
    index: dict[tuple[str, str], _ResolvedEvidence] = {}
    for event in snapshot.events:
        collections = (
            (event.primary_evidence, False),
            (event.independent_evidence, False),
            (event.syndicated_copies, False),
            (event.contradicting_evidence, True),
        )
        for items, contradictory_collection in collections:
            for item in items:
                key = (event.event_id, item.evidence_id)
                resolved = _ResolvedEvidence(
                    item=item,
                    event_status=event.verification_status,
                    verified_at=event.verified_at,
                    contradicts_claim=contradictory_collection or item.contradicts_claim,
                )
                previous = index.get(key)
                if previous is not None and previous.item != item:
                    raise ValueError("A2 evidence snapshot contains an ambiguous evidence identity")
                if previous is not None and previous.contradicts_claim:
                    resolved = _ResolvedEvidence(
                        item=item,
                        event_status=event.verification_status,
                        verified_at=event.verified_at,
                        contradicts_claim=True,
                    )
                index[key] = resolved
    return index


def _evidence_reference(row: RawMetricObservation, resolved: _ResolvedEvidence) -> EvidenceReference:
    as_of = row.provider_value.as_of_date
    item = resolved.item
    return EvidenceReference(
        evidence_id=item.evidence_id,
        source_family_id=row.identity.source_family_id,
        content_source=item.content_source,
        origin_cluster=item.origin_cluster,
        collector_source=item.collector_source,
        final_url=item.canonical_url,
        is_official=row.identity.is_official,
        is_official_attested=item.is_official,
        supports_claim=item.supports_claim,
        supports_fields=item.supports_fields,
        contradicts_claim=resolved.contradicts_claim,
        as_of_date=as_of.isoformat() if as_of is not None else None,
        verified_at=resolved.verified_at.isoformat(),
    )


def _scalar_value(value: object) -> str | int | float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("industry metric value must be a string or finite number")
    if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
        raise ValueError("industry metric value must be a string or finite number")
    return value


def _observation(
    row: RawMetricObservation,
    evidence: tuple[EvidenceReference, ...],
    *,
    verification: VerificationStatus,
    freshness: FreshnessStatus,
    raw_snapshot_id: str,
    evidence_snapshot_id: str,
    independent_source_families: tuple[str, ...] = (),
    independent_content_sources: tuple[str, ...] = (),
    independent_origin_clusters: tuple[str, ...] = (),
) -> IndustryMetricObservation:
    return IndustryMetricObservation(
        industry_id=row.industry_id,
        metric_id=row.metric_id,
        label=row.label,
        current_value=_scalar_value(row.provider_value.value),
        unit=row.provider_value.unit or None,
        change=None,
        historical_position=None,
        availability_status=AvailabilityStatus.AVAILABLE,
        verification_status=verification,
        freshness_status=freshness,
        source_run_status=SourceRunStatus.HEALTHY,
        empty_reason=None,
        as_of_date=row.provider_value.as_of_date.isoformat() if row.provider_value.as_of_date else None,
        fetched_at=row.provider_value.fetched_at.isoformat(),
        methodology=row.methodology,
        judgment_basis=row.judgment_basis,
        invalidating_conditions=row.invalidating_conditions,
        evidence=evidence,
        independent_source_families=independent_source_families,
        independent_content_sources=independent_content_sources,
        independent_origin_clusters=independent_origin_clusters,
        raw_snapshot_id=raw_snapshot_id,
        evidence_snapshot_id=evidence_snapshot_id,
    )


def admit_metric_observations(
    *,
    industry_id: str,
    raw_snapshot_id: str,
    evidence_snapshot_id: str,
    evidence_storage: EvidenceStorage,
    candidate_snapshot_id: str,
    observations: Iterable[RawMetricObservation],
    now: datetime,
) -> AdmissionProjection:
    """Partition raw values without accepting candidate content as an input."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if (
        not raw_snapshot_id.strip()
        or raw_snapshot_id != raw_snapshot_id.strip()
        or not candidate_snapshot_id.strip()
        or candidate_snapshot_id != candidate_snapshot_id.strip()
    ):
        raise ValueError("snapshot lineage must not be blank")
    if type(evidence_storage) is not EvidenceStorage:
        raise TypeError("evidence_storage must be the canonical A2 EvidenceStorage")
    if (
        not isinstance(evidence_snapshot_id, str)
        or not evidence_snapshot_id.strip()
        or evidence_snapshot_id != evidence_snapshot_id.strip()
    ):
        raise ValueError("A2 evidence snapshot_id must not be blank")
    evidence_snapshot = evidence_storage.load_current()
    if evidence_snapshot is None:
        raise ValueError("canonical A2 evidence snapshot is unavailable or invalid")
    if evidence_snapshot.snapshot_id != evidence_snapshot_id:
        raise ValueError("canonical A2 evidence snapshot_id does not match the requested snapshot")
    if evidence_snapshot.raw_snapshot_id != raw_snapshot_id:
        raise ValueError("A2 evidence raw_snapshot_id does not match the refresh raw_snapshot_id")
    evidence_index = _evidence_index(evidence_snapshot)
    template = get_industry_template(industry_id)
    allowed_metrics = set(template.cycle_metric_ids)
    rows = tuple(observations)
    groups: dict[str, list[RawMetricObservation]] = {}
    expired: list[IndustryMetricObservation] = []
    for row in rows:
        if not isinstance(row, RawMetricObservation):
            raise TypeError("observations must contain RawMetricObservation")
        if row.industry_id != industry_id:
            raise ValueError("raw observation industry_id mismatch")
        if row.metric_id not in allowed_metrics:
            raise ValueError("raw observation metric_id is not in the industry template")
        if row.provider_value.fetched_at.tzinfo is None or row.provider_value.fetched_at.utcoffset() is None:
            raise ValueError("provider fetched_at must be timezone-aware")
        resolved = evidence_index.get((row.decision.event_id, row.decision.evidence_id))
        if resolved is None:
            raise ValueError("evidence decision is absent from the bound A2 evidence snapshot")
        item = resolved.item
        if (
            item.content_source != row.identity.content_source
            or item.origin_cluster != row.identity.origin_cluster
            or item.collector_source != row.identity.collector_source
            or item.canonical_url != row.identity.final_url
            or item.is_official != row.identity.is_official_attested
        ):
            raise ValueError("A2 evidence identity does not match the raw observation source")
        if row.expires_at <= now:
            expired.append(_observation(
                row,
                (_evidence_reference(row, resolved),),
                verification=VerificationStatus.UNVERIFIED,
                freshness=FreshnessStatus.EXPIRED,
                raw_snapshot_id=raw_snapshot_id,
                evidence_snapshot_id=evidence_snapshot_id,
            ))
            continue
        groups.setdefault(row.metric_id, []).append(row)

    trusted: list[IndustryMetricObservation] = []
    unverified: list[IndustryMetricObservation] = []
    conflicting: list[ConflictingObservation] = []
    for metric_id in sorted(groups):
        metric_rows = groups[metric_id]
        resolved_rows = [evidence_index[(row.decision.event_id, row.decision.evidence_id)] for row in metric_rows]
        evidence = tuple(
            _evidence_reference(row, resolved)
            for row, resolved in zip(metric_rows, resolved_rows, strict=True)
        )
        if len({item.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("evidence_id must be unique within one metric")
        supporting = [
            row for row, resolved in zip(metric_rows, resolved_rows, strict=True)
            if resolved.item.supports_claim and metric_id in resolved.item.supports_fields
        ]
        contradicting = [
            row for row, resolved in zip(metric_rows, resolved_rows, strict=True)
            if resolved.contradicts_claim
        ]
        support_claims = {
            (
                _scalar_value(row.provider_value.value),
                row.provider_value.unit.strip().casefold(),
                row.provider_value.as_of_date,
                row.provider_value.frequency.strip().casefold(),
                row.methodology.strip(),
            )
            for row in supporting
        }
        event_conflict = any(
            resolved.event_status is A2VerificationStatus.CONFLICTING for resolved in resolved_rows
        )
        if len(metric_rows) < 2 and (contradicting or event_conflict):
            raise ValueError("conflicting metric requires two per-source values")
        if len(metric_rows) >= 2 and ((supporting and contradicting) or len(support_claims) > 1 or event_conflict):
            conflicting.append(ConflictingObservation(
                industry_id=industry_id,
                metric_id=metric_id,
                aggregate_value=None,
                source_values=tuple(
                    ConflictingSourceValue(
                        evidence_id=row.decision.evidence_id,
                        source_family_id=row.identity.source_family_id,
                        value=_scalar_value(row.provider_value.value),
                        unit=row.provider_value.unit or None,
                        as_of_date=(
                            row.provider_value.as_of_date.isoformat()
                            if row.provider_value.as_of_date else None
                        ),
                    )
                    for row in metric_rows
                ),
            ))
            continue

        official = next((
            row for row, resolved in zip(metric_rows, resolved_rows, strict=True)
            if row in supporting
            and resolved.event_status is A2VerificationStatus.VERIFIED
            and resolved.item.is_official
        ), None)
        if official is not None:
            trusted.append(_observation(
                official,
                evidence,
                verification=VerificationStatus.VERIFIED,
                freshness=FreshnessStatus.FRESH,
                raw_snapshot_id=raw_snapshot_id,
                evidence_snapshot_id=evidence_snapshot_id,
            ))
            continue

        families = tuple(sorted({row.identity.source_family_id for row in supporting}))
        content_sources = tuple(sorted({row.identity.content_source for row in supporting}))
        origins = tuple(sorted({row.identity.origin_cluster for row in supporting}))
        corroborated_by_a2 = all(
            resolved.event_status is A2VerificationStatus.CORROBORATED for resolved in resolved_rows
        )
        if corroborated_by_a2 and len(families) >= 2 and len(content_sources) >= 2 and len(origins) >= 2:
            trusted.append(_observation(
                supporting[0],
                evidence,
                verification=VerificationStatus.CORROBORATED,
                freshness=FreshnessStatus.FRESH,
                raw_snapshot_id=raw_snapshot_id,
                evidence_snapshot_id=evidence_snapshot_id,
                independent_source_families=families,
                independent_content_sources=content_sources,
                independent_origin_clusters=origins,
            ))
            continue

        representative = supporting[0] if supporting else metric_rows[0]
        unverified.append(_observation(
            representative,
            evidence,
            verification=VerificationStatus.UNVERIFIED,
            freshness=FreshnessStatus.FRESH,
            raw_snapshot_id=raw_snapshot_id,
            evidence_snapshot_id=evidence_snapshot_id,
        ))

    panel = CandidateEvidencePanel(
        industry_id=industry_id,
        candidate_snapshot_id=candidate_snapshot_id,
        counts=CandidateEvidenceCounts(
            unverified=len(unverified),
            conflicting=len(conflicting),
            unverified_events=0,
            conflicting_events=0,
        ),
        unverified=tuple(unverified),
        conflicting=tuple(conflicting),
        unverified_events=(),
        conflicting_events=(),
    )
    return AdmissionProjection(industry_id, rows, tuple(trusted), panel, tuple(expired))
