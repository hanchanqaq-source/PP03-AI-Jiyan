from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
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


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


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


def _field_document(field: KeyField) -> dict[str, Any]:
    return {
        "field_name": field.field_name,
        "raw_value": field.raw_value,
        "normalized_value": field.normalized_value,
        "verification_status": field.verification_status.value,
        "evidence_ids": list(field.evidence_ids),
        "reason": field.reason,
    }


def _transition_document(row: StatusTransition) -> dict[str, Any]:
    return {
        "from_status": row.from_status.value if row.from_status else None,
        "to_status": row.to_status.value,
        "changed_at": row.changed_at.isoformat(),
        "reason": row.reason,
    }


def event_document(event: EvidenceEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "title": event.title,
        "summary": event.summary,
        "category": event.category,
        "related_tags": [{"id": key, "name": name} for key, name in event.related_tags],
        "published_at": _timestamp(event.published_at),
        "core_claim": event.core_claim,
        "verification_status": event.verification_status.value,
        "verification_reason": event.verification_reason,
        "verified_at": event.verified_at.isoformat(),
        "evidence_as_of": event.evidence_as_of.isoformat(),
        "key_fields": [_field_document(row) for row in event.key_fields],
        "primary_evidence": [_evidence_document(row) for row in event.primary_evidence],
        "independent_evidence": [_evidence_document(row) for row in event.independent_evidence],
        "syndicated_copies": [_evidence_document(row) for row in event.syndicated_copies],
        "contradicting_evidence": [_evidence_document(row) for row in event.contradicting_evidence],
        "status_history": [_transition_document(row) for row in event.status_history],
        "holding_relevance": event.holding_relevance,
    }


def snapshot_document(snapshot: EvidenceSnapshot) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "snapshot_id": snapshot.snapshot_id,
        "generated_at": snapshot.generated_at.isoformat(),
        "events": [event_document(event) for event in snapshot.events],
    }


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _evidence_from_document(row: dict[str, Any]) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=str(row["evidence_id"]),
        content_source=str(row["content_source"]),
        collector_source=str(row["collector_source"]),
        canonical_url=str(row["canonical_url"]),
        published_at=_parse_datetime(row["published_at"]) if row.get("published_at") else None,
        source_role=SourceRole(str(row["source_role"])),
        origin_cluster=str(row["origin_cluster"]),
        supports_claim=row.get("supports_claim") is True,
        supports_fields=tuple(str(value) for value in row.get("supports_fields") or []),
        contradicts_claim=row.get("contradicts_claim") is True,
        is_official=row.get("is_official") is True,
        title=str(row.get("title") or ""),
        excerpt=str(row.get("excerpt") or ""),
    )


def _event_from_document(row: dict[str, Any]) -> EvidenceEvent:
    def evidence_list(key: str) -> tuple[EvidenceItem, ...]:
        return tuple(_evidence_from_document(value) for value in row.get(key) or [] if isinstance(value, dict))

    return EvidenceEvent(
        event_id=str(row["event_id"]),
        title=str(row.get("title") or ""),
        summary=str(row.get("summary") or ""),
        category=str(row.get("category") or "industry"),
        related_tags=tuple((str(value.get("id") or ""), str(value.get("name") or "")) for value in row.get("related_tags") or [] if isinstance(value, dict) and value.get("id")),
        published_at=_parse_datetime(row["published_at"]) if row.get("published_at") else None,
        core_claim=str(row.get("core_claim") or ""),
        verification_status=VerificationStatus(str(row["verification_status"])),
        verification_reason=str(row.get("verification_reason") or ""),
        verified_at=_parse_datetime(row["verified_at"]),
        evidence_as_of=_parse_datetime(row["evidence_as_of"]),
        key_fields=tuple(KeyField(
            field_name=str(value["field_name"]),
            raw_value=str(value.get("raw_value") or ""),
            normalized_value=str(value.get("normalized_value") or ""),
            verification_status=FieldVerificationStatus(str(value["verification_status"])),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids") or []),
            reason=str(value.get("reason") or ""),
        ) for value in row.get("key_fields") or [] if isinstance(value, dict)),
        primary_evidence=evidence_list("primary_evidence"),
        independent_evidence=evidence_list("independent_evidence"),
        syndicated_copies=evidence_list("syndicated_copies"),
        contradicting_evidence=evidence_list("contradicting_evidence"),
        status_history=tuple(StatusTransition(
            from_status=VerificationStatus(str(value["from_status"])) if value.get("from_status") else None,
            to_status=VerificationStatus(str(value["to_status"])),
            changed_at=_parse_datetime(value["changed_at"]),
            reason=str(value.get("reason") or ""),
        ) for value in row.get("status_history") or [] if isinstance(value, dict)),
        holding_relevance=str(row.get("holding_relevance") or "none"),
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
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            with CACHE_IO_LOCK:
                value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def load_current(self) -> EvidenceSnapshot | None:
        document = self._read_json(self.current_path)
        if not document:
            return None
        try:
            return EvidenceSnapshot(
                snapshot_id=str(document["snapshot_id"]),
                generated_at=_parse_datetime(document["generated_at"]),
                events=tuple(_event_from_document(row) for row in document.get("events") or [] if isinstance(row, dict)),
            )
        except (KeyError, TypeError, ValueError):
            return None

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
            self._append_transitions(snapshot, previous)
            self._atomic_write(self.last_refresh_path, {
                "status": "completed",
                "attempted_at": snapshot.generated_at.isoformat(),
                "last_successful_refresh_at": snapshot.generated_at.isoformat(),
                "error": None,
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
