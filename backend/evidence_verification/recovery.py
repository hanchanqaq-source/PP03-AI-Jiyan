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

from .archive import EvidenceArchive, _archive_public_url
from .models import EvidenceEvent, EvidenceSnapshot, VerificationStatus
from .storage import evidence_snapshot_from_document, event_document


_MAX_FILES = 128
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 10_000
_MAX_NODES = 100_000
_MAX_DIRECTORY_ENTRIES = 512
_MAX_DIAGNOSTIC_COUNT = 1_000_000
_HISTORY_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")
_LEGACY_NAME = re.compile(r"^evidence-[A-Za-z0-9._-]{1,80}\.json$")
_KNOWN_STATUSES = frozenset(status.value for status in VerificationStatus)


class _NodeLimitExceeded(ValueError):
    pass


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
class _CacheRecord:
    snapshot: EvidenceSnapshot
    source: str


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    cache_recovered: int = 0
    public_refetched: int = 0
    unrecoverable: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    opened_paths: tuple[Path, ...] = ()
    _snapshots: tuple[_CacheRecord, ...] = field(default=(), repr=False)
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
        self._evidence_history, self._history_input_error = self._bounded_input_paths(evidence_history)
        self._legacy_snapshots, self._legacy_input_error = self._bounded_input_paths(legacy_snapshots)
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
            return (), None
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
        except (KeyError, TypeError, ValueError):
            return None
        return snapshot if _snapshot_is_complete(snapshot) else None

    def _snapshot_candidates(
        self,
        value: object,
        records: list[_CacheRecord],
        reasons: dict[str, int],
        *,
        source: str,
        budget: dict[str, int],
        first_row_precounted: bool = False,
    ) -> None:
        if not first_row_precounted and not self._take_row(budget, reasons):
            return
        if type(value) is not dict or type(value.get("events")) is not list:
            self._add_reason(reasons, "missing_required_fields")
            return
        events = value["events"]
        if not events:
            self._add_reason(reasons, "missing_required_fields")
            return
        for index, row in enumerate(events):
            if index > 0 and not self._take_row(budget, reasons):
                return
            single = dict(value)
            single["events"] = [row]
            snapshot = self._parse_snapshot(single)
            if snapshot is None:
                self._add_reason(reasons, "missing_required_fields")
                continue
            records.append(_CacheRecord(snapshot=snapshot, source=source))

    def _radar_candidates(
        self,
        value: object,
        records: list[_CacheRecord],
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
                        reasons,
                        source="radar_cache",
                        budget=budget,
                        first_row_precounted=True,
                    )
                    continue
                if type(item) is not dict:
                    self._add_reason(reasons, "missing_required_fields")
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
        records: list[_CacheRecord],
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
                reasons,
                source="evidence_history",
                budget=budget,
                first_row_precounted=True,
            )

    def scan(self) -> RecoveryReport:
        reasons: dict[str, int] = {}
        records: list[_CacheRecord] = []
        refetch: list[_RefetchCandidate] = []
        opened: list[Path] = []
        total_bytes = 0
        budget = {"rows": _MAX_ROWS, "nodes": _MAX_NODES}
        history = self._evidence_history
        if history is None:
            history, history_error = self._default_named_files(self.root / "history", _HISTORY_NAME)
        else:
            history_error = self._history_input_error
        if history_error is not None:
            self._add_reason(reasons, history_error)
        legacy = self._legacy_snapshots
        if legacy is None:
            legacy, legacy_error = self._default_named_files(self.root / "legacy-snapshots", _LEGACY_NAME)
        else:
            legacy_error = self._legacy_input_error
        if legacy_error is not None:
            self._add_reason(reasons, legacy_error)

        def requested() -> Iterator[tuple[str, Path]]:
            yield "radar", self._radar_cache
            yield "current", self._evidence_current
            for path in history:
                yield "history", path
            for path in legacy:
                yield "legacy", path

        seen: set[str] = set()
        for kind, supplied in requested():
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
                continue
            if raw is None:
                self._add_reason(reasons, error or "read_failed")
                continue
            total_bytes += len(raw)
            opened.append(path)
            try:
                if kind == "history":
                    self._history_candidates(raw, records, reasons, budget)
                    continue
                if budget["nodes"] <= 0:
                    self._report_node_limit(budget, reasons)
                    continue
                value, nodes = _decode_json(raw, maximum_nodes=budget["nodes"])
                budget["nodes"] -= nodes
                if kind == "radar":
                    self._radar_candidates(value, records, refetch, reasons, budget)
                    continue
                self._snapshot_candidates(
                    value,
                    records,
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
        )

    def _now_utc(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("invalid recovery clock")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _event_fingerprint(event: EvidenceEvent) -> str:
        return json.dumps(
            event_document(event),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

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

    def _archive_one(self, snapshot: EvidenceSnapshot) -> bool:
        event = snapshot.events[0]
        try:
            self.archive.upsert(snapshot)
            archived = self.archive.get(event.event_id)
        except Exception:
            return False
        return type(archived) is dict and archived.get("event_id") == event.event_id

    def import_records(self) -> RecoveryReport:
        scanned = self.scan()
        reasons = dict(scanned.reasons)
        cache_ids: set[str] = set()
        refetched_ids: set[str] = set()
        cache_terminal_ids: set[str] = set()
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
        selected_cache: dict[str, _CacheRecord] = {}
        selected_refetch: dict[str, _RefetchCandidate] = {}
        for event_id, records in cache_groups.items():
            fingerprints = {self._event_fingerprint(record.snapshot.events[0]) for record in records}
            if len(fingerprints) != 1:
                blocked.add(event_id)
                self._add_reason(reasons, "ambiguous_record")
            else:
                selected_cache[event_id] = records[0]
        for event_id, candidates in refetch_groups.items():
            identities = {self._candidate_identity(candidate) for candidate in candidates}
            if len(identities) != 1:
                if event_id not in blocked:
                    self._add_reason(reasons, "ambiguous_record")
                blocked.add(event_id)
            else:
                selected_refetch[event_id] = candidates[0]
        for event_id in set(selected_cache) & set(selected_refetch):
            if event_id in blocked:
                continue
            if not self._candidate_matches_event(
                selected_refetch[event_id],
                selected_cache[event_id].snapshot.events[0],
            ):
                blocked.add(event_id)
                self._add_reason(reasons, "ambiguous_record")

        for event_id, record in selected_cache.items():
            if event_id in blocked:
                continue
            cache_terminal_ids.add(event_id)
            snapshot = record.snapshot
            event = snapshot.events[0]
            if not self._in_window(event, now):
                self._add_reason(reasons, "outside_retention_window")
                continue
            recovered = self._recovery_snapshot(
                snapshot,
                source=record.source,
                status="cache_recovered",
                recovered_at=now,
            )
            if not self._archive_one(recovered):
                self._add_reason(reasons, "archive_rejected")
                continue
            cache_ids.add(event_id)

        for event_id, candidate in selected_refetch.items():
            if event_id in blocked or event_id in cache_terminal_ids:
                continue
            if not (now - timedelta(days=90) <= candidate.published_at <= now):
                self._add_reason(reasons, "outside_retention_window")
                continue
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
                if not self._candidate_matches_event(candidate, event) or not self._in_window(event, now):
                    raise ValueError("invalid refetch identity")
            except Exception:
                self._add_reason(reasons, "public_refetch_failed")
                continue
            recovered = self._recovery_snapshot(
                snapshot,
                source="public_refetch",
                status="public_refetched",
                recovered_at=now,
            )
            if not self._archive_one(recovered):
                self._add_reason(reasons, "archive_rejected")
                continue
            refetched_ids.add(event_id)
        return RecoveryReport(
            cache_recovered=len(cache_ids),
            public_refetched=len(refetched_ids),
            unrecoverable=sum(reasons.values()),
            reasons=reasons,
            opened_paths=scanned.opened_paths,
        )
