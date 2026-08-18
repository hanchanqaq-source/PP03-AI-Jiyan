from __future__ import annotations

from datetime import datetime

from .claim_fields import extract_core_claim, extract_key_fields
from .evidence_matcher import match_evidence
from .models import EvidenceEvent, EvidenceItem, StatusTransition, VerificationStatus


_REASONS = {
    VerificationStatus.DISPROVED: "明确官方反证否定核心主张",
    VerificationStatus.CORRECTED: "官方文件修正了先前版本",
    VerificationStatus.CONFLICTING: "可靠证据之间存在不一致",
    VerificationStatus.VERIFIED: "明确官方证据支持核心主张",
    VerificationStatus.CORROBORATED: "至少两个独立来源链一致支持核心主张",
    VerificationStatus.UNVERIFIED: "证据不足，核心主张保持待核验",
}


def _status(match) -> VerificationStatus:
    if match.official_denial:
        return VerificationStatus.DISPROVED
    if match.official_correction:
        return VerificationStatus.CORRECTED
    if match.has_conflict:
        return VerificationStatus.CONFLICTING
    if match.official_support:
        return VerificationStatus.VERIFIED
    if match.independent_support_count >= 2:
        return VerificationStatus.CORROBORATED
    return VerificationStatus.UNVERIFIED


def verify_event(
    event,
    evidence: list[EvidenceItem] | tuple[EvidenceItem, ...],
    *,
    previous: EvidenceEvent | None,
    now: datetime,
) -> EvidenceEvent:
    core_claim = extract_core_claim(event.title, event.summary)
    key_fields = extract_key_fields(event.title, event.summary)
    match = match_evidence(core_claim, key_fields, evidence)
    status = _status(match)
    reason = _REASONS[status]
    history = tuple(previous.status_history) if previous else ()
    if previous is None or previous.verification_status != status or previous.verification_reason != reason:
        history += (StatusTransition(
            from_status=previous.verification_status if previous else None,
            to_status=status,
            changed_at=now,
            reason=reason,
        ),)
    published = getattr(event, "published_at_latest", None) or getattr(event, "published_at_first", None)
    tags = tuple((str(row.get("id") or ""), str(row.get("name") or "")) for row in getattr(event, "related_tags", []) if row.get("id"))
    return EvidenceEvent(
        event_id=event.event_id,
        title=event.title,
        summary=event.summary,
        category=event.category,
        related_tags=tags,
        published_at=published,
        core_claim=core_claim,
        verification_status=status,
        verification_reason=reason,
        verified_at=now,
        evidence_as_of=max((item.published_at for item in evidence if item.published_at), default=now),
        key_fields=match.key_fields,
        primary_evidence=match.primary_evidence,
        independent_evidence=match.independent_evidence,
        syndicated_copies=match.syndicated_copies,
        contradicting_evidence=match.contradicting_evidence,
        status_history=history,
    )
