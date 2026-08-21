"""Allowlisted, evidence-only recovery into the bounded event archive."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .archive import EvidenceArchive, _archive_public_url
from .models import EvidenceEvent, EvidenceSnapshot, VerificationStatus
from .storage import evidence_snapshot_from_document


_MAX_FILES = 128
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 10_000
_MAX_NODES = 100_000
_MAX_DIRECTORY_ENTRIES = 512
_HISTORY_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")
_LEGACY_NAME = re.compile(r"^evidence-[A-Za-z0-9._-]{1,80}\.json$")
_KNOWN_STATUSES = frozenset(status.value for status in VerificationStatus)


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


def _walk(value: object, *, depth: int = 0) -> int:
    if depth > 24:
        raise ValueError("invalid recovery document")
    if value is None or type(value) in {bool, int, str}:
        return 1
    if type(value) is list:
        total = 1
        for item in value:
            total += _walk(item, depth=depth + 1)
            if total > _MAX_NODES:
                raise ValueError("invalid recovery document")
        return total
    if type(value) is dict:
        total = 1
        for key, item in value.items():
            if type(key) is not str or len(key) > 256:
                raise ValueError("invalid recovery document")
            total += 1 + _walk(item, depth=depth + 1)
            if total > _MAX_NODES:
                raise ValueError("invalid recovery document")
        return total
    raise ValueError("invalid recovery document")


def _decode_json(raw: bytes) -> object:
    if len(raw) > _MAX_FILE_BYTES:
        raise ValueError("invalid recovery document")
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_strict_object,
        parse_int=_bounded_int,
        parse_float=lambda _value: (_ for _ in ()).throw(ValueError("invalid recovery number")),
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid recovery number")),
    )
    _walk(value)
    return value


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


def _event_is_complete(event: EvidenceEvent) -> bool:
    if not event.event_id or len(event.event_id) > 128 or not event.title.strip():
        return False
    if event.published_at is None or event.verification_status.value not in _KNOWN_STATUSES:
        return False
    links = _event_links(event)
    return bool(links) and all(_public_link(link) == link for link in links)


def _snapshot_is_complete(snapshot: EvidenceSnapshot) -> bool:
    return bool(snapshot.events) and all(_event_is_complete(event) for event in snapshot.events)


@dataclass(frozen=True, slots=True)
class _RefetchCandidate:
    event_id: str
    title: str
    public_link: str
    published_at: datetime
    verification_status: str


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    cache_recovered: int = 0
    public_refetched: int = 0
    unrecoverable: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    opened_paths: tuple[Path, ...] = ()
    _snapshots: tuple[EvidenceSnapshot, ...] = field(default=(), repr=False)
    _refetch: tuple[_RefetchCandidate, ...] = field(default=(), repr=False)

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
        self._evidence_history = tuple(Path(path) for path in evidence_history) if evidence_history is not None else None
        self._legacy_snapshots = tuple(Path(path) for path in legacy_snapshots) if legacy_snapshots is not None else None
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.archive = archive or EvidenceArchive(self.root, now=self._now)
        self._public_refetcher = public_refetcher

    @staticmethod
    def _add_reason(reasons: dict[str, int], reason: str, amount: int = 1) -> None:
        reasons[reason] = reasons.get(reason, 0) + amount

    def _default_named_files(self, directory: Path, pattern: re.Pattern[str]) -> tuple[Path, ...]:
        chain_before = self._directory_chain(directory / "candidate")
        if chain_before is None:
            return ()
        try:
            entries = []
            observed = 0
            with os.scandir(directory) as scanned:
                for entry in scanned:
                    observed += 1
                    if observed > _MAX_DIRECTORY_ENTRIES:
                        return ()
                    if len(entries) >= _MAX_FILES:
                        break
                    if pattern.fullmatch(entry.name):
                        entries.append(directory / entry.name)
            chain_after = self._directory_chain(directory / "candidate")
            if chain_after is None or chain_before != chain_after:
                return ()
            return tuple(sorted(entries, key=lambda path: path.name))
        except (FileNotFoundError, NotADirectoryError, OSError):
            return ()

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
            if before.st_size < 0 or before.st_size > _MAX_FILE_BYTES or before.st_size > remaining:
                return None, "file_too_large"
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
        except (KeyError, TypeError, ValueError):
            return None
        return snapshot if _snapshot_is_complete(snapshot) else None

    def _radar_candidates(
        self,
        value: object,
        snapshots: list[EvidenceSnapshot],
        refetch: list[_RefetchCandidate],
        reasons: dict[str, int],
    ) -> None:
        if type(value) is not dict or type(value.get("industries")) is not list:
            self._add_reason(reasons, "invalid_document")
            return
        rows = 0
        for industry in value["industries"]:
            if type(industry) is not dict or type(industry.get("items")) is not list:
                self._add_reason(reasons, "invalid_document")
                continue
            for item in industry["items"]:
                rows += 1
                if rows > _MAX_ROWS:
                    self._add_reason(reasons, "record_limit")
                    return
                if type(item) is not dict:
                    self._add_reason(reasons, "missing_required_fields")
                    continue
                embedded = self._parse_snapshot(item.get("evidence_snapshot"))
                if embedded is not None:
                    snapshots.append(embedded)
                    continue
                event_id = item.get("event_id")
                title = item.get("title")
                link_value = item.get("original_url") or item.get("canonical_url") or item.get("url")
                published = _aware_time(item.get("published_at"))
                status_value = item.get("verification_status")
                if (
                    type(event_id) is not str
                    or not event_id
                    or len(event_id) > 128
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
        snapshots: list[EvidenceSnapshot],
        reasons: dict[str, int],
    ) -> None:
        lines = raw.splitlines()
        if len(lines) > _MAX_ROWS:
            self._add_reason(reasons, "record_limit")
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                value = _decode_json(line)
            except (UnicodeDecodeError, ValueError, RecursionError, json.JSONDecodeError):
                self._add_reason(reasons, "invalid_document")
                continue
            snapshot_value = value.get("snapshot") if type(value) is dict and "snapshot" in value else value
            snapshot = self._parse_snapshot(snapshot_value)
            if snapshot is None:
                self._add_reason(reasons, "missing_required_fields")
            else:
                snapshots.append(snapshot)

    def scan(self) -> RecoveryReport:
        reasons: dict[str, int] = {}
        snapshots: list[EvidenceSnapshot] = []
        refetch: list[_RefetchCandidate] = []
        opened: list[Path] = []
        total_bytes = 0
        history = self._evidence_history
        if history is None:
            history = self._default_named_files(self.root / "history", _HISTORY_NAME)
        legacy = self._legacy_snapshots
        if legacy is None:
            legacy = self._default_named_files(self.root / "legacy-snapshots", _LEGACY_NAME)
        requested: tuple[tuple[str, Path], ...] = (
            ("radar", self._radar_cache),
            ("current", self._evidence_current),
            *(("history", path) for path in history),
            *(("legacy", path) for path in legacy),
        )
        seen: set[tuple[str, str]] = set()
        for kind, supplied in requested:
            path = Path(os.path.abspath(supplied))
            identity = (kind, os.path.normcase(str(path)))
            if identity in seen:
                continue
            seen.add(identity)
            if len(seen) > _MAX_FILES:
                self._add_reason(reasons, "file_limit")
                break
            if not self._allowed_name(kind, path):
                self._add_reason(reasons, "path_not_allowlisted")
                continue
            raw, error = self._safe_read(path, _MAX_TOTAL_BYTES - total_bytes)
            if error == "missing":
                continue
            if raw is None:
                self._add_reason(reasons, error or "read_failed")
                continue
            total_bytes += len(raw)
            opened.append(path)
            try:
                if kind == "history":
                    self._history_candidates(raw, snapshots, reasons)
                    continue
                value = _decode_json(raw)
                if kind == "radar":
                    self._radar_candidates(value, snapshots, refetch, reasons)
                    continue
                snapshot = self._parse_snapshot(value)
                if snapshot is None:
                    self._add_reason(reasons, "missing_required_fields")
                else:
                    snapshots.append(snapshot)
            except (UnicodeDecodeError, ValueError, RecursionError, json.JSONDecodeError):
                self._add_reason(reasons, "invalid_document")
        return RecoveryReport(
            unrecoverable=sum(reasons.values()),
            reasons=reasons,
            opened_paths=tuple(opened),
            _snapshots=tuple(snapshots),
            _refetch=tuple(refetch),
        )

    def import_records(self) -> RecoveryReport:
        scanned = self.scan()
        reasons = dict(scanned.reasons)
        cache_ids: set[str] = set()
        refetched_ids: set[str] = set()
        for snapshot in scanned._snapshots:
            try:
                self.archive.upsert(snapshot)
            except (OSError, TypeError, ValueError):
                self._add_reason(reasons, "archive_rejected", len(snapshot.events))
                continue
            cache_ids.update(event.event_id for event in snapshot.events)
        for candidate in scanned._refetch:
            if self._public_refetcher is None:
                self._add_reason(reasons, "public_refetch_unavailable")
                continue
            try:
                value = self._public_refetcher(candidate.public_link)
                if type(value) is EvidenceSnapshot:
                    snapshot = value
                else:
                    snapshot = self._parse_snapshot(value)
                if snapshot is None or len(snapshot.events) != 1:
                    raise ValueError("invalid refetch")
                event = snapshot.events[0]
                if event.event_id != candidate.event_id or candidate.public_link not in _event_links(event):
                    raise ValueError("invalid refetch identity")
                self.archive.upsert(snapshot)
            except (OSError, RuntimeError, TypeError, ValueError):
                self._add_reason(reasons, "public_refetch_failed")
                continue
            if event.event_id not in cache_ids:
                refetched_ids.add(event.event_id)
        return RecoveryReport(
            cache_recovered=len(cache_ids),
            public_refetched=len(refetched_ids),
            unrecoverable=sum(reasons.values()),
            reasons=reasons,
            opened_paths=scanned.opened_paths,
        )
