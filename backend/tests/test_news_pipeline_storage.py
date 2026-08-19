from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evidence_verification.models import EvidenceSnapshot
from news_pipeline.models import (
    PipelineCounts,
    PipelinePhase,
    PipelineRun,
    RawSnapshot,
    TrustedSnapshot,
)
from news_pipeline.storage import NewsPipelineStorage


NOW = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)


def raw_snapshot(raw_snapshot_id: str) -> RawSnapshot:
    return RawSnapshot(raw_snapshot_id=raw_snapshot_id, collected_at=NOW, items=())


def evidence_snapshot(raw_snapshot_id: str) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=f"evidence-{raw_snapshot_id}",
        raw_snapshot_id=raw_snapshot_id,
        generated_at=NOW,
        events=(),
    )


def trusted_snapshot(raw_snapshot_id: str) -> TrustedSnapshot:
    return TrustedSnapshot(raw_snapshot_id=raw_snapshot_id, published_at=NOW, events=())


def pipeline_run(*, phase: PipelinePhase) -> PipelineRun:
    return PipelineRun(
        run_id="run-1",
        raw_snapshot_id="raw-1",
        evidence_snapshot_id=None,
        trusted_snapshot_id=None,
        phase=phase,
        counts=PipelineCounts(),
        created_at=NOW,
        updated_at=NOW,
        redacted_error=None,
        displayed_trusted_snapshot_id="old",
    )


def test_all_pipeline_artifacts_share_raw_snapshot_id(tmp_path):
    storage = NewsPipelineStorage(tmp_path)

    storage.write_raw(raw_snapshot("raw-1"))
    storage.write_evidence(evidence_snapshot("raw-1"))
    storage.publish_trusted(trusted_snapshot("raw-1"))

    assert storage.load_current_trusted().raw_snapshot_id == "raw-1"


def test_current_pointer_is_not_switched_before_trusted_snapshot_is_durable(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    storage.publish_trusted(trusted_snapshot("old"))

    def raise_io_error(*_args, **_kwargs):
        raise OSError("pointer unavailable")

    monkeypatch.setattr(storage, "_write_current_pointer", raise_io_error)

    with pytest.raises(OSError, match="pointer unavailable"):
        storage.publish_trusted(trusted_snapshot("new"))

    assert storage.load_current_trusted().raw_snapshot_id == "old"
    assert storage.load_trusted("new").raw_snapshot_id == "new"


def test_startup_marks_only_nonterminal_runs_interrupted(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_run(pipeline_run(phase=PipelinePhase.FETCHING))

    recovered = NewsPipelineStorage(tmp_path).load_run("run-1")

    assert recovered.phase is PipelinePhase.INTERRUPTED
    assert recovered.displayed_trusted_snapshot_id == "old"
    assert recovered.redacted_error == "运行在完成前中断"


def test_legacy_evidence_identity_cannot_be_published_as_a_new_pipeline_artifact(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    legacy = EvidenceSnapshot(snapshot_id="legacy", generated_at=NOW, events=())

    with pytest.raises(ValueError, match="legacy evidence identity"):
        storage.write_evidence(legacy)


def test_write_evidence_rejects_unbounded_snapshot_identity(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    snapshot = EvidenceSnapshot(
        snapshot_id="e" * 129,
        raw_snapshot_id="raw-1",
        generated_at=NOW,
        events=(),
    )

    with pytest.raises(ValueError, match="snapshot_id"):
        storage.write_evidence(snapshot)


def test_write_run_rejects_unsafe_optional_snapshot_identity(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    run = PipelineRun(
        run_id="run-1",
        raw_snapshot_id="raw-1",
        evidence_snapshot_id="../unsafe",
        trusted_snapshot_id=None,
        phase=PipelinePhase.QUEUED,
        counts=PipelineCounts(),
        created_at=NOW,
        updated_at=NOW,
        redacted_error=None,
        displayed_trusted_snapshot_id=None,
    )

    with pytest.raises(ValueError, match="evidence_snapshot_id"):
        storage.write_run(run)
