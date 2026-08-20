from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest

from evidence_verification.models import (
    EvidenceEvent,
    EvidenceSnapshot,
    VerificationStatus,
)
from news_pipeline.models import PipelineCounts, PipelinePhase, PipelineRun, TrustedSnapshot
from news_pipeline.service import NewsPipelineActiveError, NewsPipelineService
from news_pipeline.storage import NewsPipelineStorage
from newsradar import RadarCollection


NOW = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def raw_event(*, event_id: str = "a" * 20) -> dict[str, object]:
    return {
        "event_id": event_id,
        "title": "交易所公告：星河科技建设存储算力中心",
        "summary": "星河科技披露建设存储算力中心。",
        "summary_status": "source_excerpt",
        "category": "company",
        "published_at_first": NOW.isoformat(),
        "published_at_latest": NOW.isoformat(),
        "sources": [{
            "source_name": "交易所",
            "source_url": "https://example.test/feed",
            "original_url": "https://example.test/notice/1",
            "published_at": NOW.isoformat(),
            "fetched_at": NOW.isoformat(),
            "title": "交易所公告",
            "summary_or_excerpt": "必要摘录",
            "language": "zh",
            "region": "CN",
            "data_status": "realtime",
        }],
        "source_count": 1,
        "related_tags": [{"id": "storage", "name": "存储"}],
        "tag_evidence": [{"id": "storage", "name": "存储", "provenance": "article_text"}],
        "related_companies": [],
        "related_funds": [],
        "relation_level": "none",
        "relation_evidence": [],
        "impact_tendency": "unclear",
        "impact_basis": [],
        "confidence": "unavailable",
        "original_links": ["https://example.test/notice/1"],
        "data_status": "realtime",
        "missing_information": [],
        "importance_score": 1,
        "verification_status": None,
        "verification_reason": None,
        "verified_at": None,
        "verified_key_fields": [],
    }


def collection(
    *events: dict[str, object],
    failed_sources: int = 0,
    current_attempt_success: bool = True,
    attempted_source_count: int | None = None,
    source_statuses: tuple[dict, ...] | None = None,
) -> RadarCollection:
    if attempted_source_count is None:
        attempted_source_count = max(
            1,
            failed_sources + (1 if current_attempt_success else 0),
        )
    if source_statuses is None:
        source_statuses = tuple(
            {
                "source_id": f"source-{index:03d}",
                "source_name": f"公开源 {index}",
                "source_url": f"https://source-{index}.example.test/feed",
                "status": "failed" if index < failed_sources else "ok",
                "error_type": "timeout" if index < failed_sources else None,
                "error_reason": "连接超时" if index < failed_sources else None,
                "last_success_at": None if index < failed_sources else NOW.isoformat(),
                "used_cached_items": False,
                "item_count": 0,
            }
            for index in range(attempted_source_count)
        )
    return RadarCollection(
        raw_events=tuple(events),
        source_statuses=source_statuses,
        generated_at=NOW,
        failed_source_count=failed_sources,
        radar={"generated_at": NOW.isoformat(), "industries": [], "source_statuses": [], "stats": {}},
        current_attempt_success=current_attempt_success,
        attempted_source_count=attempted_source_count,
    )


def evidence(raw_snapshot_id: str, *, status: VerificationStatus = VerificationStatus.VERIFIED) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=f"evidence-{raw_snapshot_id}",
        raw_snapshot_id=raw_snapshot_id,
        generated_at=NOW,
        events=(EvidenceEvent(
            event_id="a" * 20,
            title="交易所公告：星河科技建设存储算力中心",
            summary="星河科技披露建设存储算力中心。",
            category="company",
            related_tags=(("storage", "存储"),),
            published_at=NOW,
            core_claim="星河科技建设存储算力中心",
            verification_status=status,
            verification_reason="官方公告支持" if status is VerificationStatus.VERIFIED else "证据不足",
            verified_at=NOW,
            evidence_as_of=NOW,
        ),),
    )


def evidence_for_ids(
    raw_snapshot_id: str,
    *event_ids: str,
    status: VerificationStatus = VerificationStatus.VERIFIED,
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=f"evidence-{raw_snapshot_id}",
        raw_snapshot_id=raw_snapshot_id,
        generated_at=NOW,
        events=tuple(
            replace(evidence(raw_snapshot_id, status=status).events[0], event_id=event_id)
            for event_id in event_ids
        ),
    )


def trusted(snapshot: EvidenceSnapshot) -> TrustedSnapshot:
    rows = []
    for row in snapshot.events:
        if row.verification_status not in {VerificationStatus.VERIFIED, VerificationStatus.CORROBORATED}:
            continue
        projected = raw_event(event_id=row.event_id)
        projected.update({
            "title": row.title,
            "summary": row.summary,
            "verification_status": row.verification_status.value,
            "verification_reason": row.verification_reason,
            "verified_at": row.verified_at.isoformat(),
        })
        rows.append(projected)
    return TrustedSnapshot(snapshot.raw_snapshot_id, NOW, tuple(rows))


def service(tmp_path, *, radar_fetcher=None, verifier=None, projector=trusted) -> NewsPipelineService:
    ids = iter(("run-fixed", "raw-fixed"))
    return NewsPipelineService(
        storage=NewsPipelineStorage(tmp_path / "pipeline", now=lambda: NOW),
        radar_fetcher=radar_fetcher or (lambda: collection(raw_event(), failed_sources=2)),
        deterministic_verifier=verifier or (lambda raw: evidence(raw.raw_snapshot_id)),
        trusted_projector=projector,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )


def test_pipeline_verifies_and_publishes_the_exact_raw_snapshot(tmp_path):
    received = []

    def verifier(raw):
        received.append(raw.raw_snapshot_id)
        return evidence(raw.raw_snapshot_id)

    pipeline = service(tmp_path, verifier=verifier)
    try:
        started = pipeline.start()
        completed = pipeline.wait(started.run_id, timeout=5)

        assert received == [started.raw_snapshot_id]
        assert completed.phase is PipelinePhase.TRUSTED_PUBLISHED
        assert completed.raw_snapshot_id == completed.evidence_snapshot_id.removeprefix("evidence-")
        assert completed.trusted_snapshot_id == completed.raw_snapshot_id
        assert completed.counts.raw_event_count == 1
        assert completed.counts.verified_count == 1
        assert completed.counts.failed_source_count == 2
        assert pipeline.current_trusted().raw_snapshot_id == completed.raw_snapshot_id
        assert pipeline.get_status(started.run_id)["displayed_trusted_snapshot_id"] == completed.raw_snapshot_id
    finally:
        pipeline.close()


def test_verification_failure_keeps_previous_trusted_snapshot(tmp_path):
    root = tmp_path / "pipeline"
    initial = service(tmp_path)
    first = initial.start()
    initial.wait(first.run_id, timeout=5)
    initial.close()

    ids = iter(("run-failed", "raw-failed"))
    failing = NewsPipelineService(
        storage=NewsPipelineStorage(root, now=lambda: NOW),
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda _raw: (_ for _ in ()).throw(RuntimeError("secret https://bad.test/?token=x")),
        trusted_projector=trusted,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    try:
        run = failing.start()
        completed = failing.wait(run.run_id, timeout=5)

        assert completed.phase is PipelinePhase.FAILED
        assert completed.redacted_error == "verification_failed"
        assert failing.current_trusted().raw_snapshot_id == first.raw_snapshot_id
        assert failing.get_status(run.run_id)["displayed_trusted_snapshot_id"] == first.raw_snapshot_id
    finally:
        failing.close()


def test_second_refresh_is_rejected_while_single_worker_run_is_active(tmp_path):
    entered = Event()
    release = Event()

    def blocked_fetch():
        entered.set()
        assert release.wait(5)
        return collection(raw_event())

    pipeline = service(tmp_path, radar_fetcher=blocked_fetch)
    try:
        started = pipeline.start()
        assert entered.wait(5)
        with pytest.raises(NewsPipelineActiveError):
            pipeline.start()
        release.set()
        assert pipeline.wait(started.run_id, timeout=5).phase is PipelinePhase.TRUSTED_PUBLISHED
    finally:
        release.set()
        pipeline.close()


def test_positive_raw_count_with_zero_admitted_events_publishes_empty_trusted_snapshot(tmp_path):
    pipeline = service(
        tmp_path,
        verifier=lambda raw: evidence(raw.raw_snapshot_id, status=VerificationStatus.UNVERIFIED),
    )
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)
        status = pipeline.get_status(completed.run_id)

        assert completed.phase is PipelinePhase.TRUSTED_PUBLISHED
        assert completed.counts.raw_event_count == 1
        assert completed.counts.pending_count == 1
        assert status["admitted_count"] == 0
        assert status["has_pending_evidence_message"] is True
        assert pipeline.current_trusted().events == ()
    finally:
        pipeline.close()


def test_all_source_collection_failure_does_not_replace_previous_trusted_snapshot(tmp_path):
    root = tmp_path / "pipeline"
    first_service = service(tmp_path)
    first = first_service.wait(first_service.start().run_id, timeout=5)
    first_service.close()

    failed_collection = replace(
        collection(
            failed_sources=108,
            current_attempt_success=False,
            attempted_source_count=108,
        ),
        radar={
            "generated_at": NOW.isoformat(),
            "cache_status": "source_failure",
            "source_state": "all_failed",
            "industries": [],
            "source_statuses": [],
            "stats": {"failed_sources": 108},
        },
    )
    ids = iter(("run-all-failed", "raw-all-failed"))
    pipeline = NewsPipelineService(
        storage=NewsPipelineStorage(root, now=lambda: NOW),
        radar_fetcher=lambda: failed_collection,
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)

        assert completed.phase is PipelinePhase.FAILED
        assert completed.durable_phase is None
        assert completed.redacted_error == "collection_failed"
        assert completed.counts.failed_source_count == 108
        assert pipeline.current_trusted().raw_snapshot_id == first.raw_snapshot_id
    finally:
        pipeline.close()


def test_stale_cached_events_do_not_hide_all_source_current_attempt_failure(tmp_path):
    first_service = service(tmp_path)
    first = first_service.wait(first_service.start().run_id, timeout=5)
    first_service.close()

    stale = replace(
        collection(
            raw_event(event_id="b" * 20),
            failed_sources=108,
            current_attempt_success=False,
            attempted_source_count=108,
        ),
        radar={
            "generated_at": NOW.isoformat(),
            "cache_status": "stale",
            "source_state": "stale_cache",
            "industries": [],
            "source_statuses": [],
            "stats": {"total_sources": 108, "failed_sources": 108},
        },
    )
    ids = iter(("run-stale-failed", "raw-stale-failed"))
    pipeline = NewsPipelineService(
        storage=NewsPipelineStorage(tmp_path / "pipeline", now=lambda: NOW),
        radar_fetcher=lambda: stale,
        deterministic_verifier=lambda raw: evidence_for_ids(raw.raw_snapshot_id, "b" * 20),
        trusted_projector=trusted,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)

        assert completed.phase is PipelinePhase.FAILED
        assert completed.redacted_error == "collection_failed"
        assert pipeline.storage.load_raw(completed.raw_snapshot_id) is None
        assert pipeline.current_trusted().raw_snapshot_id == first.raw_snapshot_id
    finally:
        pipeline.close()


def test_successful_zero_item_collection_remains_a_publishable_empty_snapshot(tmp_path):
    empty_collection = collection(
        current_attempt_success=True,
        attempted_source_count=1,
        source_statuses=({
            "source_id": "source-one",
            "source_name": "公开源",
            "source_url": "https://example.test/feed",
            "status": "ok",
            "error_type": None,
            "error_reason": None,
            "last_success_at": NOW.isoformat(),
            "used_cached_items": False,
            "item_count": 0,
        },),
    )
    pipeline = service(
        tmp_path,
        radar_fetcher=lambda: empty_collection,
        verifier=lambda raw: evidence_for_ids(raw.raw_snapshot_id),
    )
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)

        assert completed.phase is PipelinePhase.TRUSTED_PUBLISHED
        assert completed.counts.raw_event_count == 0
        assert pipeline.current_trusted().events == ()
    finally:
        pipeline.close()


def test_collection_attempt_metadata_must_be_internally_consistent(tmp_path):
    inconsistent = replace(
        collection(),
        current_attempt_success=True,
        attempted_source_count=0,
        source_statuses=(),
        failed_source_count=0,
    )
    pipeline = service(tmp_path, radar_fetcher=lambda: inconsistent)
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)

        assert completed.phase is PipelinePhase.FAILED
        assert completed.redacted_error == "collection_failed"
        assert pipeline.storage.load_raw(completed.raw_snapshot_id) is None
    finally:
        pipeline.close()


def test_collection_attempt_status_rows_must_be_canonical_before_raw_write(tmp_path):
    malformed = replace(
        collection(),
        current_attempt_success=True,
        attempted_source_count=1,
        source_statuses=({"status": "ok"},),
        failed_source_count=0,
    )
    pipeline = service(tmp_path, radar_fetcher=lambda: malformed)
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)

        assert completed.phase is PipelinePhase.FAILED
        assert completed.redacted_error == "collection_failed"
        assert pipeline.storage.load_raw(completed.raw_snapshot_id) is None
    finally:
        pipeline.close()


def test_verifier_receives_the_reloaded_durable_raw_snapshot(tmp_path):
    original_title = "交易所公告：星河科技建设存储算力中心"
    mutable_event = raw_event()
    received_titles = []

    class MutatingAfterWriteStorage(NewsPipelineStorage):
        def write_raw(self, snapshot):
            super().write_raw(snapshot)
            snapshot.items[0]["title"] = "调用方内存已被修改"

    storage = MutatingAfterWriteStorage(tmp_path / "pipeline", now=lambda: NOW)
    ids = iter(("run-durable", "raw-durable"))

    def verifier(raw):
        received_titles.append(raw.items[0]["title"])
        return evidence(raw.raw_snapshot_id)

    pipeline = NewsPipelineService(
        storage=storage,
        radar_fetcher=lambda: collection(mutable_event),
        deterministic_verifier=verifier,
        trusted_projector=trusted,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)

        assert completed.phase is PipelinePhase.TRUSTED_PUBLISHED
        assert received_titles == [original_title]
    finally:
        pipeline.close()


@pytest.mark.parametrize(
    "evidence_ids",
    [
        ("a" * 20,),
        ("a" * 20, "b" * 20, "c" * 20),
        ("a" * 20, "a" * 20),
    ],
    ids=("omitted", "extra", "duplicate"),
)
def test_evidence_identity_set_must_exactly_equal_durable_raw_before_write(
    tmp_path,
    evidence_ids,
):
    pipeline = service(
        tmp_path,
        radar_fetcher=lambda: collection(
            raw_event(event_id="a" * 20),
            raw_event(event_id="b" * 20),
        ),
        verifier=lambda raw: evidence_for_ids(raw.raw_snapshot_id, *evidence_ids),
    )
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)

        assert completed.phase is PipelinePhase.FAILED
        assert completed.redacted_error == "verification_failed"
        assert pipeline.storage.load_evidence(completed.raw_snapshot_id) is None
        assert completed.counts.verified_count == 0
        assert completed.counts.pending_count == 0
    finally:
        pipeline.close()


def test_evidence_status_must_be_a_complete_known_status_before_write(tmp_path):
    invalid_event = replace(evidence("raw-fixed").events[0], verification_status="verified")
    invalid = EvidenceSnapshot(
        snapshot_id="evidence-raw-fixed",
        raw_snapshot_id="raw-fixed",
        generated_at=NOW,
        events=(invalid_event,),
    )
    pipeline = service(tmp_path, verifier=lambda _raw: invalid)
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)

        assert completed.phase is PipelinePhase.FAILED
        assert completed.redacted_error == "verification_failed"
        assert pipeline.storage.load_evidence(completed.raw_snapshot_id) is None
    finally:
        pipeline.close()


def test_evidence_events_must_be_a_canonical_tuple_before_write(tmp_path):
    invalid = evidence("raw-fixed")
    object.__setattr__(invalid, "events", list(invalid.events))
    pipeline = service(tmp_path, verifier=lambda _raw: invalid)
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)

        assert completed.phase is PipelinePhase.FAILED
        assert completed.redacted_error == "verification_failed"
        assert pipeline.storage.load_evidence(completed.raw_snapshot_id) is None
    finally:
        pipeline.close()


def test_default_status_recovers_latest_persisted_run_with_stable_tie(tmp_path):
    storage = NewsPipelineStorage(tmp_path / "pipeline", now=lambda: NOW)
    created = NOW - timedelta(minutes=5)
    for run_id in ("run-a", "run-b"):
        queued = PipelineRun(
            run_id=run_id,
            raw_snapshot_id=f"raw-{run_id}",
            evidence_snapshot_id=None,
            trusted_snapshot_id=None,
            phase=PipelinePhase.QUEUED,
            counts=PipelineCounts(),
            created_at=created,
            updated_at=created,
            redacted_error=None,
            displayed_trusted_snapshot_id=None,
        )
        storage.write_run(queued)
    assert storage.recover_incomplete_runs() == 2
    pipeline = NewsPipelineService(
        storage=storage,
        radar_fetcher=lambda: collection(),
        deterministic_verifier=lambda raw: evidence_for_ids(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW,
    )
    try:
        status = pipeline.get_status()

        assert status["loaded"] is True
        assert status["run_id"] == "run-b"
        assert status["phase"] == "interrupted"
    finally:
        pipeline.close()


def test_close_waits_for_owned_futures_before_closing_storage_but_keeps_external_executor(tmp_path):
    entered = Event()
    release = Event()
    close_returned = Event()

    class RecordingStorage(NewsPipelineStorage):
        was_closed = False

        def close(self):
            self.was_closed = True
            super().close()

    def blocked_fetch():
        entered.set()
        assert release.wait(5)
        return collection(raw_event())

    executor = ThreadPoolExecutor(max_workers=1)
    storage = RecordingStorage(tmp_path / "pipeline", now=lambda: NOW)
    ids = iter(("run-external", "raw-external"))
    pipeline = NewsPipelineService(
        storage=storage,
        radar_fetcher=blocked_fetch,
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
        executor=executor,
    )
    started = pipeline.start()
    assert entered.wait(5)
    closer = Thread(target=lambda: (pipeline.close(), close_returned.set()))
    closer.start()
    try:
        assert not close_returned.wait(0.1)
        assert storage.was_closed is False
        release.set()
        assert close_returned.wait(5)
        closer.join(timeout=5)
        assert storage.was_closed is True
        assert executor.submit(lambda: 42).result(timeout=5) == 42
        assert storage.load_run(started.run_id).phase is PipelinePhase.TRUSTED_PUBLISHED
    finally:
        release.set()
        executor.shutdown(wait=True)


def test_completed_futures_are_evicted_and_wait_uses_durable_status(tmp_path):
    identifiers = iter(
        value
        for index in range(12)
        for value in (f"run-{index:02d}", f"raw-{index:02d}")
    )
    pipeline = NewsPipelineService(
        storage=NewsPipelineStorage(tmp_path / "pipeline", now=lambda: NOW),
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW,
        id_factory=lambda: next(identifiers),
    )
    try:
        first_run_id = None
        for _ in range(12):
            started = pipeline.start()
            first_run_id = first_run_id or started.run_id
            assert pipeline.wait(started.run_id, timeout=5).phase is PipelinePhase.TRUSTED_PUBLISHED
        assert len(pipeline._futures) <= 1
        assert pipeline.wait(first_run_id, timeout=5).phase is PipelinePhase.TRUSTED_PUBLISHED
    finally:
        pipeline.close()


def test_startup_recovery_is_scheduled_without_blocking_refresh(tmp_path):
    pipeline = service(tmp_path)
    try:
        future = pipeline.recover_startup()
        assert future is not None
        assert future.result(timeout=5) == 0
    finally:
        pipeline.close()


def test_refresh_is_rejected_until_scheduled_startup_recovery_finishes(tmp_path, monkeypatch):
    entered = Event()
    release = Event()
    pipeline = service(tmp_path)

    def blocked_recovery():
        entered.set()
        assert release.wait(5)
        return 0

    monkeypatch.setattr(pipeline.storage, "recover_incomplete_runs", blocked_recovery)
    try:
        recovery = pipeline.recover_startup()
        assert entered.wait(5)
        with pytest.raises(NewsPipelineActiveError):
            pipeline.start()
        release.set()
        assert recovery.result(timeout=5) == 0
        assert pipeline.wait(pipeline.start().run_id, timeout=5).phase is PipelinePhase.TRUSTED_PUBLISHED
    finally:
        release.set()
        pipeline.close()


def test_deterministic_verifier_rejects_duplicate_raw_event_identities(tmp_path):
    from evidence_verification.service import EvidenceVerificationService
    from evidence_verification.storage import EvidenceStorage
    from news_pipeline.models import RawSnapshot

    verifier = EvidenceVerificationService(
        storage=EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW),
        document_fetcher=None,
        now=lambda: NOW,
    )
    duplicate = raw_event()

    with pytest.raises(ValueError, match="identity"):
        verifier.verify_raw_snapshot(RawSnapshot("raw-duplicates", NOW, (duplicate, duplicate)))


def test_evidence_phase_is_durable_before_fallible_compatibility_publication(tmp_path):
    root = tmp_path / "pipeline"
    storage = NewsPipelineStorage(root, now=lambda: NOW)
    ids = iter(("run-compat-evidence", "raw-compat-evidence"))

    def broken_evidence_publisher(snapshot):
        run = storage.load_run("run-compat-evidence")
        assert run.phase is PipelinePhase.EVIDENCE_SAVED
        assert run.evidence_snapshot_id == snapshot.snapshot_id
        assert run.counts.verified_count == 1
        raise OSError("C:\\private\\evidence.json?token=secret")

    pipeline = NewsPipelineService(
        storage=storage,
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        evidence_publisher=broken_evidence_publisher,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)
        assert completed.phase is PipelinePhase.FAILED
        assert completed.evidence_snapshot_id == "evidence-raw-compat-evidence"
        assert completed.counts.verified_count == 1
        assert completed.redacted_error == "evidence_compatibility_failed"
        assert pipeline.current_trusted() is None
    finally:
        pipeline.close()


def test_radar_compatibility_result_is_durable_before_trusted_pointer_commit(tmp_path):
    root = tmp_path / "pipeline"
    storage = NewsPipelineStorage(root, now=lambda: NOW)
    ids = iter(("run-radar-order", "raw-radar-order"))

    def radar_publisher(_collection):
        current = storage.load_current_trusted()
        run = storage.load_run("run-radar-order")
        assert current is None
        assert run.phase is PipelinePhase.EVIDENCE_SAVED
        raise OSError("compatibility cache unavailable")

    pipeline = NewsPipelineService(
        storage=storage,
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        radar_publisher=radar_publisher,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    try:
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)
        assert completed.phase is PipelinePhase.TRUSTED_PUBLISHED
        assert completed.redacted_error == "radar_compatibility_failed"
        assert pipeline.current_trusted().raw_snapshot_id == "raw-radar-order"
        assert pipeline.get_status(completed.run_id)["compatibility_error"] == "radar_compatibility_failed"
    finally:
        pipeline.close()


def test_terminal_retry_preserves_the_run_displayed_trusted_identity(tmp_path, monkeypatch):
    """Catches terminal compensation replacing the run's immutable prior display identity."""
    root = tmp_path / "pipeline"
    baseline = service(tmp_path)
    previous = baseline.wait(baseline.start().run_id, timeout=5)
    baseline.close()

    storage = NewsPipelineStorage(root, now=lambda: NOW + timedelta(seconds=1))
    original_write_run = storage.write_run
    terminal_attempts = 0

    def fail_first_terminal_write(run, *, expected_phase=None):
        nonlocal terminal_attempts
        if run.phase is PipelinePhase.TRUSTED_PUBLISHED:
            terminal_attempts += 1
            if terminal_attempts == 1:
                raise OSError("terminal status unavailable")
        return original_write_run(run, expected_phase=expected_phase)

    def broken_radar_publisher(_collection):
        raise OSError("legacy radar cache unavailable")

    monkeypatch.setattr(storage, "write_run", fail_first_terminal_write)
    identifiers = iter(("run-terminal-retry", "raw-terminal-retry"))
    pipeline = NewsPipelineService(
        storage=storage,
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        radar_publisher=broken_radar_publisher,
        now=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: next(identifiers),
    )
    try:
        started = pipeline.start()
        completed = pipeline.wait(started.run_id, timeout=5)

        assert terminal_attempts == 2
        assert completed.phase is PipelinePhase.TRUSTED_PUBLISHED
        assert completed.durable_phase is PipelinePhase.TRUSTED_PUBLISHED
        assert completed.displayed_trusted_snapshot_id == previous.raw_snapshot_id
        assert completed.redacted_error == "radar_compatibility_failed"
        assert pipeline.current_trusted().raw_snapshot_id == started.raw_snapshot_id
        assert pipeline.get_status(started.run_id)["compatibility_error"] == "radar_compatibility_failed"
    finally:
        pipeline.close()


def test_recovery_reconciles_a_committed_zero_admission_publication_after_both_terminal_writes_fail(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "pipeline"
    storage = NewsPipelineStorage(root, now=lambda: NOW)
    original_write_run = storage.write_run
    terminal_attempts = 0

    def fail_both_terminal_writes(run, *, expected_phase=None):
        nonlocal terminal_attempts
        if run.phase is PipelinePhase.TRUSTED_PUBLISHED:
            terminal_attempts += 1
            raise OSError("terminal status unavailable")
        return original_write_run(run, expected_phase=expected_phase)

    monkeypatch.setattr(storage, "write_run", fail_both_terminal_writes)
    identifiers = iter(("run-reconcile", "raw-reconcile"))

    def broken_radar_publisher(_collection):
        raise OSError("legacy radar cache unavailable")

    pipeline = NewsPipelineService(
        storage=storage,
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(
            raw.raw_snapshot_id,
            status=VerificationStatus.UNVERIFIED,
        ),
        trusted_projector=trusted,
        radar_publisher=broken_radar_publisher,
        now=lambda: NOW,
        id_factory=lambda: next(identifiers),
    )
    started = pipeline.start()
    stranded = pipeline.wait(started.run_id, timeout=5)

    assert terminal_attempts == 2
    assert stranded.phase is PipelinePhase.EVIDENCE_SAVED
    assert stranded.redacted_error == "radar_compatibility_failed"
    assert stranded.counts.raw_event_count == 1
    assert stranded.counts.pending_count == 1
    assert storage.load_current_trusted().raw_snapshot_id == started.raw_snapshot_id
    pipeline.close()

    restarted_storage = NewsPipelineStorage(root, now=lambda: NOW + timedelta(seconds=1))
    assert restarted_storage.recover_incomplete_runs() == 1
    recovered = restarted_storage.load_run(started.run_id)
    assert recovered.phase is PipelinePhase.TRUSTED_PUBLISHED
    assert recovered.durable_phase is PipelinePhase.TRUSTED_PUBLISHED
    assert recovered.raw_snapshot_id == started.raw_snapshot_id
    assert recovered.evidence_snapshot_id == f"evidence-{started.raw_snapshot_id}"
    assert recovered.trusted_snapshot_id == started.raw_snapshot_id
    assert recovered.counts.raw_event_count == 1
    assert recovered.counts.pending_count == 1
    assert recovered.counts.verified_count == 0
    assert recovered.counts.corroborated_count == 0
    assert recovered.redacted_error == "radar_compatibility_failed"
    assert recovered.displayed_trusted_snapshot_id == started.raw_snapshot_id

    restarted = NewsPipelineService(
        storage=restarted_storage,
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW + timedelta(seconds=1),
    )
    try:
        status = restarted.get_status(started.run_id)
        assert status["has_pending_evidence_message"] is True
        assert status["compatibility_error"] == "radar_compatibility_failed"
    finally:
        restarted.close()


def test_default_status_uses_the_cached_latest_run_in_steady_state(tmp_path, monkeypatch):
    pipeline = service(tmp_path)
    run = pipeline.wait(pipeline.start().run_id, timeout=5)

    monkeypatch.setattr(
        pipeline.storage,
        "load_latest_run",
        lambda: (_ for _ in ()).throw(AssertionError("steady status rescanned the runs directory")),
    )
    try:
        assert pipeline.get_status()["run_id"] == run.run_id
        assert pipeline.has_pipeline_state() is True
    finally:
        pipeline.close()


def test_failed_startup_recovery_blocks_refresh_until_explicit_retry_succeeds(tmp_path, monkeypatch):
    pipeline = service(tmp_path)
    attempts = iter((OSError("D:\\private\\future.json?token=secret"), 0))

    def recover():
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(pipeline.storage, "recover_incomplete_runs", recover)
    try:
        failed = pipeline.recover_startup()
        with pytest.raises(OSError):
            failed.result(timeout=5)
        with pytest.raises(RuntimeError, match="recovery"):
            pipeline.start()
        with pytest.raises(OSError, match="storage_corrupt"):
            pipeline.get_status()
        assert pipeline.recover_startup().result(timeout=5) == 0
        assert pipeline.get_status()["recovery_status"] == "ready"
        assert pipeline.wait(pipeline.start().run_id, timeout=5).phase is PipelinePhase.TRUSTED_PUBLISHED
    finally:
        pipeline.close()


def test_corrupt_latest_run_still_allows_bounded_recovery_retry_after_reconstruction(tmp_path):
    root = tmp_path / "pipeline"
    seed = NewsPipelineStorage(root, now=lambda: NOW)
    seed.write_run(PipelineRun(
        run_id="run-old",
        raw_snapshot_id="raw-old",
        evidence_snapshot_id=None,
        trusted_snapshot_id=None,
        phase=PipelinePhase.QUEUED,
        counts=PipelineCounts(),
        created_at=NOW,
        updated_at=NOW,
        redacted_error=None,
        displayed_trusted_snapshot_id=None,
    ))
    seed.close()
    corrupt = root / "runs" / "run-future.json"
    corrupt.write_text('{"schema_version":999}', encoding="utf-8")
    ids = iter(("run-after-recovery", "raw-after-recovery"))

    pipeline = NewsPipelineService(
        storage=NewsPipelineStorage(root, now=lambda: NOW),
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    try:
        failed = pipeline.recover_startup()
        with pytest.raises(OSError, match="storage_corrupt"):
            failed.result(timeout=5)
        with pytest.raises(RuntimeError, match="recovery"):
            pipeline.start()

        corrupt.unlink()
        assert pipeline.recover_startup().result(timeout=5) == 1
        completed = pipeline.wait(pipeline.start().run_id, timeout=5)
        assert completed.phase is PipelinePhase.TRUSTED_PUBLISHED
    finally:
        pipeline.close()


def test_compatibility_error_is_durable_per_run_and_survives_restart(tmp_path):
    root = tmp_path / "pipeline"
    identifiers = iter(("run-compat-first", "raw-compat-first", "run-compat-second", "raw-compat-second"))
    calls = 0

    def radar_publisher(_collection):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("legacy radar cache unavailable")

    pipeline = NewsPipelineService(
        storage=NewsPipelineStorage(root, now=lambda: NOW),
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        radar_publisher=radar_publisher,
        now=lambda: NOW,
        id_factory=lambda: next(identifiers),
    )
    first_id = pipeline.start().run_id
    first = pipeline.wait(first_id, timeout=5)
    second_id = pipeline.start().run_id
    second = pipeline.wait(second_id, timeout=5)

    assert first.phase is PipelinePhase.TRUSTED_PUBLISHED
    assert first.redacted_error == "radar_compatibility_failed"
    assert second.phase is PipelinePhase.TRUSTED_PUBLISHED
    assert second.redacted_error is None
    assert pipeline.get_status(first_id)["compatibility_error"] == "radar_compatibility_failed"
    assert pipeline.get_status(second_id)["compatibility_error"] is None
    pipeline.close()

    restarted = NewsPipelineService(
        storage=NewsPipelineStorage(root, now=lambda: NOW),
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW,
    )
    try:
        assert restarted.get_status(first_id)["compatibility_error"] == "radar_compatibility_failed"
        assert restarted.get_status(second_id)["compatibility_error"] is None
    finally:
        restarted.close()


def test_selected_corrupt_run_is_a_storage_error_not_a_missing_run(tmp_path):
    root = tmp_path / "pipeline"
    corrupt = root / "runs" / "run-corrupt.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text('{"schema_version":999}', encoding="utf-8")
    pipeline = NewsPipelineService(
        storage=NewsPipelineStorage(root, now=lambda: NOW),
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW,
    )
    try:
        with pytest.raises(OSError, match="storage_corrupt"):
            pipeline.get_status("run-corrupt")
        with pytest.raises(OSError, match="storage_corrupt"):
            pipeline.get_status("run-absent")
        corrupt.unlink()
        assert pipeline.recover_startup().result(timeout=5) == 0
        with pytest.raises(KeyError):
            pipeline.get_status("run-absent")
    finally:
        pipeline.close()


def test_recovery_submit_failure_latches_barrier_until_explicit_retry(tmp_path, monkeypatch):
    """Catches synchronous executor rejection being mistaken for no recovery need."""
    executor = ThreadPoolExecutor(max_workers=2)
    pipeline = NewsPipelineService(
        storage=NewsPipelineStorage(tmp_path / "pipeline", now=lambda: NOW),
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW,
        executor=executor,
    )
    original_submit = executor.submit

    def reject_submit(*_args, **_kwargs):
        raise RuntimeError("executor rejected recovery")

    monkeypatch.setattr(executor, "submit", reject_submit)
    with pytest.raises(RuntimeError, match="executor rejected recovery"):
        pipeline.recover_startup()
    with pytest.raises(OSError, match="storage_corrupt"):
        pipeline.get_status()
    with pytest.raises(RuntimeError, match="recovery failed"):
        pipeline.start()

    monkeypatch.setattr(executor, "submit", original_submit)
    assert pipeline.recover_startup().result(timeout=5) == 0
    assert pipeline.get_status()["recovery_status"] == "ready"
    pipeline.close()
    executor.shutdown(wait=True)


def test_multiworker_recovery_never_interrupts_an_active_worker(tmp_path):
    """Catches startup recovery running beside an active refresh on a wider executor."""
    entered = Event()
    release = Event()
    executor = ThreadPoolExecutor(max_workers=2)
    identifiers = iter(("run-active", "raw-active"))

    def blocked_fetch():
        entered.set()
        assert release.wait(timeout=5)
        return collection(raw_event())

    pipeline = NewsPipelineService(
        storage=NewsPipelineStorage(tmp_path / "pipeline", now=lambda: NOW),
        radar_fetcher=blocked_fetch,
        deterministic_verifier=lambda raw: evidence(raw.raw_snapshot_id),
        trusted_projector=trusted,
        now=lambda: NOW,
        id_factory=lambda: next(identifiers),
        executor=executor,
    )
    run = pipeline.start()
    assert entered.wait(timeout=5)

    assert pipeline.recover_startup() is None
    assert pipeline.storage.load_run(run.run_id).phase is PipelinePhase.FETCHING

    release.set()
    assert pipeline.wait(run.run_id, timeout=5).phase is PipelinePhase.TRUSTED_PUBLISHED
    assert pipeline.recover_startup().result(timeout=5) == 0
    pipeline.close()
    executor.shutdown(wait=True)


def test_failed_terminalization_latches_recovery_barrier_until_reconciled(tmp_path, monkeypatch):
    """Catches a failed terminal write being swallowed before another run starts."""
    root = tmp_path / "pipeline"
    storage = NewsPipelineStorage(root, now=lambda: NOW)
    original_write_run = storage.write_run

    def fail_terminal_write(run, *, expected_phase=None):
        if run.phase is PipelinePhase.FAILED:
            raise OSError("terminal status unavailable")
        return original_write_run(run, expected_phase=expected_phase)

    monkeypatch.setattr(storage, "write_run", fail_terminal_write)
    identifiers = iter(("run-failed", "raw-failed", "run-next", "raw-next"))
    pipeline = NewsPipelineService(
        storage=storage,
        radar_fetcher=lambda: collection(raw_event()),
        deterministic_verifier=lambda _raw: (_ for _ in ()).throw(ValueError("verification broke")),
        trusted_projector=trusted,
        now=lambda: NOW,
        id_factory=lambda: next(identifiers),
    )
    first = pipeline.start()
    persisted = pipeline.wait(first.run_id, timeout=5)
    assert persisted.phase is PipelinePhase.VERIFYING

    with pytest.raises(OSError, match="storage_corrupt"):
        pipeline.get_status(first.run_id)
    with pytest.raises(RuntimeError, match="recovery failed"):
        pipeline.start()

    monkeypatch.setattr(storage, "write_run", original_write_run)
    assert pipeline.recover_startup().result(timeout=5) == 1
    assert pipeline.get_status(first.run_id)["phase"] == PipelinePhase.INTERRUPTED.value
    pipeline.close()


@pytest.mark.parametrize(
    ("failure_stage", "expected_checkpoint", "expects_evidence"),
    [
        ("verification", PipelinePhase.RAW_SAVED, False),
        ("evidence", PipelinePhase.RAW_SAVED, False),
        ("publication", PipelinePhase.EVIDENCE_SAVED, True),
    ],
)
def test_zero_event_failure_retains_the_last_exact_durable_checkpoint(
    tmp_path,
    monkeypatch,
    failure_stage,
    expected_checkpoint,
    expects_evidence,
):
    """Catches empty raw snapshots being mistaken for a pre-collection failure."""
    root = tmp_path / failure_stage
    storage = NewsPipelineStorage(root, now=lambda: NOW)

    def empty_evidence(raw):
        raw_id = raw.raw_snapshot_id
        return EvidenceSnapshot(
            snapshot_id=f"evidence-{raw_id}",
            raw_snapshot_id=raw_id,
            generated_at=NOW,
            events=(),
        )

    verifier = empty_evidence
    projector = lambda snapshot: TrustedSnapshot(snapshot.raw_snapshot_id, NOW, ())
    if failure_stage == "verification":
        verifier = lambda _raw: (_ for _ in ()).throw(ValueError("verification failed"))
    elif failure_stage == "evidence":
        monkeypatch.setattr(
            storage,
            "write_evidence",
            lambda _snapshot: (_ for _ in ()).throw(OSError("evidence unavailable")),
        )
    else:
        projector = lambda _snapshot: (_ for _ in ()).throw(ValueError("publication failed"))
    identifiers = iter((f"run-{failure_stage}", f"raw-{failure_stage}"))
    pipeline = NewsPipelineService(
        storage=storage,
        radar_fetcher=lambda: collection(),
        deterministic_verifier=verifier,
        trusted_projector=projector,
        now=lambda: NOW,
        id_factory=lambda: next(identifiers),
    )

    started = pipeline.start()
    failed = pipeline.wait(started.run_id, timeout=5)

    assert failed.phase is PipelinePhase.FAILED
    assert failed.durable_phase is expected_checkpoint
    assert failed.counts.raw_event_count == 0
    assert storage.load_raw(started.raw_snapshot_id) is not None
    assert (storage.load_evidence(started.raw_snapshot_id) is not None) is expects_evidence
    pipeline.close()
