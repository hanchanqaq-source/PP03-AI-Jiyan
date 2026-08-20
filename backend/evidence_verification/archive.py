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
from urllib.parse import parse_qsl, unquote, urlsplit

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
    _sync_directory,
    event_document,
    validated_snapshot_document,
)
from .source_identity import canonicalize_public_url


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
_MAX_INDEX_EVENTS = 20_000
_MAX_SCAN_BYTES = 64 * 1_048_576
_MAX_SCAN_ROWS = 20_000
_MAX_SNAPSHOT_BYTES = 16 * 1_048_576
_MAX_SNAPSHOT_NODES = 200_000
_MAX_SNAPSHOT_RESOURCES = 50_000
_MAX_MUTATION_BYTES = 64 * 1_048_576
_MAX_JOURNAL_BYTES = 32 * 1_048_576
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_MAX_DIAGNOSTIC_COUNT = 1_000_000
_JOURNAL_SCHEMA_VERSION = 1
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
_JOURNAL_KEYS = {"schema_version", "rows"}
_EVIDENCE_COLLECTION_KEYS = (
    "primary_evidence",
    "independent_evidence",
    "syndicated_copies",
    "contradicting_evidence",
)
_SENSITIVE_QUERY_NAMES = {
    "apikey",
    "accesskey",
    "privatekey",
    "token",
    "accesstoken",
    "refreshtoken",
    "clientsecret",
    "secret",
    "password",
    "passwd",
    "authorization",
    "auth",
    "cookie",
    "session",
    "jwt",
    "signature",
    "sig",
    "code",
}
_NESTED_SECRET = re.compile(
    r"(?:^|[?&;])\s*(?:api[_-]?key|access[_-]?key|private[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|token|client[_-]?secret|secret|password|passwd|authorization|auth|"
    r"cookie|session|jwt|signature|sig|code)\s*=",
    re.IGNORECASE,
)
_SPECIAL_HOST_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".home",
    ".lan",
    ".test",
    ".invalid",
    ".example",
    ".onion",
)


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


def _query_name_is_sensitive(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", value.casefold())
    if compact in _SENSITIVE_QUERY_NAMES:
        return True
    words = {word for word in re.split(r"[^a-z0-9]+", value.casefold()) if word}
    return bool(words & {"token", "key", "cookie", "auth", "password", "passwd", "signature", "secret"})


def _archive_public_url(value: object) -> str:
    if value == "":
        return ""
    if type(value) is not str or len(value) > 8_192:
        raise ValueError("invalid archive evidence URL")
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        raise ValueError("invalid archive evidence URL") from None
    if parsed.fragment:
        raise ValueError("invalid archive evidence URL")
    decoded_query = parsed.query
    for _ in range(5):
        if _NESTED_SECRET.search(decoded_query):
            raise ValueError("invalid archive evidence URL")
        next_value = unquote(decoded_query)
        if next_value == decoded_query:
            break
        decoded_query = next_value
    try:
        pairs = parse_qsl(decoded_query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        raise ValueError("invalid archive evidence URL") from None
    if any(_query_name_is_sensitive(key) for key, _value in pairs):
        raise ValueError("invalid archive evidence URL")
    canonical = canonicalize_public_url(value)
    if canonical is None:
        raise ValueError("invalid archive evidence URL")
    host = (urlsplit(canonical).hostname or "").rstrip(".").casefold()
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(_SPECIAL_HOST_SUFFIXES):
        raise ValueError("invalid archive evidence URL")
    return canonical


def _sanitize_archive_urls(row: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(row)
    for collection_name in _EVIDENCE_COLLECTION_KEYS:
        items: list[dict[str, Any]] = []
        for value in row[collection_name]:
            item = dict(value)
            item["canonical_url"] = _archive_public_url(item["canonical_url"])
            items.append(item)
        sanitized[collection_name] = items
    return sanitized


def _tree_nodes(value: object) -> int:
    if type(value) is dict:
        return 1 + sum(_tree_nodes(key) + _tree_nodes(item) for key, item in value.items())
    if type(value) is list:
        return 1 + sum(_tree_nodes(item) for item in value)
    return 1


def _row_resources(row: dict[str, Any]) -> int:
    return (
        1
        + len(row["related_tags"])
        + len(row["key_fields"])
        + len(row["status_history"])
        + len(row["snapshot_history"])
        + sum(len(row[key]) for key in _EVIDENCE_COLLECTION_KEYS)
    )


def _ensure_snapshot_budget(rows: list[dict[str, Any]]) -> None:
    encoded_bytes = 0
    nodes = 1
    resources = 0
    for row in rows:
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_ROW_BYTES:
            raise ValueError("archive snapshot row budget exceeded")
        encoded_bytes += len(encoded) + 1
        nodes += _tree_nodes(row)
        resources += _row_resources(row)
        if (
            encoded_bytes > _MAX_SNAPSHOT_BYTES
            or nodes > _MAX_SNAPSHOT_NODES
            or resources > _MAX_SNAPSHOT_RESOURCES
        ):
            raise ValueError("archive snapshot budget exceeded")


def _row_times(row: dict[str, Any]) -> Iterator[datetime]:
    for key in ("published_at", "verified_at", "evidence_as_of"):
        value = row[key]
        if value is not None:
            yield _metadata_time(value, key)
    for collection_name in _EVIDENCE_COLLECTION_KEYS:
        for item in row[collection_name]:
            if item["published_at"] is not None:
                yield _metadata_time(item["published_at"], "evidence published_at")
    for transition in row["status_history"]:
        yield _metadata_time(transition["changed_at"], "status changed_at")


def _validate_temporal_row(row: dict[str, Any], now: datetime) -> None:
    current = _utc(now, "archive clock")
    maximum = current + _MAX_CLOCK_SKEW
    generated_at = _metadata_time(row["snapshot_generated_at"], "snapshot_generated_at")
    archived_at = _metadata_time(row["archived_at"], "archived_at")
    if generated_at > maximum or archived_at > maximum:
        raise ValueError("invalid archive time")
    status_times = [
        _metadata_time(transition["changed_at"], "status changed_at")
        for transition in row["status_history"]
    ]
    if status_times != sorted(status_times):
        raise ValueError("invalid archive time order")
    for value in _row_times(row):
        if value > maximum:
            raise ValueError("invalid archive time")
    lineage_times = [
        _metadata_time(lineage["generated_at"], "lineage generated_at")
        for lineage in row["snapshot_history"]
    ]
    if lineage_times != sorted(lineage_times) or any(value > maximum for value in lineage_times):
        raise ValueError("invalid archive time order")


def _archive_document(snapshot: EvidenceSnapshot, row: dict[str, Any], archived_at: datetime) -> dict[str, Any]:
    lineage = _lineage_document(snapshot)
    archived_event = _sanitize_archive_urls(row)
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
    if type(history) is not list or not history:
        raise ValueError("invalid archive snapshot history")
    parsed_history = [_lineage_from_document(item) for item in history]
    identities = {
        (item["evidence_snapshot_id"], item["raw_snapshot_id"], item["generated_at"])
        for item in parsed_history
    }
    if len(identities) != len(parsed_history):
        raise ValueError("duplicate archive lineage")
    if parsed_history != sorted(parsed_history, key=lambda row: _parse_datetime(row["generated_at"])):
        raise ValueError("archive lineage is not ordered")
    latest = max(enumerate(parsed_history), key=lambda pair: (_parse_datetime(pair[1]["generated_at"]), pair[0]))[1]
    if (
        latest["evidence_snapshot_id"] != evidence_snapshot_id
        or latest["raw_snapshot_id"] != raw_snapshot_id
        or latest["generated_at"] != generated_at.isoformat()
        or last_updated_at != generated_at
    ):
        raise ValueError("archive lineage is inconsistent")
    result = {
        **canonical_event,
        "schema_version": _ARCHIVE_SCHEMA_VERSION,
        "evidence_snapshot_id": evidence_snapshot_id,
        "raw_snapshot_id": raw_snapshot_id,
        "snapshot_generated_at": generated_at.isoformat(),
        "archived_at": archived_at.isoformat(),
        "last_updated_at": last_updated_at.isoformat(),
        "snapshot_history": parsed_history,
    }
    for collection_name in _EVIDENCE_COLLECTION_KEYS:
        for item in result[collection_name]:
            if _archive_public_url(item["canonical_url"]) != item["canonical_url"]:
                raise ValueError("archive evidence URL is not canonical")
    return result


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


def _merge_archive_rows(
    previous: dict[str, Any],
    incoming: dict[str, Any],
    *,
    lineage_cutoff: datetime | None = None,
) -> dict[str, Any]:
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
    if lineage_cutoff is not None:
        histories = [
            row for row in histories
            if _metadata_time(row["generated_at"], "lineage generated_at") >= lineage_cutoff
        ]
    if not histories:
        histories = [max(
            (*previous["snapshot_history"], *incoming["snapshot_history"]),
            key=lambda row: _parse_datetime(row["generated_at"]),
        )]
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
        self.journal_path = self.archive_root / "transaction.json"
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
        before = self._safe_file(path)
        if before is None:
            return None
        self._verify_parent(path)
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if (
                identity != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size > maximum
            ):
                return None
            handle = os.fdopen(descriptor, "rb")
            descriptor = None
            with handle:
                payload = handle.read(maximum + 1)
                after = os.fstat(handle.fileno())
            if (
                len(payload) > maximum
                or (after.st_dev, after.st_ino) != identity
                or after.st_nlink != 1
                or after.st_size > maximum
            ):
                return None
            return payload
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

    def _bucket_payload(self, name: str, rows: list[dict[str, Any]]) -> bytes:
        if not _valid_bucket_name(name) or len(rows) > _MAX_BUCKET_ROWS:
            raise ValueError("invalid archive bucket")
        canonical = sorted(rows, key=lambda row: row["event_id"])
        payload_parts: list[bytes] = []
        for row in canonical:
            validated = _archive_from_document(row)
            encoded = json.dumps(validated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > _MAX_ROW_BYTES:
                raise ValueError("archive row is too large")
            payload_parts.append(encoded + b"\n")
        payload = b"".join(payload_parts)
        if len(payload) > _MAX_BUCKET_BYTES:
            raise ValueError("archive document is too large")
        return payload

    def _write_bucket(self, name: str, rows: list[dict[str, Any]]) -> None:
        self._atomic_write(self.archive_root / name, self._bucket_payload(name, rows), _MAX_BUCKET_BYTES)

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
        validation_now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if not _valid_bucket_name(name):
            raise ValueError("invalid archive bucket name")
        path = self.archive_root / name
        maximum = _MAX_BUCKET_BYTES
        if budget is not None:
            maximum = min(maximum, max(0, budget["bytes"]))
        payload = self._read_bytes(path, maximum)
        if payload is None:
            if self._entry_present(path):
                if fail_on_budget:
                    raise OSError("storage_corrupt")
                self._increment(diagnostics, "skipped_files")
            return []
        if budget is not None:
            budget["bytes"] -= len(payload)
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
                _validate_temporal_row(parsed, validation_now or _utc(self._now(), "archive clock"))
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

    def _index_payload(self, events: dict[str, str]) -> bytes:
        if len(events) > _MAX_INDEX_EVENTS:
            raise ValueError("archive index is too large")
        canonical: dict[str, str] = {}
        for event_id, bucket in events.items():
            canonical[_bounded_text(event_id, "index event_id")] = _bounded_text(bucket, "index bucket")
            if not _valid_bucket_name(bucket):
                raise ValueError("invalid archive index bucket")
        document = {"schema_version": _INDEX_SCHEMA_VERSION, "events": dict(sorted(canonical.items()))}
        payload = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > _MAX_INDEX_BYTES:
            raise ValueError("archive index is too large")
        return payload

    def _write_index(self, events: dict[str, str]) -> None:
        self._atomic_write(self.index_path, self._index_payload(events), _MAX_INDEX_BYTES)

    def _journal_payload(self, rows: list[dict[str, Any]]) -> bytes:
        if len(rows) > _MAX_INDEX_EVENTS:
            raise ValueError("archive snapshot journal is too large")
        canonical = [_archive_from_document(row) for row in sorted(rows, key=lambda row: row["event_id"])]
        payload = (json.dumps(
            {"schema_version": _JOURNAL_SCHEMA_VERSION, "rows": canonical},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n").encode("utf-8")
        if len(payload) > _MAX_JOURNAL_BYTES:
            raise ValueError("archive snapshot journal is too large")
        return payload

    def _read_journal(self, now: datetime, diagnostics: dict[str, int]) -> list[dict[str, Any]]:
        raw = self._read_bytes(self.journal_path, _MAX_JOURNAL_BYTES)
        if raw is None:
            if self._entry_present(self.journal_path):
                self._increment(diagnostics, "skipped_files")
            return []
        try:
            document = self._parse_json(raw)
            if type(document) is not dict or set(document) != _JOURNAL_KEYS:
                raise ValueError("invalid archive journal")
            if document["schema_version"] != _JOURNAL_SCHEMA_VERSION or type(document["rows"]) is not list:
                raise ValueError("invalid archive journal")
            if len(document["rows"]) > _MAX_INDEX_EVENTS:
                raise ValueError("invalid archive journal")
            rows = [_archive_from_document(row) for row in document["rows"]]
            for row in rows:
                _validate_temporal_row(row, now)
            return rows
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError, json.JSONDecodeError):
            self._increment(diagnostics, "skipped_files")
            return []

    def _remove_journal(self) -> None:
        metadata = self._safe_file(self.journal_path)
        if metadata is None:
            return
        _cleanup_owned_temp(self.journal_path, (metadata.st_dev, metadata.st_ino))
        _sync_directory(self.archive_root)

    def _rebuild_index(self) -> dict[str, str]:
        diagnostics = self._diagnostics()
        budget = {"bytes": _MAX_SCAN_BYTES, "rows": _MAX_SCAN_ROWS}
        events: dict[str, tuple[str, datetime]] = {}
        now = _utc(self._now(), "archive clock")
        cursor = (now - timedelta(days=90)).date()
        names: list[str] = []
        while cursor <= now.date():
            names.append(f"{cursor.isoformat()}.jsonl")
            cursor += timedelta(days=1)
        for name in names:
            for row in self._read_bucket(
                name,
                diagnostics,
                budget=budget,
                fail_on_budget=True,
                validation_now=now,
            ):
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
        for row in incoming:
            _validate_temporal_row(row, archived_at)
        incoming_ids = [row["event_id"] for row in incoming]
        if len(incoming_ids) != len(set(incoming_ids)):
            raise ValueError("archive snapshot contains duplicate event identities")
        _ensure_snapshot_budget(incoming)
        with CACHE_IO_LOCK, self._process_lock():
            diagnostics = self._diagnostics()
            pending = self._read_journal(archived_at, diagnostics)
            if not pending and self._entry_present(self.journal_path) and diagnostics["skipped_files"]:
                raise OSError("storage_corrupt")
            index = self._read_index() or {}
            cutoff = archived_at - timedelta(days=90)
            names: set[str] = {
                _bucket_name(row) for row in (*pending, *incoming)
            }
            cursor = cutoff.date()
            while cursor <= archived_at.date():
                names.add(f"{cursor.isoformat()}.jsonl")
                cursor += timedelta(days=1)
            for event_id in {row["event_id"] for row in (*pending, *incoming)}:
                previous_name = index.get(event_id)
                if previous_name is not None:
                    names.add(previous_name)

            budget = {"bytes": _MAX_SCAN_BYTES, "rows": _MAX_SCAN_ROWS}
            bucket_rows: dict[str, list[dict[str, Any]]] = {}
            existing: dict[str, dict[str, Any]] = {}
            locations: dict[str, set[str]] = {}
            for name in sorted(names):
                rows = self._read_bucket(
                    name,
                    diagnostics,
                    budget=budget,
                    fail_on_budget=True,
                    validation_now=archived_at,
                )
                bucket_rows[name] = rows
                for row in rows:
                    event_id = row["event_id"]
                    locations.setdefault(event_id, set()).add(name)
                    previous = existing.get(event_id)
                    existing[event_id] = row if previous is None else _merge_archive_rows(previous, row)

            committed: dict[str, dict[str, Any]] = {}
            for row in (*pending, *incoming):
                event_id = row["event_id"]
                previous = committed.get(event_id, existing.get(event_id))
                lineage_cutoff = cutoff if _event_time(row) >= cutoff else None
                committed[event_id] = row if previous is None else _merge_archive_rows(
                    previous,
                    row,
                    lineage_cutoff=lineage_cutoff,
                )

            _ensure_snapshot_budget(list(committed.values()))
            affected: set[str] = set()
            target_names: set[str] = set()
            for event_id, row in committed.items():
                affected.update(locations.get(event_id, set()))
                target_name = _bucket_name(row)
                affected.add(target_name)
                target_names.add(target_name)
                bucket_rows.setdefault(target_name, [])

            planned_rows = {name: list(bucket_rows.get(name, [])) for name in affected}
            for event_id, row in committed.items():
                for name in locations.get(event_id, set()):
                    planned_rows[name] = [item for item in planned_rows[name] if item["event_id"] != event_id]
                target_name = _bucket_name(row)
                planned_rows[target_name] = [
                    item for item in planned_rows[target_name] if item["event_id"] != event_id
                ]
                if len(planned_rows[target_name]) >= _MAX_BUCKET_ROWS:
                    raise ValueError("archive bucket is full")
                planned_rows[target_name].append(row)

            current_rows = dict(existing)
            current_rows.update(committed)
            planned_index = {
                event_id: _bucket_name(row)
                for event_id, row in current_rows.items()
                if cutoff <= _event_time(row) <= archived_at
            }
            index_payload = self._index_payload(planned_index)
            bucket_payloads = {
                name: self._bucket_payload(name, rows)
                for name, rows in planned_rows.items()
            }
            journal_payload = self._journal_payload(list(committed.values()))
            mutation_bytes = len(index_payload) + len(journal_payload) + sum(map(len, bucket_payloads.values()))
            if mutation_bytes > _MAX_MUTATION_BYTES:
                raise ValueError("archive snapshot mutation budget exceeded")

            self._atomic_write(self.journal_path, journal_payload, _MAX_JOURNAL_BYTES)
            write_order = sorted(target_names) + sorted(affected - target_names)
            for name in write_order:
                self._atomic_write(self.archive_root / name, bucket_payloads[name], _MAX_BUCKET_BYTES)
            self._atomic_write(self.index_path, index_payload, _MAX_INDEX_BYTES)
            self._remove_journal()

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
            for row in self._read_bucket(name, diagnostics, budget=budget, validation_now=now):
                effective = _event_time(row)
                if effective < cutoff or effective > now:
                    continue
                existing = selected.get(row["event_id"])
                if existing is None:
                    selected[row["event_id"]] = row
                else:
                    self._increment(diagnostics, "duplicate_rows")
                    selected[row["event_id"]] = _merge_archive_rows(existing, row)
            cursor += timedelta(days=1)
        for row in self._read_journal(now, diagnostics):
            effective = _event_time(row)
            if effective < cutoff or effective > now:
                continue
            existing = selected.get(row["event_id"])
            if existing is None:
                selected[row["event_id"]] = row
            else:
                self._increment(diagnostics, "duplicate_rows")
                selected[row["event_id"]] = _merge_archive_rows(existing, row)
        self._last_diagnostics = diagnostics
        return sorted(
            (row for row in selected.values() if status is None or row["verification_status"] == status),
            key=lambda row: (_event_time(row), _parse_datetime(row["verified_at"]), row["event_id"]),
            reverse=True,
        )

    def query(self, days: int, status: str | None = None) -> list[dict[str, Any]]:
        if type(days) is not int or days not in _ALLOWED_DAYS:
            raise ValueError("days must be one of 1, 3, 7, 30, 90")
        if status is not None and (type(status) is not str or status not in _KNOWN_STATUSES):
            raise ValueError("invalid verification status")
        with CACHE_IO_LOCK:
            return self._query_unlocked(days, status)

    def get(self, event_id: str) -> dict[str, Any] | None:
        if type(event_id) is not str or not event_id or len(event_id) > 128:
            return None
        with CACHE_IO_LOCK:
            return next((row for row in self._query_unlocked(90, None) if row["event_id"] == event_id), None)

    def count(self) -> int:
        with CACHE_IO_LOCK:
            return len(self._query_unlocked(90, None))
