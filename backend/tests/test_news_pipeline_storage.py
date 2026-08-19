from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

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
    storage.write_raw(raw_snapshot("old"))
    storage.write_evidence(evidence_snapshot("old"))
    storage.publish_trusted(trusted_snapshot("old"))
    storage.write_raw(raw_snapshot("new"))
    storage.write_evidence(evidence_snapshot("new"))

    def raise_io_error(*_args, **_kwargs):
        raise OSError("pointer unavailable")

    monkeypatch.setattr(storage, "_write_current_pointer", raise_io_error)

    with pytest.raises(OSError, match="pointer unavailable"):
        storage.publish_trusted(trusted_snapshot("new"))

    assert storage.load_current_trusted().raw_snapshot_id == "old"
    assert storage.load_trusted("new").raw_snapshot_id == "new"


def test_explicit_recovery_marks_all_nonterminal_runs_interrupted(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    queued = pipeline_run(phase=PipelinePhase.QUEUED)
    storage.write_run(queued)

    restarted = NewsPipelineStorage(tmp_path)
    assert restarted.load_run("run-1").phase is PipelinePhase.QUEUED
    restarted.recover_incomplete_runs(recovery_owner_id="restart-owner")
    recovered = restarted.load_run("run-1")

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


def test_trusted_publish_requires_same_identity_raw_and_evidence_artifacts(tmp_path):
    storage = NewsPipelineStorage(tmp_path)

    with pytest.raises(ValueError, match="raw and evidence"):
        storage.publish_trusted(trusted_snapshot("raw-1"))

    storage.write_raw(raw_snapshot("raw-1"))
    with pytest.raises(ValueError, match="raw and evidence"):
        storage.publish_trusted(trusted_snapshot("raw-1"))

    storage.write_evidence(evidence_snapshot("raw-1"))
    storage.publish_trusted(trusted_snapshot("raw-1"))

    assert storage.load_current_trusted().raw_snapshot_id == "raw-1"


def test_run_phase_is_immutable_monotonic_and_cas_protected(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    queued = pipeline_run(phase=PipelinePhase.QUEUED)
    storage.write_run(queued)

    fetching = replace(queued, phase=PipelinePhase.FETCHING, updated_at=NOW + timedelta(seconds=1))
    storage.write_run(fetching, expected_phase=PipelinePhase.QUEUED)

    with pytest.raises(ValueError, match="expected current phase"):
        storage.write_run(replace(fetching, phase=PipelinePhase.FAILED), expected_phase=PipelinePhase.QUEUED)
    with pytest.raises(ValueError, match="immutable raw_snapshot_id"):
        storage.write_run(
            replace(fetching, raw_snapshot_id="raw-2"),
            expected_phase=PipelinePhase.FETCHING,
        )
    with pytest.raises(ValueError, match="invalid phase transition"):
        storage.write_run(replace(fetching, phase=PipelinePhase.QUEUED), expected_phase=PipelinePhase.FETCHING)


def test_terminal_run_cannot_be_revived(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    queued = pipeline_run(phase=PipelinePhase.QUEUED)
    storage.write_run(queued)
    failed = replace(queued, phase=PipelinePhase.FAILED, updated_at=NOW + timedelta(seconds=1))
    storage.write_run(failed, expected_phase=PipelinePhase.QUEUED)

    with pytest.raises(ValueError, match="terminal"):
        storage.write_run(replace(failed, phase=PipelinePhase.FETCHING), expected_phase=PipelinePhase.FAILED)


def test_constructor_does_not_interrupt_active_owner_but_explicit_stale_recovery_does(tmp_path):
    owner = "owner-a"
    active = NewsPipelineStorage(tmp_path, owner_id=owner, now=lambda: NOW)
    queued = replace(
        pipeline_run(phase=PipelinePhase.QUEUED),
        owner_id=owner,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    active.write_run(queued)

    other = NewsPipelineStorage(tmp_path, owner_id="owner-b", now=lambda: NOW)
    other.recover_incomplete_runs(recovery_owner_id="owner-b")
    assert other.load_run("run-1").phase is PipelinePhase.QUEUED

    stale = NewsPipelineStorage(tmp_path, owner_id="owner-b", now=lambda: NOW + timedelta(minutes=6))
    stale.recover_incomplete_runs(recovery_owner_id="owner-b")
    assert stale.load_run("run-1").phase is PipelinePhase.INTERRUPTED


def test_run_owner_cannot_overwrite_another_active_owner(tmp_path):
    owner_a = NewsPipelineStorage(tmp_path, owner_id="owner-a", now=lambda: NOW)
    queued = replace(
        pipeline_run(phase=PipelinePhase.QUEUED),
        owner_id="owner-a",
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    owner_a.write_run(queued)

    owner_b = NewsPipelineStorage(tmp_path, owner_id="owner-b", now=lambda: NOW)
    with pytest.raises(ValueError, match="active owner"):
        owner_b.write_run(
            replace(queued, phase=PipelinePhase.FETCHING),
            expected_phase=PipelinePhase.QUEUED,
        )


@pytest.mark.parametrize("phase", [
    PipelinePhase.QUEUED,
    PipelinePhase.FETCHING,
    PipelinePhase.RAW_SAVED,
    PipelinePhase.VERIFYING,
    PipelinePhase.EVIDENCE_SAVED,
])
def test_explicit_recovery_interrupts_every_nonterminal_phase(tmp_path, phase):
    storage = NewsPipelineStorage(tmp_path)
    run = replace(pipeline_run(phase=PipelinePhase.QUEUED), run_id=f"run-{phase.value}")
    storage.write_run(run)
    if phase in {PipelinePhase.RAW_SAVED, PipelinePhase.VERIFYING, PipelinePhase.EVIDENCE_SAVED}:
        storage.write_raw(raw_snapshot(run.raw_snapshot_id))
    if phase is PipelinePhase.EVIDENCE_SAVED:
        storage.write_evidence(evidence_snapshot(run.raw_snapshot_id))
    if phase is not PipelinePhase.QUEUED:
        current = PipelinePhase.QUEUED
        for next_phase in (
            PipelinePhase.FETCHING,
            PipelinePhase.RAW_SAVED,
            PipelinePhase.VERIFYING,
            PipelinePhase.EVIDENCE_SAVED,
        ):
            storage.write_run(replace(run, phase=next_phase), expected_phase=current)
            current = next_phase
            if next_phase is phase:
                break

    storage.recover_incomplete_runs(recovery_owner_id="restart-owner")

    assert storage.load_run(run.run_id).phase is PipelinePhase.INTERRUPTED


def test_load_raw_rejects_future_schema_unknown_keys_wrong_path_and_oversize(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    path = storage.raw_root / "raw-1.json"
    valid = json.loads(path.read_text(encoding="utf-8"))

    path.write_text(json.dumps({**valid, "schema_version": 2}), encoding="utf-8")
    assert storage.load_raw("raw-1") is None
    path.write_text(json.dumps({**valid, "unexpected": True}), encoding="utf-8")
    assert storage.load_raw("raw-1") is None
    path.write_text(json.dumps({**valid, "raw_snapshot_id": "raw-2"}), encoding="utf-8")
    assert storage.load_raw("raw-1") is None
    path.write_bytes(b"{" + b'"x":"' + b"a" * 1_048_577 + b'"}')
    assert storage.load_raw("raw-1") is None


def test_run_storage_redacts_sensitive_error_before_persisting(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    run = replace(
        pipeline_run(phase=PipelinePhase.QUEUED),
        redacted_error="token=hunter2 Authorization: Bearer abc https://example.test/?api_key=secret C:\\secret\\file",
    )

    storage.write_run(run)

    error = storage.load_run("run-1").redacted_error
    assert "hunter2" not in error
    assert "abc" not in error
    assert "secret" not in error
    assert "C:\\" not in error


def test_run_rejects_non_datetime_lease_before_serialization(tmp_path):
    storage = NewsPipelineStorage(tmp_path, owner_id="owner-a")
    run = replace(pipeline_run(phase=PipelinePhase.QUEUED), owner_id="owner-a", lease_expires_at="tomorrow")

    with pytest.raises(ValueError, match="lease_expires_at"):
        storage.write_run(run)


def test_pointer_write_oserror_preserves_previous_pointer_without_temp_cleanup(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    for raw_snapshot_id in ("old", "new"):
        storage.write_raw(raw_snapshot(raw_snapshot_id))
        storage.write_evidence(evidence_snapshot(raw_snapshot_id))
    storage.publish_trusted(trusted_snapshot("old"))
    original_pointer = storage.current_pointer_path
    blocked_parent = tmp_path / "pointer-parent-is-file"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    storage.current_pointer_path = blocked_parent / "current.json"

    with pytest.raises(OSError):
        storage.publish_trusted(trusted_snapshot("new"))

    storage.current_pointer_path = original_pointer
    assert storage.load_current_trusted().raw_snapshot_id == "old"
    assert storage.load_trusted("new").raw_snapshot_id == "new"


def test_atomic_write_never_unlinks_a_temp_path_replaced_by_another_file(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    victim = tmp_path / "victim.json"
    victim.write_text("must survive", encoding="utf-8")

    real_replace = __import__("os").replace
    swapped_paths = []

    def swap_temp_then_fail(source, _destination):
        source_path = __import__("pathlib").Path(source)
        source_path.unlink()
        real_replace(victim, source_path)
        swapped_paths.append(source_path)
        raise OSError("replace failed after temp path changed")

    monkeypatch.setattr("news_pipeline.storage.os.replace", swap_temp_then_fail)

    with pytest.raises(OSError, match="replace failed"):
        storage.write_raw(raw_snapshot("raw-1"))

    assert swapped_paths[0].exists()
    assert swapped_paths[0].read_text(encoding="utf-8") == "must survive"


@pytest.mark.parametrize("raw_snapshot_id", ["CON", "aux", "RAW-1", "raw-1.", "raw-1 "])
def test_ids_reject_windows_reserved_noncanonical_and_case_colliding_forms(tmp_path, raw_snapshot_id):
    storage = NewsPipelineStorage(tmp_path)

    with pytest.raises(ValueError, match="raw_snapshot_id"):
        storage.write_raw(raw_snapshot(raw_snapshot_id))
