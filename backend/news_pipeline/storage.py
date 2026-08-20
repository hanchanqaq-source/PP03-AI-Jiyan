from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Callable, Iterator

from cache_io_lock import CACHE_IO_LOCK
from evidence_verification.models import EvidenceSnapshot, FieldVerificationStatus, VerificationStatus
from evidence_verification.storage import (
    event_document,
    evidence_snapshot_from_document,
    field_document,
    snapshot_document,
    validated_snapshot_document,
)
from .models import PipelineCounts, PipelinePhase, PipelineRun, RawSnapshot, TrustedSnapshot


_SAFE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_NONTERMINAL = {PipelinePhase.QUEUED, PipelinePhase.FETCHING, PipelinePhase.RAW_SAVED, PipelinePhase.VERIFYING, PipelinePhase.EVIDENCE_SAVED}
_TERMINAL = {PipelinePhase.TRUSTED_PUBLISHED, PipelinePhase.FAILED, PipelinePhase.INTERRUPTED}
_NEXT = {
    PipelinePhase.QUEUED: {PipelinePhase.FETCHING, PipelinePhase.FAILED, PipelinePhase.INTERRUPTED},
    PipelinePhase.FETCHING: {PipelinePhase.RAW_SAVED, PipelinePhase.FAILED, PipelinePhase.INTERRUPTED},
    PipelinePhase.RAW_SAVED: {PipelinePhase.VERIFYING, PipelinePhase.FAILED, PipelinePhase.INTERRUPTED},
    PipelinePhase.VERIFYING: {PipelinePhase.EVIDENCE_SAVED, PipelinePhase.FAILED, PipelinePhase.INTERRUPTED},
    PipelinePhase.EVIDENCE_SAVED: {PipelinePhase.TRUSTED_PUBLISHED, PipelinePhase.FAILED, PipelinePhase.INTERRUPTED},
}
_MAX_BYTES, _MAX_DEPTH, _MAX_ENTRIES, _MAX_TEXT, _MAX_INT = 1_048_576, 16, 5_000, 8_192, 1_000_000_000
_RUN_KEYS = {"schema_version", "run_id", "raw_snapshot_id", "evidence_snapshot_id", "trusted_snapshot_id", "phase", "counts", "created_at", "updated_at", "redacted_error", "displayed_trusted_snapshot_id"}
_RAW_V1_KEYS = {"schema_version", "raw_snapshot_id", "collected_at", "items"}
_RAW_V2_KEYS = {
    "schema_version", "raw_snapshot_id", "collected_at", "items", "source_statuses",
    "total_source_count", "failed_source_count", "cache_status", "source_state",
}
_GENERATION_KEYS = {"schema_version", "generation"}
_POINTER_KEYS = {"schema_version", "generation", "raw_snapshot_id", "trusted_digest"}
_TRUSTED_KEYS = {"schema_version", "pointer_generation", "raw_snapshot_id", "published_at", "events"}
_INTENT_KEYS = {"schema_version", "expected_generation", "target_generation", "raw_snapshot_id", "trusted_digest"}
_COMPLETE_KEYS = {"schema_version", "expected_generation", "target_generation", "generation", "raw_snapshot_id", "trusted_digest"}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TEMP_NONCE = re.compile(r"^[a-z0-9_-]{8}$", re.IGNORECASE)
_MAX_TEMP_SCAN = 1024
_MAX_TEMP_CLEANUP = 128
_SAFE_ERROR_CODES = {
    "collection_failed",
    "evidence_compatibility_failed",
    "evidence_persistence_failed",
    "pipeline_error",
    "pipeline_interrupted",
    "publication_failed",
    "storage_error",
    "verification_failed",
}


@dataclass(frozen=True, slots=True)
class _WriterCapability:
    generation: int
    lock_handle: Any
    creator_pid: int


@dataclass(frozen=True, slots=True)
class _TrustedRecord:
    snapshot: TrustedSnapshot
    pointer_generation: int


@dataclass(frozen=True, slots=True)
class _PointerRecord:
    raw_snapshot_id: str
    generation: int
    trusted_digest: str


_SOURCE_KEYS = {
    "source_name", "source_url", "original_url", "published_at", "fetched_at",
    "title", "summary_or_excerpt", "language", "region", "data_status",
}
_SOURCE_STATUS_KEYS = {
    "source_id", "source_name", "source_url", "status", "error_type",
    "error_reason", "last_success_at", "used_cached_items", "item_count",
}
_CACHE_STATUSES = {"unknown", "realtime", "partial", "cache", "stale", "source_failure", "empty"}
_SOURCE_STATES = {"unknown", "all_success", "partial_failure", "cached", "stale_cache", "all_failed", "empty"}
_MARKET_EVENT_KEYS = {
    "event_id", "title", "summary", "summary_status", "category",
    "published_at_first", "published_at_latest", "sources", "source_count",
    "related_tags", "tag_evidence", "related_companies", "related_funds",
    "relation_level", "relation_evidence", "impact_tendency", "impact_basis",
    "confidence", "original_links", "data_status", "missing_information",
    "importance_score", "verification_status", "verification_reason", "verified_at",
    "verified_key_fields",
}
_TAG_KEYS = {"id", "name"}
_TAG_EVIDENCE_KEYS = {"id", "name", "provenance"}
_COMPANY_KEYS = {"stock_code", "stock_name"}
_FUND_KEYS = {"fund_code", "fund_name"}
_RELATION_FULL_KEYS = {
    "fund_code", "fund_name", "holding_disclosure_date", "stock_code", "stock_name",
    "industry_classification", "classification_standard", "matched_kind", "matched_value",
    "source_name", "source_reference",
}
_RELATION_WATCH_KEYS = {"matched_kind", "matched_value", "tag_id"}
_VERIFIED_FIELD_KEYS = {
    "field_name", "raw_value", "normalized_value", "verification_status",
    "evidence_ids", "reason",
}
_VERIFICATION_STATUSES = {"verified", "corroborated", "unverified", "conflicting", "corrected", "disproved"}
_TRUSTED_STATUSES = {"verified", "corroborated"}


def _json_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("invalid JSON object")
        result[key] = value
    return result


def _json_int(raw: str) -> int:
    if len(raw) > 11:
        raise ValueError("integer too large")
    value = int(raw)
    if not -_MAX_INT <= value <= _MAX_INT:
        raise ValueError("integer too large")
    return value


def _no_float(_: str) -> float:
    raise ValueError("non-integer numbers are not allowed")


def _bounded(value: object, depth: int = 0) -> object:
    if depth > _MAX_DEPTH:
        raise ValueError("JSON too deep")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -_MAX_INT <= value <= _MAX_INT:
            raise ValueError("integer too large")
        return value
    if type(value) is str:
        if len(value) > _MAX_TEXT:
            raise ValueError("text too large")
        return value
    if type(value) is list:
        if len(value) > _MAX_ENTRIES:
            raise ValueError("list too large")
        return [_bounded(item, depth + 1) for item in value]
    if type(value) is dict:
        if len(value) > _MAX_ENTRIES:
            raise ValueError("object too large")
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or len(key) > 128 or key in result:
                raise ValueError("invalid JSON key")
            result[key] = _bounded(item, depth + 1)
        return result
    raise ValueError("JSON requires exact built-in values")


def _id(value: object, name: str) -> str:
    if type(value) is not str or not _SAFE_ID.fullmatch(value) or value.split(".", 1)[0] in _RESERVED:
        raise ValueError(f"invalid {name}")
    return value


def _optional_id(value: object, name: str) -> str | None:
    return None if value is None else _id(value, name)


def _timestamp(value: object) -> datetime:
    if type(value) is not str or len(value) > 64:
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError("timestamp must be UTC")
    return parsed


def _safe_error(value: str) -> str:
    """Persist only a closed set of diagnostic codes, never caller text."""
    return value if type(value) is str and value in _SAFE_ERROR_CODES else "pipeline_error"


def _single_link(info: os.stat_result) -> bool:
    return info.st_nlink == 1 or os.name == "nt" and info.st_nlink == 0


def _counts(value: object) -> PipelineCounts:
    fields = set(PipelineCounts.__dataclass_fields__)
    if type(value) is not dict or set(value) != fields:
        raise ValueError("invalid pipeline counts")
    if any(type(item) is not int or item < 0 or item > _MAX_INT for item in value.values()):
        raise ValueError("invalid pipeline counts")
    return PipelineCounts(**value)


def _row(value: object, keys: set[str], name: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"invalid {name} schema")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT:
        raise ValueError(f"invalid {name}")
    return value


def _nullable_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _text_list(value: object, name: str) -> list[str]:
    if type(value) is not list or len(value) > _MAX_ENTRIES:
        raise ValueError(f"invalid {name}")
    return [_text(item, name) for item in value]


def _datetime_text(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    try:
        if type(value) is not str or len(value) > 64:
            raise ValueError("invalid timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp timezone is required")
    except (TypeError, ValueError):
        raise ValueError(f"invalid {name}") from None
    return value


def _validate_source(value: object) -> dict[str, object]:
    row = _row(value, _SOURCE_KEYS, "source")
    for key in _SOURCE_KEYS - {"published_at"}:
        _text(row[key], f"source {key}")
    _datetime_text(row["published_at"], "source published_at", optional=True)
    _datetime_text(row["fetched_at"], "source fetched_at")
    return row


def _validate_tag(value: object, *, evidence: bool = False) -> dict[str, object]:
    keys = _TAG_EVIDENCE_KEYS if evidence else _TAG_KEYS
    row = _row(value, keys, "tag evidence" if evidence else "tag")
    for key in keys:
        _text(row[key], f"tag {key}")
    return row


def _validate_named_row(value: object, keys: set[str], name: str) -> dict[str, object]:
    row = _row(value, keys, name)
    for key in keys:
        item = row[key]
        if item is not None:
            _text(item, f"{name} {key}")
    return row


def _validate_relation(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError("invalid relation evidence schema")
    keys = set(value)
    if keys == _RELATION_FULL_KEYS:
        return _validate_named_row(value, _RELATION_FULL_KEYS, "relation evidence")
    if keys == _RELATION_WATCH_KEYS:
        return _validate_named_row(value, _RELATION_WATCH_KEYS, "relation evidence")
    raise ValueError("invalid relation evidence schema")


def _validate_verified_field(value: object) -> dict[str, object]:
    row = _row(value, _VERIFIED_FIELD_KEYS, "verified key field")
    for key in ("field_name", "raw_value", "normalized_value", "reason"):
        _text(row[key], f"verified key field {key}")
    status = _text(row["verification_status"], "verified key field status")
    if status not in _TRUSTED_STATUSES:
        raise ValueError("invalid verified key field status")
    _text_list(row["evidence_ids"], "verified key field evidence_ids")
    return row


def _validate_market_event(value: object, *, trusted: bool) -> dict[str, object]:
    row = _row(value, _MARKET_EVENT_KEYS, "trusted event" if trusted else "raw event")
    for key in (
        "event_id", "title", "summary", "summary_status", "category", "relation_level",
        "impact_tendency", "confidence", "data_status",
    ):
        _text(row[key], f"event {key}")
    _datetime_text(row["published_at_first"], "published_at_first", optional=True)
    _datetime_text(row["published_at_latest"], "published_at_latest", optional=True)
    for key, validator in (
        ("sources", _validate_source),
        ("related_tags", _validate_tag),
        ("tag_evidence", lambda item: _validate_tag(item, evidence=True)),
        ("related_companies", lambda item: _validate_named_row(item, _COMPANY_KEYS, "related company")),
        ("related_funds", lambda item: _validate_named_row(item, _FUND_KEYS, "related fund")),
        ("relation_evidence", _validate_relation),
        ("verified_key_fields", _validate_verified_field),
    ):
        values = row[key]
        if type(values) is not list or len(values) > _MAX_ENTRIES:
            raise ValueError(f"invalid event {key}")
        for item in values:
            validator(item)
    if type(row["source_count"]) is not int or row["source_count"] != len(row["sources"]):
        raise ValueError("invalid event source_count")
    if type(row["importance_score"]) is not int or not 0 <= row["importance_score"] <= _MAX_INT:
        raise ValueError("invalid event importance_score")
    for key in ("impact_basis", "original_links", "missing_information"):
        _text_list(row[key], f"event {key}")
    status = _nullable_text(row["verification_status"], "verification_status")
    if status is not None and status not in _VERIFICATION_STATUSES:
        raise ValueError("invalid verification_status")
    if trusted and status not in _TRUSTED_STATUSES:
        raise ValueError("invalid trusted verification_status")
    _nullable_text(row["verification_reason"], "verification_reason")
    verified_at = row["verified_at"]
    _datetime_text(verified_at, "verified_at", optional=not trusted)
    if not trusted and verified_at is None and status is not None:
        raise ValueError("verification status requires verified_at")
    return row


def _validate_raw_items(value: object) -> list[dict[str, object]]:
    if type(value) is not list or len(value) > _MAX_ENTRIES:
        raise ValueError("raw items must be a list")
    result: list[dict[str, object]] = []
    for item in value:
        if type(item) is not dict:
            raise ValueError("invalid raw event schema")
        if set(item) == _SOURCE_KEYS:
            result.append(_validate_source(item))
        else:
            result.append(_validate_market_event(item, trusted=False))
    return result


def _validate_source_statuses(value: object) -> list[dict[str, object]]:
    if type(value) is not list or len(value) > _MAX_ENTRIES:
        raise ValueError("source statuses must be a list")
    result: list[dict[str, object]] = []
    for value_row in value:
        row = _row(value_row, _SOURCE_STATUS_KEYS, "source status")
        for key in ("source_id", "source_name", "source_url"):
            _text(row[key], f"source status {key}")
        if row["status"] not in {"ok", "failed"}:
            raise ValueError("invalid source status")
        for key in ("error_type", "error_reason", "last_success_at"):
            _nullable_text(row[key], f"source status {key}")
        if type(row["used_cached_items"]) is not bool:
            raise ValueError("invalid source status cache flag")
        if type(row["item_count"]) is not int or not 0 <= row["item_count"] <= _MAX_INT:
            raise ValueError("invalid source status item count")
        result.append(row)
    return result


def canonical_source_statuses(value: object) -> tuple[dict[str, object], ...]:
    """Return the exact durable source-status shape accepted by raw storage."""
    if type(value) is not tuple:
        raise ValueError("source statuses must be a tuple")
    return tuple(_validate_source_statuses(_bounded(list(value))))


def _validate_trusted_events(value: object) -> list[dict[str, object]]:
    if type(value) is not list or len(value) > _MAX_ENTRIES:
        raise ValueError("trusted events must be a list")
    return [_validate_market_event(item, trusted=True) for item in value]


def _document_digest(document: dict[str, object]) -> str:
    checked = _bounded(document)
    if type(checked) is not dict:
        raise ValueError("trusted document must be an object")
    payload = json.dumps(checked, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _trusted_document(snapshot: TrustedSnapshot, events: list[dict[str, object]], generation: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "pointer_generation": generation,
        "raw_snapshot_id": snapshot.raw_snapshot_id,
        "published_at": snapshot.published_at.isoformat(),
        "events": events,
    }


def _validate_trusted_admission(events: list[dict[str, object]], evidence: EvidenceSnapshot) -> None:
    evidence_by_id: dict[str, object] = {}
    for event in evidence.events:
        if type(event.event_id) is not str or not event.event_id or event.event_id in evidence_by_id:
            raise ValueError("evidence event identity is ambiguous")
        evidence_by_id[event.event_id] = event
    seen: set[str] = set()
    approved_event_statuses = {VerificationStatus.VERIFIED, VerificationStatus.CORROBORATED}
    approved_field_statuses = {FieldVerificationStatus.VERIFIED, FieldVerificationStatus.CORROBORATED}
    eligible = {
        event_id
        for event_id, event in evidence_by_id.items()
        if event.verification_status in approved_event_statuses
    }
    for trusted in events:
        event_id = trusted["event_id"]
        if event_id in seen:
            raise ValueError("trusted event identity is duplicated")
        seen.add(event_id)
        evidence_event = evidence_by_id.get(event_id)
        if evidence_event is None or evidence_event.verification_status not in approved_event_statuses:
            raise ValueError("trusted event requires approved same-snapshot evidence")
        if trusted["verification_status"] != evidence_event.verification_status.value:
            raise ValueError("trusted event status must match evidence")
        canonical = event_document(evidence_event)
        if trusted["title"] != canonical["title"] or trusted["summary"] != canonical["summary"]:
            raise ValueError("trusted title and summary must match canonical evidence text")
        approved_fields = [
            field_document(field)
            for field in evidence_event.key_fields
            if field.verification_status in approved_field_statuses
        ]
        approved_names = [field["field_name"] for field in approved_fields]
        if len(approved_names) != len(set(approved_names)):
            raise ValueError("approved evidence key field identity is ambiguous")
        seen_fields: set[str] = set()
        for trusted_field in trusted["verified_key_fields"]:
            field_name = trusted_field["field_name"]
            if field_name in seen_fields or trusted_field not in approved_fields:
                raise ValueError("trusted key field requires approved same-snapshot evidence")
            seen_fields.add(field_name)
    if seen != eligible:
        raise ValueError("trusted event IDs must equal eligible evidence set")


class NewsPipelineStorage:
    def __init__(self, root: str | os.PathLike[str] | None = None, *, now: Callable[[], datetime] | None = None, lock_timeout_seconds: float = 2.0) -> None:
        if root is None:
            configured = os.environ.get("VR_DATA_DIR")
            root = Path(configured) / "evidence-verification" / "v1" if configured else Path(os.environ.get("USERPROFILE") or Path.home()) / ".vibe-research" / "evidence-verification" / "v1"
        self.root = Path(os.path.abspath(root))
        self.raw_root, self.evidence_root, self.trusted_root, self.runs_root = (self.root / name for name in ("raw", "evidence", "trusted", "runs"))
        self.current_pointer_path, self._lock_path = self.root / "current-trusted.json", self.root / ".news-pipeline.lock"
        self._generation_path = self.root / ".news-pipeline-generation.json"
        self._writer_lock_path = self.root / ".news-pipeline-writer.lock"
        self._publication_intent_path = self.root / ".current-trusted-intent.json"
        self._publication_complete_path = self.root / ".current-trusted-complete.json"
        self._writer: _WriterCapability | None = None
        self._closed = False
        self._now = now or (lambda: datetime.now(timezone.utc))
        if type(lock_timeout_seconds) not in (int, float) or lock_timeout_seconds <= 0:
            raise ValueError("invalid lock timeout")
        self._lock_timeout = float(lock_timeout_seconds)

    def _clock(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("clock must be aware UTC")
        return value

    def _prepare(self) -> None:
        self._ensure_directory(self.root)
        self._verify_directory_chain(self.root)

    @staticmethod
    def _is_safe_directory(path: Path) -> bool:
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            return False
        return stat.S_ISDIR(info.st_mode) and not getattr(info, "st_reparse_tag", 0)

    @classmethod
    def _ensure_directory(cls, path: Path) -> None:
        missing: list[Path] = []
        cursor = path
        while not cursor.exists():
            missing.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                raise OSError("storage_error")
            cursor = parent
        if not cls._is_safe_directory(cursor):
            raise OSError("storage_error")
        for directory in reversed(missing):
            try:
                directory.mkdir()
                cls._sync_dir(directory.parent)
            except OSError:
                raise OSError("storage_error") from None
            if not cls._is_safe_directory(directory):
                raise OSError("storage_error")

    def _inside_root(self, path: Path) -> bool:
        try:
            return os.path.commonpath((str(self.root), os.path.abspath(path))) == str(self.root)
        except (OSError, ValueError):
            return False

    def _verify_directory_chain(self, directory: Path) -> None:
        if not self._inside_root(directory):
            raise OSError("storage_error")
        relative = directory.relative_to(self.root)
        cursor = self.root
        if not self._is_safe_directory(cursor):
            raise OSError("storage_error")
        for component in relative.parts:
            cursor = cursor / component
            if not self._is_safe_directory(cursor):
                raise OSError("storage_error")

    def _verify_parent(self, path: Path) -> None:
        if not self._inside_root(path):
            raise OSError("storage_error")
        self._verify_directory_chain(path.parent)

    def _open_lock_file(self, path: Path) -> Any:
        self._verify_parent(path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            try:
                before = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                before = None
            if before is not None and (
                not stat.S_ISREG(before.st_mode)
                or getattr(before, "st_reparse_tag", 0)
                or not _single_link(before)
            ):
                raise OSError("storage_error")
            if before is None:
                try:
                    descriptor = os.open(path, flags | os.O_EXCL, 0o600)
                except FileExistsError:
                    before = path.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or getattr(before, "st_reparse_tag", 0)
                        or not _single_link(before)
                    ):
                        raise OSError("storage_error")
                    descriptor = os.open(path, flags, 0o600)
            else:
                descriptor = os.open(path, flags, 0o600)
            opened = os.stat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if (
                not stat.S_ISREG(opened.st_mode)
                or getattr(opened, "st_reparse_tag", 0)
                or not _single_link(opened)
                or before is not None
                and (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise OSError("storage_error")
            try:
                handle = io.FileIO(descriptor, mode="r+", closefd=True)
            except BaseException:
                self._close_owned_descriptor(descriptor, identity)
                descriptor = None
                raise
            descriptor = None
            return handle
        except OSError:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise OSError("storage_error") from None

    @staticmethod
    def _try_lock(handle: Any) -> bool:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            return False

    @staticmethod
    def _unlock(handle: Any) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        try:
            self._prepare()
            handle = self._open_lock_file(self._lock_path)
        except OSError:
            raise OSError("storage_error") from None
        acquired = False
        try:
            deadline = time.monotonic() + self._lock_timeout
            while not (acquired := self._try_lock(handle)):
                if time.monotonic() >= deadline:
                    raise OSError("storage_lock_unavailable")
                time.sleep(0.01)
            yield
        finally:
            if acquired:
                self._unlock(handle)
            try:
                handle.close()
            except OSError:
                pass

    @staticmethod
    def _sync_dir(path: Path) -> None:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            create_file = ctypes.windll.kernel32.CreateFileW
            create_file.argtypes = (
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            )
            create_file.restype = wintypes.HANDLE
            handle = create_file(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x02000000, None)
            invalid = ctypes.c_void_p(-1).value
            if handle == invalid:
                raise OSError("storage_error")
            try:
                if not ctypes.windll.kernel32.FlushFileBuffers(handle):
                    error = ctypes.windll.kernel32.GetLastError()
                    # Some supported Windows filesystems do not expose directory
                    # flushes. File publication still uses MOVEFILE_WRITE_THROUGH.
                    if error not in {1, 5, 50}:
                        raise OSError("storage_error")
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
            return
        descriptor = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _replace_durable(source: Path, destination: Path) -> None:
        if os.name == "nt":
            import ctypes

            move_file_ex = ctypes.windll.kernel32.MoveFileExW
            move_file_ex.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
            move_file_ex.restype = ctypes.c_int
            # MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH makes the
            # directory entry durable before returning on Windows.
            if not move_file_ex(str(source), str(destination), 0x1 | 0x8):
                raise ctypes.WinError()
            return
        os.replace(source, destination)
        NewsPipelineStorage._sync_dir(destination.parent)

    @staticmethod
    def _replace_final_commit(source: Path, destination: Path) -> None:
        """Make the pointer replacement the final propagated publication operation."""
        if os.name == "nt":
            import ctypes

            move_file_ex = ctypes.windll.kernel32.MoveFileExW
            move_file_ex.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
            move_file_ex.restype = ctypes.c_int
            if not move_file_ex(str(source), str(destination), 0x1 | 0x8):
                raise ctypes.WinError()
            return
        os.replace(source, destination)
        try:
            NewsPipelineStorage._sync_dir(destination.parent)
        except OSError:
            # The namespace already names the new pointer. Propagating a
            # post-rename fsync error would let the caller report a failed run
            # while readers observe the new current snapshot. Treat this as an
            # ambiguous-but-committed durability result; a later write must not
            # attempt to roll the pointer back.
            pass

    @staticmethod
    def _close_owned_descriptor(descriptor: int, identity: tuple[int, int]) -> None:
        try:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != identity:
                return
        except OSError:
            return
        try:
            os.close(descriptor)
        except OSError:
            pass

    @staticmethod
    def _cleanup_owned_temp(path: Path, identity: tuple[int, int]) -> None:
        try:
            current = path.lstat()
            if (
                stat.S_ISREG(current.st_mode)
                and (current.st_dev, current.st_ino) == identity
            ):
                path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return

    def _atomic_write(self, path: Path, document: dict[str, object], *, final_commit: bool = False) -> None:
        checked = _bounded(document)
        if type(checked) is not dict:
            raise ValueError("atomic document must be an object")
        payload = (json.dumps(checked, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > _MAX_BYTES:
            raise ValueError("JSON document too large")
        descriptor: int | None = None
        temporary: Path | None = None
        identity: tuple[int, int] | None = None
        try:
            self._ensure_directory(path.parent)
            self._verify_parent(path)
            descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temporary = Path(raw)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            try:
                handle = os.fdopen(descriptor, "wb")
            except BaseException:
                self._close_owned_descriptor(descriptor, identity)
                descriptor = None
                raise
            descriptor = None
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if final_commit:
                self._replace_final_commit(temporary, path)
            else:
                self._replace_durable(temporary, path)
        except OSError:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None and identity is not None:
                self._cleanup_owned_temp(temporary, identity)
            raise OSError("storage_error") from None
        except BaseException:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None and identity is not None:
                self._cleanup_owned_temp(temporary, identity)
            raise

    def _read(self, path: Path) -> dict[str, object] | None:
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            self._prepare()
            self._verify_parent(path)
            before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or getattr(before, "st_reparse_tag", 0) or not _single_link(before):
                return None
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if identity != (before.st_dev, before.st_ino) or not _single_link(opened):
                return None
            try:
                handle = os.fdopen(descriptor, "rb")
            except BaseException:
                self._close_owned_descriptor(descriptor, identity)
                descriptor = None
                raise
            descriptor = None
            with handle:
                raw = handle.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                return None
            result = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object, parse_int=_json_int, parse_float=_no_float, parse_constant=_no_float)
            checked = _bounded(result)
            return checked if type(checked) is dict else None
        except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError):
            return None
        finally:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    self._close_owned_descriptor(descriptor, identity)

    def _path(self, directory: Path, raw_snapshot_id: str) -> Path:
        return directory / f"{_id(raw_snapshot_id, 'raw_snapshot_id')}.json"

    def _run_path(self, run_id: str) -> Path:
        return self.runs_root / f"{_id(run_id, 'run_id')}.json"

    @staticmethod
    def _schema(document: dict[str, object], keys: set[str], *, version: int = 1) -> None:
        if (
            set(document) != keys
            or type(document.get("schema_version")) is not int
            or document["schema_version"] != version
        ):
            raise ValueError("invalid pipeline schema")

    def _load_generation(self) -> int | None:
        document = self._read(self._generation_path)
        if document is None:
            if self._generation_path.exists():
                raise OSError("storage_corrupt")
            return None
        try:
            self._schema(document, _GENERATION_KEYS)
            generation = document["generation"]
            if type(generation) is not int or generation < 1:
                raise ValueError("invalid generation")
            return generation
        except (KeyError, TypeError, ValueError):
            raise OSError("storage_corrupt") from None

    def _has_persisted_state_without_generation(self) -> bool:
        if self.current_pointer_path.exists() or self._publication_intent_path.exists() or self._publication_complete_path.exists():
            return True
        for directory in (self.raw_root, self.evidence_root, self.trusted_root, self.runs_root):
            try:
                if directory.exists():
                    self._verify_directory_chain(directory)
                if directory.exists() and any(directory.iterdir()):
                    return True
            except OSError:
                raise OSError("storage_corrupt") from None
        return False

    def _owned_temp_target(self, directory: Path, name: str) -> str | None:
        if not name.startswith(".") or not name.endswith(".tmp"):
            return None
        body = name[1:-4]
        try:
            target, nonce = body.rsplit(".", 1)
        except ValueError:
            return None
        if not _TEMP_NONCE.fullmatch(nonce):
            return None
        if directory == self.root:
            allowed = {
                self._generation_path.name,
                self.current_pointer_path.name,
                self._publication_intent_path.name,
                self._publication_complete_path.name,
            }
            return target if target in allowed else None
        if directory not in {self.raw_root, self.evidence_root, self.trusted_root, self.runs_root} or not target.endswith(".json"):
            return None
        try:
            _id(target[:-5], "temporary artifact identity")
        except ValueError:
            return None
        return target

    def _cleanup_stale_owned_temps(self) -> None:
        scanned = 0
        removed = 0
        removed_by_directory: set[Path] = set()
        for directory in (self.root, self.raw_root, self.evidence_root, self.trusted_root, self.runs_root):
            if not directory.exists():
                continue
            self._verify_directory_chain(directory)
            try:
                entries = os.scandir(directory)
            except OSError:
                raise OSError("storage_error") from None
            with entries:
                for entry in entries:
                    scanned += 1
                    if scanned > _MAX_TEMP_SCAN or removed >= _MAX_TEMP_CLEANUP:
                        break
                    if self._owned_temp_target(directory, entry.name) is None:
                        continue
                    try:
                        before = Path(entry.path)
                        info = before.lstat()
                    except OSError:
                        continue
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or getattr(info, "st_reparse_tag", 0)
                        or not _single_link(info)
                    ):
                        continue
                    self._cleanup_owned_temp(before, (info.st_dev, info.st_ino))
                    if not before.exists():
                        removed += 1
                        removed_by_directory.add(directory)
            if scanned > _MAX_TEMP_SCAN:
                break
        for directory in removed_by_directory:
            self._sync_dir(directory)

    def _claim_writer_unlocked(self) -> _WriterCapability:
        if self._closed:
            raise ValueError("pipeline storage is closed")
        if self._writer is not None:
            if self._writer.creator_pid != os.getpid():
                raise ValueError("pipeline writer belongs to another process")
            generation = self._load_generation()
            if generation != self._writer.generation:
                raise ValueError("pipeline writer generation is stale")
            return self._writer
        self._prepare()
        handle = self._open_lock_file(self._writer_lock_path)
        if not self._try_lock(handle):
            try:
                handle.close()
            except OSError:
                pass
            raise ValueError("pipeline writer is active")
        try:
            current = self._load_generation()
            if current is None and self._has_persisted_state_without_generation():
                raise OSError("storage_corrupt")
            generation = (current or 0) + 1
            self._atomic_write(self._generation_path, {"schema_version": 1, "generation": generation})
            capability = _WriterCapability(generation, handle, os.getpid())
            self._writer = capability
            try:
                self._cleanup_stale_owned_temps()
            except OSError:
                raise OSError("storage_error") from None
            return capability
        except BaseException:
            self._writer = None
            self._unlock(handle)
            try:
                handle.close()
            except OSError:
                pass
            raise

    def _require_writer(self) -> _WriterCapability:
        return self._claim_writer_unlocked()

    def close(self) -> None:
        writer, self._writer = self._writer, None
        self._closed = True
        if writer is not None:
            if writer.creator_pid != os.getpid():
                # A forked child owns only its inherited descriptor reference.
                # LOCK_UN would release the parent's open-file-description
                # lock, while returning without close would keep the parent
                # lock alive after the parent closes its own reference.
                try:
                    writer.lock_handle.close()
                except OSError:
                    pass
                return
            self._unlock(writer.lock_handle)
            try:
                writer.lock_handle.close()
            except OSError:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def write_raw(self, snapshot: RawSnapshot) -> None:
        raw_id = _id(snapshot.raw_snapshot_id, "raw_snapshot_id")
        if not isinstance(snapshot.collected_at, datetime) or snapshot.collected_at.tzinfo is None or snapshot.collected_at.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("collected_at must be aware UTC")
        items = _validate_raw_items(_bounded(list(snapshot.items)))
        source_statuses = _validate_source_statuses(_bounded(list(snapshot.source_statuses)))
        if (
            type(snapshot.total_source_count) is not int
            or type(snapshot.failed_source_count) is not int
            or not 0 <= snapshot.failed_source_count <= snapshot.total_source_count <= _MAX_INT
        ):
            raise ValueError("invalid raw source counts")
        if snapshot.cache_status not in _CACHE_STATUSES or snapshot.source_state not in _SOURCE_STATES:
            raise ValueError("invalid raw source state")
        document: dict[str, object] = {
            "schema_version": 2,
            "raw_snapshot_id": raw_id,
            "collected_at": snapshot.collected_at.isoformat(),
            "items": items,
            "source_statuses": source_statuses,
            "total_source_count": snapshot.total_source_count,
            "failed_source_count": snapshot.failed_source_count,
            "cache_status": snapshot.cache_status,
            "source_state": snapshot.source_state,
        }
        with CACHE_IO_LOCK, self._process_lock():
            self._require_writer()
            path = self._path(self.raw_root, raw_id)
            previous = self._load_raw(raw_id)
            if previous is None and path.exists():
                raise OSError("storage_corrupt")
            if previous is not None and previous != snapshot:
                raise ValueError("raw snapshot identity is immutable")
            if previous is None:
                self._atomic_write(path, document)

    def _load_raw(self, raw_id: str) -> RawSnapshot | None:
        document = self._read(self._path(self.raw_root, raw_id))
        try:
            if document is None:
                return None
            version = document.get("schema_version")
            if version == 1:
                self._schema(document, _RAW_V1_KEYS)
            elif version == 2:
                self._schema(document, _RAW_V2_KEYS, version=2)
            else:
                return None
            identity, items = _id(document["raw_snapshot_id"], "raw_snapshot_id"), document["items"]
            if identity != raw_id:
                return None
            if version == 1:
                return RawSnapshot(
                    identity,
                    _timestamp(document["collected_at"]),
                    tuple(_validate_raw_items(items)),
                )
            total_source_count = document["total_source_count"]
            failed_source_count = document["failed_source_count"]
            cache_status = document["cache_status"]
            source_state = document["source_state"]
            if (
                type(total_source_count) is not int
                or type(failed_source_count) is not int
                or not 0 <= failed_source_count <= total_source_count <= _MAX_INT
                or cache_status not in _CACHE_STATUSES
                or source_state not in _SOURCE_STATES
            ):
                return None
            return RawSnapshot(
                identity,
                _timestamp(document["collected_at"]),
                tuple(_validate_raw_items(items)),
                tuple(_validate_source_statuses(document["source_statuses"])),
                total_source_count,
                failed_source_count,
                cache_status,
                source_state,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def load_raw(self, raw_snapshot_id: str) -> RawSnapshot | None:
        with CACHE_IO_LOCK, self._process_lock():
            return self._load_raw(_id(raw_snapshot_id, "raw_snapshot_id"))

    def write_evidence(self, snapshot: EvidenceSnapshot) -> None:
        if snapshot.recovery_metadata.get("legacy_identity") is True:
            raise ValueError("legacy evidence identity cannot be published as a new pipeline artifact")
        _id(snapshot.snapshot_id, "snapshot_id")
        raw_id = _id(snapshot.raw_snapshot_id, "raw_snapshot_id")
        document = validated_snapshot_document(snapshot)
        _bounded(document)
        with CACHE_IO_LOCK, self._process_lock():
            self._require_writer()
            path = self._path(self.evidence_root, raw_id)
            previous = self._load_evidence(raw_id)
            if previous is None and path.exists():
                raise OSError("storage_corrupt")
            if previous is not None and snapshot_document(previous) != document:
                raise ValueError("evidence snapshot identity is immutable")
            if previous is None:
                self._atomic_write(path, document)

    def _load_evidence(self, raw_id: str) -> EvidenceSnapshot | None:
        document = self._read(self._path(self.evidence_root, raw_id))
        if document is None:
            return None
        try:
            snapshot = evidence_snapshot_from_document(document)
            return snapshot if snapshot.raw_snapshot_id == raw_id else None
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            return None

    def load_evidence(self, raw_snapshot_id: str) -> EvidenceSnapshot | None:
        with CACHE_IO_LOCK, self._process_lock():
            return self._load_evidence(_id(raw_snapshot_id, "raw_snapshot_id"))

    def publish_trusted(self, snapshot: TrustedSnapshot) -> None:
        if type(snapshot) is not TrustedSnapshot or type(snapshot.events) is not tuple:
            raise ValueError("invalid trusted snapshot")
        raw_id = _id(snapshot.raw_snapshot_id, "raw_snapshot_id")
        if type(snapshot.published_at) is not datetime or snapshot.published_at.tzinfo is None or snapshot.published_at.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("published_at must be aware UTC")
        events = _validate_trusted_events(_bounded(list(snapshot.events)))
        with CACHE_IO_LOCK, self._process_lock():
            evidence = self._load_evidence(raw_id)
            if self._load_raw(raw_id) is None or evidence is None:
                raise ValueError("raw and evidence artifacts must be durable before trusted publish")
            if evidence.recovery_metadata.get("legacy_identity") is True:
                raise ValueError("legacy evidence must be deterministically reverified before trusted publish")
            _validate_trusted_admission(events, evidence)
            self._require_writer()
            initial_document = _trusted_document(snapshot, events, 1)
            initial_intent = {
                "raw_snapshot_id": raw_id,
                "expected_generation": 0,
                "target_generation": 1,
                "trusted_digest": _document_digest(initial_document),
            }
            pointer = self._load_pointer_record(allow_initial_intent=initial_intent)
            current_generation = pointer.generation if pointer is not None else 0
            target_generation = current_generation + 1
            document = _trusted_document(snapshot, events, target_generation)
            intent = {
                "raw_snapshot_id": raw_id,
                "expected_generation": current_generation,
                "target_generation": target_generation,
                "trusted_digest": _document_digest(document),
            }
            path = self._path(self.trusted_root, raw_id)
            existing = self._load_trusted_record(raw_id)
            if existing is None and path.exists():
                raise OSError("storage_corrupt")
            if existing is not None and existing.snapshot != snapshot:
                raise ValueError("trusted snapshot identity is immutable")
            if pointer is not None and pointer.raw_snapshot_id == raw_id:
                committed = {
                    "raw_snapshot_id": raw_id,
                    "expected_generation": pointer.generation - 1,
                    "target_generation": pointer.generation,
                    "trusted_digest": pointer.trusted_digest,
                }
                if (
                    existing is None
                    or existing.pointer_generation != pointer.generation
                    or _document_digest(_trusted_document(existing.snapshot, list(existing.snapshot.events), existing.pointer_generation)) != pointer.trusted_digest
                    or self._load_publication_intent() != committed
                    or self._load_publication_complete() != {
                        **committed,
                        "generation": committed["target_generation"],
                    }
                ):
                    raise OSError("trusted pointer is corrupt")
                return
            if existing is not None and existing.pointer_generation != target_generation:
                raise ValueError("trusted pointer cannot move backwards")
            self._write_publication_intent(intent, pointer)
            if existing is None:
                self._atomic_write(path, document)
            durable = self._load_trusted_record(raw_id)
            if (
                durable is None
                or durable.pointer_generation != target_generation
                or _document_digest(_trusted_document(durable.snapshot, list(durable.snapshot.events), durable.pointer_generation)) != intent["trusted_digest"]
            ):
                raise OSError("trusted snapshot did not become durable")
            self._write_publication_complete(intent, pointer)
            self._write_current_pointer(intent)

    def _write_current_pointer(self, intent: dict[str, object]) -> None:
        expected_generation = intent["expected_generation"]
        current = self._load_pointer_record(allow_initial_intent=intent if expected_generation == 0 else None)
        actual_generation = current.generation if current is not None else 0
        if actual_generation != expected_generation:
            raise ValueError("trusted pointer generation changed")
        if self._load_publication_intent() != intent or self._load_publication_complete() != {
            **intent,
            "generation": intent["target_generation"],
        }:
            raise OSError("storage_corrupt")
        self._atomic_write(
            self.current_pointer_path,
            {
                "schema_version": 1,
                "generation": intent["target_generation"],
                "raw_snapshot_id": intent["raw_snapshot_id"],
                "trusted_digest": intent["trusted_digest"],
            },
            final_commit=True,
        )

    def _write_publication_complete(self, intent: dict[str, object], pointer: _PointerRecord | None) -> None:
        current = self._load_publication_complete()
        expected = {**intent, "generation": intent["target_generation"]}
        if current == expected:
            return
        if current is not None and (
            pointer is None
            or current["generation"] != pointer.generation
            or current["raw_snapshot_id"] != pointer.raw_snapshot_id
            or current["trusted_digest"] != pointer.trusted_digest
        ):
            raise OSError("storage_corrupt")
        self._atomic_write(
            self._publication_complete_path,
            {"schema_version": 1, **expected},
        )

    def _load_publication_complete(self) -> dict[str, object] | None:
        document = self._read(self._publication_complete_path)
        if document is None:
            if self._publication_complete_path.exists():
                raise OSError("storage_corrupt")
            return None
        try:
            self._schema(document, _COMPLETE_KEYS)
            raw_id = _id(document["raw_snapshot_id"], "raw_snapshot_id")
            expected = document["expected_generation"]
            target = document["target_generation"]
            generation = document["generation"]
            digest = document["trusted_digest"]
            if type(expected) is not int or type(target) is not int or type(generation) is not int or expected < 0 or target != expected + 1 or generation != target or type(digest) is not str or not _DIGEST.fullmatch(digest):
                raise ValueError("invalid publication completion")
            return {"raw_snapshot_id": raw_id, "expected_generation": expected, "target_generation": generation, "trusted_digest": digest, "generation": generation}
        except (KeyError, TypeError, ValueError):
            raise OSError("storage_corrupt") from None

    def _write_publication_intent(self, intent: dict[str, object], pointer: _PointerRecord | None) -> None:
        current = self._load_publication_intent()
        if current == intent:
            return
        if current is not None and (
            pointer is None
            or current["target_generation"] != pointer.generation
            or current["raw_snapshot_id"] != pointer.raw_snapshot_id
            or current["trusted_digest"] != pointer.trusted_digest
        ):
            raise OSError("storage_corrupt")
        self._atomic_write(
            self._publication_intent_path,
            {"schema_version": 1, **intent},
        )

    def _load_publication_intent(self) -> dict[str, object] | None:
        document = self._read(self._publication_intent_path)
        if document is None:
            if self._publication_intent_path.exists():
                raise OSError("storage_corrupt")
            return None
        try:
            self._schema(document, _INTENT_KEYS)
            raw_id = _id(document["raw_snapshot_id"], "raw_snapshot_id")
            expected = document["expected_generation"]
            target = document["target_generation"]
            digest = document["trusted_digest"]
            if type(expected) is not int or type(target) is not int or expected < 0 or target != expected + 1 or type(digest) is not str or not _DIGEST.fullmatch(digest):
                raise ValueError("invalid publication intent")
            return {"raw_snapshot_id": raw_id, "expected_generation": expected, "target_generation": target, "trusted_digest": digest}
        except (KeyError, TypeError, ValueError):
            raise OSError("storage_corrupt") from None

    def _load_trusted_record(self, raw_id: str) -> _TrustedRecord | None:
        document = self._read(self._path(self.trusted_root, raw_id))
        try:
            if document is None:
                return None
            self._schema(document, _TRUSTED_KEYS)
            identity, events, pointer_generation = (
                _id(document["raw_snapshot_id"], "raw_snapshot_id"),
                document["events"],
                document["pointer_generation"],
            )
            if (
                identity != raw_id
                or type(events) is not list
                or any(type(item) is not dict for item in events)
                or type(pointer_generation) is not int
                or pointer_generation < 1
            ):
                return None
            return _TrustedRecord(
                TrustedSnapshot(identity, _timestamp(document["published_at"]), tuple(_validate_trusted_events(events))),
                pointer_generation,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _load_trusted(self, raw_id: str) -> TrustedSnapshot | None:
        record = self._load_trusted_record(raw_id)
        return record.snapshot if record is not None else None

    def load_trusted(self, raw_snapshot_id: str) -> TrustedSnapshot | None:
        with CACHE_IO_LOCK, self._process_lock():
            return self._load_trusted(_id(raw_snapshot_id, "raw_snapshot_id"))

    def _trusted_artifact_names(self) -> list[str]:
        if not self.trusted_root.exists():
            return []
        try:
            self._verify_directory_chain(self.trusted_root)
            return sorted(
                entry.name for entry in os.scandir(self.trusted_root)
                if entry.name.endswith(".json")
                and entry.is_file(follow_symlinks=False)
                and _single_link(entry.stat(follow_symlinks=False))
            )
        except OSError:
            raise OSError("storage_corrupt") from None

    def _load_pointer_record(self, *, allow_initial_intent: dict[str, object] | None = None) -> _PointerRecord | None:
        document = self._read(self.current_pointer_path)
        if document is None:
            if self.current_pointer_path.exists():
                raise OSError("storage_corrupt")
            artifacts = self._trusted_artifact_names()
            if artifacts:
                intent = self._load_publication_intent()
                completed = self._load_publication_complete()
                permitted = (
                    allow_initial_intent is not None
                    and artifacts == [f"{allow_initial_intent['raw_snapshot_id']}.json"]
                    and intent == allow_initial_intent
                    and (
                        completed is None
                        or completed == {
                            **allow_initial_intent,
                            "generation": allow_initial_intent["target_generation"],
                        }
                    )
                    and (trusted := self._load_trusted_record(str(allow_initial_intent["raw_snapshot_id"]))) is not None
                    and trusted.pointer_generation == 1
                    and _document_digest(_trusted_document(trusted.snapshot, list(trusted.snapshot.events), 1)) == allow_initial_intent["trusted_digest"]
                )
                if not permitted:
                    raise OSError("storage_corrupt")
            return None
        try:
            self._schema(document, _POINTER_KEYS)
            raw_id, generation, digest = _id(document["raw_snapshot_id"], "raw_snapshot_id"), document["generation"], document["trusted_digest"]
            if type(generation) is not int or generation < 1 or type(digest) is not str or not _DIGEST.fullmatch(digest):
                raise ValueError("invalid pointer generation")
            trusted = self._load_trusted_record(raw_id)
            if (
                trusted is None
                or trusted.pointer_generation != generation
                or _document_digest(_trusted_document(trusted.snapshot, list(trusted.snapshot.events), generation)) != digest
            ):
                raise ValueError("pointer target is unavailable")
            return _PointerRecord(raw_id, generation, digest)
        except (KeyError, TypeError, ValueError):
            raise OSError("storage_corrupt") from None

    def _load_current(self) -> TrustedSnapshot | None:
        pointer = self._load_pointer_record()
        return self._load_trusted(pointer.raw_snapshot_id) if pointer is not None else None

    def load_current_trusted(self) -> TrustedSnapshot | None:
        with CACHE_IO_LOCK, self._process_lock():
            return self._load_current()

    def _run_document(self, run: PipelineRun) -> dict[str, object]:
        if type(run.phase) is not PipelinePhase or type(run.counts) is not PipelineCounts or not isinstance(run.created_at, datetime) or not isinstance(run.updated_at, datetime):
            raise ValueError("invalid pipeline run")
        if any(value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None) for value in (run.created_at, run.updated_at)):
            raise ValueError("run timestamps must be aware UTC")
        if run.updated_at < run.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if run.redacted_error is not None and type(run.redacted_error) is not str:
            raise ValueError("invalid redacted_error")
        return {"schema_version": 1, "run_id": _id(run.run_id, "run_id"), "raw_snapshot_id": _id(run.raw_snapshot_id, "raw_snapshot_id"), "evidence_snapshot_id": _optional_id(run.evidence_snapshot_id, "evidence_snapshot_id"), "trusted_snapshot_id": _optional_id(run.trusted_snapshot_id, "trusted_snapshot_id"), "phase": run.phase.value, "counts": asdict(_counts(asdict(run.counts))), "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat(), "redacted_error": _safe_error(run.redacted_error) if run.redacted_error else None, "displayed_trusted_snapshot_id": _optional_id(run.displayed_trusted_snapshot_id, "displayed_trusted_snapshot_id")}

    def _load_run(self, run_id: str) -> PipelineRun | None:
        document = self._read(self._run_path(run_id))
        try:
            if document is None:
                return None
            self._schema(document, _RUN_KEYS)
            if _id(document["run_id"], "run_id") != run_id or type(document["phase"]) is not str:
                return None
            error = document["redacted_error"]
            if error is not None and (type(error) is not str or error not in _SAFE_ERROR_CODES):
                return None
            return PipelineRun(run_id, _id(document["raw_snapshot_id"], "raw_snapshot_id"), _optional_id(document["evidence_snapshot_id"], "evidence_snapshot_id"), _optional_id(document["trusted_snapshot_id"], "trusted_snapshot_id"), PipelinePhase(document["phase"]), _counts(document["counts"]), _timestamp(document["created_at"]), _timestamp(document["updated_at"]), error, _optional_id(document["displayed_trusted_snapshot_id"], "displayed_trusted_snapshot_id"))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def _artifact_phase(self, run: PipelineRun) -> None:
        raw, evidence = self._load_raw(run.raw_snapshot_id), self._load_evidence(run.raw_snapshot_id)
        if run.phase is PipelinePhase.RAW_SAVED and raw is None:
            raise ValueError("raw snapshot must be durable before raw_saved")
        if run.phase is PipelinePhase.EVIDENCE_SAVED and (raw is None or evidence is None):
            raise ValueError("raw and evidence artifacts must be durable before evidence_saved")
        if run.phase is PipelinePhase.EVIDENCE_SAVED and (run.evidence_snapshot_id is None or evidence.snapshot_id != run.evidence_snapshot_id or evidence.recovery_metadata.get("legacy_identity") is True):
            raise ValueError("snapshot IDs must match durable non-legacy evidence")
        if run.phase is PipelinePhase.TRUSTED_PUBLISHED and (raw is None or evidence is None or self._load_trusted(run.raw_snapshot_id) is None or (current := self._load_current()) is None or current.raw_snapshot_id != run.raw_snapshot_id):
            raise ValueError("raw, evidence, trusted, and current pointer must be durable before trusted_published")
        if run.phase is PipelinePhase.TRUSTED_PUBLISHED and (run.evidence_snapshot_id is None or evidence.snapshot_id != run.evidence_snapshot_id or run.trusted_snapshot_id != run.raw_snapshot_id or evidence.recovery_metadata.get("legacy_identity") is True):
            raise ValueError("snapshot IDs must match durable non-legacy artifacts")

    def write_run(self, run: PipelineRun, *, expected_phase: PipelinePhase | None = None) -> None:
        document = self._run_document(run)
        with CACHE_IO_LOCK, self._process_lock():
            self._require_writer()
            current = self._load_run(run.run_id)
            if current is None and self._run_path(run.run_id).exists():
                raise OSError("storage_corrupt")
            if current is None:
                if run.phase is not PipelinePhase.QUEUED or expected_phase is not None:
                    raise ValueError("new runs must begin queued")
            else:
                if expected_phase is None or expected_phase is not current.phase:
                    raise ValueError("expected current phase does not match")
                if current.phase in _TERMINAL:
                    raise ValueError("terminal run cannot be revived")
                if run.raw_snapshot_id != current.raw_snapshot_id:
                    raise ValueError("immutable raw_snapshot_id")
                if run.created_at != current.created_at:
                    raise ValueError("created_at is immutable")
                if run.updated_at < current.updated_at:
                    raise ValueError("updated_at cannot decrease")
                if any(
                    getattr(run.counts, field) < getattr(current.counts, field)
                    for field in PipelineCounts.__dataclass_fields__
                ):
                    raise ValueError("pipeline counts cannot decrease")
                for field in ("evidence_snapshot_id", "trusted_snapshot_id", "displayed_trusted_snapshot_id"):
                    if getattr(current, field) is not None and getattr(current, field) != getattr(run, field):
                        raise ValueError(f"immutable {field}")
                if run.phase not in _NEXT.get(current.phase, set()):
                    raise ValueError("invalid phase transition")
            self._artifact_phase(run)
            self._atomic_write(self._run_path(run.run_id), document)

    def load_run(self, run_id: str) -> PipelineRun | None:
        with CACHE_IO_LOCK, self._process_lock():
            return self._load_run(_id(run_id, "run_id"))

    def load_latest_run(self) -> PipelineRun | None:
        """Load the newest durable run using updated time and run ID as a stable tie."""
        with CACHE_IO_LOCK, self._process_lock():
            if not self.runs_root.exists():
                return None
            try:
                self._verify_directory_chain(self.runs_root)
                paths = sorted(
                    Path(entry.path)
                    for entry in os.scandir(self.runs_root)
                    if entry.name.endswith(".json")
                    and entry.is_file(follow_symlinks=False)
                    and _single_link(entry.stat(follow_symlinks=False))
                )
            except OSError:
                raise OSError("storage_corrupt") from None
            runs: list[PipelineRun] = []
            for path in paths:
                try:
                    run_id = _id(path.stem, "run_id")
                except ValueError:
                    continue
                run = self._load_run(run_id)
                if run is None:
                    raise OSError("storage_corrupt")
                runs.append(run)
            return max(runs, key=lambda run: (run.updated_at, run.run_id), default=None)

    def recover_incomplete_runs(self) -> int:
        interrupted = 0
        with CACHE_IO_LOCK, self._process_lock():
            self._claim_writer_unlocked()
            if not self.runs_root.exists():
                return 0
            now = self._clock()
            try:
                self._verify_directory_chain(self.runs_root)
                paths = sorted(
                    Path(entry.path)
                    for entry in os.scandir(self.runs_root)
                    if entry.name.endswith(".json")
                    and entry.is_file(follow_symlinks=False)
                    and _single_link(entry.stat(follow_symlinks=False))
                )
            except OSError:
                raise OSError("storage_corrupt") from None
            runs: list[PipelineRun] = []
            for path in paths:
                try:
                    run_id = _id(path.stem, "run_id")
                except ValueError:
                    continue
                run = self._load_run(run_id)
                if run is None:
                    raise OSError("storage_corrupt")
                runs.append(run)
            for run in runs:
                if run.phase not in _NONTERMINAL:
                    continue
                recovered = replace(
                    run,
                    phase=PipelinePhase.INTERRUPTED,
                    updated_at=max(now, run.updated_at),
                    redacted_error="pipeline_interrupted",
                )
                self._atomic_write(self._run_path(run.run_id), self._run_document(recovered))
                interrupted += 1
        return interrupted
