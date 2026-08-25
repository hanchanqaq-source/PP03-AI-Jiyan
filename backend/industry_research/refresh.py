from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
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
from .storage import IndustryResearchStorage


_CAPABILITY_ID = "industry_price_snapshot"
_INDUSTRY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_STATE_BYTES = 4 * 1024 * 1024


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
    return RefreshRun(
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

    def write(self, run: RefreshRun, candidate: CandidateEvidencePanel | None) -> None:
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
        state = {
            "run": run.to_dict(),
            "candidate": candidate.to_dict() if candidate is not None else None,
        }
        document = {
            "schema_version": 1,
            "checksum": hashlib.sha256(_canonical(state)).hexdigest(),
            "state": state,
        }
        payload = _canonical(document) + b"\n"
        if len(payload) > _MAX_STATE_BYTES:
            raise ValueError("refresh state is too large")
        with self._lock:
            self._writer._atomic_write(self._path(run.industry_id), payload)

    def load(self, industry_id: str) -> tuple[RefreshRun | None, CandidateEvidencePanel | None]:
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
                    return None, None
                descriptor = os.open(
                    path,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                opened = os.fstat(descriptor)
                if (
                    (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or opened.st_nlink != 1
                ):
                    return None, None
                chunks = bytearray()
                while len(chunks) <= _MAX_STATE_BYTES:
                    chunk = os.read(descriptor, _MAX_STATE_BYTES + 1 - len(chunks))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                raw = bytes(chunks)
            if len(raw) > _MAX_STATE_BYTES:
                return None, None
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
            if type(document) is not dict or set(document) != {"schema_version", "checksum", "state"}:
                return None, None
            if document["schema_version"] != 1 or type(document["checksum"]) is not str:
                return None, None
            state = document["state"]
            if type(state) is not dict or set(state) != {"run", "candidate"}:
                return None, None
            if hashlib.sha256(_canonical(state)).hexdigest() != document["checksum"]:
                return None, None
            run = _run_from_dict(state["run"])
            candidate = _candidate_from_dict(state["candidate"]) if state["candidate"] is not None else None
            if run.industry_id != industry_id:
                return None, None
            if (run.candidate_snapshot_id is None) != (candidate is None):
                return None, None
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
                return None, None
            return run, candidate
        except (
            AttributeError, FileNotFoundError, OSError, OverflowError, RecursionError,
            UnicodeDecodeError, ValueError, TypeError, KeyError, json.JSONDecodeError,
        ):
            return None, None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
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
        run, candidate = self._state.load(industry_id)
        if (
            run is None
            or run.phase is RefreshPhase.TRUSTED_PUBLISHED
            or not run.raw_snapshot_id
            or not run.evidence_snapshot_id
        ):
            return run
        displayed = self._report_storage.load_current(industry_id)
        if (
            displayed is None
            or displayed.industry_id != industry_id
            or displayed.raw_snapshot_id != run.raw_snapshot_id
            or displayed.evidence_snapshot_id != run.evidence_snapshot_id
            or not displayed.trusted_snapshot_id
        ):
            return run
        recovered = RefreshRun(
            industry_id=industry_id,
            run_id=run.run_id,
            raw_snapshot_id=run.raw_snapshot_id,
            evidence_snapshot_id=run.evidence_snapshot_id,
            candidate_snapshot_id=run.candidate_snapshot_id,
            phase=RefreshPhase.TRUSTED_PUBLISHED,
            error_code=None,
            displayed_trusted_snapshot_id=displayed.trusted_snapshot_id,
            published_trusted_snapshot_id=displayed.trusted_snapshot_id,
            displayed_raw_snapshot_id=displayed.raw_snapshot_id,
            displayed_evidence_snapshot_id=displayed.evidence_snapshot_id,
        )
        try:
            self._state.write(recovered, candidate)
        except (OSError, ValueError):
            pass
        return recovered

    def current_candidate(self, industry_id: str) -> CandidateEvidencePanel | None:
        return self._state.load(industry_id)[1]

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
    ) -> RefreshRun:
        self._state.write(run, candidate)
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

    def _eligible_descriptors(self) -> tuple[Any, ...]:
        eligible = []
        for descriptor in getattr(self._catalog, "adapters", ()):
            if _CAPABILITY_ID not in getattr(descriptor, "capability_ids", ()):
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
            run_id = self._run_id_factory()
            if type(run_id) is not str or not run_id.strip() or run_id != run_id.strip():
                raise ValueError("run_id_factory returned an invalid run_id")
            displayed_id, displayed_raw, displayed_evidence = self._display_lineage(industry_id)
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
            self._inflight[industry_id] = future

            def finished(done: Future[RefreshRun], *, expected: Future[RefreshRun] = future) -> None:
                if done.cancelled():
                    current = self.current_run(industry_id) or initial
                    self._failed(current, "refresh_cancelled", self.current_candidate(industry_id))
                with self._lock:
                    if self._inflight.get(industry_id) is expected:
                        self._inflight.pop(industry_id, None)

            future.add_done_callback(finished)
            return future

    def _provider_values(self) -> tuple[tuple[ProviderValue, ...], int, int]:
        descriptors = self._eligible_descriptors()
        if not descriptors:
            return (), 0, 0
        values: list[ProviderValue] = []
        failures = 0
        for descriptor in descriptors:
            if self._stop.is_set():
                break
            try:
                provider = self._registry.adapter(descriptor.adapter_id)
                with self._lock:
                    self._resolved_provider_ids.append(descriptor.adapter_id)
                if getattr(provider, "descriptor", None) != descriptor:
                    raise ValueError("provider descriptor does not match catalog")
                fetched = provider.fetch(ProviderRequest(_CAPABILITY_ID, {}))
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
                values.extend(fetched)
            except Exception:
                failures += 1
        return tuple(values), failures, len(descriptors)

    def _execute(self, initial: RefreshRun) -> RefreshRun:
        run = initial
        candidate: CandidateEvidencePanel | None = None
        try:
            values, failures, eligible_count = self._provider_values()
            if self._stop.is_set():
                return self._failed(run, "refresh_shutdown")
            if eligible_count == 0:
                return self._failed(run, "no_eligible_provider")
            if not values:
                return self._failed(run, "all_sources_failed")

            try:
                raw = self._raw_builder(initial.industry_id, initial.run_id or "", values)
                if type(raw) is not RefreshRawSnapshot or raw.industry_id != initial.industry_id:
                    raise ValueError("raw refresh snapshot industry mismatch")
            except Exception:
                return self._failed(run, "admission_failed")
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
            try:
                return self._write_state(completed, candidate)
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


__all__ = [
    "IndustryResearchRefreshOrchestrator",
    "RefreshRawSnapshot",
]
