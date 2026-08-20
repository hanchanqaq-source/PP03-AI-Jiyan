from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Callable, Iterator

from cache_io_lock import CACHE_IO_LOCK
from evidence_verification.models import EvidenceSnapshot
from evidence_verification.storage import evidence_snapshot_from_document, snapshot_document
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
_GENERATION_KEYS = {"schema_version", "generation"}
_POINTER_KEYS = {"schema_version", "generation", "raw_snapshot_id"}
_TRUSTED_KEYS = {"schema_version", "pointer_generation", "raw_snapshot_id", "published_at", "events"}
_INTENT_KEYS = {"schema_version", "expected_generation", "target_generation", "raw_snapshot_id"}
_COMPLETE_KEYS = {"schema_version", "generation", "raw_snapshot_id"}
_SAFE_ERROR_CODES = {
    "collection_failed",
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


@dataclass(frozen=True, slots=True)
class _TrustedRecord:
    snapshot: TrustedSnapshot
    pointer_generation: int


@dataclass(frozen=True, slots=True)
class _PointerRecord:
    raw_snapshot_id: str
    generation: int


_SOURCE_KEYS = {
    "source_name", "source_url", "original_url", "published_at", "fetched_at",
    "title", "summary_or_excerpt", "language", "region", "data_status",
}
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


def _validate_trusted_events(value: object) -> list[dict[str, object]]:
    if type(value) is not list or len(value) > _MAX_ENTRIES:
        raise ValueError("trusted events must be a list")
    return [_validate_market_event(item, trusted=True) for item in value]


class NewsPipelineStorage:
    def __init__(self, root: str | os.PathLike[str] | None = None, *, now: Callable[[], datetime] | None = None, lock_timeout_seconds: float = 2.0) -> None:
        if root is None:
            configured = os.environ.get("VR_DATA_DIR")
            root = Path(configured) / "evidence-verification" / "v1" if configured else Path(os.environ.get("USERPROFILE") or Path.home()) / ".vibe-research" / "evidence-verification" / "v1"
        self.root = Path(root)
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
            handle = self._lock_path.open("a+b")
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
            handle.close()

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

    def _atomic_write(self, path: Path, document: dict[str, object]) -> None:
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
            descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temporary = Path(raw)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            handle = os.fdopen(descriptor, "wb")
            descriptor = None
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
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
        try:
            before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or getattr(before, "st_reparse_tag", 0):
                return None
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
            except BaseException:
                os.close(descriptor)
                raise
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                os.close(descriptor)
                return None
            with os.fdopen(descriptor, "rb") as handle:
                raw = handle.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                return None
            result = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object, parse_int=_json_int, parse_float=_no_float, parse_constant=_no_float)
            checked = _bounded(result)
            return checked if type(checked) is dict else None
        except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError):
            return None

    def _path(self, directory: Path, raw_snapshot_id: str) -> Path:
        return directory / f"{_id(raw_snapshot_id, 'raw_snapshot_id')}.json"

    def _run_path(self, run_id: str) -> Path:
        return self.runs_root / f"{_id(run_id, 'run_id')}.json"

    @staticmethod
    def _schema(document: dict[str, object], keys: set[str]) -> None:
        if set(document) != keys or type(document.get("schema_version")) is not int or document["schema_version"] != 1:
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
                if directory.exists() and any(directory.iterdir()):
                    return True
            except OSError:
                raise OSError("storage_corrupt") from None
        return False

    def _claim_writer_unlocked(self) -> _WriterCapability:
        if self._closed:
            raise ValueError("pipeline storage is closed")
        if self._writer is not None:
            generation = self._load_generation()
            if generation != self._writer.generation:
                raise ValueError("pipeline writer generation is stale")
            return self._writer
        try:
            handle = self._writer_lock_path.open("a+b")
        except OSError:
            raise OSError("storage_error") from None
        if not self._try_lock(handle):
            handle.close()
            raise ValueError("pipeline writer is active")
        try:
            current = self._load_generation()
            if current is None and self._has_persisted_state_without_generation():
                raise OSError("storage_corrupt")
            generation = (current or 0) + 1
            self._atomic_write(self._generation_path, {"schema_version": 1, "generation": generation})
            capability = _WriterCapability(generation, handle)
            self._writer = capability
            return capability
        except BaseException:
            self._unlock(handle)
            handle.close()
            raise

    def _require_writer(self) -> _WriterCapability:
        return self._claim_writer_unlocked()

    def close(self) -> None:
        writer, self._writer = self._writer, None
        self._closed = True
        if writer is not None:
            self._unlock(writer.lock_handle)
            writer.lock_handle.close()

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
        document: dict[str, object] = {"schema_version": 1, "raw_snapshot_id": raw_id, "collected_at": snapshot.collected_at.isoformat(), "items": items}
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
            self._schema(document, {"schema_version", "raw_snapshot_id", "collected_at", "items"})
            identity, items = _id(document["raw_snapshot_id"], "raw_snapshot_id"), document["items"]
            if identity != raw_id:
                return None
            return RawSnapshot(identity, _timestamp(document["collected_at"]), tuple(_validate_raw_items(items)))
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
        document = snapshot_document(snapshot)
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
        raw_id = _id(snapshot.raw_snapshot_id, "raw_snapshot_id")
        if not isinstance(snapshot.published_at, datetime) or snapshot.published_at.tzinfo is None or snapshot.published_at.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("published_at must be aware UTC")
        events = _validate_trusted_events(_bounded(list(snapshot.events)))
        with CACHE_IO_LOCK, self._process_lock():
            self._require_writer()
            evidence = self._load_evidence(raw_id)
            if self._load_raw(raw_id) is None or evidence is None:
                raise ValueError("raw and evidence artifacts must be durable before trusted publish")
            if evidence.recovery_metadata.get("legacy_identity") is True:
                raise ValueError("legacy evidence must be deterministically reverified before trusted publish")
            pointer = self._load_pointer_record(allow_initial_intent=raw_id)
            current_generation = pointer.generation if pointer is not None else 0
            target_generation = current_generation + 1
            path = self._path(self.trusted_root, raw_id)
            existing = self._load_trusted_record(raw_id)
            if existing is None and path.exists():
                raise OSError("storage_corrupt")
            if existing is not None and existing.snapshot != snapshot:
                raise ValueError("trusted snapshot identity is immutable")
            if pointer is not None and pointer.raw_snapshot_id == raw_id:
                if existing is None or existing.pointer_generation != pointer.generation:
                    raise OSError("trusted pointer is corrupt")
                self._write_publication_complete(raw_id, pointer.generation)
                return
            if existing is not None and existing.pointer_generation != target_generation:
                raise ValueError("trusted pointer cannot move backwards")
            if existing is None:
                self._write_publication_intent(raw_id, current_generation, target_generation)
                self._atomic_write(
                    path,
                    {
                        "schema_version": 1,
                        "pointer_generation": target_generation,
                        "raw_snapshot_id": raw_id,
                        "published_at": snapshot.published_at.isoformat(),
                        "events": events,
                    },
                )
            durable = self._load_trusted_record(raw_id)
            if durable is None or durable.pointer_generation != target_generation:
                raise OSError("trusted snapshot did not become durable")
            self._write_current_pointer(raw_id, expected_generation=current_generation)

    def _write_current_pointer(self, raw_id: str, *, expected_generation: int) -> None:
        current = self._load_pointer_record(allow_initial_intent=raw_id if expected_generation == 0 else None)
        actual_generation = current.generation if current is not None else 0
        if actual_generation != expected_generation:
            raise ValueError("trusted pointer generation changed")
        self._atomic_write(
            self.current_pointer_path,
            {
                "schema_version": 1,
                "generation": expected_generation + 1,
                "raw_snapshot_id": _id(raw_id, "raw_snapshot_id"),
            },
        )
        self._write_publication_complete(raw_id, expected_generation + 1)

    def _write_publication_complete(self, raw_id: str, generation: int) -> None:
        current = self._load_publication_complete()
        expected = {"raw_snapshot_id": raw_id, "generation": generation}
        if current == expected:
            return
        self._atomic_write(
            self._publication_complete_path,
            {"schema_version": 1, "generation": generation, "raw_snapshot_id": raw_id},
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
            generation = document["generation"]
            if type(generation) is not int or generation < 1:
                raise ValueError("invalid publication completion")
            return {"raw_snapshot_id": raw_id, "generation": generation}
        except (KeyError, TypeError, ValueError):
            raise OSError("storage_corrupt") from None

    def _write_publication_intent(self, raw_id: str, expected_generation: int, target_generation: int) -> None:
        self._atomic_write(
            self._publication_intent_path,
            {
                "schema_version": 1,
                "expected_generation": expected_generation,
                "target_generation": target_generation,
                "raw_snapshot_id": raw_id,
            },
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
            if type(expected) is not int or type(target) is not int or expected < 0 or target != expected + 1:
                raise ValueError("invalid publication intent")
            return {"raw_snapshot_id": raw_id, "expected_generation": expected, "target_generation": target}
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
            return sorted(
                entry.name for entry in os.scandir(self.trusted_root)
                if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False)
            )
        except OSError:
            raise OSError("storage_corrupt") from None

    def _load_pointer_record(self, *, allow_initial_intent: str | None = None) -> _PointerRecord | None:
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
                    and completed is None
                    and artifacts == [f"{allow_initial_intent}.json"]
                    and intent == {
                        "raw_snapshot_id": allow_initial_intent,
                        "expected_generation": 0,
                        "target_generation": 1,
                    }
                    and (trusted := self._load_trusted_record(allow_initial_intent)) is not None
                    and trusted.pointer_generation == 1
                )
                if not permitted:
                    raise OSError("storage_corrupt")
            return None
        try:
            self._schema(document, _POINTER_KEYS)
            raw_id, generation = _id(document["raw_snapshot_id"], "raw_snapshot_id"), document["generation"]
            if type(generation) is not int or generation < 1:
                raise ValueError("invalid pointer generation")
            trusted = self._load_trusted_record(raw_id)
            if trusted is None or trusted.pointer_generation != generation:
                raise ValueError("pointer target is unavailable")
            return _PointerRecord(raw_id, generation)
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

    def recover_incomplete_runs(self) -> int:
        interrupted = 0
        with CACHE_IO_LOCK, self._process_lock():
            self._claim_writer_unlocked()
            if not self.runs_root.exists():
                return 0
            now = self._clock()
            try:
                paths = sorted(
                    Path(entry.path)
                    for entry in os.scandir(self.runs_root)
                    if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False)
                )
            except OSError:
                raise OSError("storage_corrupt") from None
            for path in paths:
                try:
                    run = self._load_run(_id(path.stem, "run_id"))
                except ValueError:
                    continue
                if run is None or run.phase not in _NONTERMINAL:
                    continue
                recovered = replace(run, phase=PipelinePhase.INTERRUPTED, updated_at=now, redacted_error="pipeline_interrupted")
                self._atomic_write(self._run_path(run.run_id), self._run_document(recovered))
                interrupted += 1
        return interrupted
