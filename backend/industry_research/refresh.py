from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, Callable, Mapping
from uuid import uuid4

from data_sources.models import ProviderValue
from data_sources.provider_contract import ProviderRequest
from evidence_verification.models import EvidenceSnapshot
from evidence_verification.storage import EvidenceStorage

from .admission import RawMetricObservation, admit_metric_observations
from .models import (
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    CandidateIndustryEvidenceEvent,
    ConflictingObservation,
    ConflictingSourceValue,
    IndustryMetricObservation,
    MetricChange,
    RefreshPhase,
    RefreshRun,
    VerificationStatus,
    _conflicting_truth_key,
    _verified_conflicting_observation,
)
from .source_qualification import (
    SourceQualificationResult,
    qualification_allows_enabled_adapter,
)
from .storage import IndustryResearchStorage, _ensure_directory


_CAPABILITY_ID = "industry_price_snapshot"
_INDUSTRY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_STATE_BYTES = 4 * 1024 * 1024
_ERROR_CODES = frozenset({
    "admission_failed",
    "all_sources_failed",
    "assembly_failed",
    "conflicting_evidence",
    "evidence_verification_failed",
    "internal_error",
    "no_eligible_provider",
    "no_trusted_observations",
    "partial_source_failure",
    "publication_failed",
    "publication_proof_invalid",
    "raw_build_failed",
    "raw_industry_mismatch",
    "refresh_cancelled",
    "refresh_interrupted",
    "refresh_shutdown",
    "source_industry_mismatch",
    "storage_error",
})


@dataclass(frozen=True, slots=True)
class RefreshRawSnapshot:
    industry_id: str
    raw_snapshot_id: str
    observations: tuple[RawMetricObservation, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("industry_id", self.industry_id),
            ("raw_snapshot_id", self.raw_snapshot_id),
        ):
            if type(value) is not str or not value.strip() or value != value.strip():
                raise ValueError(f"refresh raw {name} must not be blank")
        if type(self.observations) is not tuple:
            raise TypeError("refresh raw observations must be a tuple")
        if any(type(row) is not RawMetricObservation for row in self.observations):
            raise TypeError("refresh raw observations must contain RawMetricObservation")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _valid_run_id(value: object) -> bool:
    return type(value) is str and value not in {".", ".."} and _RUN_ID.fullmatch(value) is not None


def _run_from_dict(value: object) -> RefreshRun:
    required = {
        "industry_id", "run_id", "raw_snapshot_id", "evidence_snapshot_id",
        "candidate_snapshot_id", "phase", "error_code",
        "displayed_trusted_snapshot_id", "published_trusted_snapshot_id",
        "displayed_raw_snapshot_id", "displayed_evidence_snapshot_id",
    }
    if type(value) is not dict or set(value) != required:
        raise ValueError("invalid refresh run schema")
    row = value
    run = RefreshRun(
        industry_id=row["industry_id"],
        run_id=row["run_id"],
        raw_snapshot_id=row["raw_snapshot_id"],
        evidence_snapshot_id=row["evidence_snapshot_id"],
        candidate_snapshot_id=row["candidate_snapshot_id"],
        phase=RefreshPhase(row["phase"]),
        error_code=row["error_code"],
        displayed_trusted_snapshot_id=row["displayed_trusted_snapshot_id"],
        published_trusted_snapshot_id=row["published_trusted_snapshot_id"],
        displayed_raw_snapshot_id=row["displayed_raw_snapshot_id"],
        displayed_evidence_snapshot_id=row["displayed_evidence_snapshot_id"],
    )
    if not _valid_run_id(run.run_id):
        raise ValueError("invalid persisted refresh run_id")
    expected_candidate = f"candidate-{run.run_id}"
    if run.candidate_snapshot_id is not None and run.candidate_snapshot_id != expected_candidate:
        raise ValueError("invalid derived candidate_snapshot_id")
    if run.evidence_snapshot_id is not None and run.raw_snapshot_id is None:
        raise ValueError("evidence lineage requires raw_snapshot_id")
    if run.candidate_snapshot_id is not None and (
        run.raw_snapshot_id is None or run.evidence_snapshot_id is None
    ):
        raise ValueError("candidate lineage requires raw and evidence snapshots")
    if run.phase is RefreshPhase.FAILED:
        if run.error_code not in _ERROR_CODES:
            raise ValueError("unknown persisted refresh error_code")
    elif run.error_code is not None:
        raise ValueError("non-failed refresh run cannot contain error_code")
    if run.phase is RefreshPhase.TRUSTED_PUBLISHED:
        expected_trusted = f"trusted-{run.run_id}"
        if (
            run.published_trusted_snapshot_id != expected_trusted
            or run.displayed_trusted_snapshot_id != expected_trusted
        ):
            raise ValueError("invalid derived trusted_snapshot_id")
    return run


def _candidate_from_dict(value: object) -> CandidateEvidencePanel:
    required = {
        "industry_id", "candidate_snapshot_id", "counts", "unverified", "conflicting",
        "unverified_events", "conflicting_events", "raw_snapshot_id", "evidence_snapshot_id",
    }
    if type(value) is not dict or set(value) != required:
        raise ValueError("invalid candidate state schema")
    row = value
    counts = row["counts"]
    if type(counts) is not dict or set(counts) != {
        "unverified", "conflicting", "unverified_events", "conflicting_events",
    }:
        raise ValueError("invalid candidate counts schema")

    conflicts: list[ConflictingObservation] = []
    for item in row["conflicting"]:
        if type(item) is not dict or set(item) != {
            "industry_id", "metric_id", "aggregate_value", "source_values",
            "raw_snapshot_id", "evidence_snapshot_id",
        }:
            raise ValueError("invalid conflicting observation schema")
        source_values = []
        for source in item["source_values"]:
            if type(source) is not dict or set(source) != {
                "evidence_id", "source_family_id", "value", "unit", "as_of_date", "change",
            }:
                raise ValueError("invalid conflicting source schema")
            change = source["change"]
            source_values.append(ConflictingSourceValue(
                evidence_id=source["evidence_id"],
                source_family_id=source["source_family_id"],
                value=source["value"],
                unit=source["unit"],
                as_of_date=source["as_of_date"],
                change=MetricChange(**change) if type(change) is dict else None,
            ))
        values = tuple(source_values)
        kwargs = dict(
            industry_id=item["industry_id"],
            metric_id=item["metric_id"],
            source_values=values,
            raw_snapshot_id=item["raw_snapshot_id"],
            evidence_snapshot_id=item["evidence_snapshot_id"],
        )
        if len({_conflicting_truth_key(source) for source in values}) < 2:
            conflict = _verified_conflicting_observation(**kwargs)
        else:
            conflict = ConflictingObservation(aggregate_value=None, **kwargs)
        conflicts.append(conflict)

    def candidate_event(item: object) -> CandidateIndustryEvidenceEvent:
        required_event = {
            "industry_id", "event_id", "status", "occurred_at", "evidence_ids",
            "supporting_evidence_ids", "contradicting_evidence_ids", "roles",
            "candidate_snapshot_id", "raw_snapshot_id", "evidence_snapshot_id",
        }
        if type(item) is not dict or set(item) != required_event:
            raise ValueError("invalid candidate event schema")
        return CandidateIndustryEvidenceEvent(
            industry_id=item["industry_id"],
            event_id=item["event_id"],
            status=VerificationStatus(item["status"]),
            occurred_at=item["occurred_at"],
            evidence_ids=tuple(item["evidence_ids"]),
            supporting_evidence_ids=tuple(item["supporting_evidence_ids"]),
            contradicting_evidence_ids=tuple(item["contradicting_evidence_ids"]),
            roles=tuple(item["roles"]),
            candidate_snapshot_id=item["candidate_snapshot_id"],
            raw_snapshot_id=item["raw_snapshot_id"],
            evidence_snapshot_id=item["evidence_snapshot_id"],
        )

    return CandidateEvidencePanel(
        industry_id=row["industry_id"],
        candidate_snapshot_id=row["candidate_snapshot_id"],
        counts=CandidateEvidenceCounts(**counts),
        unverified=tuple(IndustryMetricObservation.from_dict(item) for item in row["unverified"]),
        conflicting=tuple(conflicts),
        unverified_events=tuple(candidate_event(item) for item in row["unverified_events"]),
        conflicting_events=tuple(candidate_event(item) for item in row["conflicting_events"]),
        raw_snapshot_id=row["raw_snapshot_id"],
        evidence_snapshot_id=row["evidence_snapshot_id"],
    )


@dataclass(frozen=True, slots=True)
class _PublicationProof:
    run_id: str
    industry_id: str
    raw_snapshot_id: str
    evidence_snapshot_id: str
    candidate_snapshot_id: str
    trusted_snapshot_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "industry_id": self.industry_id,
            "raw_snapshot_id": self.raw_snapshot_id,
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "candidate_snapshot_id": self.candidate_snapshot_id,
            "trusted_snapshot_id": self.trusted_snapshot_id,
        }


@dataclass(frozen=True, slots=True)
class _PersistedState:
    run: RefreshRun
    candidate: CandidateEvidencePanel | None
    generation: int
    publication: _PublicationProof | None


class _StateConflict(OSError):
    pass


def _publication_from_dict(value: object) -> _PublicationProof:
    keys = {
        "run_id", "industry_id", "raw_snapshot_id", "evidence_snapshot_id",
        "candidate_snapshot_id", "trusted_snapshot_id",
    }
    if type(value) is not dict or set(value) != keys:
        raise ValueError("invalid publication proof schema")
    proof = _PublicationProof(**value)
    if not _valid_run_id(proof.run_id):
        raise ValueError("invalid publication proof run_id")
    if proof.candidate_snapshot_id != f"candidate-{proof.run_id}":
        raise ValueError("invalid publication proof candidate id")
    if proof.trusted_snapshot_id != f"trusted-{proof.run_id}":
        raise ValueError("invalid publication proof trusted id")
    return proof


class _RefreshStateStore:
    """Checksum-bound latest run/candidate state, atomically replaced per industry."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(os.path.abspath(root))
        self._writer = IndustryResearchStorage(root=self.root, production=False)
        self._lock = threading.RLock()

    def _path(self, industry_id: str) -> Path:
        if type(industry_id) is not str or not _INDUSTRY_ID.fullmatch(industry_id):
            raise ValueError("invalid industry_id for refresh state")
        path = self.root / industry_id / "refresh_run.json"
        if os.path.commonpath((str(self.root), os.path.abspath(path))) != str(self.root):
            raise ValueError("refresh state path escapes root")
        return path

    def write(
        self,
        run: RefreshRun,
        candidate: CandidateEvidencePanel | None,
        *,
        publication: _PublicationProof | None = None,
        expected_generation: int | None = None,
    ) -> int:
        if _run_from_dict(run.to_dict()) != run:
            raise ValueError("invalid refresh run state")
        if candidate is not None and candidate.industry_id != run.industry_id:
            raise ValueError("candidate state industry_id mismatch")
        if (run.candidate_snapshot_id is None) != (candidate is None):
            raise ValueError("refresh run/candidate state must be provided together")
        if candidate is not None and (
            candidate.candidate_snapshot_id,
            candidate.raw_snapshot_id,
            candidate.evidence_snapshot_id,
        ) != (
            run.candidate_snapshot_id,
            run.raw_snapshot_id,
            run.evidence_snapshot_id,
        ):
            raise ValueError("candidate state does not match refresh lineage")
        if publication is not None and (
            run.phase is not RefreshPhase.TRUSTED_PUBLISHED
            or publication.run_id != run.run_id
            or publication.industry_id != run.industry_id
            or publication.raw_snapshot_id != run.raw_snapshot_id
            or publication.evidence_snapshot_id != run.evidence_snapshot_id
            or publication.candidate_snapshot_id != run.candidate_snapshot_id
            or publication.trusted_snapshot_id != run.published_trusted_snapshot_id
        ):
            raise ValueError("publication proof does not match refresh run")
        if run.phase is RefreshPhase.TRUSTED_PUBLISHED and publication is None:
            raise ValueError("trusted publication requires durable proof")
        with self._lock:
            current = self.load_record(run.industry_id)
            current_generation = current.generation if current is not None else 0
            if expected_generation is not None and current_generation != expected_generation:
                raise _StateConflict("refresh state generation conflict")
            generation = current_generation + 1
            state = {
                "generation": generation,
                "run": run.to_dict(),
                "candidate": candidate.to_dict() if candidate is not None else None,
                "publication": publication.to_dict() if publication is not None else None,
            }
            document = {
                "schema_version": 2,
                "checksum": hashlib.sha256(_canonical(state)).hexdigest(),
                "state": state,
            }
            payload = _canonical(document) + b"\n"
            if len(payload) > _MAX_STATE_BYTES:
                raise ValueError("refresh state is too large")
            self._writer._atomic_write(self._path(run.industry_id), payload)
            return generation

    def load(self, industry_id: str) -> tuple[RefreshRun | None, CandidateEvidencePanel | None]:
        record = self.load_record(industry_id)
        if record is None:
            return None, None
        return record.run, record.candidate

    def load_record(self, industry_id: str) -> _PersistedState | None:
        path = self._path(industry_id)
        descriptor: int | None = None
        try:
            with self._lock:
                self._writer._verify_parent(path)
                before = path.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or getattr(before, "st_reparse_tag", 0)
                    or before.st_nlink != 1
                ):
                    return None
                descriptor = os.open(
                    path,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                opened = os.fstat(descriptor)
                if (
                    (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or opened.st_nlink != 1
                ):
                    return None
                chunks = bytearray()
                while len(chunks) <= _MAX_STATE_BYTES:
                    chunk = os.read(descriptor, _MAX_STATE_BYTES + 1 - len(chunks))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                raw = bytes(chunks)
            if len(raw) > _MAX_STATE_BYTES:
                return None
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
            if type(document) is not dict or set(document) != {"schema_version", "checksum", "state"}:
                return None
            if document["schema_version"] not in {1, 2} or type(document["checksum"]) is not str:
                return None
            state = document["state"]
            expected_keys = (
                {"run", "candidate"}
                if document["schema_version"] == 1
                else {"generation", "run", "candidate", "publication"}
            )
            if type(state) is not dict or set(state) != expected_keys:
                return None
            if hashlib.sha256(_canonical(state)).hexdigest() != document["checksum"]:
                return None
            generation = 1 if document["schema_version"] == 1 else state["generation"]
            if type(generation) is not int or generation <= 0:
                return None
            run = _run_from_dict(state["run"])
            candidate = _candidate_from_dict(state["candidate"]) if state["candidate"] is not None else None
            if run.industry_id != industry_id:
                return None
            if (run.candidate_snapshot_id is None) != (candidate is None):
                return None
            if candidate is not None and (
                candidate.industry_id,
                candidate.candidate_snapshot_id,
                candidate.raw_snapshot_id,
                candidate.evidence_snapshot_id,
            ) != (
                run.industry_id,
                run.candidate_snapshot_id,
                run.raw_snapshot_id,
                run.evidence_snapshot_id,
            ):
                return None
            publication = (
                _publication_from_dict(state["publication"])
                if document["schema_version"] == 2 and state["publication"] is not None
                else None
            )
            if publication is not None and (
                run.phase is not RefreshPhase.TRUSTED_PUBLISHED
                or publication.run_id != run.run_id
                or publication.industry_id != run.industry_id
                or publication.raw_snapshot_id != run.raw_snapshot_id
                or publication.evidence_snapshot_id != run.evidence_snapshot_id
                or publication.candidate_snapshot_id != run.candidate_snapshot_id
                or publication.trusted_snapshot_id != run.published_trusted_snapshot_id
            ):
                return None
            if document["schema_version"] == 2 and (
                run.phase is RefreshPhase.TRUSTED_PUBLISHED
            ) != (publication is not None):
                return None
            return _PersistedState(run, candidate, generation, publication)
        except (
            AttributeError, FileNotFoundError, OSError, OverflowError, RecursionError,
            UnicodeDecodeError, ValueError, TypeError, KeyError, json.JSONDecodeError,
        ):
            return None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


class _IndustryLease:
    """Process-scoped nonblocking advisory lock held for one industry run."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor
        self._released = False

    @classmethod
    def try_acquire(
        cls,
        state: _RefreshStateStore,
        industry_id: str,
    ) -> _IndustryLease | None:
        if type(industry_id) is not str or not _INDUSTRY_ID.fullmatch(industry_id):
            raise ValueError("invalid industry_id for refresh lease")
        directory = state.root / ".refresh-locks"
        path = directory / f"{industry_id}.lock"
        descriptor: int | None = None
        try:
            _ensure_directory(directory)
            state._writer._verify_parent(path)
            descriptor = os.open(
                path,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise OSError("storage_error")
            if opened.st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise OSError("storage_error") from None
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                return None
            raise OSError("storage_error") from None
        try:
            current = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or getattr(current, "st_reparse_tag", 0)
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise OSError("storage_error")
            return cls(descriptor)
        except OSError:
            cls._unlock(descriptor)
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise OSError("storage_error") from None

    @staticmethod
    def _unlock(descriptor: int) -> None:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._unlock(self._descriptor)
        try:
            os.close(self._descriptor)
        except OSError:
            pass


class IndustryResearchRefreshOrchestrator:
    """Explicit, bounded refresh pipeline; read paths never call ``request_refresh``."""

    def __init__(
        self,
        *,
        catalog: Any,
        provider_registry: Any,
        qualifications: Mapping[str, SourceQualificationResult],
        raw_builder: Callable[[str, str, tuple[ProviderValue, ...]], RefreshRawSnapshot],
        evidence_verifier: Callable[[RefreshRawSnapshot], EvidenceSnapshot],
        evidence_storage_factory: Callable[[str, str], EvidenceStorage],
        report_service: Any,
        report_storage: IndustryResearchStorage,
        state_root: str | os.PathLike[str],
        max_workers: int = 2,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        run_id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        if type(max_workers) is not int or max_workers <= 0:
            raise ValueError("max_workers must be a positive integer")
        self._catalog = catalog
        self._registry = provider_registry
        self._qualifications = dict(qualifications)
        self._raw_builder = raw_builder
        self._evidence_verifier = evidence_verifier
        self._evidence_storage_factory = evidence_storage_factory
        self._report_service = report_service
        self._report_storage = report_storage
        self._state = _RefreshStateStore(state_root)
        self._now = now
        self._run_id_factory = run_id_factory
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="industry-refresh",
        )
        self._lock = threading.RLock()
        self._publish_gate = threading.Lock()
        self._stop = threading.Event()
        self._accepting = True
        self._inflight: dict[str, Future[RefreshRun]] = {}
        self._leases: dict[str, _IndustryLease] = {}
        self._generations: dict[str, int] = {}
        self._resolved_provider_ids: list[str] = []

    @property
    def provider_resolution_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._resolved_provider_ids)

    def _clock(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("refresh clock must be timezone-aware")
        return value

    def current_run(self, industry_id: str) -> RefreshRun | None:
        record = self._state.load_record(industry_id)
        if record is None:
            return None
        run = record.run
        if run.phase is not RefreshPhase.TRUSTED_PUBLISHED:
            return run
        proof = record.publication
        displayed = self._report_storage.load_current(industry_id)
        if (
            proof is None
            or displayed is None
            or displayed.industry_id != industry_id
            or displayed.raw_snapshot_id != proof.raw_snapshot_id
            or displayed.evidence_snapshot_id != proof.evidence_snapshot_id
            or displayed.trusted_snapshot_id != proof.trusted_snapshot_id
        ):
            return RefreshRun(
                industry_id=run.industry_id,
                run_id=run.run_id,
                raw_snapshot_id=run.raw_snapshot_id,
                evidence_snapshot_id=run.evidence_snapshot_id,
                candidate_snapshot_id=run.candidate_snapshot_id,
                phase=RefreshPhase.FAILED,
                error_code="publication_proof_invalid",
                displayed_trusted_snapshot_id=(
                    displayed.trusted_snapshot_id if displayed is not None else None
                ),
                published_trusted_snapshot_id=None,
                displayed_raw_snapshot_id=(
                    displayed.raw_snapshot_id if displayed is not None else None
                ),
                displayed_evidence_snapshot_id=(
                    displayed.evidence_snapshot_id if displayed is not None else None
                ),
            )
        return run

    def current_candidate(self, industry_id: str) -> CandidateEvidencePanel | None:
        record = self._state.load_record(industry_id)
        return record.candidate if record is not None else None

    def _display_lineage(self, industry_id: str) -> tuple[str | None, str | None, str | None]:
        displayed = self._report_storage.load_current(industry_id)
        if displayed is None:
            return None, None, None
        return (
            displayed.trusted_snapshot_id,
            displayed.raw_snapshot_id,
            displayed.evidence_snapshot_id,
        )

    def _write_state(
        self,
        run: RefreshRun,
        candidate: CandidateEvidencePanel | None = None,
        *,
        publication: _PublicationProof | None = None,
        expected_generation: int | None = None,
    ) -> RefreshRun:
        with self._lock:
            expected = (
                self._generations.get(run.industry_id)
                if expected_generation is None
                else expected_generation
            )
        generation = self._state.write(
            run,
            candidate,
            publication=publication,
            expected_generation=expected,
        )
        with self._lock:
            self._generations[run.industry_id] = generation
        return run

    def _failed(
        self,
        run: RefreshRun,
        error_code: str,
        candidate: CandidateEvidencePanel | None = None,
    ) -> RefreshRun:
        failed = RefreshRun(
            industry_id=run.industry_id,
            run_id=run.run_id,
            raw_snapshot_id=run.raw_snapshot_id,
            evidence_snapshot_id=run.evidence_snapshot_id,
            candidate_snapshot_id=run.candidate_snapshot_id,
            phase=RefreshPhase.FAILED,
            error_code=error_code,
            displayed_trusted_snapshot_id=run.displayed_trusted_snapshot_id,
            published_trusted_snapshot_id=None,
            displayed_raw_snapshot_id=run.displayed_raw_snapshot_id,
            displayed_evidence_snapshot_id=run.displayed_evidence_snapshot_id,
        )
        try:
            return self._write_state(failed, candidate)
        except (OSError, ValueError):
            return failed

    def _eligible_descriptors(self, industry_id: str) -> tuple[Any, ...]:
        eligible = []
        for descriptor in getattr(self._catalog, "adapters", ()):
            if _CAPABILITY_ID not in getattr(descriptor, "capability_ids", ()):
                continue
            if industry_id not in getattr(descriptor, "supported_industry_ids", ()):
                continue
            qualification = self._qualifications.get(getattr(descriptor, "adapter_id", ""))
            if qualification is None:
                continue
            if qualification_allows_enabled_adapter(qualification, descriptor):
                eligible.append(descriptor)
        return tuple(sorted(
            eligible,
            key=lambda row: (getattr(row, "current_provider_priority", 0), row.adapter_id),
        ))

    def request_refresh(self, industry_id: str) -> Future[RefreshRun]:
        if type(industry_id) is not str or not _INDUSTRY_ID.fullmatch(industry_id):
            raise ValueError("invalid industry_id for refresh")
        with self._lock:
            if not self._accepting:
                raise RuntimeError("refresh orchestrator is shut down")
            existing = self._inflight.get(industry_id)
            if existing is not None and not existing.done():
                return existing
            lease = _IndustryLease.try_acquire(self._state, industry_id)
            if lease is None:
                future = self._executor.submit(self._follow_owner, industry_id)
                self._inflight[industry_id] = future
                self._register_finished(industry_id, future, owner=False)
                return future
            record = self._state.load_record(industry_id)
            self._generations[industry_id] = record.generation if record is not None else 0
            if record is not None and record.run.phase in {
                RefreshPhase.COLLECTING,
                RefreshPhase.VERIFYING,
            }:
                interrupted = self._failed(
                    record.run,
                    "refresh_interrupted",
                    record.candidate,
                )
                lease.release()
                completed: Future[RefreshRun] = Future()
                completed.set_result(interrupted)
                return completed
            run_id = self._run_id_factory()
            if not _valid_run_id(run_id):
                lease.release()
                raise ValueError("run_id_factory returned an invalid run_id")
            try:
                displayed_id, displayed_raw, displayed_evidence = self._display_lineage(
                    industry_id
                )
                initial = RefreshRun(
                    industry_id=industry_id,
                    run_id=run_id,
                    raw_snapshot_id=None,
                    evidence_snapshot_id=None,
                    candidate_snapshot_id=None,
                    phase=RefreshPhase.COLLECTING,
                    error_code=None,
                    displayed_trusted_snapshot_id=displayed_id,
                    published_trusted_snapshot_id=None,
                    displayed_raw_snapshot_id=displayed_raw,
                    displayed_evidence_snapshot_id=displayed_evidence,
                )
                self._write_state(initial)
                future = self._executor.submit(self._execute, initial)
            except BaseException:
                lease.release()
                raise
            self._inflight[industry_id] = future
            self._leases[industry_id] = lease
            self._register_finished(industry_id, future, owner=True, initial=initial)
            return future

    def _register_finished(
        self,
        industry_id: str,
        future: Future[RefreshRun],
        *,
        owner: bool,
        initial: RefreshRun | None = None,
    ) -> None:
        def finished(done: Future[RefreshRun], *, expected: Future[RefreshRun] = future) -> None:
            lease: _IndustryLease | None = None
            if owner and done.cancelled() and initial is not None:
                record = self._state.load_record(industry_id)
                if record is not None and record.run.run_id == initial.run_id:
                    with self._lock:
                        self._generations[industry_id] = record.generation
                    self._failed(record.run, "refresh_cancelled", record.candidate)
            with self._lock:
                if self._inflight.get(industry_id) is expected:
                    self._inflight.pop(industry_id, None)
                if owner:
                    lease = self._leases.pop(industry_id, None)
            if lease is not None:
                lease.release()

        future.add_done_callback(finished)

    def _follow_owner(self, industry_id: str) -> RefreshRun:
        while not self._stop.wait(0.02):
            lease = _IndustryLease.try_acquire(self._state, industry_id)
            if lease is None:
                continue
            try:
                record = self._state.load_record(industry_id)
                if record is None:
                    raise RuntimeError("refresh owner disappeared before state commit")
                if record.run.phase in {RefreshPhase.COLLECTING, RefreshPhase.VERIFYING}:
                    with self._lock:
                        self._generations[industry_id] = record.generation
                    return self._failed(record.run, "refresh_interrupted", record.candidate)
                return self.current_run(industry_id) or record.run
            finally:
                lease.release()
        record = self._state.load_record(industry_id)
        if record is None:
            raise RuntimeError("refresh follower shut down without owner state")
        return RefreshRun(
            industry_id=record.run.industry_id,
            run_id=record.run.run_id,
            raw_snapshot_id=record.run.raw_snapshot_id,
            evidence_snapshot_id=record.run.evidence_snapshot_id,
            candidate_snapshot_id=record.run.candidate_snapshot_id,
            phase=RefreshPhase.FAILED,
            error_code="refresh_shutdown",
            displayed_trusted_snapshot_id=record.run.displayed_trusted_snapshot_id,
            published_trusted_snapshot_id=None,
            displayed_raw_snapshot_id=record.run.displayed_raw_snapshot_id,
            displayed_evidence_snapshot_id=record.run.displayed_evidence_snapshot_id,
        )

    def _provider_values(self, industry_id: str) -> tuple[tuple[ProviderValue, ...], int, int, int]:
        descriptors = self._eligible_descriptors(industry_id)
        if not descriptors:
            return (), 0, 0, 0
        values: list[ProviderValue] = []
        failures = 0
        industry_mismatches = 0
        for descriptor in descriptors:
            if self._stop.is_set():
                break
            try:
                provider = self._registry.adapter(descriptor.adapter_id)
                with self._lock:
                    self._resolved_provider_ids.append(descriptor.adapter_id)
                if getattr(provider, "descriptor", None) != descriptor:
                    raise ValueError("provider descriptor does not match catalog")
                fetched = provider.fetch(ProviderRequest(
                    _CAPABILITY_ID,
                    {"industry_id": industry_id},
                ))
                if type(fetched) is not tuple or not fetched:
                    raise ValueError("provider returned no values")
                for value in fetched:
                    if type(value) is not ProviderValue:
                        raise TypeError("provider returned an invalid value")
                    if (
                        value.adapter_id != descriptor.adapter_id
                        or value.source_family_id != descriptor.source_family_id
                        or value.capability_id != _CAPABILITY_ID
                    ):
                        raise ValueError("provider value lineage does not match catalog")
                    if value.source_metadata.get("industry_id") != industry_id:
                        industry_mismatches += 1
                        raise ValueError("provider value industry does not match request")
                values.extend(fetched)
            except Exception:
                failures += 1
        return tuple(values), failures, len(descriptors), industry_mismatches

    def _execute(self, initial: RefreshRun) -> RefreshRun:
        run = initial
        candidate: CandidateEvidencePanel | None = None
        try:
            values, failures, eligible_count, industry_mismatches = self._provider_values(
                initial.industry_id
            )
            if self._stop.is_set():
                return self._failed(run, "refresh_shutdown")
            if eligible_count == 0:
                return self._failed(run, "no_eligible_provider")
            if not values:
                if industry_mismatches:
                    return self._failed(run, "source_industry_mismatch")
                return self._failed(run, "all_sources_failed")

            try:
                raw = self._raw_builder(initial.industry_id, initial.run_id or "", values)
            except Exception:
                return self._failed(run, "raw_build_failed")
            if type(raw) is not RefreshRawSnapshot:
                return self._failed(run, "raw_build_failed")
            if raw.industry_id != initial.industry_id or any(
                row.industry_id != initial.industry_id for row in raw.observations
            ):
                return self._failed(run, "raw_industry_mismatch")
            candidate_id = f"candidate-{initial.run_id}"

            if self._stop.is_set():
                return self._failed(run, "refresh_shutdown")
            try:
                evidence_snapshot = self._evidence_verifier(raw)
                if (
                    type(evidence_snapshot) is not EvidenceSnapshot
                    or evidence_snapshot.raw_snapshot_id != raw.raw_snapshot_id
                ):
                    raise ValueError("evidence snapshot lineage mismatch")
                evidence_storage = self._evidence_storage_factory(
                    initial.industry_id, initial.run_id or ""
                )
                if type(evidence_storage) is not EvidenceStorage:
                    raise TypeError("evidence storage factory must return canonical EvidenceStorage")
                evidence_storage.publish(evidence_snapshot)
                canonical_evidence = evidence_storage.load_current()
                if canonical_evidence != evidence_snapshot:
                    raise ValueError("canonical evidence publication failed")
            except Exception:
                run = RefreshRun(
                    industry_id=initial.industry_id,
                    run_id=initial.run_id,
                    raw_snapshot_id=raw.raw_snapshot_id,
                    evidence_snapshot_id=None,
                    candidate_snapshot_id=None,
                    phase=RefreshPhase.FAILED,
                    error_code="evidence_verification_failed",
                    displayed_trusted_snapshot_id=initial.displayed_trusted_snapshot_id,
                    published_trusted_snapshot_id=None,
                    displayed_raw_snapshot_id=initial.displayed_raw_snapshot_id,
                    displayed_evidence_snapshot_id=initial.displayed_evidence_snapshot_id,
                )
                return self._write_state(run)

            run = RefreshRun(
                industry_id=initial.industry_id,
                run_id=initial.run_id,
                raw_snapshot_id=raw.raw_snapshot_id,
                evidence_snapshot_id=evidence_snapshot.snapshot_id,
                candidate_snapshot_id=candidate_id,
                phase=RefreshPhase.VERIFYING,
                error_code=None,
                displayed_trusted_snapshot_id=initial.displayed_trusted_snapshot_id,
                published_trusted_snapshot_id=None,
                displayed_raw_snapshot_id=initial.displayed_raw_snapshot_id,
                displayed_evidence_snapshot_id=initial.displayed_evidence_snapshot_id,
            )
            candidate = CandidateEvidencePanel(
                industry_id=initial.industry_id,
                candidate_snapshot_id=candidate_id,
                counts=CandidateEvidenceCounts(0, 0, 0, 0),
                unverified=(),
                conflicting=(),
                unverified_events=(),
                conflicting_events=(),
                raw_snapshot_id=raw.raw_snapshot_id,
                evidence_snapshot_id=evidence_snapshot.snapshot_id,
            )
            self._write_state(run, candidate)
            if self._stop.is_set():
                return self._failed(run, "refresh_shutdown", candidate)
            try:
                projection = admit_metric_observations(
                    industry_id=initial.industry_id,
                    raw_snapshot_id=raw.raw_snapshot_id,
                    evidence_snapshot_id=evidence_snapshot.snapshot_id,
                    evidence_storage=evidence_storage,
                    candidate_snapshot_id=candidate_id,
                    observations=raw.observations,
                    now=self._clock(),
                )
                candidate = projection.candidate
                self._write_state(run, candidate)
            except Exception:
                return self._failed(run, "admission_failed", candidate)

            if candidate.conflicting or candidate.conflicting_events:
                return self._failed(run, "conflicting_evidence", candidate)
            if failures:
                return self._failed(run, "partial_source_failure", candidate)
            if not projection.trusted:
                return self._failed(run, "no_trusted_observations", candidate)
            if self._stop.is_set():
                return self._failed(run, "refresh_shutdown", candidate)

            trusted_id = f"trusted-{initial.run_id}"
            try:
                assembly = self._report_service.assemble_storage_report(
                    trusted_snapshot_id=trusted_id,
                    raw_snapshot_id=raw.raw_snapshot_id,
                    evidence_snapshot_id=evidence_snapshot.snapshot_id,
                    generated_at=self._clock(),
                    trusted_observations=projection.trusted,
                    expired_observations=projection.expired,
                    metric_candidates=candidate,
                    candidate_evidence_storage=evidence_storage,
                    news_snapshot=None,
                )
                if assembly.report.industry_id != initial.industry_id:
                    raise ValueError("assembled report industry_id mismatch")
                candidate = assembly.candidate_evidence
                self._write_state(run, candidate)
            except Exception:
                return self._failed(run, "assembly_failed", candidate)

            with self._publish_gate:
                if self._stop.is_set():
                    return self._failed(run, "refresh_shutdown", candidate)
                publication = self._report_storage.publish(
                    assembly.report,
                    expected_industry_id=initial.industry_id,
                    expected_raw_snapshot_id=raw.raw_snapshot_id,
                    expected_evidence_snapshot_id=evidence_snapshot.snapshot_id,
                )
            if publication.error_code is not None:
                return self._failed(run, publication.error_code, candidate)
            displayed = publication.displayed_report
            if (
                displayed is None
                or publication.published_trusted_snapshot_id != trusted_id
                or displayed.trusted_snapshot_id != trusted_id
            ):
                return self._failed(run, "publication_failed", candidate)
            completed = RefreshRun(
                industry_id=initial.industry_id,
                run_id=initial.run_id,
                raw_snapshot_id=raw.raw_snapshot_id,
                evidence_snapshot_id=evidence_snapshot.snapshot_id,
                candidate_snapshot_id=candidate_id,
                phase=RefreshPhase.TRUSTED_PUBLISHED,
                error_code=None,
                displayed_trusted_snapshot_id=trusted_id,
                published_trusted_snapshot_id=trusted_id,
                displayed_raw_snapshot_id=raw.raw_snapshot_id,
                displayed_evidence_snapshot_id=evidence_snapshot.snapshot_id,
            )
            proof = _PublicationProof(
                run_id=initial.run_id or "",
                industry_id=initial.industry_id,
                raw_snapshot_id=raw.raw_snapshot_id,
                evidence_snapshot_id=evidence_snapshot.snapshot_id,
                candidate_snapshot_id=candidate_id,
                trusted_snapshot_id=trusted_id,
            )
            try:
                return self._write_state(completed, candidate, publication=proof)
            except (OSError, ValueError):
                # The trusted-report replace above is the irreversible commit point.
                # A run-state write failure cannot roll it back or truthfully report old.
                return completed
        except Exception:
            return self._failed(run, "internal_error", candidate)

    def shutdown(self) -> None:
        with self._lock:
            self._accepting = False
        with self._publish_gate:
            self._stop.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            leases = tuple(self._leases.values())
            self._leases.clear()
        for lease in leases:
            lease.release()


__all__ = [
    "IndustryResearchRefreshOrchestrator",
    "RefreshRawSnapshot",
]
