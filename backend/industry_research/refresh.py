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
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from data_sources.models import ProviderValue
from data_sources.provider_contract import ProviderRequest
from evidence_verification.models import EvidenceSnapshot
from evidence_verification.storage import EvidenceStorage

from .admission import RawMetricObservation, admit_metric_observations
from .models import (
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    CandidateExternalLineage,
    CandidateIndustryEvidenceEvent,
    ConflictingObservation,
    ConflictingSourceValue,
    IndustryMetricObservation,
    MetricChange,
    RefreshPhase,
    RefreshRun,
    VerificationStatus,
    _candidate_panel_with_canonical_a2_lineage,
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
_ATTEMPT_TOKEN = re.compile(r"^[0-9a-f]{32}$")
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


def load_canonical_a2_news_snapshot(_industry_id: str) -> EvidenceSnapshot | None:
    """Read the current A2 trusted context without triggering a pipeline refresh."""
    try:
        from news_pipeline.models import RawSnapshot, TrustedSnapshot
        from news_pipeline.service import get_service

        context = get_service().current_trusted_context()
        if context is None:
            return None
        trusted, raw, evidence = context
        if (
            type(trusted) is not TrustedSnapshot
            or type(raw) is not RawSnapshot
            or type(evidence) is not EvidenceSnapshot
            or trusted.raw_snapshot_id != raw.raw_snapshot_id
            or evidence.raw_snapshot_id != raw.raw_snapshot_id
        ):
            return None
        return evidence
    except Exception:
        return None


def _no_company_candidates(
    _industry_id: str,
    _evidence_snapshot: EvidenceSnapshot,
) -> tuple[Mapping[str, object], ...]:
    return ()


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


def _candidate_from_dict(
    value: object,
    *,
    restore_canonical_external_lineage: bool = False,
) -> CandidateEvidencePanel:
    required = {
        "industry_id", "candidate_snapshot_id", "counts", "unverified", "conflicting",
        "unverified_events", "conflicting_events", "raw_snapshot_id", "evidence_snapshot_id",
    }
    if type(value) is not dict:
        raise ValueError("invalid candidate state schema")
    has_external = "external_lineages" in value
    if set(value) != required | ({"external_lineages"} if has_external else set()):
        raise ValueError("invalid candidate state schema")
    if has_external and not restore_canonical_external_lineage:
        raise ValueError("external candidate lineage requires verified state envelope")
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

    external_lineages: tuple[CandidateExternalLineage, ...] = ()
    if has_external:
        if type(row["external_lineages"]) is not list:
            raise ValueError("invalid external candidate lineage schema")
        parsed: list[CandidateExternalLineage] = []
        for item in row["external_lineages"]:
            if type(item) is not dict or set(item) != {
                "kind", "candidate_snapshot_id", "raw_snapshot_id", "evidence_snapshot_id",
            }:
                raise ValueError("invalid external candidate lineage schema")
            parsed.append(CandidateExternalLineage(**item))
        external_lineages = tuple(parsed)
    constructor = (
        _candidate_panel_with_canonical_a2_lineage
        if external_lineages
        else CandidateEvidencePanel
    )
    return constructor(
        industry_id=row["industry_id"],
        candidate_snapshot_id=row["candidate_snapshot_id"],
        counts=CandidateEvidenceCounts(**counts),
        unverified=tuple(IndustryMetricObservation.from_dict(item) for item in row["unverified"]),
        conflicting=tuple(conflicts),
        unverified_events=tuple(candidate_event(item) for item in row["unverified_events"]),
        conflicting_events=tuple(candidate_event(item) for item in row["conflicting_events"]),
        raw_snapshot_id=row["raw_snapshot_id"],
        evidence_snapshot_id=row["evidence_snapshot_id"],
        external_lineages=external_lineages,
    )


@dataclass(frozen=True, slots=True)
class _PublicationProof:
    phase: str
    run_id: str
    industry_id: str
    raw_snapshot_id: str
    evidence_snapshot_id: str
    candidate_snapshot_id: str
    trusted_snapshot_id: str
    report_checksum: str
    publication_token: str
    snapshot_checksum: str
    prepared_generation: int

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "run_id": self.run_id,
            "industry_id": self.industry_id,
            "raw_snapshot_id": self.raw_snapshot_id,
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "candidate_snapshot_id": self.candidate_snapshot_id,
            "trusted_snapshot_id": self.trusted_snapshot_id,
            "report_checksum": self.report_checksum,
            "publication_token": self.publication_token,
            "snapshot_checksum": self.snapshot_checksum,
            "prepared_generation": self.prepared_generation,
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
        "phase",
        "run_id", "industry_id", "raw_snapshot_id", "evidence_snapshot_id",
        "candidate_snapshot_id", "trusted_snapshot_id", "report_checksum",
        "publication_token", "snapshot_checksum", "prepared_generation",
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
    if proof.phase not in {"prepared", "committed"}:
        raise ValueError("invalid publication proof phase")
    if (
        type(proof.report_checksum) is not str
        or re.fullmatch(r"[0-9a-f]{64}", proof.report_checksum) is None
    ):
        raise ValueError("invalid publication report checksum")
    if type(proof.publication_token) is not str or not _ATTEMPT_TOKEN.fullmatch(
        proof.publication_token
    ):
        raise ValueError("invalid publication token")
    if (
        type(proof.snapshot_checksum) is not str
        or re.fullmatch(r"[0-9a-f]{64}", proof.snapshot_checksum) is None
    ):
        raise ValueError("invalid publication snapshot checksum")
    if type(proof.prepared_generation) is not int or proof.prepared_generation <= 0:
        raise ValueError("invalid publication prepared generation")
    return proof


def _legacy_publication_is_valid(value: object) -> bool:
    keys = {
        "run_id", "industry_id", "raw_snapshot_id", "evidence_snapshot_id",
        "candidate_snapshot_id", "trusted_snapshot_id",
    }
    if type(value) is not dict or set(value) != keys:
        return False
    return (
        _valid_run_id(value["run_id"])
        and value["candidate_snapshot_id"] == f"candidate-{value['run_id']}"
        and value["trusted_snapshot_id"] == f"trusted-{value['run_id']}"
    )


def _legacy_v3_publication_is_valid(value: object) -> bool:
    keys = {
        "phase", "run_id", "industry_id", "raw_snapshot_id", "evidence_snapshot_id",
        "candidate_snapshot_id", "trusted_snapshot_id", "report_checksum",
        "prepared_generation",
    }
    if type(value) is not dict or set(value) != keys:
        return False
    return (
        _valid_run_id(value["run_id"])
        and value["candidate_snapshot_id"] == f"candidate-{value['run_id']}"
        and value["trusted_snapshot_id"] == f"trusted-{value['run_id']}"
        and value["phase"] in {"prepared", "committed"}
        and type(value["report_checksum"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", value["report_checksum"]) is not None
        and type(value["prepared_generation"]) is int
        and value["prepared_generation"] > 0
    )


def _report_checksum(report: object) -> str:
    if not hasattr(report, "to_dict"):
        raise TypeError("trusted report must provide canonical serialization")
    return hashlib.sha256(_canonical(report.to_dict())).hexdigest()


def _proof_identity(proof: _PublicationProof) -> tuple[object, ...]:
    return (
        proof.run_id,
        proof.industry_id,
        proof.raw_snapshot_id,
        proof.evidence_snapshot_id,
        proof.candidate_snapshot_id,
        proof.trusted_snapshot_id,
        proof.report_checksum,
        proof.publication_token,
        proof.snapshot_checksum,
        proof.prepared_generation,
    )


def _proof_matches_run(proof: _PublicationProof, run: RefreshRun) -> bool:
    return (
        proof.run_id == run.run_id
        and proof.industry_id == run.industry_id
        and proof.raw_snapshot_id == run.raw_snapshot_id
        and proof.evidence_snapshot_id == run.evidence_snapshot_id
        and proof.candidate_snapshot_id == run.candidate_snapshot_id
    )


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
        if publication is not None and not _proof_matches_run(publication, run):
            raise ValueError("publication proof does not match refresh run")
        if publication is None and run.phase is RefreshPhase.TRUSTED_PUBLISHED:
            raise ValueError("trusted publication requires durable proof")
        with self._lock:
            current = self.load_record(run.industry_id)
            current_generation = current.generation if current is not None else 0
            if expected_generation is not None and current_generation != expected_generation:
                raise _StateConflict("refresh state generation conflict")
            generation = current_generation + 1
            if publication is not None:
                if publication.phase == "prepared":
                    if (
                        run.phase is not RefreshPhase.VERIFYING
                        or run.published_trusted_snapshot_id is not None
                        or publication.prepared_generation != generation
                    ):
                        raise ValueError("invalid prepared publication state")
                elif publication.phase == "committed":
                    if (
                        run.phase is not RefreshPhase.TRUSTED_PUBLISHED
                        or run.published_trusted_snapshot_id != publication.trusted_snapshot_id
                        or run.displayed_trusted_snapshot_id != publication.trusted_snapshot_id
                        or current is None
                        or current.generation != publication.prepared_generation
                        or current.publication is None
                        or current.publication.phase != "prepared"
                        or _proof_identity(current.publication) != _proof_identity(publication)
                    ):
                        raise ValueError("invalid committed publication state")
                else:
                    raise ValueError("invalid publication phase")
            state = {
                "generation": generation,
                "run": run.to_dict(),
                "candidate": candidate.to_dict() if candidate is not None else None,
                "publication": publication.to_dict() if publication is not None else None,
            }
            document = {
                "schema_version": 5,
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
            if document["schema_version"] not in {1, 2, 3, 4, 5} or type(document["checksum"]) is not str:
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
            if state["candidate"] is not None:
                candidate_row = state["candidate"]
                if type(candidate_row) is not dict or (
                    candidate_row.get("industry_id"),
                    candidate_row.get("candidate_snapshot_id"),
                    candidate_row.get("raw_snapshot_id"),
                    candidate_row.get("evidence_snapshot_id"),
                ) != (
                    run.industry_id,
                    run.candidate_snapshot_id,
                    run.raw_snapshot_id,
                    run.evidence_snapshot_id,
                ):
                    return None
                if (
                    document["schema_version"] == 5
                    and "external_lineages" not in candidate_row
                ):
                    return None
                candidate = _candidate_from_dict(
                    candidate_row,
                    restore_canonical_external_lineage=document["schema_version"] == 5,
                )
            else:
                candidate = None
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
            publication = None
            if document["schema_version"] == 2 and state["publication"] is not None:
                if not _legacy_publication_is_valid(state["publication"]):
                    return None
            elif document["schema_version"] == 3 and state["publication"] is not None:
                if not _legacy_v3_publication_is_valid(state["publication"]):
                    return None
            elif document["schema_version"] in {4, 5} and state["publication"] is not None:
                publication = _publication_from_dict(state["publication"])
            if publication is not None:
                if not _proof_matches_run(publication, run):
                    return None
                if publication.phase == "prepared":
                    if (
                        run.phase is not RefreshPhase.VERIFYING
                        or run.published_trusted_snapshot_id is not None
                        or publication.prepared_generation != generation
                    ):
                        return None
                elif (
                    run.phase is not RefreshPhase.TRUSTED_PUBLISHED
                    or run.published_trusted_snapshot_id != publication.trusted_snapshot_id
                    or run.displayed_trusted_snapshot_id != publication.trusted_snapshot_id
                    or publication.prepared_generation != generation - 1
                ):
                    return None
            if document["schema_version"] in {4, 5} and (
                run.phase is RefreshPhase.TRUSTED_PUBLISHED
            ) != (publication is not None and publication.phase == "committed"):
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
        attempt_token_factory: Callable[[], str] = lambda: uuid4().hex,
        canonical_news_snapshot_loader: Callable[[str], EvidenceSnapshot | None] = load_canonical_a2_news_snapshot,
        company_candidate_loader: Callable[
            [str, EvidenceSnapshot], Iterable[Mapping[str, object]]
        ] = _no_company_candidates,
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
        self._attempt_token_factory = attempt_token_factory
        self._canonical_news_snapshot_loader = canonical_news_snapshot_loader
        self._company_candidate_loader = company_candidate_loader
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
        if record.publication is not None and record.publication.phase == "prepared":
            lease = _IndustryLease.try_acquire(self._state, industry_id)
            if lease is None:
                return self._reconcile_prepared(record, owns_lease=False)
            try:
                return self._reconcile_prepared(record, owns_lease=True)
            finally:
                lease.release()
        run = record.run
        proof = record.publication
        if run.phase is not RefreshPhase.TRUSTED_PUBLISHED:
            return run
        visible = self._report_storage.load_current_publication(industry_id)
        if proof is None or proof.phase != "committed" or not self._display_matches_proof(
            visible, proof
        ):
            displayed = visible.report if visible is not None else None
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

    @staticmethod
    def _display_matches_proof(visible: object, proof: _PublicationProof) -> bool:
        try:
            displayed = visible.report
            metadata = visible.metadata
            return (
                visible is not None
                and displayed is not None
                and metadata is not None
                and displayed.industry_id == proof.industry_id
                and displayed.raw_snapshot_id == proof.raw_snapshot_id
                and displayed.evidence_snapshot_id == proof.evidence_snapshot_id
                and displayed.trusted_snapshot_id == proof.trusted_snapshot_id
                and _report_checksum(displayed) == proof.report_checksum
                and metadata.publication_token == proof.publication_token
                and metadata.report_checksum == proof.report_checksum
                and metadata.snapshot_checksum == proof.snapshot_checksum
            )
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _committed_proof(proof: _PublicationProof) -> _PublicationProof:
        return _PublicationProof(
            phase="committed",
            run_id=proof.run_id,
            industry_id=proof.industry_id,
            raw_snapshot_id=proof.raw_snapshot_id,
            evidence_snapshot_id=proof.evidence_snapshot_id,
            candidate_snapshot_id=proof.candidate_snapshot_id,
            trusted_snapshot_id=proof.trusted_snapshot_id,
            report_checksum=proof.report_checksum,
            publication_token=proof.publication_token,
            snapshot_checksum=proof.snapshot_checksum,
            prepared_generation=proof.prepared_generation,
        )

    def _reconcile_prepared(
        self,
        record: _PersistedState,
        *,
        owns_lease: bool,
    ) -> RefreshRun:
        proof = record.publication
        if proof is None or proof.phase != "prepared":
            return record.run
        visible = self._report_storage.load_current_publication(record.run.industry_id)
        if self._display_matches_proof(visible, proof):
            completed = self._completed_run(record.run, proof)
            if not owns_lease:
                return completed
            with self._lock:
                self._generations[record.run.industry_id] = record.generation
            try:
                return self._write_state(
                    completed,
                    record.candidate,
                    publication=self._committed_proof(proof),
                    expected_generation=record.generation,
                )
            except (OSError, ValueError):
                return completed
        if not owns_lease:
            return record.run
        with self._lock:
            self._generations[record.run.industry_id] = record.generation
        return self._failed(record.run, "refresh_interrupted", record.candidate)

    @staticmethod
    def _completed_run(run: RefreshRun, proof: _PublicationProof) -> RefreshRun:
        return RefreshRun(
            industry_id=run.industry_id,
            run_id=run.run_id,
            raw_snapshot_id=proof.raw_snapshot_id,
            evidence_snapshot_id=proof.evidence_snapshot_id,
            candidate_snapshot_id=proof.candidate_snapshot_id,
            phase=RefreshPhase.TRUSTED_PUBLISHED,
            error_code=None,
            displayed_trusted_snapshot_id=proof.trusted_snapshot_id,
            published_trusted_snapshot_id=proof.trusted_snapshot_id,
            displayed_raw_snapshot_id=proof.raw_snapshot_id,
            displayed_evidence_snapshot_id=proof.evidence_snapshot_id,
        )

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
            if (
                record is not None
                and record.publication is not None
                and record.publication.phase == "prepared"
            ):
                recovered = self._reconcile_prepared(record, owns_lease=True)
                lease.release()
                completed: Future[RefreshRun] = Future()
                completed.set_result(recovered)
                return completed
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
            if record is not None and record.run.run_id == run_id:
                lease.release()
                raise ValueError("run_id_factory returned a reused run_id")
            attempt_token = self._attempt_token_factory()
            if type(attempt_token) is not str or not _ATTEMPT_TOKEN.fullmatch(attempt_token):
                lease.release()
                raise ValueError("attempt_token_factory returned an invalid token")
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
                future = self._executor.submit(self._execute, initial, attempt_token)
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
                    proof = record.publication
                    if proof is not None and proof.phase == "prepared":
                        return self._reconcile_prepared(record, owns_lease=True)
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

    def _execute(self, initial: RefreshRun, attempt_token: str) -> RefreshRun:
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
            if industry_mismatches:
                return self._failed(run, "source_industry_mismatch")
            if not values:
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
                try:
                    news_snapshot = self._canonical_news_snapshot_loader(initial.industry_id)
                except Exception:
                    news_snapshot = None
                if news_snapshot is not None and type(news_snapshot) is not EvidenceSnapshot:
                    news_snapshot = None
                try:
                    company_candidates = tuple(
                        self._company_candidate_loader(initial.industry_id, evidence_snapshot)
                    )
                    if any(not isinstance(item, Mapping) for item in company_candidates):
                        company_candidates = ()
                except Exception:
                    company_candidates = ()
                assembly = self._report_service.assemble_storage_report(
                    trusted_snapshot_id=trusted_id,
                    raw_snapshot_id=raw.raw_snapshot_id,
                    evidence_snapshot_id=evidence_snapshot.snapshot_id,
                    generated_at=self._clock(),
                    trusted_observations=projection.trusted,
                    expired_observations=projection.expired,
                    metric_candidates=candidate,
                    candidate_evidence_storage=evidence_storage,
                    news_snapshot=news_snapshot,
                    company_candidates=company_candidates,
                    company_evidence_snapshot=evidence_snapshot,
                )
                if (
                    assembly.report.industry_id != initial.industry_id
                    or assembly.report.trusted_snapshot_id != trusted_id
                    or assembly.report.raw_snapshot_id != raw.raw_snapshot_id
                    or assembly.report.evidence_snapshot_id != evidence_snapshot.snapshot_id
                ):
                    raise ValueError("assembled report publication lineage mismatch")
                storage_prepared = self._report_storage.prepare_publication(
                    assembly.report,
                    expected_industry_id=initial.industry_id,
                    expected_raw_snapshot_id=raw.raw_snapshot_id,
                    expected_evidence_snapshot_id=evidence_snapshot.snapshot_id,
                    publication_token=attempt_token,
                )
                report_checksum = storage_prepared.metadata.report_checksum
                candidate = assembly.candidate_evidence
                self._write_state(run, candidate)
            except Exception:
                return self._failed(run, "assembly_failed", candidate)

            with self._lock:
                current_generation = self._generations.get(initial.industry_id)
            if current_generation is None:
                return self._failed(run, "storage_error", candidate)
            prepared = _PublicationProof(
                phase="prepared",
                run_id=initial.run_id or "",
                industry_id=initial.industry_id,
                raw_snapshot_id=raw.raw_snapshot_id,
                evidence_snapshot_id=evidence_snapshot.snapshot_id,
                candidate_snapshot_id=candidate_id,
                trusted_snapshot_id=trusted_id,
                report_checksum=report_checksum,
                publication_token=storage_prepared.metadata.publication_token,
                snapshot_checksum=storage_prepared.metadata.snapshot_checksum,
                prepared_generation=current_generation + 1,
            )
            try:
                self._write_state(
                    run,
                    candidate,
                    publication=prepared,
                    expected_generation=current_generation,
                )
            except (OSError, ValueError):
                return self._failed(run, "storage_error", candidate)

            with self._publish_gate:
                if self._stop.is_set():
                    return self._failed(run, "refresh_shutdown", candidate)
                publication = self._report_storage.publish_prepared(storage_prepared)
            if publication.error_code is not None:
                return self._failed(run, publication.error_code, candidate)
            visible = self._report_storage.load_current_publication(initial.industry_id)
            if (
                publication.published_trusted_snapshot_id != trusted_id
                or not self._display_matches_proof(visible, prepared)
            ):
                return self._failed(run, "publication_failed", candidate)
            completed = self._completed_run(run, prepared)
            committed = self._committed_proof(prepared)
            try:
                return self._write_state(completed, candidate, publication=committed)
            except (OSError, ValueError):
                # The exact checksum-bound PREPARED proof remains durable and permits
                # deterministic recovery of this already-visible report.
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
