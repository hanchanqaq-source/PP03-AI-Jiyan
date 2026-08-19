from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Iterator

from cache_io_lock import CACHE_IO_LOCK
from evidence_verification.models import EvidenceSnapshot
from evidence_verification.storage import evidence_snapshot_from_document, snapshot_document
from source_health.probe_errors import redact_probe_message

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
_RUN_KEYS = {"schema_version", "run_id", "raw_snapshot_id", "evidence_snapshot_id", "trusted_snapshot_id", "phase", "counts", "created_at", "updated_at", "redacted_error", "displayed_trusted_snapshot_id", "owner_id", "lease_expires_at"}


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
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp timezone is required")
    return parsed


def _counts(value: object) -> PipelineCounts:
    fields = set(PipelineCounts.__dataclass_fields__)
    if type(value) is not dict or set(value) != fields:
        raise ValueError("invalid pipeline counts")
    if any(type(item) is not int or item < 0 or item > _MAX_INT for item in value.values()):
        raise ValueError("invalid pipeline counts")
    return PipelineCounts(**value)


class NewsPipelineStorage:
    def __init__(self, root: str | os.PathLike[str] | None = None, *, now: Callable[[], datetime] | None = None, owner_id: str | None = None, lock_timeout_seconds: float = 2.0) -> None:
        if root is None:
            configured = os.environ.get("VR_DATA_DIR")
            root = Path(configured) / "evidence-verification" / "v1" if configured else Path(os.environ.get("USERPROFILE") or Path.home()) / ".vibe-research" / "evidence-verification" / "v1"
        self.root = Path(root)
        self.raw_root, self.evidence_root, self.trusted_root, self.runs_root = (self.root / name for name in ("raw", "evidence", "trusted", "runs"))
        self.current_pointer_path, self._lock_path = self.root / "current-trusted.json", self.root / ".news-pipeline.lock"
        self._now, self.owner_id = now or (lambda: datetime.now(timezone.utc)), _optional_id(owner_id, "owner_id")
        if type(lock_timeout_seconds) not in (int, float) or lock_timeout_seconds <= 0:
            raise ValueError("invalid lock timeout")
        self._lock_timeout = float(lock_timeout_seconds)

    def _clock(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime):
            raise ValueError("invalid clock")
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise OSError("pipeline root unavailable")

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
        self._prepare()
        handle = self._lock_path.open("a+b")
        acquired = False
        try:
            deadline = time.monotonic() + self._lock_timeout
            while not (acquired := self._try_lock(handle)):
                if time.monotonic() >= deadline:
                    raise OSError("pipeline lock unavailable")
                time.sleep(0.01)
            yield
        finally:
            if acquired:
                self._unlock(handle)
            handle.close()

    @staticmethod
    def _sync_dir(path: Path) -> None:
        try:
            descriptor = os.open(str(path), os.O_RDONLY)
        except OSError:
            return  # Directory handles are not available on supported Windows filesystems.
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_write(self, path: Path, document: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(raw)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._sync_dir(path.parent)
        except BaseException:
            # Do not unlink by pathname: an attacker/race may have replaced it.
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def _read(self, path: Path) -> dict[str, object] | None:
        try:
            with path.open("rb") as handle:
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

    def write_raw(self, snapshot: RawSnapshot) -> None:
        raw_id = _id(snapshot.raw_snapshot_id, "raw_snapshot_id")
        if not isinstance(snapshot.collected_at, datetime):
            raise ValueError("invalid collected_at")
        items = _bounded(list(snapshot.items))
        if type(items) is not list or any(type(item) is not dict for item in items):
            raise ValueError("raw items must be a list of objects")
        document: dict[str, object] = {"schema_version": 1, "raw_snapshot_id": raw_id, "collected_at": snapshot.collected_at.isoformat(), "items": items}
        with CACHE_IO_LOCK, self._process_lock():
            previous = self._load_raw(raw_id)
            if previous is not None and previous != snapshot:
                raise ValueError("raw snapshot identity is immutable")
            self._atomic_write(self._path(self.raw_root, raw_id), document)

    def _load_raw(self, raw_id: str) -> RawSnapshot | None:
        document = self._read(self._path(self.raw_root, raw_id))
        try:
            if document is None:
                return None
            self._schema(document, {"schema_version", "raw_snapshot_id", "collected_at", "items"})
            identity, items = _id(document["raw_snapshot_id"], "raw_snapshot_id"), document["items"]
            if identity != raw_id or type(items) is not list or any(type(item) is not dict for item in items):
                return None
            return RawSnapshot(identity, _timestamp(document["collected_at"]), tuple(items))
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
            self._atomic_write(self._path(self.evidence_root, raw_id), document)

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
        if not isinstance(snapshot.published_at, datetime):
            raise ValueError("invalid published_at")
        events = _bounded(list(snapshot.events))
        if type(events) is not list or any(type(item) is not dict for item in events):
            raise ValueError("trusted events must be a list of objects")
        with CACHE_IO_LOCK, self._process_lock():
            if self._load_raw(raw_id) is None or self._load_evidence(raw_id) is None:
                raise ValueError("raw and evidence artifacts must be durable before trusted publish")
            self._atomic_write(self._path(self.trusted_root, raw_id), {"schema_version": 1, "raw_snapshot_id": raw_id, "published_at": snapshot.published_at.isoformat(), "events": events})
            if self._load_trusted(raw_id) is None:
                raise OSError("trusted snapshot did not become durable")
            self._write_current_pointer(raw_id)

    def _write_current_pointer(self, raw_id: str) -> None:
        self._atomic_write(self.current_pointer_path, {"schema_version": 1, "raw_snapshot_id": _id(raw_id, "raw_snapshot_id")})

    def _load_trusted(self, raw_id: str) -> TrustedSnapshot | None:
        document = self._read(self._path(self.trusted_root, raw_id))
        try:
            if document is None:
                return None
            self._schema(document, {"schema_version", "raw_snapshot_id", "published_at", "events"})
            identity, events = _id(document["raw_snapshot_id"], "raw_snapshot_id"), document["events"]
            if identity != raw_id or type(events) is not list or any(type(item) is not dict for item in events):
                return None
            return TrustedSnapshot(identity, _timestamp(document["published_at"]), tuple(events))
        except (KeyError, TypeError, ValueError):
            return None

    def load_trusted(self, raw_snapshot_id: str) -> TrustedSnapshot | None:
        with CACHE_IO_LOCK, self._process_lock():
            return self._load_trusted(_id(raw_snapshot_id, "raw_snapshot_id"))

    def _load_current(self) -> TrustedSnapshot | None:
        document = self._read(self.current_pointer_path)
        try:
            if document is None:
                return None
            self._schema(document, {"schema_version", "raw_snapshot_id"})
            return self._load_trusted(_id(document["raw_snapshot_id"], "raw_snapshot_id"))
        except (KeyError, ValueError):
            return None

    def load_current_trusted(self) -> TrustedSnapshot | None:
        with CACHE_IO_LOCK, self._process_lock():
            return self._load_current()

    def _run_document(self, run: PipelineRun) -> dict[str, object]:
        if type(run.phase) is not PipelinePhase or type(run.counts) is not PipelineCounts or not isinstance(run.created_at, datetime) or not isinstance(run.updated_at, datetime):
            raise ValueError("invalid pipeline run")
        if run.redacted_error is not None and type(run.redacted_error) is not str:
            raise ValueError("invalid redacted_error")
        if run.lease_expires_at is not None and not isinstance(run.lease_expires_at, datetime):
            raise ValueError("invalid lease_expires_at")
        return {"schema_version": 1, "run_id": _id(run.run_id, "run_id"), "raw_snapshot_id": _id(run.raw_snapshot_id, "raw_snapshot_id"), "evidence_snapshot_id": _optional_id(run.evidence_snapshot_id, "evidence_snapshot_id"), "trusted_snapshot_id": _optional_id(run.trusted_snapshot_id, "trusted_snapshot_id"), "phase": run.phase.value, "counts": asdict(_counts(asdict(run.counts))), "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat(), "redacted_error": redact_probe_message(run.redacted_error)[:500] if run.redacted_error else None, "displayed_trusted_snapshot_id": _optional_id(run.displayed_trusted_snapshot_id, "displayed_trusted_snapshot_id"), "owner_id": _optional_id(run.owner_id, "owner_id"), "lease_expires_at": run.lease_expires_at.isoformat() if isinstance(run.lease_expires_at, datetime) else None}

    def _load_run(self, run_id: str) -> PipelineRun | None:
        document = self._read(self._run_path(run_id))
        try:
            if document is None:
                return None
            self._schema(document, _RUN_KEYS)
            if _id(document["run_id"], "run_id") != run_id or type(document["phase"]) is not str:
                return None
            owner, lease, error = _optional_id(document["owner_id"], "owner_id"), document["lease_expires_at"], document["redacted_error"]
            if owner is None and lease is not None or error is not None and type(error) is not str:
                return None
            parsed_lease = _timestamp(lease) if lease is not None else None
            return PipelineRun(run_id, _id(document["raw_snapshot_id"], "raw_snapshot_id"), _optional_id(document["evidence_snapshot_id"], "evidence_snapshot_id"), _optional_id(document["trusted_snapshot_id"], "trusted_snapshot_id"), PipelinePhase(document["phase"]), _counts(document["counts"]), _timestamp(document["created_at"]), _timestamp(document["updated_at"]), error, _optional_id(document["displayed_trusted_snapshot_id"], "displayed_trusted_snapshot_id"), owner, parsed_lease)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def _artifact_phase(self, run: PipelineRun) -> None:
        raw, evidence = self._load_raw(run.raw_snapshot_id), self._load_evidence(run.raw_snapshot_id)
        if run.phase is PipelinePhase.RAW_SAVED and raw is None:
            raise ValueError("raw snapshot must be durable before raw_saved")
        if run.phase is PipelinePhase.EVIDENCE_SAVED and (raw is None or evidence is None):
            raise ValueError("raw and evidence artifacts must be durable before evidence_saved")
        if run.phase is PipelinePhase.TRUSTED_PUBLISHED and (raw is None or evidence is None or self._load_trusted(run.raw_snapshot_id) is None or (current := self._load_current()) is None or current.raw_snapshot_id != run.raw_snapshot_id):
            raise ValueError("raw, evidence, trusted, and current pointer must be durable before trusted_published")

    def write_run(self, run: PipelineRun, *, expected_phase: PipelinePhase | None = None) -> None:
        document = self._run_document(run)
        with CACHE_IO_LOCK, self._process_lock():
            current = self._load_run(run.run_id)
            if current is None:
                if run.phase is not PipelinePhase.QUEUED or expected_phase is not None:
                    raise ValueError("new runs must begin queued")
                if self.owner_id is not None and run.owner_id != self.owner_id:
                    raise ValueError("run owner does not match storage owner")
            else:
                if expected_phase is None or expected_phase is not current.phase:
                    raise ValueError("expected current phase does not match")
                if current.phase in _TERMINAL:
                    raise ValueError("terminal run cannot be revived")
                if run.raw_snapshot_id != current.raw_snapshot_id:
                    raise ValueError("immutable raw_snapshot_id")
                for field in ("evidence_snapshot_id", "trusted_snapshot_id", "displayed_trusted_snapshot_id", "owner_id"):
                    if getattr(current, field) is not None and getattr(current, field) != getattr(run, field):
                        raise ValueError(f"immutable {field}")
                if current.owner_id and current.lease_expires_at and current.lease_expires_at > self._clock() and self.owner_id != current.owner_id:
                    raise ValueError("active owner cannot be overwritten")
                if run.phase not in _NEXT.get(current.phase, set()):
                    raise ValueError("invalid phase transition")
            self._artifact_phase(run)
            self._atomic_write(self._run_path(run.run_id), document)

    def load_run(self, run_id: str) -> PipelineRun | None:
        with CACHE_IO_LOCK, self._process_lock():
            return self._load_run(_id(run_id, "run_id"))

    def recover_incomplete_runs(self, *, recovery_owner_id: str) -> int:
        _id(recovery_owner_id, "recovery_owner_id")
        interrupted = 0
        with CACHE_IO_LOCK, self._process_lock():
            if not self.runs_root.exists():
                return 0
            now = self._clock()
            for path in sorted(self.runs_root.glob("*.json")):
                try:
                    run = self._load_run(_id(path.stem, "run_id"))
                except ValueError:
                    continue
                if run is None or run.phase not in _NONTERMINAL or (run.owner_id and run.lease_expires_at and run.lease_expires_at > now):
                    continue
                recovered = replace(run, phase=PipelinePhase.INTERRUPTED, updated_at=now, redacted_error="运行在完成前中断")
                self._atomic_write(self._run_path(run.run_id), self._run_document(recovered))
                interrupted += 1
        return interrupted
