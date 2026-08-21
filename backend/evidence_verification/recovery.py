"""Allowlisted, evidence-only recovery into the bounded event archive."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .archive import (
    ArchiveBatchOutcome,
    ArchiveRawAuthorityError,
    EvidenceArchive,
    _MAX_BATCH_SNAPSHOTS,
    _archive_document,
    _archive_public_url,
    _preflight_snapshot_input,
    _raw_input_canonical,
    _raw_input_event_digest,
)
from .models import (
    EvidenceEvent,
    EvidenceItem,
    EvidenceSnapshot,
    FieldVerificationStatus,
    KeyField,
    SourceRole,
    StatusTransition,
    VerificationStatus,
)
from .storage import evidence_snapshot_from_document, event_document, snapshot_document
from news_pipeline.storage import _id as _pipeline_opaque_id


_MAX_FILES = 128
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 10_000
_MAX_NODES = 100_000
_MAX_DIRECTORY_ENTRIES = 512
_MAX_DIAGNOSTIC_COUNT = 1_000_000
_HISTORY_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")
_LEGACY_NAME = re.compile(r"^evidence-[A-Za-z0-9._-]{1,80}\.json$")
_EVENT_ID = re.compile(r"^[a-f0-9]{20}$")
_KNOWN_STATUSES = frozenset(status.value for status in VerificationStatus)
_CACHE_SOURCE_ORDER = {
    "evidence_current": 0,
    "evidence_history": 1,
    "legacy_snapshot": 2,
    "radar_cache": 3,
}
_STATUS_ORDER = {
    VerificationStatus.UNVERIFIED.value: 0,
    VerificationStatus.VERIFIED.value: 1,
    VerificationStatus.CORROBORATED.value: 2,
    VerificationStatus.CONFLICTING.value: 3,
    VerificationStatus.CORRECTED.value: 4,
    VerificationStatus.DISPROVED.value: 5,
}
_MAX_REFETCH_EVENTS = 1
_MAX_REFETCH_ITEMS = 512
_MAX_REFETCH_NODES = 4_096
_MAX_REFETCH_TEXT_BYTES = 256 * 1_024
_MAX_REFETCH_TEXT_CHARS = 8_192
_MAX_REFETCH_DOCUMENT_BYTES = 512 * 1_024
_MAX_REFETCH_DEPTH = 24


class _NodeLimitExceeded(ValueError):
    pass


class _RefetchLimitExceeded(ValueError):
    pass


class _RecoveryInputInvalid(ValueError):
    pass


def _new_refetch_budget() -> dict[str, int | bool]:
    return {
        "events": _MAX_BATCH_SNAPSHOTS,
        "items": _MAX_REFETCH_ITEMS,
        "nodes": _MAX_REFETCH_NODES,
        "text_bytes": _MAX_REFETCH_TEXT_BYTES,
        "document_bytes": _MAX_REFETCH_DOCUMENT_BYTES,
        "exhausted": False,
    }


def _canonical_event_id(value: object) -> str | None:
    if type(value) is not str:
        return None
    normalized = value.strip()
    if normalized != value or _EVENT_ID.fullmatch(normalized) is None:
        return None
    return normalized


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("invalid recovery document")
        result[key] = value
    return result


def _bounded_int(raw: str) -> int:
    if len(raw) > 20:
        raise ValueError("invalid recovery number")
    value = int(raw)
    if value < -(2**63) or value > 2**63 - 1:
        raise ValueError("invalid recovery number")
    return value


def _walk(value: object, *, depth: int = 0, maximum: int = _MAX_NODES) -> int:
    if depth > 24:
        raise ValueError("invalid recovery document")
    if value is None or type(value) in {bool, int, str}:
        return 1
    if type(value) is list:
        total = 1
        for item in value:
            total += _walk(item, depth=depth + 1, maximum=maximum - total)
            if total > maximum:
                raise _NodeLimitExceeded("recovery node limit exceeded")
        return total
    if type(value) is dict:
        total = 1
        for key, item in value.items():
            if type(key) is not str or len(key) > 256:
                raise ValueError("invalid recovery document")
            total += 1 + _walk(item, depth=depth + 1, maximum=maximum - total - 1)
            if total > maximum:
                raise _NodeLimitExceeded("recovery node limit exceeded")
        return total
    raise ValueError("invalid recovery document")


def _decode_json(raw: bytes, *, maximum_nodes: int = _MAX_NODES) -> tuple[object, int]:
    if len(raw) > _MAX_FILE_BYTES:
        raise ValueError("invalid recovery document")
    if type(maximum_nodes) is not int or maximum_nodes < 1:
        raise _NodeLimitExceeded("recovery node limit exceeded")
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_strict_object,
        parse_int=_bounded_int,
        parse_float=lambda _value: (_ for _ in ()).throw(ValueError("invalid recovery number")),
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid recovery number")),
    )
    nodes = _walk(value, maximum=maximum_nodes)
    return value, nodes


def _aware_time(value: object) -> datetime | None:
    if type(value) is not str or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _public_link(value: object) -> str | None:
    if type(value) is not str or not value or len(value) > 4_096:
        return None
    try:
        return value if _archive_public_url(value) == value else None
    except (TypeError, ValueError):
        return None


def _event_links(event: EvidenceEvent) -> tuple[str, ...]:
    links: list[str] = []
    for group in (
        event.primary_evidence,
        event.independent_evidence,
        event.syndicated_copies,
        event.contradicting_evidence,
    ):
        for item in group:
            if item.canonical_url:
                links.append(item.canonical_url)
    return tuple(links)


def _refetch_text(value: str, budget: dict[str, int | bool]) -> str:
    if type(value) is not str or len(value) > _MAX_REFETCH_TEXT_CHARS:
        raise _RefetchLimitExceeded("invalid public refetch text")
    encoded = _refetch_utf8(value)
    if len(encoded) > int(budget["text_bytes"]):
        budget["exhausted"] = True
        raise _RefetchLimitExceeded("public refetch text limit exceeded")
    budget["text_bytes"] = int(budget["text_bytes"]) - len(encoded)
    return value


def _refetch_utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _RefetchLimitExceeded("invalid public refetch text") from error


def _refetch_node(budget: dict[str, int | bool]) -> None:
    if int(budget["nodes"]) <= 0:
        budget["exhausted"] = True
        raise _RefetchLimitExceeded("public refetch node limit exceeded")
    budget["nodes"] = int(budget["nodes"]) - 1


def _refetch_item(budget: dict[str, int | bool]) -> None:
    if int(budget["items"]) <= 0:
        budget["exhausted"] = True
        raise _RefetchLimitExceeded("public refetch item limit exceeded")
    budget["items"] = int(budget["items"]) - 1


def _validated_refetch_text(value: object, budget: dict[str, int | bool]) -> str:
    _refetch_node(budget)
    if type(value) is not str:
        raise _RefetchLimitExceeded("invalid public refetch text")
    return _refetch_text(value, budget)


def _validated_refetch_time(
    value: object,
    budget: dict[str, int | bool],
    *,
    optional: bool = False,
) -> datetime | None:
    _refetch_node(budget)
    if value is None and optional:
        return None
    if type(value) is not datetime or value.tzinfo is None:
        raise _RefetchLimitExceeded("invalid public refetch timestamp")
    try:
        if value.utcoffset() is None:
            raise _RefetchLimitExceeded("invalid public refetch timestamp")
    except Exception as error:
        raise _RefetchLimitExceeded("invalid public refetch timestamp") from error
    return value


def _validated_refetch_enum(
    value: object,
    enum_type: type[VerificationStatus] | type[FieldVerificationStatus] | type[SourceRole],
    budget: dict[str, int | bool],
) -> object:
    _refetch_node(budget)
    if type(value) is not enum_type:
        raise _RefetchLimitExceeded("invalid public refetch enum")
    return value


def _validated_refetch_bool(value: object, budget: dict[str, int | bool]) -> bool:
    _refetch_node(budget)
    if type(value) is not bool:
        raise _RefetchLimitExceeded("invalid public refetch boolean")
    return value


def _bounded_refetch_iterable(
    value: object,
    budget: dict[str, int | bool],
    *,
    maximum: int,
) -> list[object]:
    if maximum < 0:
        raise _RefetchLimitExceeded("invalid public refetch shape")
    try:
        iterator = iter(value)
    except Exception as error:
        raise _RefetchLimitExceeded("invalid public refetch iterable") from error
    result: list[object] = []
    while True:
        try:
            item = next(iterator)
        except StopIteration:
            return result
        except Exception as error:
            raise _RefetchLimitExceeded("public refetch iterable failed") from error
        if len(result) >= maximum:
            raise _RefetchLimitExceeded("public refetch item limit exceeded")
        _refetch_item(budget)
        result.append(item)


def _materialize_refetch_json(
    value: object,
    budget: dict[str, int | bool],
    *,
    depth: int = 0,
) -> object:
    if depth > _MAX_REFETCH_DEPTH:
        raise _RefetchLimitExceeded("public refetch depth limit exceeded")
    _refetch_node(budget)
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value < -(2**63) or value > 2**63 - 1:
            raise _RefetchLimitExceeded("invalid public refetch number")
        return value
    if type(value) is float:
        raise _RefetchLimitExceeded("invalid public refetch number")
    if type(value) is str:
        return _refetch_text(value, budget)
    if type(value) is dict:
        result: dict[str, object] = {}
        for key, item in value.items():
            _refetch_item(budget)
            if type(key) is not str or key in result:
                raise _RefetchLimitExceeded("invalid public refetch object")
            result[_refetch_text(key, budget)] = _materialize_refetch_json(
                item,
                budget,
                depth=depth + 1,
            )
        return result
    if type(value) in {bytes, bytearray, memoryview}:
        raise _RefetchLimitExceeded("invalid public refetch value")
    items = _bounded_refetch_iterable(value, budget, maximum=_MAX_REFETCH_ITEMS)
    return [
        _materialize_refetch_json(item, budget, depth=depth + 1)
        for item in items
    ]


def _normalized_refetch_event(
    event: object,
    budget: dict[str, int | bool],
) -> EvidenceEvent:
    if type(event) is not EvidenceEvent:
        raise _RefetchLimitExceeded("invalid public refetch event")
    _refetch_node(budget)

    event_id = _validated_refetch_text(event.event_id, budget)
    if _canonical_event_id(event_id) is None:
        raise _RefetchLimitExceeded("invalid public refetch event identity")
    title = _validated_refetch_text(event.title, budget)
    summary = _validated_refetch_text(event.summary, budget)
    category = _validated_refetch_text(event.category, budget)
    core_claim = _validated_refetch_text(event.core_claim, budget)
    verification_reason = _validated_refetch_text(event.verification_reason, budget)
    holding_relevance = _validated_refetch_text(event.holding_relevance, budget)
    published_at = _validated_refetch_time(event.published_at, budget, optional=True)
    verified_at = _validated_refetch_time(event.verified_at, budget)
    evidence_as_of = _validated_refetch_time(event.evidence_as_of, budget)
    verification_status = _validated_refetch_enum(
        event.verification_status,
        VerificationStatus,
        budget,
    )

    tags: list[tuple[str, str]] = []
    for tag in _bounded_refetch_iterable(event.related_tags, budget, maximum=_MAX_REFETCH_ITEMS):
        pair = _bounded_refetch_iterable(tag, budget, maximum=2)
        if len(pair) != 2:
            raise _RefetchLimitExceeded("invalid public refetch tag")
        tags.append((
            _validated_refetch_text(pair[0], budget),
            _validated_refetch_text(pair[1], budget),
        ))

    key_fields: list[KeyField] = []
    for field_value in _bounded_refetch_iterable(
        event.key_fields,
        budget,
        maximum=_MAX_REFETCH_ITEMS,
    ):
        if type(field_value) is not KeyField:
            raise _RefetchLimitExceeded("invalid public refetch key field")
        _refetch_node(budget)
        evidence_ids = _bounded_refetch_iterable(
            field_value.evidence_ids,
            budget,
            maximum=_MAX_REFETCH_ITEMS,
        )
        key_fields.append(KeyField(
            field_name=_validated_refetch_text(field_value.field_name, budget),
            raw_value=_validated_refetch_text(field_value.raw_value, budget),
            normalized_value=_validated_refetch_text(field_value.normalized_value, budget),
            verification_status=_validated_refetch_enum(
                field_value.verification_status,
                FieldVerificationStatus,
                budget,
            ),
            evidence_ids=tuple(
                _validated_refetch_text(value, budget) for value in evidence_ids
            ),
            reason=_validated_refetch_text(field_value.reason, budget),
        ))

    def evidence_group(value: object) -> tuple[EvidenceItem, ...]:
        result: list[EvidenceItem] = []
        for item_value in _bounded_refetch_iterable(value, budget, maximum=_MAX_REFETCH_ITEMS):
            if type(item_value) is not EvidenceItem:
                raise _RefetchLimitExceeded("invalid public refetch evidence")
            _refetch_node(budget)
            supports_fields = _bounded_refetch_iterable(
                item_value.supports_fields,
                budget,
                maximum=_MAX_REFETCH_ITEMS,
            )
            result.append(EvidenceItem(
                evidence_id=_validated_refetch_text(item_value.evidence_id, budget),
                content_source=_validated_refetch_text(item_value.content_source, budget),
                collector_source=_validated_refetch_text(item_value.collector_source, budget),
                canonical_url=_validated_refetch_text(item_value.canonical_url, budget),
                published_at=_validated_refetch_time(
                    item_value.published_at,
                    budget,
                    optional=True,
                ),
                source_role=_validated_refetch_enum(item_value.source_role, SourceRole, budget),
                origin_cluster=_validated_refetch_text(item_value.origin_cluster, budget),
                supports_claim=_validated_refetch_bool(item_value.supports_claim, budget),
                supports_fields=tuple(
                    _validated_refetch_text(field_name, budget)
                    for field_name in supports_fields
                ),
                contradicts_claim=_validated_refetch_bool(
                    item_value.contradicts_claim,
                    budget,
                ),
                is_official=_validated_refetch_bool(item_value.is_official, budget),
                title=_validated_refetch_text(item_value.title, budget),
                excerpt=_validated_refetch_text(item_value.excerpt, budget),
            ))
        return tuple(result)

    transitions = _bounded_refetch_iterable(
        event.status_history,
        budget,
        maximum=_MAX_REFETCH_ITEMS,
    )
    normalized_transitions: list[StatusTransition] = []
    for transition in transitions:
        if type(transition) is not StatusTransition:
            raise _RefetchLimitExceeded("invalid public refetch status history")
        _refetch_node(budget)
        from_status = transition.from_status
        if from_status is not None:
            from_status = _validated_refetch_enum(from_status, VerificationStatus, budget)
        else:
            _refetch_node(budget)
        normalized_transitions.append(StatusTransition(
            from_status=from_status,
            to_status=_validated_refetch_enum(
                transition.to_status,
                VerificationStatus,
                budget,
            ),
            changed_at=_validated_refetch_time(transition.changed_at, budget),
            reason=_validated_refetch_text(transition.reason, budget),
        ))
    return EvidenceEvent(
        event_id=event_id,
        title=title,
        summary=summary,
        category=category,
        related_tags=tuple(tags),
        published_at=published_at,
        core_claim=core_claim,
        verification_status=verification_status,
        verification_reason=verification_reason,
        verified_at=verified_at,
        evidence_as_of=evidence_as_of,
        key_fields=tuple(key_fields),
        primary_evidence=evidence_group(event.primary_evidence),
        independent_evidence=evidence_group(event.independent_evidence),
        syndicated_copies=evidence_group(event.syndicated_copies),
        contradicting_evidence=evidence_group(event.contradicting_evidence),
        status_history=tuple(normalized_transitions),
        holding_relevance=holding_relevance,
    )


def _shallow_refetch_marker(value: object) -> _RawGroupMarker | None:
    if type(value) is EvidenceSnapshot:
        marker = _raw_group_marker_from_document(
            {
                "snapshot_id": value.snapshot_id,
                "raw_snapshot_id": value.raw_snapshot_id,
                "events": [],
            },
            invalid=True,
            allow_empty=True,
        )
        if marker is None:
            return None
        try:
            events = value.events
            if type(events) not in {tuple, list}:
                return marker
            shallow_events = events[:_MAX_REFETCH_EVENTS + 1]
            event_ids = tuple(
                event_id
                for event in shallow_events
                for event_id in (
                    _canonical_event_id(event.event_id)
                    if type(event) is EvidenceEvent
                    else None,
                )
                if event_id is not None
            )
            generated_at = value.generated_at
            if type(generated_at) is not datetime:
                return replace(
                    marker,
                    declared_event_ids=tuple(sorted(set(event_ids))),
                )
            try:
                if generated_at.tzinfo is None or generated_at.utcoffset() is None:
                    return replace(
                        marker,
                        declared_event_ids=tuple(sorted(set(event_ids))),
                    )
                normalized_time = generated_at.astimezone(timezone.utc)
            except Exception:
                return replace(
                    marker,
                    declared_event_ids=tuple(sorted(set(event_ids))),
                )
            invalid = (
                len(shallow_events) != 1
                or len(event_ids) != len(shallow_events)
                or len(event_ids) != len(set(event_ids))
            )
            return replace(
                marker,
                generated_at=normalized_time,
                declared_event_ids=tuple(sorted(set(event_ids))),
                invalid=invalid,
            )
        except Exception:
            return marker
    if type(value) is dict:
        marker = _raw_group_marker_from_document(
            {
                "snapshot_id": value.get("snapshot_id"),
                "raw_snapshot_id": value.get("raw_snapshot_id"),
                "events": [],
            },
            invalid=True,
            allow_empty=True,
        )
        if marker is None:
            return None
        try:
            events = value.get("events")
            if type(events) is not list:
                return marker
            shallow_events = events[:_MAX_REFETCH_EVENTS + 1]
            event_ids = tuple(
                event_id
                for row in shallow_events
                for event_id in (
                    _canonical_event_id(row.get("event_id"))
                    if type(row) is dict
                    else None,
                )
                if event_id is not None
            )
            generated_at = _aware_time(value.get("generated_at"))
            invalid = (
                generated_at is None
                or len(shallow_events) != 1
                or len(event_ids) != len(shallow_events)
                or len(event_ids) != len(set(event_ids))
            )
            return replace(
                marker,
                generated_at=generated_at,
                declared_event_ids=tuple(sorted(set(event_ids))),
                invalid=invalid,
            )
        except Exception:
            return marker
    return None


def _take_refetch_document_bytes(
    payload: bytes,
    budget: dict[str, int | bool],
) -> None:
    if len(payload) > int(budget["document_bytes"]):
        budget["exhausted"] = True
        raise _RefetchLimitExceeded("public refetch byte limit exceeded")
    budget["document_bytes"] = int(budget["document_bytes"]) - len(payload)


def _bounded_refetch_snapshot(
    value: object,
    budget: dict[str, int | bool],
) -> tuple[EvidenceSnapshot | None, _RawGroupMarker | None]:
    marker = _shallow_refetch_marker(value)
    try:
        if type(value) is EvidenceSnapshot:
            _refetch_node(budget)
            snapshot_id = _pipeline_opaque_id(
                _validated_refetch_text(value.snapshot_id, budget),
                "snapshot_id",
            )
            raw_snapshot_id = _pipeline_opaque_id(
                _validated_refetch_text(value.raw_snapshot_id, budget),
                "raw_snapshot_id",
            )
            generated_at = _validated_refetch_time(value.generated_at, budget)
            if type(value.recovery_metadata) is not dict:
                raise _RefetchLimitExceeded("invalid public refetch recovery metadata")
            source_snapshot_id = value.recovery_metadata.get("source_snapshot_id")
            if source_snapshot_id is not None:
                _pipeline_opaque_id(
                    _validated_refetch_text(source_snapshot_id, budget),
                    "source_snapshot_id",
                )
            _materialize_refetch_json(value.recovery_metadata, budget)
            event_values = _bounded_refetch_iterable(
                value.events,
                budget,
                maximum=_MAX_REFETCH_EVENTS,
            )
            if len(event_values) != 1:
                return None, marker
            if int(budget["events"]) <= 0:
                budget["exhausted"] = True
                raise _RefetchLimitExceeded("public refetch event limit exceeded")
            budget["events"] = int(budget["events"]) - 1
            normalized = EvidenceSnapshot(
                snapshot_id=snapshot_id,
                raw_snapshot_id=raw_snapshot_id,
                generated_at=generated_at,
                events=(_normalized_refetch_event(event_values[0], budget),),
            )
            encoded = _refetch_utf8(json.dumps(
                _raw_input_canonical(normalized),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ))
            _take_refetch_document_bytes(encoded, budget)
            return normalized if _snapshot_is_complete(normalized) else None, marker
        if type(value) is not dict or "events" not in value:
            return None, marker
        root: dict[str, object] = {}
        for key, item in value.items():
            if len(root) >= _MAX_REFETCH_ITEMS or type(key) is not str:
                raise _RefetchLimitExceeded("invalid public refetch object")
            if key == "events":
                event_values = _bounded_refetch_iterable(
                    item,
                    budget,
                    maximum=_MAX_REFETCH_EVENTS,
                )
                if len(event_values) != 1:
                    return None, marker
                if int(budget["events"]) <= 0:
                    budget["exhausted"] = True
                    raise _RefetchLimitExceeded("public refetch event limit exceeded")
                budget["events"] = int(budget["events"]) - 1
                root[key] = event_values
            else:
                root[key] = item
        materialized = _materialize_refetch_json(root, budget)
        encoded = _refetch_utf8(json.dumps(
            materialized,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ))
        _take_refetch_document_bytes(encoded, budget)
        marker = _raw_group_marker_from_document(materialized, invalid=False) or marker
        return HistoryRecovery._parse_snapshot(materialized), marker
    except _RefetchLimitExceeded as error:
        error.raw_marker = marker
        raise
    except Exception as error:
        closed = _RefetchLimitExceeded("invalid public refetch value")
        closed.raw_marker = marker
        raise closed from error


def _preflight_refetch_candidates(
    candidates: list[_RefetchCandidate],
    *,
    materialized_snapshots: tuple[EvidenceSnapshot, ...] = (),
) -> bool:
    if len(candidates) + len(materialized_snapshots) > _MAX_BATCH_SNAPSHOTS:
        return False
    budget = _new_refetch_budget()

    def take_identity(document: dict[str, object], values: tuple[str, ...]) -> None:
        if int(budget["events"]) <= 0:
            budget["exhausted"] = True
            raise _RefetchLimitExceeded("public refetch event limit exceeded")
        budget["events"] = int(budget["events"]) - 1
        _refetch_item(budget)
        _refetch_node(budget)
        for value in values:
            _validated_refetch_text(value, budget)
        encoded = _refetch_utf8(json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ))
        _take_refetch_document_bytes(encoded, budget)

    try:
        for snapshot in materialized_snapshots:
            if type(snapshot) is not EvidenceSnapshot or len(snapshot.events) != 1:
                raise _RefetchLimitExceeded("invalid materialized recovery snapshot")
            event = snapshot.events[0]
            published_at = event.published_at
            if (
                type(published_at) is not datetime
                or published_at.tzinfo is None
                or published_at.utcoffset() is None
            ):
                raise _RefetchLimitExceeded("invalid materialized recovery timestamp")
            public_links = _event_links(event)
            if not public_links:
                raise _RefetchLimitExceeded("invalid materialized recovery evidence")
            published = published_at.isoformat()
            status = event.verification_status.value
            take_identity(
                {
                    "event_id": event.event_id,
                    "title": event.title,
                    "public_links": list(public_links),
                    "published_at": published,
                    "verification_status": status,
                },
                (
                    event.event_id,
                    event.title,
                    *public_links,
                    published,
                    status,
                ),
            )
        for candidate in candidates:
            published = candidate.published_at.isoformat()
            take_identity(
                {
                    "event_id": candidate.event_id,
                    "title": candidate.title,
                    "public_link": candidate.public_link,
                    "published_at": published,
                    "verification_status": candidate.verification_status,
                },
                (
                    candidate.event_id,
                    candidate.title,
                    candidate.public_link,
                    published,
                    candidate.verification_status,
                ),
            )
    except _RefetchLimitExceeded:
        return False
    return True


def _event_is_complete(event: EvidenceEvent) -> bool:
    if _canonical_event_id(event.event_id) is None or not event.title.strip():
        return False
    if event.published_at is None or event.verification_status.value not in _KNOWN_STATUSES:
        return False
    links = _event_links(event)
    return bool(links) and all(_public_link(link) == link for link in links)


def _snapshot_is_complete(snapshot: EvidenceSnapshot) -> bool:
    return bool(snapshot.events) and all(_event_is_complete(event) for event in snapshot.events)


def _event_fingerprint(event: EvidenceEvent) -> str:
    try:
        return _raw_input_event_digest(event)
    except UnicodeEncodeError as error:
        raise _RecoveryInputInvalid("invalid recovery text") from error


@dataclass(frozen=True, slots=True)
class _RefetchCandidate:
    event_id: str
    title: str
    public_link: str
    published_at: datetime
    verification_status: str


@dataclass(frozen=True, slots=True)
class _CacheRecord:
    snapshot: EvidenceSnapshot
    source: str
    raw_event_set: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _RawGroupMarker:
    raw_snapshot_id: str
    snapshot_id: str | None
    generated_at: datetime | None
    declared_event_ids: tuple[str, ...]
    invalid: bool
    accounted_event_ids: tuple[str, ...] = ()


def _raw_group_marker_from_document(
    value: object,
    *,
    invalid: bool,
    accounted_event_ids: tuple[str, ...] = (),
    allow_empty: bool = False,
) -> _RawGroupMarker | None:
    if type(value) is not dict:
        return None
    events = value.get("events")
    if type(events) is not list:
        if not (invalid and allow_empty):
            return None
        events = []
    raw_value = value.get("raw_snapshot_id")
    raw_candidate = (
        value.get("snapshot_id")
        if raw_value is None or (type(raw_value) is str and raw_value == "")
        else raw_value
    )
    try:
        raw_snapshot_id = _pipeline_opaque_id(
            raw_candidate,
            "raw_snapshot_id",
        )
    except (TypeError, ValueError):
        return None
    try:
        snapshot_id = _pipeline_opaque_id(value.get("snapshot_id"), "snapshot_id")
    except (TypeError, ValueError):
        snapshot_id = None
    generated_at = _aware_time(value.get("generated_at"))
    event_ids = tuple(
        event_id
        for row in events
        for event_id in (
            _canonical_event_id(row.get("event_id")) if type(row) is dict else None,
        )
        if event_id is not None
    )
    if not event_ids and not allow_empty:
        return None
    duplicate = len(event_ids) != len(set(event_ids))
    return _RawGroupMarker(
        raw_snapshot_id=raw_snapshot_id,
        snapshot_id=snapshot_id,
        generated_at=generated_at,
        declared_event_ids=tuple(sorted(set(event_ids))),
        invalid=(
            invalid
            or not event_ids
            or duplicate
            or len(event_ids) != len(events)
        ),
        accounted_event_ids=tuple(sorted(set(accounted_event_ids))),
    )


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    cache_recovered: int = 0
    public_refetched: int = 0
    unrecoverable: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    opened_paths: tuple[Path, ...] = ()
    _snapshots: tuple[_CacheRecord, ...] = field(default=(), repr=False)
    _refetch: tuple[_RefetchCandidate, ...] = field(default=(), repr=False)
    _raw_markers: tuple[_RawGroupMarker, ...] = field(default=(), repr=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "cache_recovered": self.cache_recovered,
            "public_refetched": self.public_refetched,
            "unrecoverable": self.unrecoverable,
            "reasons": dict(sorted(self.reasons.items())),
        }


class HistoryRecovery:
    """Read only named legacy evidence inputs and import validated snapshots."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        radar_cache: str | os.PathLike[str] | None = None,
        evidence_current: str | os.PathLike[str] | None = None,
        evidence_history: Iterable[str | os.PathLike[str]] | None = None,
        legacy_snapshots: Iterable[str | os.PathLike[str]] | None = None,
        archive: EvidenceArchive | None = None,
        public_refetcher: Callable[[str], object] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(os.path.abspath(root))
        self._radar_cache = Path(radar_cache) if radar_cache is not None else self.root / "radar.json"
        self._evidence_current = (
            Path(evidence_current) if evidence_current is not None else self.root / "current.json"
        )
        self._radar_present = bool(self._present_input_paths((self._radar_cache,)))
        self._current_present = bool(self._present_input_paths((self._evidence_current,)))
        self._evidence_history, self._history_input_error = self._bounded_input_paths(evidence_history)
        self._legacy_snapshots, self._legacy_input_error = self._bounded_input_paths(legacy_snapshots)
        self._explicit_history_present = self._present_input_paths(self._evidence_history)
        self._explicit_legacy_present = self._present_input_paths(self._legacy_snapshots)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.archive = archive or EvidenceArchive(self.root, now=self._now)
        self._public_refetcher = public_refetcher

    @staticmethod
    def _add_reason(reasons: dict[str, int], reason: str, amount: int = 1) -> None:
        if type(reason) is not str or not reason or type(amount) is not int or amount < 1:
            return
        reasons[reason] = min(_MAX_DIAGNOSTIC_COUNT, reasons.get(reason, 0) + amount)

    def _take_row(self, budget: dict[str, int], reasons: dict[str, int]) -> bool:
        if budget["rows"] > 0:
            budget["rows"] -= 1
            return True
        if not budget.get("row_limit_reported"):
            self._add_reason(reasons, "record_limit")
            budget["row_limit_reported"] = 1
        return False

    def _report_node_limit(self, budget: dict[str, int], reasons: dict[str, int]) -> None:
        budget["nodes"] = 0
        if not budget.get("node_limit_reported"):
            self._add_reason(reasons, "node_limit")
            budget["node_limit_reported"] = 1

    @staticmethod
    def _bounded_input_paths(
        supplied: Iterable[str | os.PathLike[str]] | None,
    ) -> tuple[tuple[Path, ...] | None, str | None]:
        if supplied is None:
            return None, None
        paths: list[Path] = []
        try:
            iterator = iter(supplied)
        except Exception:
            return (), "path_iterator_failed"
        while len(paths) <= _MAX_FILES:
            try:
                item = next(iterator)
            except StopIteration:
                return tuple(paths), None
            except Exception:
                return tuple(paths), "path_iterator_failed"
            if len(paths) == _MAX_FILES:
                return tuple(paths), "file_limit"
            try:
                paths.append(Path(item))
            except Exception:
                return tuple(paths), "invalid_path"
        return tuple(paths), "file_limit"

    @staticmethod
    def _present_input_paths(paths: tuple[Path, ...] | None) -> frozenset[str]:
        present: set[str] = set()
        for path in paths or ():
            try:
                absolute = Path(os.path.abspath(path))
                absolute.stat(follow_symlinks=False)
            except (OSError, TypeError, ValueError):
                continue
            present.add(os.path.normcase(str(absolute)))
        return frozenset(present)

    def _default_named_files(
        self,
        directory: Path,
        pattern: re.Pattern[str],
    ) -> tuple[tuple[Path, ...], str | None]:
        chain_before = self._directory_chain(directory / "candidate")
        if chain_before is None:
            try:
                os.lstat(directory)
            except FileNotFoundError:
                return (), None
            except OSError:
                return (), "directory_scan_failed"
            return (), "unsafe_directory"
        try:
            entries: list[Path] = []
            observed = 0
            with os.scandir(directory) as scanned:
                for entry in scanned:
                    observed += 1
                    if observed > _MAX_DIRECTORY_ENTRIES:
                        return (), "directory_entry_limit"
                    if len(entries) >= _MAX_FILES:
                        return tuple(entries), "file_limit"
                    if pattern.fullmatch(entry.name):
                        entries.append(directory / entry.name)
            chain_after = self._directory_chain(directory / "candidate")
            if chain_after is None or chain_before != chain_after:
                return (), "directory_changed"
            return tuple(sorted(entries, key=lambda path: path.name)), None
        except FileNotFoundError:
            return (), "directory_scan_failed"
        except (NotADirectoryError, OSError):
            return (), "directory_scan_failed"

    @staticmethod
    def _allowed_name(kind: str, path: Path) -> bool:
        if kind == "radar":
            return path.name == "radar.json"
        if kind == "current":
            return path.name == "current.json"
        if kind == "history":
            return bool(_HISTORY_NAME.fullmatch(path.name))
        if kind == "legacy":
            return bool(_LEGACY_NAME.fullmatch(path.name))
        return False

    @staticmethod
    def _directory_chain(path: Path) -> tuple[tuple[object, ...], ...] | None:
        parts = Path(os.path.abspath(path)).parent.parts
        if not parts:
            return None
        cursor = Path(parts[0])
        chain: list[tuple[object, ...]] = []
        for component in (None, *parts[1:]):
            if component is not None:
                cursor /= component
            try:
                metadata = cursor.stat(follow_symlinks=False)
            except OSError:
                return None
            if not stat.S_ISDIR(metadata.st_mode) or getattr(metadata, "st_reparse_tag", 0):
                return None
            chain.append((
                os.path.normcase(str(cursor)),
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                getattr(metadata, "st_reparse_tag", 0),
            ))
        return tuple(chain)

    @staticmethod
    def _safe_read(path: Path, remaining: int) -> tuple[bytes | None, str | None]:
        descriptor: int | None = None
        try:
            if remaining <= 0:
                return None, "total_byte_limit"
            chain_before = HistoryRecovery._directory_chain(path)
            if chain_before is None:
                return None, "unsafe_file"
            before = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or getattr(before, "st_reparse_tag", 0)
                or before.st_nlink != 1
            ):
                return None, "unsafe_file"
            if before.st_size < 0 or before.st_size > _MAX_FILE_BYTES:
                return None, "file_too_large"
            if before.st_size > remaining:
                return None, "total_byte_limit"
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_nlink != 1
                or not stat.S_ISREG(opened.st_mode)
            ):
                return None, "unsafe_file"
            chunks: list[bytes] = []
            unread = before.st_size + 1
            while unread:
                chunk = os.read(descriptor, min(unread, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                unread -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(descriptor)
            final = path.stat(follow_symlinks=False)
            chain_after = HistoryRecovery._directory_chain(path)
            signature = lambda value: (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
                value.st_nlink,
                value.st_mode,
                getattr(value, "st_reparse_tag", 0),
            )
            if (
                signature(opened) != signature(after)
                or signature(before) != signature(final)
                or chain_after is None
                or chain_before != chain_after
            ):
                return None, "file_changed"
            if len(raw) != before.st_size:
                return None, "file_changed"
            return raw, None
        except FileNotFoundError:
            return None, "missing"
        except OSError:
            return None, "read_failed"
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @staticmethod
    def _parse_snapshot(value: object) -> EvidenceSnapshot | None:
        if type(value) is not dict:
            return None
        try:
            snapshot = evidence_snapshot_from_document(value)
            _pipeline_opaque_id(snapshot.snapshot_id, "snapshot_id")
            _pipeline_opaque_id(snapshot.raw_snapshot_id, "raw_snapshot_id")
            source_snapshot_id = snapshot.recovery_metadata.get("source_snapshot_id")
            if source_snapshot_id is not None:
                _pipeline_opaque_id(source_snapshot_id, "source_snapshot_id")
        except (KeyError, TypeError, ValueError):
            return None
        return snapshot if _snapshot_is_complete(snapshot) else None

    def _snapshot_candidates(
        self,
        value: object,
        records: list[_CacheRecord],
        markers: list[_RawGroupMarker],
        reasons: dict[str, int],
        *,
        source: str,
        budget: dict[str, int],
        first_row_precounted: bool = False,
    ) -> None:
        if not first_row_precounted and not self._take_row(budget, reasons):
            return
        valid_source_ids: list[str] = []

        def record_marker(*, invalid: bool) -> None:
            marker = _raw_group_marker_from_document(
                value,
                invalid=invalid,
                accounted_event_ids=(
                    tuple(sorted(set(valid_source_ids))) if invalid else ()
                ),
                allow_empty=invalid,
            )
            if marker is not None:
                markers.append(marker)

        if type(value) is not dict:
            self._add_reason(reasons, "missing_required_fields")
            return
        events = value.get("events")
        if type(events) is not list or not events:
            self._add_reason(reasons, "missing_required_fields")
            record_marker(invalid=True)
            return
        source_event_ids = [
            _canonical_event_id(row.get("event_id")) if type(row) is dict else None
            for row in events
        ]
        valid_source_ids = [event_id for event_id in source_event_ids if event_id is not None]

        if len(valid_source_ids) != len(set(valid_source_ids)):
            self._add_reason(reasons, "ambiguous_record", len(set(valid_source_ids)) or 1)
            record_marker(invalid=True)
            return
        parsed: list[tuple[EvidenceSnapshot, str]] = []
        invalid_rows = 0

        def append_parsed() -> None:
            binding = tuple(sorted(
                (snapshot.events[0].event_id, fingerprint)
                for snapshot, fingerprint in parsed
            ))
            records.extend(
                _CacheRecord(snapshot=snapshot, source=source, raw_event_set=binding)
                for snapshot, _fingerprint in parsed
            )

        for index, row in enumerate(events):
            if index > 0 and not self._take_row(budget, reasons):
                record_marker(invalid=True)
                return
            single = dict(value)
            single["events"] = [row]
            snapshot = self._parse_snapshot(single)
            if snapshot is None:
                self._add_reason(reasons, "missing_required_fields")
                invalid_rows += 1
                continue
            try:
                fingerprint = _event_fingerprint(snapshot.events[0])
            except _RecoveryInputInvalid:
                self._add_reason(reasons, "missing_required_fields")
                invalid_rows += 1
                continue
            parsed.append((snapshot, fingerprint))
        if invalid_rows:
            self._add_reason(reasons, "ambiguous_record", len(parsed))
            record_marker(invalid=True)
            return
        append_parsed()
        record_marker(invalid=False)

    def _radar_candidates(
        self,
        value: object,
        records: list[_CacheRecord],
        markers: list[_RawGroupMarker],
        refetch: list[_RefetchCandidate],
        reasons: dict[str, int],
        budget: dict[str, int],
    ) -> None:
        if type(value) is not dict or type(value.get("industries")) is not list:
            self._add_reason(reasons, "invalid_document")
            return
        for industry in value["industries"]:
            if type(industry) is not dict or type(industry.get("items")) is not list:
                self._add_reason(reasons, "invalid_document")
                continue
            for item in industry["items"]:
                if not self._take_row(budget, reasons):
                    return
                if type(item) is dict and "evidence_snapshot" in item:
                    self._snapshot_candidates(
                        item.get("evidence_snapshot"),
                        records,
                        markers,
                        reasons,
                        source="radar_cache",
                        budget=budget,
                        first_row_precounted=True,
                    )
                    continue
                if type(item) is not dict:
                    self._add_reason(reasons, "missing_required_fields")
                    continue
                event_id = _canonical_event_id(item.get("event_id"))
                title = item.get("title")
                link_value = item.get("original_url") or item.get("canonical_url") or item.get("url")
                published = _aware_time(item.get("published_at"))
                status_value = item.get("verification_status")
                if (
                    event_id is None
                    or type(title) is not str
                    or not title.strip()
                    or published is None
                    or type(status_value) is not str
                    or status_value not in _KNOWN_STATUSES
                    or type(link_value) is not str
                    or not link_value
                ):
                    self._add_reason(reasons, "missing_required_fields")
                    continue
                link = _public_link(link_value)
                if link is None:
                    self._add_reason(reasons, "unsafe_public_link")
                    continue
                refetch.append(_RefetchCandidate(event_id, title, link, published, status_value))

    def _history_candidates(
        self,
        raw: bytes,
        records: list[_CacheRecord],
        markers: list[_RawGroupMarker],
        reasons: dict[str, int],
        budget: dict[str, int],
    ) -> None:
        for line in BytesIO(raw):
            if not line.strip():
                continue
            if not self._take_row(budget, reasons):
                return
            if budget["nodes"] <= 0:
                self._report_node_limit(budget, reasons)
                return
            try:
                value, nodes = _decode_json(line, maximum_nodes=budget["nodes"])
                budget["nodes"] -= nodes
            except _NodeLimitExceeded:
                self._report_node_limit(budget, reasons)
                return
            except (UnicodeDecodeError, ValueError, RecursionError, json.JSONDecodeError):
                self._add_reason(reasons, "invalid_document")
                continue
            snapshot_value = value.get("snapshot") if type(value) is dict and "snapshot" in value else value
            self._snapshot_candidates(
                snapshot_value,
                records,
                markers,
                reasons,
                source="evidence_history",
                budget=budget,
                first_row_precounted=True,
            )

    def scan(self) -> RecoveryReport:
        reasons: dict[str, int] = {}
        records: list[_CacheRecord] = []
        markers: list[_RawGroupMarker] = []
        refetch: list[_RefetchCandidate] = []
        opened: list[Path] = []
        total_bytes = 0
        budget = {"rows": _MAX_ROWS, "nodes": _MAX_NODES}
        history = self._evidence_history
        history_discovered = history is None
        if history is None:
            history, history_error = self._default_named_files(self.root / "history", _HISTORY_NAME)
        else:
            history_error = self._history_input_error
        if history_error is not None:
            self._add_reason(reasons, history_error)
        legacy = self._legacy_snapshots
        legacy_discovered = legacy is None
        if legacy is None:
            legacy, legacy_error = self._default_named_files(self.root / "legacy-snapshots", _LEGACY_NAME)
        else:
            legacy_error = self._legacy_input_error
        if legacy_error is not None:
            self._add_reason(reasons, legacy_error)

        def requested() -> Iterator[tuple[str, Path, bool]]:
            yield "radar", self._radar_cache, self._radar_present
            yield "current", self._evidence_current, self._current_present
            for path in history:
                try:
                    explicit_present = os.path.normcase(str(Path(os.path.abspath(path)))) in self._explicit_history_present
                except (OSError, TypeError, ValueError):
                    explicit_present = False
                yield "history", path, history_discovered or explicit_present
            for path in legacy:
                try:
                    explicit_present = os.path.normcase(str(Path(os.path.abspath(path)))) in self._explicit_legacy_present
                except (OSError, TypeError, ValueError):
                    explicit_present = False
                yield "legacy", path, legacy_discovered or explicit_present

        seen: set[str] = set()
        for kind, supplied, discovered in requested():
            try:
                path = Path(os.path.abspath(supplied))
            except Exception:
                self._add_reason(reasons, "invalid_path")
                continue
            identity = os.path.normcase(str(path))
            if identity in seen:
                continue
            seen.add(identity)
            if len(seen) > _MAX_FILES:
                if "file_limit" not in reasons:
                    self._add_reason(reasons, "file_limit")
                break
            if not self._allowed_name(kind, path):
                self._add_reason(reasons, "path_not_allowlisted")
                continue
            raw, error = self._safe_read(path, _MAX_TOTAL_BYTES - total_bytes)
            if error == "missing":
                if discovered:
                    self._add_reason(reasons, f"{kind}_entry_disappeared")
                continue
            if raw is None:
                self._add_reason(reasons, error or "read_failed")
                continue
            total_bytes += len(raw)
            opened.append(path)
            try:
                if kind == "history":
                    self._history_candidates(raw, records, markers, reasons, budget)
                    continue
                if budget["nodes"] <= 0:
                    self._report_node_limit(budget, reasons)
                    continue
                value, nodes = _decode_json(raw, maximum_nodes=budget["nodes"])
                budget["nodes"] -= nodes
                if kind == "radar":
                    self._radar_candidates(
                        value,
                        records,
                        markers,
                        refetch,
                        reasons,
                        budget,
                    )
                    continue
                self._snapshot_candidates(
                    value,
                    records,
                    markers,
                    reasons,
                    source="evidence_current" if kind == "current" else "legacy_snapshot",
                    budget=budget,
                )
            except _NodeLimitExceeded:
                self._report_node_limit(budget, reasons)
            except (UnicodeDecodeError, ValueError, RecursionError, json.JSONDecodeError):
                self._add_reason(reasons, "invalid_document")
        return RecoveryReport(
            unrecoverable=sum(reasons.values()),
            reasons=reasons,
            opened_paths=tuple(opened),
            _snapshots=tuple(records),
            _refetch=tuple(refetch),
            _raw_markers=tuple(markers),
        )

    def _now_utc(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("invalid recovery clock")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _event_fingerprint(event: EvidenceEvent) -> str:
        return _event_fingerprint(event)


    @staticmethod
    def _lineage_identity(snapshot: EvidenceSnapshot) -> tuple[str, str, datetime]:
        return (
            snapshot.snapshot_id,
            snapshot.raw_snapshot_id,
            snapshot.generated_at.astimezone(timezone.utc),
        )


    @staticmethod
    def _transition_identity(transition: StatusTransition) -> tuple[object, ...]:
        return (
            transition.from_status.value if transition.from_status is not None else None,
            transition.to_status.value,
            transition.changed_at.astimezone(timezone.utc),
            transition.reason,
        )


    @staticmethod
    def _cache_source_expression(sources: set[str]) -> str:
        if not sources or any(source not in _CACHE_SOURCE_ORDER for source in sources):
            raise ValueError("invalid cache recovery source")
        return "+".join(sorted(sources, key=lambda source: _CACHE_SOURCE_ORDER[source]))


    @classmethod
    def _cache_record_order(cls, record: _CacheRecord) -> tuple[object, ...]:
        event = record.snapshot.events[0]
        return (
            record.snapshot.generated_at.astimezone(timezone.utc),
            len(event.status_history),
            _STATUS_ORDER[event.verification_status.value],
            record.snapshot.snapshot_id,
            record.snapshot.raw_snapshot_id,
            cls._event_fingerprint(event),
            record.source,
        )


    @classmethod
    def _resolve_cache_records(
        cls,
        records: list[_CacheRecord],
    ) -> tuple[_CacheRecord, ...] | None:
        by_lineage: dict[
            tuple[str, str, datetime],
            tuple[str, EvidenceSnapshot, set[str]],
        ] = {}
        by_raw_snapshot: dict[
            str,
            tuple[str, datetime, tuple[tuple[str, str], ...]],
        ] = {}
        raw_event_sets_by_lineage: dict[
            tuple[str, str, datetime],
            tuple[tuple[str, str], ...],
        ] = {}
        for record in records:
            event = record.snapshot.events[0]
            fingerprint = cls._event_fingerprint(event)
            raw_binding = (
                record.snapshot.snapshot_id,
                record.snapshot.generated_at.astimezone(timezone.utc),
                record.raw_event_set,
            )
            previous_raw = by_raw_snapshot.get(record.snapshot.raw_snapshot_id)
            if previous_raw is not None and previous_raw != raw_binding:
                return None
            by_raw_snapshot[record.snapshot.raw_snapshot_id] = raw_binding
            identity = cls._lineage_identity(record.snapshot)
            raw_event_sets_by_lineage[identity] = record.raw_event_set
            previous = by_lineage.get(identity)
            if previous is None:
                by_lineage[identity] = (fingerprint, record.snapshot, {record.source})
                continue
            if previous[0] != fingerprint:
                return None
            previous[2].add(record.source)

        resolved = [
            _CacheRecord(
                snapshot=snapshot,
                source=cls._cache_source_expression(sources),
                raw_event_set=raw_event_sets_by_lineage[
                    cls._lineage_identity(snapshot)
                ],
            )
            for _fingerprint, snapshot, sources in by_lineage.values()
        ]
        resolved.sort(key=cls._cache_record_order)
        for previous, current in zip(resolved, resolved[1:]):
            previous_event = previous.snapshot.events[0]
            current_event = current.snapshot.events[0]
            previous_fingerprint = cls._event_fingerprint(previous_event)
            current_fingerprint = cls._event_fingerprint(current_event)
            if previous_fingerprint == current_fingerprint:
                continue
            previous_history = tuple(
                cls._transition_identity(transition)
                for transition in previous_event.status_history
            )
            current_history = tuple(
                cls._transition_identity(transition)
                for transition in current_event.status_history
            )
            if (
                current_history == previous_history
                and previous_event.verification_status == current_event.verification_status
                and current.snapshot.generated_at > previous.snapshot.generated_at
            ):
                continue
            if (
                not previous_history
                or len(current_history) <= len(previous_history)
                or current_history[:len(previous_history)] != previous_history
            ):
                return None
        return tuple(resolved)

    @staticmethod
    def _candidate_identity(candidate: _RefetchCandidate) -> tuple[object, ...]:
        return (
            candidate.event_id,
            candidate.title,
            candidate.public_link,
            candidate.published_at,
            candidate.verification_status,
        )

    @staticmethod
    def _candidate_matches_event(candidate: _RefetchCandidate, event: EvidenceEvent) -> bool:
        published = event.published_at
        return bool(
            event.event_id == candidate.event_id
            and event.title == candidate.title
            and published is not None
            and published.astimezone(timezone.utc) == candidate.published_at
            and event.verification_status.value == candidate.verification_status
            and candidate.public_link in _event_links(event)
        )

    @staticmethod
    def _in_window(event: EvidenceEvent, now: datetime) -> bool:
        published = event.published_at
        if published is None or published.tzinfo is None or published.utcoffset() is None:
            return False
        observed = published.astimezone(timezone.utc)
        return now - timedelta(days=90) <= observed <= now

    @staticmethod
    def _recovery_snapshot(
        snapshot: EvidenceSnapshot,
        *,
        source: str,
        status: str,
        recovered_at: datetime,
    ) -> EvidenceSnapshot:
        metadata: dict[str, object] = {}
        if snapshot.recovery_metadata.get("legacy_identity") is True:
            metadata["legacy_identity"] = True
        metadata.update({
            "source": source,
            "recovered_at": recovered_at.isoformat(),
            "recovery_status": status,
            "source_snapshot_id": snapshot.snapshot_id,
        })
        return replace(snapshot, recovery_metadata=metadata)

    @staticmethod
    def _source_parts(value: object) -> frozenset[str]:
        if type(value) is not str:
            return frozenset()
        if value == "public_refetch":
            return frozenset({value})
        parts = value.split("+")
        if (
            not parts
            or len(parts) != len(set(parts))
            or any(part not in _CACHE_SOURCE_ORDER for part in parts)
        ):
            return frozenset()
        return frozenset(parts)


    def _archive_many(
        self,
        snapshots: tuple[EvidenceSnapshot, ...],
        *,
        now: datetime,
        expected_raw_event_sets: dict[str, tuple[str, ...]],
    ) -> dict[str, str]:
        if not snapshots:
            return {}
        event_ids = {snapshot.events[0].event_id for snapshot in snapshots if snapshot.events}

        def fail_all(reason: str) -> dict[str, str]:
            return {event_id: reason for event_id in event_ids}

        if any(
            len(snapshot.events) != 1
            for snapshot in snapshots
        ):
            return fail_all("archive_rejected")
        expected_by_event: dict[
            str,
            list[tuple[dict[str, object], set[tuple[object, ...]]]],
        ] = {}
        rejected_reasons: dict[str, str] = {}
        try:
            operation_archive = (
                EvidenceArchive(self.archive.root, now=lambda: now)
                if type(self.archive) is EvidenceArchive
                else self.archive
            )
            for snapshot in snapshots:
                event_id = snapshot.events[0].event_id
                raw_input_digests = _preflight_snapshot_input(snapshot)
                expected_by_event.setdefault(event_id, []).append((
                    _archive_document(
                        snapshot,
                        event_document(snapshot.events[0]),
                        now,
                        raw_input_digests[event_id],
                    )[
                        "snapshot_history"
                    ][0],
                    {
                        self._transition_identity(transition)
                        for transition in snapshot.events[0].status_history
                    },
                ))
            batch_upsert = getattr(operation_archive, "upsert_many", None)
            if callable(batch_upsert):
                rejected_value = batch_upsert(
                    snapshots,
                    expected_raw_event_sets=expected_raw_event_sets,
                    continue_on_raw_conflict=True,
                )
                if type(rejected_value) is ArchiveBatchOutcome:
                    if (
                        (
                            rejected_value.accepted_event_ids
                            | rejected_value.rejected_event_ids
                        ) != event_ids
                        or rejected_value.accepted_event_ids
                        & rejected_value.rejected_event_ids
                        or set(rejected_value.reasons_by_event)
                        != set(rejected_value.rejected_event_ids)
                        or any(
                            reason not in {
                                "raw_authority_conflict",
                                "component_conflict",
                            }
                            for reason in rejected_value.reasons_by_event.values()
                        )
                    ):
                        return fail_all("archive_rejected")
                    rejected_event_ids = rejected_value.rejected_event_ids
                    rejected_reasons = {
                        event_id: "ambiguous_record"
                        for event_id in rejected_event_ids
                    }
                elif rejected_value is None:
                    rejected_event_ids: frozenset[str] = frozenset()
                elif (
                    type(rejected_value) is frozenset
                    and rejected_value <= event_ids
                    and all(type(event_id) is str for event_id in rejected_value)
                ):
                    rejected_event_ids = rejected_value
                else:
                    return fail_all("archive_rejected")
            elif len(snapshots) == 1:
                operation_archive.upsert(snapshots[0])
                rejected_event_ids = frozenset()
            else:
                return fail_all("archive_rejected")
            query = getattr(operation_archive, "query", None)
            if callable(query):
                rows = query(90)
                if type(rows) is not list:
                    return fail_all("archive_rejected")
                archived_rows = {
                    row.get("event_id"): row
                    for row in rows
                    if (
                        type(row) is dict
                        and row.get("event_id") in event_ids - rejected_event_ids
                    )
                }
            else:
                get = getattr(operation_archive, "get", None)
                if not callable(get):
                    return fail_all("archive_rejected")
                archived_rows = {
                    event_id: get(event_id)
                    for event_id in event_ids - rejected_event_ids
                }
        except ArchiveRawAuthorityError:
            return fail_all("ambiguous_record")
        except Exception:
            return fail_all("archive_rejected")
        errors = rejected_reasons or {
            event_id: "ambiguous_record"
            for event_id in rejected_event_ids
        }
        cutoff = now - timedelta(days=90)
        for event_id, expected in expected_by_event.items():
            if event_id in errors:
                continue
            archived = archived_rows.get(event_id)
            if type(archived) is not dict or archived.get("event_id") != event_id:
                errors[event_id] = "archive_rejected"
                continue
            history = archived.get("snapshot_history")
            if type(history) is not list:
                errors[event_id] = "archive_rejected"
                continue
            archived_by_identity = {
                (
                    lineage.get("evidence_snapshot_id"),
                    lineage.get("raw_snapshot_id"),
                    lineage.get("generated_at"),
                    lineage.get("content_digest"),
                    lineage.get("raw_input_digest"),
                ): lineage
                for lineage in history
                if type(lineage) is dict
            }
            for lineage, _transitions in expected:
                identity = (
                    lineage["evidence_snapshot_id"],
                    lineage["raw_snapshot_id"],
                    lineage["generated_at"],
                    lineage["content_digest"],
                    lineage["raw_input_digest"],
                )
                persisted = archived_by_identity.get(identity)
                if persisted is None:
                    generated = _aware_time(lineage.get("generated_at"))
                    errors[event_id] = (
                        "recovery_out_of_window"
                        if generated is None or generated < cutoff or generated > now
                        else "archive_rejected"
                    )
                    break
                expected_recovery = lineage.get("recovery")
                persisted_recovery = persisted.get("recovery")
                if type(expected_recovery) is not dict or type(persisted_recovery) is not dict:
                    errors[event_id] = "archive_rejected"
                    break
                if (
                    expected_recovery.get("status") != persisted_recovery.get("status")
                    or expected_recovery.get("source_snapshot_id")
                    != persisted_recovery.get("source_snapshot_id")
                    or not self._source_parts(expected_recovery.get("source"))
                    <= self._source_parts(persisted_recovery.get("source"))
                ):
                    errors[event_id] = "archive_rejected"
                    break
                persisted_at = _aware_time(persisted_recovery.get("recovered_at"))
                generated_at = _aware_time(persisted.get("generated_at"))
                if (
                    persisted_at is None
                    or generated_at is None
                    or persisted_at < generated_at
                    or persisted_at > now
                ):
                    errors[event_id] = "archive_rejected"
                    break
            if event_id in errors:
                continue
            persisted_transitions = archived.get("status_history")
            if type(persisted_transitions) is not list:
                errors[event_id] = "archive_rejected"
                continue
            persisted_transition_identities = {
                (
                    row.get("from_status"),
                    row.get("to_status"),
                    _aware_time(row.get("changed_at")),
                    row.get("reason"),
                )
                for row in persisted_transitions
                if type(row) is dict
            }
            if any(
                not expected_transitions <= persisted_transition_identities
                for _lineage, expected_transitions in expected
            ):
                errors[event_id] = "archive_rejected"
        return errors

    def import_records(self) -> RecoveryReport:
        scanned = self.scan()
        reasons = dict(scanned.reasons)
        cache_ids: set[str] = set()
        refetched_ids: set[str] = set()
        try:
            now = self._now_utc()
        except (TypeError, ValueError):
            self._add_reason(reasons, "invalid_clock")
            return RecoveryReport(
                unrecoverable=sum(reasons.values()),
                reasons=reasons,
                opened_paths=scanned.opened_paths,
            )

        cache_groups: dict[str, list[_CacheRecord]] = {}
        for record in scanned._snapshots:
            event = record.snapshot.events[0]
            cache_groups.setdefault(event.event_id, []).append(record)
        refetch_groups: dict[str, list[_RefetchCandidate]] = {}
        for candidate in scanned._refetch:
            refetch_groups.setdefault(candidate.event_id, []).append(candidate)

        blocked: set[str] = set()

        def block(event_ids: Iterable[str], reason: str) -> None:
            for event_id in sorted(set(event_ids)):
                if event_id in blocked:
                    continue
                blocked.add(event_id)
                self._add_reason(reasons, reason)

        selected_cache: dict[str, tuple[_CacheRecord, ...]] = {}
        selected_refetch: dict[str, _RefetchCandidate] = {}
        for event_id, records in cache_groups.items():
            resolved = self._resolve_cache_records(records)
            if resolved is None:
                block((event_id,), "ambiguous_record")
            else:
                selected_cache[event_id] = resolved
        for event_id, candidates in refetch_groups.items():
            identities = {self._candidate_identity(candidate) for candidate in candidates}
            if len(identities) != 1:
                block((event_id,), "ambiguous_record")
            else:
                selected_refetch[event_id] = candidates[0]
        for event_id in set(selected_cache) & set(selected_refetch):
            if event_id in blocked:
                continue
            if not self._candidate_matches_event(
                selected_refetch[event_id],
                selected_cache[event_id][-1].snapshot.events[0],
            ):
                block((event_id,), "ambiguous_record")

        units: dict[
            str,
            tuple[str, tuple[EvidenceSnapshot, ...], tuple[_CacheRecord, ...] | None],
        ] = {}
        raw_markers = list(scanned._raw_markers)
        cache_terminal_ids = set(selected_cache)
        for event_id, records in sorted(selected_cache.items()):
            if event_id in blocked:
                continue
            if any(not self._in_window(record.snapshot.events[0], now) for record in records):
                block((event_id,), "outside_retention_window")
                continue
            recovered = tuple(
                self._recovery_snapshot(
                    record.snapshot,
                    source=record.source,
                    status="cache_recovered",
                    recovered_at=now,
                )
                for record in records
            )
            units[event_id] = ("cache", recovered, records)

        # Close cache/refetch identities connected to a known-invalid raw group
        # before they can consume the combined gate or enter a callback. Marker
        # declarations and selected cache lineages form one bounded bipartite
        # graph, so a conflict propagates across every linked raw component.
        raw_to_declared_events: dict[str, set[str]] = {}
        event_to_raw_ids: dict[str, set[str]] = {}
        invalid_raw_ids: set[str] = set()
        invalid_accounted_event_ids: set[str] = set()
        for marker in raw_markers:
            raw_to_declared_events.setdefault(marker.raw_snapshot_id, set()).update(
                marker.declared_event_ids
            )
            for declared_event_id in marker.declared_event_ids:
                event_to_raw_ids.setdefault(declared_event_id, set()).add(
                    marker.raw_snapshot_id
                )
            if marker.invalid:
                invalid_raw_ids.add(marker.raw_snapshot_id)
                invalid_accounted_event_ids.update(marker.accounted_event_ids)
        for event_id, (_kind, snapshots, _records) in units.items():
            for snapshot in snapshots:
                raw_to_declared_events.setdefault(snapshot.raw_snapshot_id, set()).add(
                    event_id
                )
                event_to_raw_ids.setdefault(event_id, set()).add(snapshot.raw_snapshot_id)

        closed_raw_ids: set[str] = set()
        closed_event_ids: set[str] = set()
        pending_raw_ids = list(sorted(invalid_raw_ids, reverse=True))
        while pending_raw_ids:
            raw_snapshot_id = pending_raw_ids.pop()
            if raw_snapshot_id in closed_raw_ids:
                continue
            closed_raw_ids.add(raw_snapshot_id)
            for event_id in sorted(raw_to_declared_events.get(raw_snapshot_id, ())):
                if event_id in closed_event_ids:
                    continue
                closed_event_ids.add(event_id)
                pending_raw_ids.extend(
                    sorted(
                        event_to_raw_ids.get(event_id, ()) - closed_raw_ids,
                        reverse=True,
                    )
                )
        for event_id in sorted(closed_event_ids & (set(units) | set(selected_refetch))):
            if event_id in invalid_accounted_event_ids:
                blocked.add(event_id)
            else:
                block((event_id,), "ambiguous_record")

        public_candidates: list[tuple[str, _RefetchCandidate]] = []
        for event_id, candidate in sorted(selected_refetch.items()):
            if event_id in blocked or event_id in cache_terminal_ids:
                continue
            if not (now - timedelta(days=90) <= candidate.published_at <= now):
                block((event_id,), "outside_retention_window")
                continue
            if self._public_refetcher is None:
                block((event_id,), "public_refetch_unavailable")
                continue
            public_candidates.append((event_id, candidate))

        # Candidate count and static identity costs are checked before the
        # first dependency callback. Returned graphs then consume one shared
        # budget immediately, before any archive mutation.
        eligible_cache_ids = tuple(
            event_id
            for event_id, (kind, _snapshots, _records) in sorted(units.items())
            if kind == "cache" and event_id not in blocked
        )
        materialized_cache_snapshots = tuple(
            snapshot
            for event_id in eligible_cache_ids
            for snapshot in units[event_id][1]
        )
        if public_candidates and not _preflight_refetch_candidates(
            [candidate for _event_id, candidate in public_candidates],
            materialized_snapshots=materialized_cache_snapshots,
        ):
            block(eligible_cache_ids, "archive_rejected")
            block(
                (event_id for event_id, _candidate in public_candidates),
                "public_refetch_failed",
            )
            public_candidates = []

        refetch_budget = _new_refetch_budget()
        for index, (event_id, candidate) in enumerate(public_candidates):
            marker: _RawGroupMarker | None = None
            try:
                value = self._public_refetcher(candidate.public_link)
                snapshot, marker = _bounded_refetch_snapshot(value, refetch_budget)
                if snapshot is None:
                    raise ValueError("invalid refetch")
                event = snapshot.events[0]
                if not self._candidate_matches_event(candidate, event) or not self._in_window(event, now):
                    raise ValueError("invalid refetch identity")
            except _RefetchLimitExceeded as error:
                marker = getattr(error, "raw_marker", marker)
                if marker is not None:
                    raw_markers.append(replace(
                        marker,
                        declared_event_ids=tuple(sorted({
                            *marker.declared_event_ids,
                            candidate.event_id,
                        })),
                        invalid=True,
                        accounted_event_ids=(candidate.event_id,),
                    ))
                block((event_id,), "public_refetch_failed")
                if refetch_budget["exhausted"] is True:
                    block(
                        (
                            remaining_event_id
                            for remaining_event_id, _remaining_candidate
                            in public_candidates[index + 1:]
                        ),
                        "public_refetch_failed",
                    )
                    break
                continue
            except Exception:
                if marker is not None:
                    raw_markers.append(replace(
                        marker,
                        declared_event_ids=tuple(sorted({
                            *marker.declared_event_ids,
                            candidate.event_id,
                        })),
                        invalid=True,
                        accounted_event_ids=(candidate.event_id,),
                    ))
                block((event_id,), "public_refetch_failed")
                continue
            raw_markers.append(_RawGroupMarker(
                raw_snapshot_id=snapshot.raw_snapshot_id,
                snapshot_id=snapshot.snapshot_id,
                generated_at=snapshot.generated_at.astimezone(timezone.utc),
                declared_event_ids=(event.event_id,),
                invalid=False,
            ))
            recovered = self._recovery_snapshot(
                snapshot,
                source="public_refetch",
                status="public_refetched",
                recovered_at=now,
            )
            units[event_id] = ("public", (recovered,), None)

        raw_headers: dict[str, tuple[str, datetime]] = {}
        raw_complete: dict[str, dict[str, str]] = {}
        raw_partial: dict[str, dict[str, str]] = {}
        raw_to_events: dict[str, set[str]] = {}
        invalid_marker_accounted_ids: set[str] = set()
        for marker in raw_markers:
            if not marker.invalid:
                continue
            raw_to_events.setdefault(marker.raw_snapshot_id, set()).update(
                marker.declared_event_ids
            )
            invalid_marker_accounted_ids.update(marker.accounted_event_ids)
        unit_raw_ids: dict[str, set[str]] = {event_id: set() for event_id in units}
        conflicting_raw_ids: set[str] = {
            marker.raw_snapshot_id for marker in raw_markers if marker.invalid
        }

        def bind_raw(
            unit_event_id: str,
            selected: EvidenceSnapshot,
            manifest_items: tuple[tuple[str, str], ...],
            *,
            complete: bool,
        ) -> None:
            raw_snapshot_id = selected.raw_snapshot_id
            header = (
                selected.snapshot_id,
                selected.generated_at.astimezone(timezone.utc),
            )
            unit_raw_ids[unit_event_id].add(raw_snapshot_id)
            manifest: dict[str, str] = {}
            for event_id, fingerprint in manifest_items:
                if event_id in manifest and manifest[event_id] != fingerprint:
                    conflicting_raw_ids.add(raw_snapshot_id)
                elif event_id in manifest:
                    conflicting_raw_ids.add(raw_snapshot_id)
                manifest[event_id] = fingerprint
            raw_to_events.setdefault(raw_snapshot_id, set()).update(manifest)
            raw_to_events[raw_snapshot_id].add(unit_event_id)
            previous_header = raw_headers.get(raw_snapshot_id)
            if previous_header is not None and previous_header != header:
                conflicting_raw_ids.add(raw_snapshot_id)
            else:
                raw_headers[raw_snapshot_id] = header
            target = raw_complete if complete else raw_partial
            previous_manifest = target.get(raw_snapshot_id)
            if complete:
                if previous_manifest is not None and previous_manifest != manifest:
                    conflicting_raw_ids.add(raw_snapshot_id)
                else:
                    target[raw_snapshot_id] = manifest
            else:
                combined = {} if previous_manifest is None else dict(previous_manifest)
                for event_id, fingerprint in manifest.items():
                    prior = combined.get(event_id)
                    if prior is not None and prior != fingerprint:
                        conflicting_raw_ids.add(raw_snapshot_id)
                    combined[event_id] = fingerprint
                target[raw_snapshot_id] = combined

        for event_id, (kind, snapshots, records) in units.items():
            if kind == "cache":
                if records is None or len(records) != len(snapshots):
                    block((event_id,), "archive_rejected")
                    continue
                for selected, record in zip(snapshots, records):
                    bind_raw(
                        event_id,
                        selected,
                        record.raw_event_set,
                        complete=True,
                    )
            else:
                selected = snapshots[0]
                selected_event = selected.events[0]
                bind_raw(
                    event_id,
                    selected,
                    ((selected_event.event_id, self._event_fingerprint(selected_event)),),
                    complete=False,
                )

        raw_expected: dict[str, tuple[str, ...]] = {}
        for raw_snapshot_id in set(raw_complete) | set(raw_partial):
            complete = raw_complete.get(raw_snapshot_id)
            partial = raw_partial.get(raw_snapshot_id, {})
            if complete is not None:
                for event_id, fingerprint in partial.items():
                    if complete.get(event_id) != fingerprint:
                        conflicting_raw_ids.add(raw_snapshot_id)
                manifest = complete
            else:
                manifest = partial
            if not manifest:
                conflicting_raw_ids.add(raw_snapshot_id)
                continue
            raw_expected[raw_snapshot_id] = tuple(sorted(manifest))

        parent = {event_id: event_id for event_id in units}

        def find(event_id: str) -> str:
            while parent[event_id] != event_id:
                parent[event_id] = parent[parent[event_id]]
                event_id = parent[event_id]
            return event_id

        def union(first: str, second: str) -> None:
            first_root = find(first)
            second_root = find(second)
            if first_root != second_root:
                parent[max(first_root, second_root)] = min(first_root, second_root)

        raw_owner: dict[str, str] = {}
        for event_id, raw_ids in unit_raw_ids.items():
            for raw_snapshot_id in raw_ids:
                owner = raw_owner.get(raw_snapshot_id)
                if owner is None:
                    raw_owner[raw_snapshot_id] = event_id
                else:
                    union(owner, event_id)

        conflicting_roots = {
            find(event_id)
            for raw_snapshot_id in conflicting_raw_ids
            for event_id in (
                {*raw_to_events.get(raw_snapshot_id, set())}
                | ({raw_owner[raw_snapshot_id]} if raw_snapshot_id in raw_owner else set())
            )
            if event_id in parent
        }
        for event_id in units:
            if find(event_id) in conflicting_roots:
                if event_id in invalid_marker_accounted_ids:
                    blocked.add(event_id)
                else:
                    block((event_id,), "ambiguous_record")

        components: dict[str, list[str]] = {}
        for event_id in units:
            if event_id not in blocked:
                components.setdefault(find(event_id), []).append(event_id)
        eligible_components: list[list[str]] = []
        batch_snapshots: list[EvidenceSnapshot] = []
        batch_expected_raw_event_sets: dict[str, tuple[str, ...]] = {}
        for component_event_ids in sorted(components.values(), key=lambda value: tuple(sorted(value))):
            component_event_ids.sort()
            component_snapshots = tuple(
                selected
                for event_id in component_event_ids
                for selected in units[event_id][1]
            )
            component_raw_ids = {
                selected.raw_snapshot_id for selected in component_snapshots
            }
            if any(raw_snapshot_id not in raw_expected for raw_snapshot_id in component_raw_ids):
                block(component_event_ids, "ambiguous_record")
                continue
            eligible_components.append(component_event_ids)
            batch_snapshots.extend(component_snapshots)
            batch_expected_raw_event_sets.update({
                raw_snapshot_id: raw_expected[raw_snapshot_id]
                for raw_snapshot_id in sorted(component_raw_ids)
            })

        archive_errors = self._archive_many(
            tuple(batch_snapshots),
            now=now,
            expected_raw_event_sets=batch_expected_raw_event_sets,
        ) if batch_snapshots else {}
        error_priority = (
            "ambiguous_record",
            "recovery_out_of_window",
            "archive_rejected",
        )
        for component_event_ids in eligible_components:
            component_errors = {
                archive_errors[event_id]
                for event_id in component_event_ids
                if event_id in archive_errors
            }
            if component_errors:
                reason = next(
                    selected
                    for selected in error_priority
                    if selected in component_errors
                )
                block(component_event_ids, reason)
                continue
            for event_id in component_event_ids:
                if units[event_id][0] == "cache":
                    cache_ids.add(event_id)
                else:
                    refetched_ids.add(event_id)
        return RecoveryReport(
            cache_recovered=len(cache_ids),
            public_refetched=len(refetched_ids),
            unrecoverable=sum(reasons.values()),
            reasons=reasons,
            opened_paths=scanned.opened_paths,
        )
