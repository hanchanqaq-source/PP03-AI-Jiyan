from __future__ import annotations

from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
import threading
from typing import Callable
from uuid import uuid4

import newsradar
from evidence_verification.models import EvidenceSnapshot, VerificationStatus
from evidence_verification import service as evidence_service
from news_intelligence.service import project_trusted_snapshot

from .models import PipelineCounts, PipelinePhase, PipelineRun, RawSnapshot, TrustedSnapshot
from .storage import NewsPipelineStorage


_NONTERMINAL = {
    PipelinePhase.QUEUED,
    PipelinePhase.FETCHING,
    PipelinePhase.RAW_SAVED,
    PipelinePhase.VERIFYING,
    PipelinePhase.EVIDENCE_SAVED,
}


class NewsPipelineActiveError(RuntimeError):
    pass


class NewsPipelineService:
    def __init__(
        self,
        *,
        storage: NewsPipelineStorage,
        radar_fetcher: Callable[[], newsradar.RadarCollection],
        deterministic_verifier: Callable[[RawSnapshot], EvidenceSnapshot],
        trusted_projector: Callable[[EvidenceSnapshot], TrustedSnapshot],
        radar_publisher: Callable[[newsradar.RadarCollection], object] | None = None,
        evidence_publisher: Callable[[EvidenceSnapshot], object] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.storage = storage
        self._radar_fetcher = radar_fetcher
        self._deterministic_verifier = deterministic_verifier
        self._trusted_projector = trusted_projector
        self._radar_publisher = radar_publisher
        self._evidence_publisher = evidence_publisher
        self._now = now
        self._id_factory = id_factory
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="pp03-news-pipeline")
        self._owns_executor = executor is None
        self._lock = threading.RLock()
        self._active_run_id: str | None = None
        self._latest_run_id: str | None = None
        self._futures: dict[str, Future[None]] = {}
        self._recovery_future: Future[int] | None = None
        self._closed = False

    def _clock(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("pipeline clock must be aware UTC")
        return value

    def _transition(self, run_id: str, phase: PipelinePhase, **changes) -> PipelineRun:
        current = self.storage.load_run(run_id)
        if current is None:
            raise OSError("storage_error")
        updated = replace(
            current,
            phase=phase,
            updated_at=max(self._clock(), current.updated_at),
            **changes,
        )
        self.storage.write_run(updated, expected_phase=current.phase)
        return updated

    @staticmethod
    def _evidence_counts(snapshot: EvidenceSnapshot, previous: PipelineCounts) -> PipelineCounts:
        values = Counter(event.verification_status for event in snapshot.events)
        return PipelineCounts(
            raw_event_count=previous.raw_event_count,
            verified_count=values[VerificationStatus.VERIFIED],
            corroborated_count=values[VerificationStatus.CORROBORATED],
            pending_count=values[VerificationStatus.UNVERIFIED],
            conflicting_count=values[VerificationStatus.CONFLICTING],
            corrected_count=values[VerificationStatus.CORRECTED],
            disproved_count=values[VerificationStatus.DISPROVED],
            failed_source_count=previous.failed_source_count,
        )

    def _record_failure(self, run_id: str, error_code: str) -> None:
        try:
            current = self.storage.load_run(run_id)
            if current is None or current.phase not in _NONTERMINAL:
                return
            displayed = self.storage.load_current_trusted()
            if displayed is not None and displayed.raw_snapshot_id == current.raw_snapshot_id:
                self._transition(
                    run_id,
                    PipelinePhase.TRUSTED_PUBLISHED,
                    trusted_snapshot_id=current.raw_snapshot_id,
                    redacted_error=None,
                )
                return
            self._transition(run_id, PipelinePhase.FAILED, redacted_error=error_code)
        except Exception:
            return

    def _run(self, run_id: str) -> None:
        error_code = "pipeline_error"
        try:
            run = self._transition(run_id, PipelinePhase.FETCHING)
            error_code = "collection_failed"
            collection = self._radar_fetcher()
            if type(collection) is not newsradar.RadarCollection:
                raise ValueError("radar_fetcher must return RadarCollection")
            if str(collection.radar.get("source_state") or "") == "all_failed":
                self._transition(
                    run_id,
                    PipelinePhase.FAILED,
                    counts=replace(run.counts, failed_source_count=collection.failed_source_count),
                    redacted_error="collection_failed",
                )
                return
            raw = RawSnapshot(
                raw_snapshot_id=run.raw_snapshot_id,
                collected_at=collection.generated_at,
                items=collection.raw_events,
            )
            error_code = "storage_error"
            self.storage.write_raw(raw)
            counts = replace(
                run.counts,
                raw_event_count=len(raw.items),
                failed_source_count=collection.failed_source_count,
            )
            run = self._transition(run_id, PipelinePhase.RAW_SAVED, counts=counts)
            run = self._transition(run_id, PipelinePhase.VERIFYING)

            error_code = "verification_failed"
            evidence = self._deterministic_verifier(raw)
            if type(evidence) is not EvidenceSnapshot or evidence.raw_snapshot_id != raw.raw_snapshot_id:
                raise ValueError("verifier returned a different raw snapshot identity")
            counts = self._evidence_counts(evidence, run.counts)
            error_code = "evidence_persistence_failed"
            self.storage.write_evidence(evidence)
            if self._evidence_publisher is not None:
                self._evidence_publisher(evidence)
            run = self._transition(
                run_id,
                PipelinePhase.EVIDENCE_SAVED,
                counts=counts,
                evidence_snapshot_id=evidence.snapshot_id,
            )

            error_code = "publication_failed"
            trusted = self._trusted_projector(evidence)
            if type(trusted) is not TrustedSnapshot or trusted.raw_snapshot_id != raw.raw_snapshot_id:
                raise ValueError("trusted projector returned a different raw snapshot identity")
            if self._radar_publisher is not None:
                self._radar_publisher(collection)
            self.storage.publish_trusted(trusted)
            self._transition(
                run_id,
                PipelinePhase.TRUSTED_PUBLISHED,
                trusted_snapshot_id=trusted.raw_snapshot_id,
                displayed_trusted_snapshot_id=(
                    run.displayed_trusted_snapshot_id or trusted.raw_snapshot_id
                ),
            )
        except Exception:
            self._record_failure(run_id, error_code)
        finally:
            with self._lock:
                if self._active_run_id == run_id:
                    self._active_run_id = None

    def start(self) -> PipelineRun:
        with self._lock:
            if self._closed:
                raise RuntimeError("news pipeline service is closed")
            if self._recovery_future is not None and not self._recovery_future.done():
                raise NewsPipelineActiveError("news pipeline recovery is pending")
            if self._active_run_id is not None:
                active = self.storage.load_run(self._active_run_id)
                if active is not None and active.phase in _NONTERMINAL:
                    raise NewsPipelineActiveError("news pipeline refresh is active")
                self._active_run_id = None
            run_id = self._id_factory()
            raw_snapshot_id = self._id_factory()
            created_at = self._clock()
            displayed = self.storage.load_current_trusted()
            run = PipelineRun(
                run_id=run_id,
                raw_snapshot_id=raw_snapshot_id,
                evidence_snapshot_id=None,
                trusted_snapshot_id=None,
                phase=PipelinePhase.QUEUED,
                counts=PipelineCounts(),
                created_at=created_at,
                updated_at=created_at,
                redacted_error=None,
                displayed_trusted_snapshot_id=displayed.raw_snapshot_id if displayed is not None else None,
            )
            self.storage.write_run(run)
            self._active_run_id = run_id
            self._latest_run_id = run_id
            try:
                future = self._executor.submit(self._run, run_id)
            except Exception:
                self._active_run_id = None
                self._record_failure(run_id, "pipeline_error")
                raise
            self._futures[run_id] = future
            return run

    def wait(self, run_id: str, timeout: float | None = None) -> PipelineRun:
        with self._lock:
            future = self._futures.get(run_id)
        if future is not None:
            future.result(timeout=timeout)
        run = self.storage.load_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def current_trusted(self) -> TrustedSnapshot | None:
        return self.storage.load_current_trusted()

    def get_status(self, run_id: str | None = None) -> dict[str, object]:
        selected = self.storage.load_run(run_id) if run_id is not None else None
        if run_id is not None and selected is None:
            raise KeyError(run_id)
        if selected is None and self._latest_run_id is not None:
            selected = self.storage.load_run(self._latest_run_id)
        displayed = self.storage.load_current_trusted()
        if selected is None:
            return {
                "loaded": False,
                "run_id": None,
                "raw_snapshot_id": None,
                "evidence_snapshot_id": None,
                "trusted_snapshot_id": None,
                "phase": None,
                "counts": None,
                "admitted_count": None,
                "has_pending_evidence_message": False,
                "created_at": None,
                "updated_at": None,
                "redacted_error": None,
                "displayed_trusted_snapshot_id": displayed.raw_snapshot_id if displayed is not None else None,
                "displayed_trusted": None if displayed is None else {
                    "snapshot_id": displayed.raw_snapshot_id,
                    "published_at": displayed.published_at.isoformat(),
                    "event_count": len(displayed.events),
                },
            }
        counts = asdict(selected.counts)
        admitted = selected.counts.verified_count + selected.counts.corroborated_count
        result: dict[str, object] = {
            "loaded": True,
            "run_id": selected.run_id,
            "raw_snapshot_id": selected.raw_snapshot_id,
            "evidence_snapshot_id": selected.evidence_snapshot_id,
            "trusted_snapshot_id": selected.trusted_snapshot_id,
            "phase": selected.phase.value,
            "counts": counts,
            **counts,
            "admitted_count": admitted,
            "has_pending_evidence_message": (
                selected.phase is PipelinePhase.TRUSTED_PUBLISHED
                and selected.counts.raw_event_count > 0
                and admitted == 0
            ),
            "created_at": selected.created_at.isoformat(),
            "updated_at": selected.updated_at.isoformat(),
            "redacted_error": selected.redacted_error,
            "displayed_trusted_snapshot_id": displayed.raw_snapshot_id if displayed is not None else None,
            "displayed_trusted": None if displayed is None else {
                "snapshot_id": displayed.raw_snapshot_id,
                "published_at": displayed.published_at.isoformat(),
                "event_count": len(displayed.events),
            },
        }
        return result

    @staticmethod
    def _consume_future(future: Future[object]) -> None:
        try:
            future.exception()
        except Exception:
            return

    def recover_startup(self) -> Future[int] | None:
        with self._lock:
            if self._closed:
                return None
            if self._recovery_future is not None and not self._recovery_future.done():
                return self._recovery_future
            future = self._executor.submit(self.storage.recover_incomplete_runs)
            future.add_done_callback(self._consume_future)
            self._recovery_future = future
            return future

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._owns_executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
        self.storage.close()


_service: NewsPipelineService | None = None
_service_lock = threading.Lock()


def get_service() -> NewsPipelineService:
    global _service
    with _service_lock:
        if _service is None:
            storage = NewsPipelineStorage()
            verifier_service = evidence_service.get_service()

            def projector(snapshot: EvidenceSnapshot) -> TrustedSnapshot:
                raw = storage.load_raw(snapshot.raw_snapshot_id)
                if raw is None:
                    raise ValueError("raw snapshot is unavailable for trusted projection")
                return project_trusted_snapshot(snapshot, raw)

            _service = NewsPipelineService(
                storage=storage,
                radar_fetcher=newsradar.collect_radar,
                deterministic_verifier=verifier_service.verify_raw_snapshot,
                trusted_projector=projector,
                radar_publisher=newsradar.publish_radar_collection,
                evidence_publisher=verifier_service.publish_snapshot,
            )
        return _service


def reset_service() -> None:
    global _service
    with _service_lock:
        current, _service = _service, None
    if current is not None:
        current.close()
