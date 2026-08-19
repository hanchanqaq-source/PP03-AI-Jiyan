from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable

from cache_io_lock import CACHE_IO_LOCK
from evidence_verification.models import EvidenceSnapshot
from evidence_verification.storage import evidence_snapshot_from_document, snapshot_document

from .models import PipelineCounts, PipelinePhase, PipelineRun, RawSnapshot, TrustedSnapshot


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RECOVERABLE_PHASES = {PipelinePhase.QUEUED, PipelinePhase.FETCHING, PipelinePhase.VERIFYING}


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _required_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


def _document_counts(counts: PipelineCounts) -> dict[str, int]:
    return {key: int(value) for key, value in asdict(counts).items()}


def _counts_from_document(document: object) -> PipelineCounts:
    if not isinstance(document, dict):
        raise ValueError("invalid pipeline counts")
    values = {}
    for name in PipelineCounts.__dataclass_fields__:
        value = document.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("invalid pipeline count")
        values[name] = value
    return PipelineCounts(**values)


class NewsPipelineStorage:
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
        self.raw_root = self.root / "raw"
        self.evidence_root = self.root / "evidence"
        self.trusted_root = self.root / "trusted"
        self.runs_root = self.root / "runs"
        self.current_pointer_path = self.root / "current-trusted.json"
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.recover_incomplete_runs()

    def _atomic_write(self, path: Path, document: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary.unlink()
            except OSError:
                pass
            raise

    def _read_document(self, path: Path) -> dict[str, Any] | None:
        try:
            with CACHE_IO_LOCK:
                document = json.loads(path.read_text(encoding="utf-8"))
            return document if isinstance(document, dict) else None
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _snapshot_path(self, directory: Path, raw_snapshot_id: str) -> Path:
        return directory / f"{_required_id(raw_snapshot_id, 'raw_snapshot_id')}.json"

    def _run_path(self, run_id: str) -> Path:
        return self.runs_root / f"{_required_id(run_id, 'run_id')}.json"

    def write_raw(self, snapshot: RawSnapshot) -> None:
        raw_snapshot_id = _required_id(snapshot.raw_snapshot_id, "raw_snapshot_id")
        self._atomic_write(self._snapshot_path(self.raw_root, raw_snapshot_id), {
            "schema_version": 1,
            "raw_snapshot_id": raw_snapshot_id,
            "collected_at": snapshot.collected_at.isoformat(),
            "items": list(snapshot.items),
        })

    def load_raw(self, raw_snapshot_id: str) -> RawSnapshot | None:
        document = self._read_document(self._snapshot_path(self.raw_root, raw_snapshot_id))
        if not document:
            return None
        try:
            return RawSnapshot(
                raw_snapshot_id=_required_id(document["raw_snapshot_id"], "raw_snapshot_id"),
                collected_at=_parse_datetime(document["collected_at"]),
                items=tuple(item for item in document.get("items") or [] if isinstance(item, dict)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def write_evidence(self, snapshot: EvidenceSnapshot) -> None:
        if snapshot.recovery_metadata.get("legacy_identity") is True:
            raise ValueError("legacy evidence identity cannot be published as a new pipeline artifact")
        _required_id(snapshot.snapshot_id, "snapshot_id")
        raw_snapshot_id = _required_id(snapshot.raw_snapshot_id, "raw_snapshot_id")
        self._atomic_write(self._snapshot_path(self.evidence_root, raw_snapshot_id), snapshot_document(snapshot))

    def load_evidence(self, raw_snapshot_id: str) -> EvidenceSnapshot | None:
        document = self._read_document(self._snapshot_path(self.evidence_root, raw_snapshot_id))
        if not document:
            return None
        try:
            snapshot = evidence_snapshot_from_document(document)
            return snapshot if snapshot.raw_snapshot_id == raw_snapshot_id else None
        except (KeyError, TypeError, ValueError):
            return None

    def publish_trusted(self, snapshot: TrustedSnapshot) -> None:
        raw_snapshot_id = _required_id(snapshot.raw_snapshot_id, "raw_snapshot_id")
        with CACHE_IO_LOCK:
            self._atomic_write(self._snapshot_path(self.trusted_root, raw_snapshot_id), {
                "schema_version": 1,
                "raw_snapshot_id": raw_snapshot_id,
                "published_at": snapshot.published_at.isoformat(),
                "events": list(snapshot.events),
            })
            self._write_current_pointer(raw_snapshot_id)

    def _write_current_pointer(self, raw_snapshot_id: str) -> None:
        self._atomic_write(self.current_pointer_path, {"raw_snapshot_id": _required_id(raw_snapshot_id, "raw_snapshot_id")})

    def load_trusted(self, raw_snapshot_id: str) -> TrustedSnapshot | None:
        document = self._read_document(self._snapshot_path(self.trusted_root, raw_snapshot_id))
        if not document:
            return None
        try:
            return TrustedSnapshot(
                raw_snapshot_id=_required_id(document["raw_snapshot_id"], "raw_snapshot_id"),
                published_at=_parse_datetime(document["published_at"]),
                events=tuple(event for event in document.get("events") or [] if isinstance(event, dict)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def load_current_trusted(self) -> TrustedSnapshot | None:
        document = self._read_document(self.current_pointer_path)
        if not document:
            return None
        try:
            return self.load_trusted(_required_id(document["raw_snapshot_id"], "raw_snapshot_id"))
        except (KeyError, ValueError):
            return None

    def write_run(self, run: PipelineRun) -> None:
        run_id = _required_id(run.run_id, "run_id")
        raw_snapshot_id = _required_id(run.raw_snapshot_id, "raw_snapshot_id")
        for name in ("evidence_snapshot_id", "trusted_snapshot_id", "displayed_trusted_snapshot_id"):
            value = getattr(run, name)
            if value is not None:
                _required_id(value, name)
        if run.phase is PipelinePhase.RAW_SAVED and self.load_raw(raw_snapshot_id) is None:
            raise ValueError("raw snapshot must be durable before raw_saved")
        if run.phase is PipelinePhase.EVIDENCE_SAVED and self.load_evidence(raw_snapshot_id) is None:
            raise ValueError("evidence snapshot must be durable before evidence_saved")
        if run.phase is PipelinePhase.TRUSTED_PUBLISHED:
            current = self.load_current_trusted()
            if current is None or current.raw_snapshot_id != raw_snapshot_id:
                raise ValueError("trusted pointer must be durable before trusted_published")
        self._atomic_write(self._run_path(run_id), {
            "schema_version": 1,
            "run_id": run_id,
            "raw_snapshot_id": raw_snapshot_id,
            "evidence_snapshot_id": run.evidence_snapshot_id,
            "trusted_snapshot_id": run.trusted_snapshot_id,
            "phase": run.phase.value,
            "counts": _document_counts(run.counts),
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
            "redacted_error": run.redacted_error[:500] if run.redacted_error else None,
            "displayed_trusted_snapshot_id": run.displayed_trusted_snapshot_id,
        })

    def load_run(self, run_id: str) -> PipelineRun | None:
        document = self._read_document(self._run_path(run_id))
        if not document:
            return None
        try:
            optional_ids = ("evidence_snapshot_id", "trusted_snapshot_id", "displayed_trusted_snapshot_id")
            for name in optional_ids:
                value = document.get(name)
                if value is not None:
                    _required_id(value, name)
            error = document.get("redacted_error")
            if error is not None and (not isinstance(error, str) or len(error) > 500):
                raise ValueError("invalid redacted_error")
            return PipelineRun(
                run_id=_required_id(document["run_id"], "run_id"),
                raw_snapshot_id=_required_id(document["raw_snapshot_id"], "raw_snapshot_id"),
                evidence_snapshot_id=document.get("evidence_snapshot_id"),
                trusted_snapshot_id=document.get("trusted_snapshot_id"),
                phase=PipelinePhase(document["phase"]),
                counts=_counts_from_document(document.get("counts")),
                created_at=_parse_datetime(document["created_at"]),
                updated_at=_parse_datetime(document["updated_at"]),
                redacted_error=error,
                displayed_trusted_snapshot_id=document.get("displayed_trusted_snapshot_id"),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def recover_incomplete_runs(self) -> None:
        if not self.runs_root.exists():
            return
        for path in sorted(self.runs_root.glob("*.json")):
            run = self.load_run(path.stem)
            if run is not None and run.phase in _RECOVERABLE_PHASES:
                self.write_run(replace(
                    run,
                    phase=PipelinePhase.INTERRUPTED,
                    updated_at=self._now(),
                    redacted_error="运行在完成前中断",
                ))
