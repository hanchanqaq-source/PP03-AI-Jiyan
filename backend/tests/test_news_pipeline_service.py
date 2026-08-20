from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import Event

import pytest

from evidence_verification.models import (
    EvidenceEvent,
    EvidenceSnapshot,
    VerificationStatus,
)
from news_pipeline.models import PipelinePhase, TrustedSnapshot
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


def collection(*events: dict[str, object], failed_sources: int = 0) -> RadarCollection:
    return RadarCollection(
        raw_events=tuple(events),
        source_statuses=(),
        generated_at=NOW,
        failed_source_count=failed_sources,
        radar={"generated_at": NOW.isoformat(), "industries": [], "source_statuses": [], "stats": {}},
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
        collection(failed_sources=108),
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
        assert completed.redacted_error == "collection_failed"
        assert completed.counts.failed_source_count == 108
        assert pipeline.current_trusted().raw_snapshot_id == first.raw_snapshot_id
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
