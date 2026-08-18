from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import replace
from difflib import SequenceMatcher

from .claim_fields import extract_core_claim, extract_key_fields
from .models import EvidenceItem, FieldVerificationStatus, KeyField, MatchResult, SourceRole


_CORRECTION_RE = re.compile(r"更正|修正|勘误|correction|corrigendum", re.I)
_DENIAL_RE = re.compile(r"否认|不实|不成立|不存在|澄清.{0,20}(?:不实|错误)|den(?:y|ies|ied)|false", re.I)


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)


def _supports(core_claim: str, item: EvidenceItem) -> bool:
    candidate = extract_core_claim(item.title, item.excerpt)
    left = _normalized_text(core_claim)
    right = _normalized_text(candidate)
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.58


def _content_fingerprint(item: EvidenceItem) -> str:
    value = _normalized_text(f"{item.title}\0{item.excerpt}")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _partition_evidence(core_claim: str, evidence: list[EvidenceItem]):
    representatives: list[EvidenceItem] = []
    syndicated: list[EvidenceItem] = []
    seen_fingerprints: set[str] = set()
    for raw in evidence:
        support = _supports(core_claim, raw)
        item = replace(raw, supports_claim=support)
        fingerprint = _content_fingerprint(item)
        if fingerprint in seen_fingerprints:
            syndicated.append(replace(item, source_role=SourceRole.SYNDICATED))
        else:
            seen_fingerprints.add(fingerprint)
            representatives.append(item)
    return representatives, syndicated


def _evidence_field_values(items: list[EvidenceItem]) -> dict[str, dict[str, list[EvidenceItem]]]:
    values: dict[str, dict[str, list[EvidenceItem]]] = {}
    for item in items:
        for field in extract_key_fields(item.title, item.excerpt):
            values.setdefault(field.field_name, {}).setdefault(field.normalized_value, []).append(item)
    return values


def match_evidence(core_claim: str, fields: tuple[KeyField, ...] | list[KeyField], evidence: list[EvidenceItem] | tuple[EvidenceItem, ...]) -> MatchResult:
    representatives, syndicated = _partition_evidence(core_claim, list(evidence))
    blob_by_item = {item.evidence_id: f"{item.title} {item.excerpt}" for item in representatives}
    official_correction = any(item.is_official and _CORRECTION_RE.search(blob_by_item[item.evidence_id]) for item in representatives)
    official_denial = any(item.is_official and _DENIAL_RE.search(blob_by_item[item.evidence_id]) for item in representatives)
    supporting = [item for item in representatives if item.supports_claim]
    primary = [item for item in representatives if item.is_official]
    independent = [item for item in supporting if not item.is_official]
    field_values = _evidence_field_values(representatives)
    has_conflict = any(len(values) > 1 for values in field_values.values())
    contradicting_ids: set[str] = set()
    if has_conflict:
        for values in field_values.values():
            if len(values) > 1:
                contradicting_ids.update(item.evidence_id for group in values.values() for item in group)
    updated_fields: list[KeyField] = []
    for field in fields:
        values = field_values.get(field.field_name, {})
        matching = values.get(field.normalized_value, [])
        different = [item for value, group in values.items() if value != field.normalized_value for item in group]
        if different and matching:
            ids = tuple(sorted({item.evidence_id for item in matching + different}))
            updated_fields.append(replace(
                field,
                verification_status=FieldVerificationStatus.CONFLICTING,
                evidence_ids=ids,
                reason="可靠证据对该字段给出不同值",
            ))
            continue
        official_matches = [item for item in matching if item.is_official]
        independent_origins = {item.origin_cluster for item in matching if not item.is_official}
        if official_matches:
            updated_fields.append(replace(
                field,
                verification_status=FieldVerificationStatus.VERIFIED,
                evidence_ids=tuple(sorted(item.evidence_id for item in official_matches)),
                reason="字段值与官方证据一致",
            ))
        elif len(independent_origins) >= 2:
            updated_fields.append(replace(
                field,
                verification_status=FieldVerificationStatus.CORROBORATED,
                evidence_ids=tuple(sorted(item.evidence_id for item in matching)),
                reason="字段值获得两个独立来源链一致支持",
            ))
        else:
            updated_fields.append(field)
    official_support = any(item.is_official and item.supports_claim for item in representatives)
    independent_origins = {item.origin_cluster for item in independent}
    return MatchResult(
        key_fields=tuple(updated_fields),
        primary_evidence=tuple(primary),
        independent_evidence=tuple(independent),
        syndicated_copies=tuple(syndicated),
        contradicting_evidence=tuple(item for item in representatives if item.evidence_id in contradicting_ids),
        official_support=official_support,
        independent_support_count=len(independent_origins),
        has_conflict=has_conflict,
        official_correction=official_correction,
        official_denial=official_denial,
    )
