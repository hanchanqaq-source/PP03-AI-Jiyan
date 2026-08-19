from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Callable

from cache_io_lock import CACHE_IO_LOCK
from source_health.probe_errors import redact_probe_message

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


_MAX_EVIDENCE_DOCUMENT_BYTES = 1_048_576
_EVENT_KEYS = {
    "event_id",
    "title",
    "summary",
    "category",
    "related_tags",
    "published_at",
    "core_claim",
    "verification_status",
    "verification_reason",
    "verified_at",
    "evidence_as_of",
    "key_fields",
    "primary_evidence",
    "independent_evidence",
    "syndicated_copies",
    "contradicting_evidence",
    "status_history",
    "holding_relevance",
}
_EVIDENCE_KEYS = {
    "evidence_id",
    "content_source",
    "collector_source",
    "canonical_url",
    "published_at",
    "source_role",
    "origin_cluster",
    "supports_claim",
    "supports_fields",
    "contradicts_claim",
    "is_official",
    "title",
    "excerpt",
}
_KEY_FIELD_KEYS = {
    "field_name",
    "raw_value",
    "normalized_value",
    "verification_status",
    "evidence_ids",
    "reason",
}
_TRANSITION_KEYS = {"from_status", "to_status", "changed_at", "reason"}
_TAG_KEYS = {"id", "name"}


def _replace_durable(source: Path, destination: Path) -> None:
    if os.name == "nt":
        import ctypes

        move_file_ex = ctypes.windll.kernel32.MoveFileExW
        move_file_ex.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        move_file_ex.restype = ctypes.c_int
        if not move_file_ex(str(source), str(destination), 0x1 | 0x8):
            raise ctypes.WinError()
        return
    os.replace(source, destination)
    descriptor = os.open(str(destination.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cleanup_owned_temp(path: Path, identity: tuple[int, int]) -> None:
    try:
        current = path.lstat()
        if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity:
            path.unlink()
    except (FileNotFoundError, OSError):
        return


def _strict_json_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in document:
            raise ValueError("invalid JSON object")
        document[key] = value
    return document


def _strict_json_int(raw: str) -> int:
    if len(raw) > 11:
        raise ValueError("JSON integer is too large")
    value = int(raw)
    if value < -1_000_000_000 or value > 1_000_000_000:
        raise ValueError("JSON integer is too large")
    return value


def _reject_json_number(_raw: str) -> float:
    raise ValueError("non-integer JSON numbers are not allowed")


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp timezone is required")
    return value.isoformat()


def _evidence_document(item: EvidenceItem) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "content_source": item.content_source,
        "collector_source": item.collector_source,
        "canonical_url": item.canonical_url,
        "published_at": _timestamp(item.published_at),
        "source_role": item.source_role.value,
        "origin_cluster": item.origin_cluster,
        "supports_claim": item.supports_claim,
        "supports_fields": list(item.supports_fields),
        "contradicts_claim": item.contradicts_claim,
        "is_official": item.is_official,
        "title": item.title[:500],
        "excerpt": item.excerpt[:1200],
    }


def field_document(field: KeyField) -> dict[str, Any]:
    return {
        "field_name": field.field_name,
        "raw_value": field.raw_value,
        "normalized_value": field.normalized_value,
        "verification_status": field.verification_status.value,
        "evidence_ids": list(field.evidence_ids),
        "reason": field.reason,
    }


_TRUSTED_FIELD_STATUSES = {
    FieldVerificationStatus.VERIFIED,
    FieldVerificationStatus.CORROBORATED,
}


def trusted_event_text(event: EvidenceEvent, value: str) -> str:
    """Remove key-field values that are not independently trusted from display text."""
    projected = value
    untrusted_values = sorted({
        field.raw_value
        for field in event.key_fields
        if field.verification_status not in _TRUSTED_FIELD_STATUSES and field.raw_value
    }, key=len, reverse=True)
    for raw_value in untrusted_values:
        projected = projected.replace(raw_value, "")
    projected = re.sub(r"\s{2,}", " ", projected).strip()
    projected = re.sub(r"\s+([，。；：、！？])", r"\1", projected)
    return projected


def _transition_document(row: StatusTransition) -> dict[str, Any]:
    return {
        "from_status": row.from_status.value if row.from_status else None,
        "to_status": row.to_status.value,
        "changed_at": _timestamp(row.changed_at),
        "reason": row.reason,
    }


def event_document(event: EvidenceEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "title": trusted_event_text(event, event.title),
        "summary": trusted_event_text(event, event.summary),
        "category": event.category,
        "related_tags": [{"id": key, "name": name} for key, name in event.related_tags],
        "published_at": _timestamp(event.published_at),
        "core_claim": trusted_event_text(event, event.core_claim),
        "verification_status": event.verification_status.value,
        "verification_reason": event.verification_reason,
        "verified_at": _timestamp(event.verified_at),
        "evidence_as_of": _timestamp(event.evidence_as_of),
        "key_fields": [field_document(row) for row in event.key_fields],
        "primary_evidence": [_evidence_document(row) for row in event.primary_evidence],
        "independent_evidence": [_evidence_document(row) for row in event.independent_evidence],
        "syndicated_copies": [_evidence_document(row) for row in event.syndicated_copies],
        "contradicting_evidence": [_evidence_document(row) for row in event.contradicting_evidence],
        "status_history": [_transition_document(row) for row in event.status_history],
        "holding_relevance": event.holding_relevance,
    }


def event_summary_document(event: EvidenceEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "title": trusted_event_text(event, event.title),
        "published_at": _timestamp(event.published_at),
        "verified_at": _timestamp(event.verified_at),
        "evidence_as_of": _timestamp(event.evidence_as_of),
        "category": event.category,
        "related_tags": [{"id": key, "name": name} for key, name in event.related_tags],
        "core_claim": trusted_event_text(event, event.core_claim),
        "verification_status": event.verification_status.value,
        "verification_reason": event.verification_reason,
        "verified_key_fields": [field_document(field) for field in event.key_fields if field.verification_status in _TRUSTED_FIELD_STATUSES],
        "pending_key_field_count": sum(field.verification_status == FieldVerificationStatus.UNVERIFIED for field in event.key_fields),
        "conflicting_key_field_count": sum(field.verification_status == FieldVerificationStatus.CONFLICTING for field in event.key_fields),
        "primary_evidence_count": len(event.primary_evidence),
        "independent_evidence_count": len(event.independent_evidence),
        "syndicated_copy_count": len(event.syndicated_copies),
        "contradicting_evidence_count": len(event.contradicting_evidence),
        "status_change_count": len(event.status_history),
        "latest_transition": _transition_document(event.status_history[-1]) if event.status_history else None,
        "holding_relevance": event.holding_relevance,
    }


def snapshot_document(snapshot: EvidenceSnapshot) -> dict[str, Any]:
    if type(snapshot.snapshot_id) is not str or type(snapshot.raw_snapshot_id) is not str:
        raise ValueError("invalid evidence snapshot identity")
    if type(snapshot.recovery_metadata) is not dict:
        raise ValueError("invalid recovery metadata")
    document = {
        "schema_version": 1,
        "snapshot_id": snapshot.snapshot_id,
        "generated_at": _timestamp(snapshot.generated_at),
        "events": [event_document(event) for event in snapshot.events],
    }
    if snapshot.raw_snapshot_id:
        document["raw_snapshot_id"] = snapshot.raw_snapshot_id
    if snapshot.recovery_metadata:
        document["recovery_metadata"] = dict(snapshot.recovery_metadata)
    _exact_builtin(document)
    return document


def _parse_datetime(value: Any) -> datetime:
    if type(value) is not str or len(value) > 64:
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp timezone is required")
    return parsed


def _exact_row(value: object, keys: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"invalid {name} schema")
    return value


def _string(value: object, name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"invalid {name}")
    return value


def _string_list(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ValueError(f"invalid {name}")
    return tuple(value)


def _evidence_from_document(row: dict[str, Any]) -> EvidenceItem:
    row = _exact_row(row, _EVIDENCE_KEYS, "evidence")
    published_at = row["published_at"]
    if published_at is not None and type(published_at) is not str:
        raise ValueError("invalid evidence published_at")
    for name in ("supports_claim", "contradicts_claim", "is_official"):
        if type(row[name]) is not bool:
            raise ValueError(f"invalid evidence {name}")
    return EvidenceItem(
        evidence_id=_string(row["evidence_id"], "evidence_id"),
        content_source=_string(row["content_source"], "content_source"),
        collector_source=_string(row["collector_source"], "collector_source"),
        canonical_url=_string(row["canonical_url"], "canonical_url"),
        published_at=_parse_datetime(published_at) if published_at is not None else None,
        source_role=SourceRole(_string(row["source_role"], "source_role")),
        origin_cluster=_string(row["origin_cluster"], "origin_cluster"),
        supports_claim=row["supports_claim"],
        supports_fields=_string_list(row["supports_fields"], "supports_fields"),
        contradicts_claim=row["contradicts_claim"],
        is_official=row["is_official"],
        title=_string(row["title"], "evidence title"),
        excerpt=_string(row["excerpt"], "evidence excerpt"),
    )


def _event_from_document(row: dict[str, Any]) -> EvidenceEvent:
    def evidence_list(key: str) -> tuple[EvidenceItem, ...]:
        value = row[key]
        if type(value) is not list:
            raise ValueError(f"invalid {key}")
        return tuple(_evidence_from_document(item) for item in value)

    row = _exact_row(row, _EVENT_KEYS, "event")
    related_tags = row["related_tags"]
    key_fields = row["key_fields"]
    status_history = row["status_history"]
    if type(related_tags) is not list or type(key_fields) is not list or type(status_history) is not list:
        raise ValueError("invalid event collections")
    published_at = row["published_at"]
    if published_at is not None and type(published_at) is not str:
        raise ValueError("invalid event published_at")

    parsed_tags: list[tuple[str, str]] = []
    for value in related_tags:
        tag = _exact_row(value, _TAG_KEYS, "tag")
        parsed_tags.append((_string(tag["id"], "tag id"), _string(tag["name"], "tag name")))

    parsed_fields: list[KeyField] = []
    for value in key_fields:
        field = _exact_row(value, _KEY_FIELD_KEYS, "key field")
        parsed_fields.append(KeyField(
            field_name=_string(field["field_name"], "field_name"),
            raw_value=_string(field["raw_value"], "raw_value"),
            normalized_value=_string(field["normalized_value"], "normalized_value"),
            verification_status=FieldVerificationStatus(_string(field["verification_status"], "field verification_status")),
            evidence_ids=_string_list(field["evidence_ids"], "evidence_ids"),
            reason=_string(field["reason"], "field reason"),
        ))

    parsed_history: list[StatusTransition] = []
    for value in status_history:
        transition = _exact_row(value, _TRANSITION_KEYS, "transition")
        from_status = transition["from_status"]
        if from_status is not None and type(from_status) is not str:
            raise ValueError("invalid transition from_status")
        parsed_history.append(StatusTransition(
            from_status=VerificationStatus(from_status) if from_status is not None else None,
            to_status=VerificationStatus(_string(transition["to_status"], "transition to_status")),
            changed_at=_parse_datetime(transition["changed_at"]),
            reason=_string(transition["reason"], "transition reason"),
        ))

    return EvidenceEvent(
        event_id=_string(row["event_id"], "event_id"),
        title=_string(row["title"], "event title"),
        summary=_string(row["summary"], "event summary"),
        category=_string(row["category"], "event category"),
        related_tags=tuple(parsed_tags),
        published_at=_parse_datetime(published_at) if published_at is not None else None,
        core_claim=_string(row["core_claim"], "core_claim"),
        verification_status=VerificationStatus(_string(row["verification_status"], "verification_status")),
        verification_reason=_string(row["verification_reason"], "verification_reason"),
        verified_at=_parse_datetime(row["verified_at"]),
        evidence_as_of=_parse_datetime(row["evidence_as_of"]),
        key_fields=tuple(parsed_fields),
        primary_evidence=evidence_list("primary_evidence"),
        independent_evidence=evidence_list("independent_evidence"),
        syndicated_copies=evidence_list("syndicated_copies"),
        contradicting_evidence=evidence_list("contradicting_evidence"),
        status_history=tuple(parsed_history),
        holding_relevance=_string(row["holding_relevance"], "holding_relevance"),
    )


def _exact_builtin(value: object, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("document is too deep")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if value < -1_000_000_000 or value > 1_000_000_000:
            raise ValueError("integer is too large")
        return
    if type(value) is str:
        if len(value) > 8_192:
            raise ValueError("text is too large")
        return
    if type(value) is list:
        if len(value) > 5_000:
            raise ValueError("list is too large")
        for item in value:
            _exact_builtin(item, depth + 1)
        return
    if type(value) is dict:
        if len(value) > 5_000 or any(type(key) is not str or len(key) > 128 for key in value):
            raise ValueError("object is invalid")
        for item in value.values():
            _exact_builtin(item, depth + 1)
        return
    raise ValueError("document requires exact built-in values")


def evidence_snapshot_from_document(document: dict[str, Any]) -> EvidenceSnapshot:
    _exact_builtin(document)
    required = {"schema_version", "snapshot_id", "generated_at", "events"}
    allowed = required | {"raw_snapshot_id", "recovery_metadata"}
    if (
        type(document) is not dict
        or set(document) - allowed
        or not required.issubset(document)
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or type(document["snapshot_id"]) is not str
        or type(document["generated_at"]) is not str
        or type(document["events"]) is not list
    ):
        raise ValueError("invalid evidence snapshot schema")
    snapshot_id = document["snapshot_id"]
    raw_snapshot_id = document.get("raw_snapshot_id")
    recovery_metadata = document.get("recovery_metadata")
    if recovery_metadata is not None and type(recovery_metadata) is not dict:
        raise ValueError("invalid recovery metadata")
    metadata = dict(recovery_metadata) if recovery_metadata is not None else {}
    if raw_snapshot_id is not None and type(raw_snapshot_id) is not str:
        raise ValueError("invalid raw snapshot identity")
    events = document["events"]
    if any(type(row) is not dict for row in events):
        raise ValueError("invalid evidence event")
    if not raw_snapshot_id:
        raw_snapshot_id = snapshot_id
        metadata["legacy_identity"] = True
    return EvidenceSnapshot(
        snapshot_id=snapshot_id,
        raw_snapshot_id=raw_snapshot_id,
        generated_at=_parse_datetime(document["generated_at"]),
        events=tuple(_event_from_document(row) for row in events),
        recovery_metadata=metadata,
    )


class EvidenceStorage:
    def __init__(self, root: str | os.PathLike[str] | None = None, *, now: Callable[[], datetime] | None = None) -> None:
        if root is not None:
            self.root = Path(root)
        else:
            configured = os.environ.get("VR_DATA_DIR")
            if configured:
                self.root = Path(configured) / "evidence-verification" / "v1"
            else:
                profile = Path(os.environ.get("USERPROFILE") or Path.home())
                self.root = profile / ".vibe-research" / "evidence-verification" / "v1"
        self.current_path = self.root / "current.json"
        self.last_refresh_path = self.root / "last-refresh.json"
        self.history_root = self.root / "history"
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _atomic_write(self, path: Path, document: dict[str, Any]) -> None:
        _exact_builtin(document)
        payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(payload) > _MAX_EVIDENCE_DOCUMENT_BYTES:
            raise ValueError("evidence document is too large")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(raw_path)
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_durable(temp_path, path)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            _cleanup_owned_temp(temp_path, identity)
            raise

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            with CACHE_IO_LOCK:
                with path.open("rb") as handle:
                    raw = handle.read(_MAX_EVIDENCE_DOCUMENT_BYTES + 1)
                if len(raw) > _MAX_EVIDENCE_DOCUMENT_BYTES:
                    return None
                value = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_strict_json_object,
                    parse_int=_strict_json_int,
                    parse_float=_reject_json_number,
                    parse_constant=_reject_json_number,
                )
            _exact_builtin(value)
            return value if type(value) is dict else None
        except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError, RecursionError, json.JSONDecodeError):
            return None

    def load_current(self) -> EvidenceSnapshot | None:
        document = self._read_json(self.current_path)
        if not document:
            return None
        try:
            return evidence_snapshot_from_document(document)
        except (KeyError, TypeError, ValueError):
            return None

    def load_last_refresh(self) -> dict[str, Any] | None:
        return self._read_json(self.last_refresh_path)

    def history_path(self, observed_at: datetime) -> Path:
        return self.history_root / f"{observed_at.date().isoformat()}.jsonl"

    def _append_transitions(self, snapshot: EvidenceSnapshot, previous: EvidenceSnapshot | None) -> None:
        previous_lengths = {event.event_id: len(event.status_history) for event in previous.events} if previous else {}
        rows = []
        for event in snapshot.events:
            start = previous_lengths.get(event.event_id, 0)
            for transition in event.status_history[start:]:
                rows.append({"event_id": event.event_id, **_transition_document(transition)})
        if not rows:
            return
        path = self.history_path(snapshot.generated_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()

    def publish(self, snapshot: EvidenceSnapshot) -> None:
        with CACHE_IO_LOCK:
            previous = self.load_current()
            self._atomic_write(self.current_path, snapshot_document(snapshot))
            history_status = "completed"
            history_error = None
            try:
                self._append_transitions(snapshot, previous)
            except Exception as error:
                history_status = "failed"
                history_error = type(error).__name__
            self._atomic_write(self.last_refresh_path, {
                "status": "completed",
                "attempted_at": snapshot.generated_at.isoformat(),
                "last_successful_refresh_at": snapshot.generated_at.isoformat(),
                "error": None,
                "history_status": history_status,
                "history_error": history_error,
            })

    def record_failure(self, error: object) -> None:
        current = self.load_current()
        attempted_at = self._now().isoformat()
        with CACHE_IO_LOCK:
            self._atomic_write(self.last_refresh_path, {
                "status": "failed",
                "attempted_at": attempted_at,
                "last_successful_refresh_at": current.generated_at.isoformat() if current else None,
                "error": redact_probe_message(error)[:500],
            })
