from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
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

from .models import EvidenceSnapshot, VerificationStatus
from .storage import (
    _EVENT_KEYS,
    _cleanup_owned_temp,
    _close_owned_descriptor,
    _ensure_directory,
    _event_from_document,
    _exact_builtin,
    _parse_datetime,
    _reject_json_number,
    _replace_durable,
    _safe_directory,
    _strict_json_int,
    _strict_json_object,
    event_document,
    validated_snapshot_document,
)


_ARCHIVE_SCHEMA_VERSION = 1
_INDEX_SCHEMA_VERSION = 1
_ALLOWED_DAYS = {1, 3, 7, 30, 90}
_KNOWN_STATUSES = {status.value for status in VerificationStatus}
_BUCKET_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")
_MAX_BUCKET_BYTES = 32 * 1_048_576
_MAX_INDEX_BYTES = 8 * 1_048_576
_MAX_ROW_BYTES = 1_048_576
_MAX_BUCKET_ROWS = 4_096
_MAX_ARCHIVE_FILES = 512
_MAX_INDEX_EVENTS = 5_000
_MAX_LINEAGES = 256
_MAX_SCAN_BYTES = 64 * 1_048_576
_MAX_SCAN_ROWS = 20_000
_MAX_DIAGNOSTIC_COUNT = 1_000_000
_ARCHIVE_KEYS = _EVENT_KEYS | {
    "schema_version",
    "evidence_snapshot_id",
    "raw_snapshot_id",
    "snapshot_generated_at",
    "archived_at",
    "last_updated_at",
    "snapshot_history",
}
_LINEAGE_KEYS = {"evidence_snapshot_id", "raw_snapshot_id", "generated_at"}
_INDEX_KEYS = {"schema_version", "events"}


def _utc(value: datetime, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} timezone must be aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime, name: str) -> str:
    return _utc(value, name).isoformat()


def _bounded_text(value: object, name: str, *, maximum: int = 128) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"invalid {name}")
    return value


def _metadata_time(value: object, name: str) -> datetime:
    parsed = _parse_datetime(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")
    return parsed


def _valid_bucket_name(value: object) -> bool:
    if type(value) is not str:
        return False
    match = _BUCKET_NAME.fullmatch(value)
    if match is None:
        return False
    try:
        return date.fromisoformat(match.group(1)).isoformat() == match.group(1)
    except ValueError:
        return False


def _lineage_document(snapshot: EvidenceSnapshot) -> dict[str, str]:
    return {
        "evidence_snapshot_id": _bounded_text(snapshot.snapshot_id, "evidence_snapshot_id"),
        "raw_snapshot_id": _bounded_text(snapshot.raw_snapshot_id, "raw_snapshot_id"),
        "generated_at": _timestamp(snapshot.generated_at, "snapshot generated_at"),
    }


def _lineage_from_document(value: object) -> dict[str, str]:
    if type(value) is not dict or set(value) != _LINEAGE_KEYS:
        raise ValueError("invalid archive lineage schema")
    lineage = {
        "evidence_snapshot_id": _bounded_text(value["evidence_snapshot_id"], "evidence_snapshot_id"),
        "raw_snapshot_id": _bounded_text(value["raw_snapshot_id"], "raw_snapshot_id"),
        "generated_at": _metadata_time(value["generated_at"], "lineage generated_at").isoformat(),
    }
    return lineage


def _event_time(row: dict[str, Any]) -> datetime:
    published_at = row["published_at"]
    if published_at is not None:
        return _parse_datetime(published_at).astimezone(timezone.utc)
    return _parse_datetime(row["verified_at"]).astimezone(timezone.utc)


def _bucket_name(row: dict[str, Any]) -> str:
    return f"{_event_time(row).date().isoformat()}.jsonl"


def _archive_document(snapshot: EvidenceSnapshot, row: dict[str, Any], archived_at: datetime) -> dict[str, Any]:
    lineage = _lineage_document(snapshot)
    archived_event = dict(row)
    archived_event["title"] = archived_event["title"][:500]
    archived_event["summary"] = archived_event["summary"][:1_200]
    archived_event["core_claim"] = archived_event["core_claim"][:1_200]
    document = {
        "schema_version": _ARCHIVE_SCHEMA_VERSION,
        **archived_event,
        "evidence_snapshot_id": lineage["evidence_snapshot_id"],
        "raw_snapshot_id": lineage["raw_snapshot_id"],
        "snapshot_generated_at": lineage["generated_at"],
        "archived_at": _timestamp(archived_at, "archived_at"),
        "last_updated_at": lineage["generated_at"],
        "snapshot_history": [lineage],
    }
    _exact_builtin(document)
    return document


def _archive_from_document(value: object) -> dict[str, Any]:
    _exact_builtin(value)
    if type(value) is not dict or set(value) != _ARCHIVE_KEYS or value.get("schema_version") != _ARCHIVE_SCHEMA_VERSION:
        raise ValueError("invalid archive row schema")
    event = _event_from_document({key: value[key] for key in _EVENT_KEYS})
    canonical_event = event_document(event)
    if canonical_event != {key: value[key] for key in _EVENT_KEYS}:
        raise ValueError("archive event is not canonical")
    event_id = _bounded_text(value["event_id"], "event_id")
    evidence_snapshot_id = _bounded_text(value["evidence_snapshot_id"], "evidence_snapshot_id")
    raw_snapshot_id = _bounded_text(value["raw_snapshot_id"], "raw_snapshot_id")
    generated_at = _metadata_time(value["snapshot_generated_at"], "snapshot_generated_at")
    archived_at = _metadata_time(value["archived_at"], "archived_at")
    last_updated_at = _metadata_time(value["last_updated_at"], "last_updated_at")
    history = value["snapshot_history"]
    if type(history) is not list or not history or len(history) > _MAX_LINEAGES:
        raise ValueError("invalid archive snapshot history")
    parsed_history = [_lineage_from_document(item) for item in history]
    identities = {
        (item["evidence_snapshot_id"], item["raw_snapshot_id"], item["generated_at"])
        for item in parsed_history
    }
    if len(identities) != len(parsed_history):
        raise ValueError("duplicate archive lineage")
    latest = max(enumerate(parsed_history), key=lambda pair: (_parse_datetime(pair[1]["generated_at"]), pair[0]))[1]
    if (
        latest["evidence_snapshot_id"] != evidence_snapshot_id
        or latest["raw_snapshot_id"] != raw_snapshot_id
        or latest["generated_at"] != generated_at.isoformat()
        or last_updated_at != generated_at
    ):
        raise ValueError("archive lineage is inconsistent")
    return {
        **canonical_event,
        "schema_version": _ARCHIVE_SCHEMA_VERSION,
        "evidence_snapshot_id": evidence_snapshot_id,
        "raw_snapshot_id": raw_snapshot_id,
        "snapshot_generated_at": generated_at.isoformat(),
        "archived_at": archived_at.isoformat(),
        "last_updated_at": last_updated_at.isoformat(),
        "snapshot_history": parsed_history,
    }


def _merge_unique(
    previous: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    matches: Callable[[dict[str, Any], dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    merged = list(previous)
    for item in incoming:
        merged = [existing for existing in merged if not matches(existing, item)]
        merged.append(item)
    return merged


def _merge_archive_rows(previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    if previous["event_id"] != incoming["event_id"]:
        raise ValueError("archive event identity mismatch")
    previous_time = _metadata_time(previous["snapshot_generated_at"], "previous snapshot_generated_at")
    incoming_time = _metadata_time(incoming["snapshot_generated_at"], "incoming snapshot_generated_at")
    use_incoming = incoming_time >= previous_time
    current, other = (incoming, previous) if use_incoming else (previous, incoming)

    histories = list(previous["snapshot_history"])
    seen_lineages = {
        (row["evidence_snapshot_id"], row["raw_snapshot_id"], row["generated_at"])
        for row in histories
    }
    for row in incoming["snapshot_history"]:
        identity = (row["evidence_snapshot_id"], row["raw_snapshot_id"], row["generated_at"])
        if identity not in seen_lineages:
            histories.append(row)
            seen_lineages.add(identity)
    histories.sort(key=lambda row: _parse_datetime(row["generated_at"]))
    if len(histories) > _MAX_LINEAGES:
        histories = histories[-_MAX_LINEAGES:]
    latest = histories[-1]

    merged = dict(current)
    if not current["title"] and other["title"]:
        merged["title"] = other["title"]
    if not current["summary"] and other["summary"]:
        merged["summary"] = other["summary"]
    merged["related_tags"] = _merge_unique(
        list(previous["related_tags"]),
        list(incoming["related_tags"]),
        matches=lambda left, right: left["id"] == right["id"],
    )
    merged["key_fields"] = _merge_unique(
        list(previous["key_fields"]),
        list(incoming["key_fields"]),
        matches=lambda left, right: (
            left["field_name"], left["verification_status"]
        ) == (
            right["field_name"], right["verification_status"]
        ),
    )
    for key in ("primary_evidence", "independent_evidence", "syndicated_copies", "contradicting_evidence"):
        merged[key] = _merge_unique(
            list(previous[key]),
            list(incoming[key]),
            matches=lambda left, right: (
                left["evidence_id"] == right["evidence_id"]
                or bool(left["canonical_url"])
                and left["canonical_url"] == right["canonical_url"]
            ),
        )
    merged["status_history"] = _merge_unique(
        list(previous["status_history"]),
        list(incoming["status_history"]),
        matches=lambda left, right: left == right,
    )
    merged["status_history"].sort(key=lambda row: _parse_datetime(row["changed_at"]))
    merged["snapshot_history"] = histories
    merged["evidence_snapshot_id"] = latest["evidence_snapshot_id"]
    merged["raw_snapshot_id"] = latest["raw_snapshot_id"]
    merged["snapshot_generated_at"] = latest["generated_at"]
    merged["last_updated_at"] = latest["generated_at"]
    merged["archived_at"] = min(previous["archived_at"], incoming["archived_at"])
    return _archive_from_document(merged)


class EvidenceArchive:
    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        lock_timeout: float = 10.0,
    ) -> None:
        if root is None:
            configured = os.environ.get("VR_DATA_DIR")
            if configured:
                root = Path(configured) / "evidence-verification" / "v1"
            else:
                profile = Path(os.environ.get("USERPROFILE") or Path.home())
                root = profile / ".vibe-research" / "evidence-verification" / "v1"
        self.root = Path(os.path.abspath(root))
        self.archive_root = self.root / "archive"
        self.index_path = self.archive_root / "index.json"
        self.lock_path = self.archive_root / ".archive.lock"
        self._now = now or (lambda: datetime.now(timezone.utc))
        if type(lock_timeout) not in {int, float} or isinstance(lock_timeout, bool) or lock_timeout <= 0 or lock_timeout > 30:
            raise ValueError("invalid archive lock timeout")
        self._lock_timeout = float(lock_timeout)
        self._last_diagnostics = self._diagnostics()

    @staticmethod
    def _diagnostics() -> dict[str, int]:
        return {
            "scanned_files": 0,
            "skipped_files": 0,
            "scanned_rows": 0,
            "skipped_corrupt_rows": 0,
            "duplicate_rows": 0,
        }

    @staticmethod
    def _increment(diagnostics: dict[str, int], key: str, amount: int = 1) -> None:
        diagnostics[key] = min(_MAX_DIAGNOSTIC_COUNT, diagnostics[key] + amount)

    @property
    def last_diagnostics(self) -> dict[str, int]:
        return dict(self._last_diagnostics)

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
        if not _safe_directory(cursor):
            raise OSError("storage_error")
        for component in relative.parts:
            cursor = cursor / component
            if not _safe_directory(cursor):
                raise OSError("storage_error")

    def _verify_parent(self, path: Path) -> None:
        if not self._inside_root(path):
            raise OSError("storage_error")
        self._verify_directory_chain(path.parent)

    def _prepare(self) -> None:
        for directory in (self.root, self.archive_root):
            try:
                _ensure_directory(directory)
            except FileExistsError:
                if not _safe_directory(directory):
                    raise OSError("storage_error") from None
        self._verify_directory_chain(self.archive_root)

    def _open_lock(self) -> io.FileIO:
        self._prepare()
        self._verify_parent(self.lock_path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            try:
                before = self.lock_path.stat(follow_symlinks=False)
            except FileNotFoundError:
                before = None
            if before is not None and (
                not stat.S_ISREG(before.st_mode)
                or getattr(before, "st_reparse_tag", 0)
                or before.st_nlink != 1
            ):
                raise OSError("storage_error")
            try:
                descriptor = os.open(
                    self.lock_path,
                    flags | (os.O_EXCL if before is None else 0),
                    0o600,
                )
            except FileExistsError:
                before = self.lock_path.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or getattr(before, "st_reparse_tag", 0)
                    or before.st_nlink != 1
                ):
                    raise OSError("storage_error")
                descriptor = os.open(self.lock_path, flags, 0o600)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or before is not None
                and identity != (before.st_dev, before.st_ino)
            ):
                raise OSError("storage_error")
            handle = io.FileIO(descriptor, mode="r+", closefd=True)
            descriptor = None
            return handle
        except OSError:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)
            raise OSError("storage_error") from None

    @staticmethod
    def _try_lock(handle: io.FileIO) -> bool:
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
    def _unlock(handle: io.FileIO) -> None:
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
        handle = self._open_lock()
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

    def _safe_file(self, path: Path) -> os.stat_result | None:
        try:
            metadata = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError:
            raise OSError("storage_error") from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or getattr(metadata, "st_reparse_tag", 0)
            or metadata.st_nlink != 1
        ):
            return None
        return metadata

    @staticmethod
    def _entry_present(path: Path) -> bool:
        try:
            path.stat(follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return True

    def _read_bytes(self, path: Path, maximum: int) -> bytes | None:
        self._verify_parent(path)
        before = self._safe_file(path)
        if before is None:
            return None
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if identity != (before.st_dev, before.st_ino) or opened.st_nlink != 1:
                return None
            handle = os.fdopen(descriptor, "rb")
            descriptor = None
            with handle:
                payload = handle.read(maximum + 1)
            return None if len(payload) > maximum else payload
        except OSError:
            return None
        finally:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)

    def _atomic_write(self, path: Path, payload: bytes, maximum: int) -> None:
        if len(payload) > maximum:
            raise ValueError("archive document is too large")
        self._prepare()
        self._verify_parent(path)
        present = self._entry_present(path)
        before = self._safe_file(path)
        if present and before is None:
            raise OSError("storage_error")
        descriptor: int | None = None
        temp_path: Path | None = None
        identity: tuple[int, int] | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temp_path = Path(raw_path)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            try:
                handle = os.fdopen(descriptor, "wb")
            except BaseException:
                _close_owned_descriptor(descriptor, identity)
                descriptor = None
                raise
            descriptor = None
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_durable(temp_path, path)
        except OSError:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)
            if temp_path is not None and identity is not None:
                _cleanup_owned_temp(temp_path, identity)
            raise OSError("storage_error") from None
        except BaseException:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)
            if temp_path is not None and identity is not None:
                _cleanup_owned_temp(temp_path, identity)
            raise

    def _write_bucket(self, name: str, rows: list[dict[str, Any]]) -> None:
        if not _valid_bucket_name(name) or len(rows) > _MAX_BUCKET_ROWS:
            raise ValueError("invalid archive bucket")
        canonical = sorted(rows, key=lambda row: row["event_id"])
        payload_parts = []
        for row in canonical:
            validated = _archive_from_document(row)
            encoded = json.dumps(validated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > _MAX_ROW_BYTES:
                raise ValueError("archive row is too large")
            payload_parts.append(encoded + b"\n")
        self._atomic_write(self.archive_root / name, b"".join(payload_parts), _MAX_BUCKET_BYTES)

    def _parse_json(self, raw: bytes) -> object:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_int=_strict_json_int,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )

    def _read_bucket(
        self,
        name: str,
        diagnostics: dict[str, int],
        *,
        budget: dict[str, int] | None = None,
        fail_on_budget: bool = False,
    ) -> list[dict[str, Any]]:
        if not _valid_bucket_name(name):
            raise ValueError("invalid archive bucket name")
        path = self.archive_root / name
        before = self._safe_file(path)
        if before is None:
            if self._entry_present(path):
                self._increment(diagnostics, "skipped_files")
            return []
        if budget is not None and before.st_size > budget["bytes"]:
            if fail_on_budget:
                raise OSError("storage_corrupt")
            self._increment(diagnostics, "skipped_files")
            return []
        if budget is not None:
            budget["bytes"] -= before.st_size
        payload = self._read_bytes(path, _MAX_BUCKET_BYTES)
        if payload is None:
            self._increment(diagnostics, "skipped_files")
            return []
        self._increment(diagnostics, "scanned_files")
        lines = payload.splitlines()
        if len(lines) > _MAX_BUCKET_ROWS:
            self._increment(diagnostics, "skipped_files")
            return []
        if budget is not None and len(lines) > budget["rows"]:
            if fail_on_budget:
                raise OSError("storage_corrupt")
            self._increment(diagnostics, "skipped_files")
            return []
        if budget is not None:
            budget["rows"] -= len(lines)
        rows: list[dict[str, Any]] = []
        for line in lines:
            self._increment(diagnostics, "scanned_rows")
            if not line or len(line) > _MAX_ROW_BYTES:
                self._increment(diagnostics, "skipped_corrupt_rows")
                continue
            try:
                parsed = _archive_from_document(self._parse_json(line))
            except (KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError, json.JSONDecodeError):
                self._increment(diagnostics, "skipped_corrupt_rows")
                continue
            rows.append(parsed)
        return rows

    def _bucket_names(self) -> list[str]:
        self._prepare()
        try:
            entries = list(os.scandir(self.archive_root))
        except OSError:
            raise OSError("storage_error") from None
        names = sorted(entry.name for entry in entries if _valid_bucket_name(entry.name))
        if len(names) > _MAX_ARCHIVE_FILES:
            raise OSError("storage_corrupt")
        return names

    def _read_index(self) -> dict[str, str] | None:
        raw = self._read_bytes(self.index_path, _MAX_INDEX_BYTES)
        if raw is None:
            return None
        try:
            document = self._parse_json(raw)
            _exact_builtin(document)
            if type(document) is not dict or set(document) != _INDEX_KEYS or document["schema_version"] != _INDEX_SCHEMA_VERSION:
                raise ValueError("invalid archive index")
            events = document["events"]
            if type(events) is not dict or len(events) > _MAX_INDEX_EVENTS:
                raise ValueError("invalid archive index events")
            result: dict[str, str] = {}
            for event_id, bucket in events.items():
                result[_bounded_text(event_id, "index event_id")] = _bounded_text(bucket, "index bucket")
                if not _valid_bucket_name(bucket):
                    raise ValueError("invalid archive index bucket")
            return result
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError, json.JSONDecodeError):
            return None

    def _write_index(self, events: dict[str, str]) -> None:
        if len(events) > _MAX_INDEX_EVENTS:
            raise ValueError("archive index is too large")
        document = {"schema_version": _INDEX_SCHEMA_VERSION, "events": dict(sorted(events.items()))}
        _exact_builtin(document)
        payload = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self._atomic_write(self.index_path, payload, _MAX_INDEX_BYTES)

    def _rebuild_index(self) -> dict[str, str]:
        diagnostics = self._diagnostics()
        budget = {"bytes": _MAX_SCAN_BYTES, "rows": _MAX_SCAN_ROWS}
        events: dict[str, tuple[str, datetime]] = {}
        for name in self._bucket_names():
            for row in self._read_bucket(name, diagnostics, budget=budget, fail_on_budget=True):
                generated_at = _metadata_time(row["snapshot_generated_at"], "snapshot_generated_at")
                current = events.get(row["event_id"])
                if current is None or generated_at >= current[1]:
                    events[row["event_id"]] = (name, generated_at)
        result = {event_id: name for event_id, (name, _updated) in events.items()}
        self._write_index(result)
        return result

    def upsert(self, snapshot: EvidenceSnapshot) -> None:
        document = validated_snapshot_document(snapshot)
        archived_at = _utc(self._now(), "archive clock")
        incoming = [
            _archive_document(snapshot, event, archived_at)
            for event in document["events"]
        ]
        incoming_ids = [row["event_id"] for row in incoming]
        if len(incoming_ids) != len(set(incoming_ids)):
            raise ValueError("archive snapshot contains duplicate event identities")
        with CACHE_IO_LOCK, self._process_lock():
            index = self._read_index()
            if index is None:
                index = self._rebuild_index()
            for row in incoming:
                target_name = _bucket_name(row)
                previous_name = index.get(row["event_id"])
                target_rows = self._read_bucket(target_name, self._diagnostics())
                previous_rows = target_rows
                if previous_name is not None and previous_name != target_name:
                    previous_rows = self._read_bucket(previous_name, self._diagnostics())
                previous = next((item for item in previous_rows if item["event_id"] == row["event_id"]), None)
                if previous_name is not None and previous is None:
                    index = self._rebuild_index()
                    previous_name = index.get(row["event_id"])
                    if previous_name is not None:
                        previous_rows = self._read_bucket(previous_name, self._diagnostics())
                        previous = next((item for item in previous_rows if item["event_id"] == row["event_id"]), None)
                merged = row if previous is None else _merge_archive_rows(previous, row)
                target_name = _bucket_name(merged)
                if previous_name is not None and previous_name != target_name:
                    retained = [item for item in previous_rows if item["event_id"] != row["event_id"]]
                    self._write_bucket(previous_name, retained)
                    target_rows = self._read_bucket(target_name, self._diagnostics())
                retained_target = [item for item in target_rows if item["event_id"] != row["event_id"]]
                if len(retained_target) >= _MAX_BUCKET_ROWS:
                    raise ValueError("archive bucket is full")
                self._write_bucket(target_name, [*retained_target, merged])
                index[row["event_id"]] = target_name
            self._write_index(index)

    def _query_unlocked(self, days: int, status: str | None) -> list[dict[str, Any]]:
        now = _utc(self._now(), "archive clock")
        cutoff = now - timedelta(days=days)
        diagnostics = self._diagnostics()
        budget = {"bytes": _MAX_SCAN_BYTES, "rows": _MAX_SCAN_ROWS}
        selected: dict[str, dict[str, Any]] = {}
        start_date = cutoff.date()
        current_date = now.date()
        cursor = start_date
        while cursor <= current_date:
            name = f"{cursor.isoformat()}.jsonl"
            for row in self._read_bucket(name, diagnostics, budget=budget):
                effective = _event_time(row)
                if effective < cutoff or effective > now:
                    continue
                if status is not None and row["verification_status"] != status:
                    continue
                existing = selected.get(row["event_id"])
                if existing is None:
                    selected[row["event_id"]] = row
                else:
                    self._increment(diagnostics, "duplicate_rows")
                    selected[row["event_id"]] = _merge_archive_rows(existing, row)
            cursor += timedelta(days=1)
        self._last_diagnostics = diagnostics
        return sorted(
            selected.values(),
            key=lambda row: (_event_time(row), _parse_datetime(row["verified_at"]), row["event_id"]),
            reverse=True,
        )

    def query(self, days: int, status: str | None = None) -> list[dict[str, Any]]:
        if type(days) is not int or days not in _ALLOWED_DAYS:
            raise ValueError("days must be one of 1, 3, 7, 30, 90")
        if status is not None and (type(status) is not str or status not in _KNOWN_STATUSES):
            raise ValueError("invalid verification status")
        with CACHE_IO_LOCK, self._process_lock():
            return self._query_unlocked(days, status)

    def get(self, event_id: str) -> dict[str, Any] | None:
        if type(event_id) is not str or not event_id or len(event_id) > 128:
            return None
        with CACHE_IO_LOCK, self._process_lock():
            return next((row for row in self._query_unlocked(90, None) if row["event_id"] == event_id), None)

    def count(self) -> int:
        with CACHE_IO_LOCK, self._process_lock():
            return len(self._query_unlocked(90, None))
