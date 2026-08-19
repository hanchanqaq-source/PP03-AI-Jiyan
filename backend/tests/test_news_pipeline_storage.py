from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

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
    storage = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    queued = pipeline_run(phase=PipelinePhase.QUEUED)
    storage.write_run(queued)

    restarted = NewsPipelineStorage(tmp_path, now=lambda: NOW + timedelta(seconds=31))
    assert restarted.load_run("run-1").phase is PipelinePhase.QUEUED
    restarted.recover_incomplete_runs()
    recovered = restarted.load_run("run-1")

    assert recovered.phase is PipelinePhase.INTERRUPTED
    assert recovered.displayed_trusted_snapshot_id == "old"
    assert recovered.redacted_error == "pipeline_interrupted"


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


def test_constructor_does_not_recover_an_active_writer_but_explicit_stale_recovery_does(tmp_path):
    active = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    queued = pipeline_run(phase=PipelinePhase.QUEUED)
    active.write_run(queued)

    other = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    with pytest.raises(ValueError, match="authority is active"):
        other.recover_incomplete_runs()
    assert other.load_run("run-1").phase is PipelinePhase.QUEUED

    stale = NewsPipelineStorage(tmp_path, now=lambda: NOW + timedelta(seconds=31))
    stale.recover_incomplete_runs()
    assert stale.load_run("run-1").phase is PipelinePhase.INTERRUPTED


def test_second_storage_instance_cannot_overwrite_an_active_private_writer(tmp_path):
    writer = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    queued = pipeline_run(phase=PipelinePhase.QUEUED)
    writer.write_run(queued)

    other = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    with pytest.raises(ValueError, match="authority is active"):
        other.write_run(
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
    storage = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    run = replace(pipeline_run(phase=PipelinePhase.QUEUED), run_id=f"run-{phase.value}", evidence_snapshot_id="evidence-raw-1")
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

    restarted = NewsPipelineStorage(tmp_path, now=lambda: NOW + timedelta(seconds=31))
    restarted.recover_incomplete_runs()

    assert restarted.load_run(run.run_id).phase is PipelinePhase.INTERRUPTED


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

    monkeypatch.setattr(storage, "_replace_durable", swap_temp_then_fail)

    with pytest.raises(OSError, match="replace failed"):
        storage.write_raw(raw_snapshot("raw-1"))

    assert swapped_paths[0].exists()
    assert swapped_paths[0].read_text(encoding="utf-8") == "must survive"


@pytest.mark.parametrize("raw_snapshot_id", ["CON", "aux", "RAW-1", "raw-1.", "raw-1 "])
def test_ids_reject_windows_reserved_noncanonical_and_case_colliding_forms(tmp_path, raw_snapshot_id):
    storage = NewsPipelineStorage(tmp_path)

    with pytest.raises(ValueError, match="raw_snapshot_id"):
        storage.write_raw(raw_snapshot(raw_snapshot_id))


def test_trusted_publish_rejects_legacy_evidence_document_even_when_path_identity_matches(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    legacy = EvidenceSnapshot(
        snapshot_id="evidence-raw-1",
        raw_snapshot_id="raw-1",
        generated_at=NOW,
        events=(),
        recovery_metadata={"legacy_identity": True},
    )
    storage.evidence_root.mkdir(parents=True)
    (storage.evidence_root / "raw-1.json").write_text(json.dumps(__import__("evidence_verification.storage", fromlist=["snapshot_document"]).snapshot_document(legacy)), encoding="utf-8")

    with pytest.raises(ValueError, match="legacy evidence"):
        storage.publish_trusted(trusted_snapshot("raw-1"))


def test_persisted_phase_requires_present_matching_snapshot_ids(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    queued = pipeline_run(phase=PipelinePhase.QUEUED)
    storage.write_run(queued)
    storage.write_raw(raw_snapshot("raw-1"))
    fetching = replace(queued, phase=PipelinePhase.FETCHING)
    storage.write_run(fetching, expected_phase=PipelinePhase.QUEUED)
    raw_saved = replace(fetching, phase=PipelinePhase.RAW_SAVED)
    storage.write_run(raw_saved, expected_phase=PipelinePhase.FETCHING)
    verifying = replace(raw_saved, phase=PipelinePhase.VERIFYING)
    storage.write_run(verifying, expected_phase=PipelinePhase.RAW_SAVED)
    storage.write_evidence(evidence_snapshot("raw-1"))

    with pytest.raises(ValueError, match="snapshot IDs"):
        storage.write_run(
            replace(verifying, phase=PipelinePhase.EVIDENCE_SAVED),
            expected_phase=PipelinePhase.VERIFYING,
        )


def test_naive_datetimes_are_rejected_before_persistence(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    naive = NOW.replace(tzinfo=None)

    with pytest.raises(ValueError, match="UTC"):
        storage.write_raw(RawSnapshot("raw-1", naive, ()))


def test_corrupt_existing_run_fails_closed_instead_of_being_overwritten(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.runs_root.mkdir(parents=True)
    (storage.runs_root / "run-1.json").write_text('{"schema_version":2}', encoding="utf-8")

    with pytest.raises(OSError, match="corrupt"):
        storage.write_run(pipeline_run(phase=PipelinePhase.QUEUED))


def test_redacted_error_removes_unix_unc_state_and_arbitrary_headers(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    run = replace(
        pipeline_run(phase=PipelinePhase.QUEUED),
        redacted_error="X-Session: abc state=secret /srv/private \\server\\share https://host.test/?state=secret",
    )

    storage.write_run(run)

    error = storage.load_run("run-1").redacted_error
    assert all(value not in error for value in ("abc", "secret", "/srv/private", "\\server\\share", "host.test"))


def test_recovery_fences_the_stale_private_writer_without_exposing_a_token(tmp_path):
    current_time = [NOW]
    storage = NewsPipelineStorage(tmp_path, now=lambda: current_time[0])
    queued = pipeline_run(phase=PipelinePhase.QUEUED)
    storage.write_run(queued)
    current_time[0] = NOW + timedelta(seconds=31)
    restarted = NewsPipelineStorage(tmp_path, now=lambda: current_time[0])
    restarted.recover_incomplete_runs()

    with pytest.raises(ValueError, match="authority"):
        storage.write_run(
            replace(queued, phase=PipelinePhase.FETCHING),
            expected_phase=PipelinePhase.QUEUED,
        )


def test_public_pipeline_run_and_storage_mutators_expose_no_authority_inputs():
    assert "owner_id" not in PipelineRun.__dataclass_fields__
    assert "lease_expires_at" not in PipelineRun.__dataclass_fields__
    assert "authority" not in inspect.signature(NewsPipelineStorage.write_run).parameters
    assert "authority" not in inspect.signature(NewsPipelineStorage.write_raw).parameters
    assert "authority" not in inspect.signature(NewsPipelineStorage.write_evidence).parameters
    assert "authority" not in inspect.signature(NewsPipelineStorage.publish_trusted).parameters
    assert "owner_id" not in inspect.signature(NewsPipelineStorage).parameters
    assert "recovery_owner_id" not in inspect.signature(NewsPipelineStorage.recover_incomplete_runs).parameters
    assert not hasattr(NewsPipelineStorage, "claim_authority")


def test_evidence_and_trusted_artifacts_are_immutable_and_pointer_cannot_rewind(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    for raw_snapshot_id in ("old", "new"):
        storage.write_raw(raw_snapshot(raw_snapshot_id))
        storage.write_evidence(evidence_snapshot(raw_snapshot_id))
        storage.publish_trusted(trusted_snapshot(raw_snapshot_id))

    with pytest.raises(ValueError, match="evidence snapshot identity is immutable"):
        storage.write_evidence(replace(evidence_snapshot("old"), generated_at=NOW + timedelta(seconds=1)))
    with pytest.raises(ValueError, match="trusted pointer cannot move backwards"):
        storage.publish_trusted(trusted_snapshot("old"))

    assert storage.load_current_trusted().raw_snapshot_id == "new"


@pytest.mark.parametrize("artifact", ["raw", "evidence", "trusted", "pointer"])
def test_corrupt_artifacts_fail_closed_and_are_never_overwritten(tmp_path, artifact):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    storage.write_evidence(evidence_snapshot("raw-1"))
    if artifact in {"trusted", "pointer"}:
        storage.publish_trusted(trusted_snapshot("raw-1"))
    if artifact == "pointer":
        storage.write_raw(raw_snapshot("raw-2"))
        storage.write_evidence(evidence_snapshot("raw-2"))
    paths = {
        "raw": storage.raw_root / "raw-1.json",
        "evidence": storage.evidence_root / "raw-1.json",
        "trusted": storage.trusted_root / "raw-1.json",
        "pointer": storage.current_pointer_path,
    }
    path = paths[artifact]
    path.write_text('{"schema_version":999}', encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(OSError, match="corrupt"):
        if artifact == "raw":
            storage.write_raw(raw_snapshot("raw-1"))
        elif artifact == "evidence":
            storage.write_evidence(evidence_snapshot("raw-1"))
        else:
            storage.publish_trusted(trusted_snapshot("raw-1" if artifact == "trusted" else "raw-2"))

    assert path.read_bytes() == before


def test_persisted_diagnostics_are_known_codes_only(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_run(replace(pipeline_run(phase=PipelinePhase.QUEUED), redacted_error="hunter2"))

    assert storage.load_run("run-1").redacted_error == "pipeline_error"


def test_failed_atomic_write_cleans_only_the_temp_file_it_created(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    real_replace = os.replace
    created = []

    def fail_replace(source, destination):
        created.append(Path(source))
        raise OSError("replace failed")

    monkeypatch.setattr(storage, "_replace_durable", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        storage.write_raw(raw_snapshot("raw-1"))

    assert created and not created[0].exists()


def test_real_subprocess_writers_use_cross_process_cas(tmp_path):
    repo_backend = Path(__file__).resolve().parents[1]
    script = """
import sys
from datetime import datetime, timezone
from news_pipeline import NewsPipelineStorage, PipelineCounts, PipelinePhase, PipelineRun
root, run_id = sys.argv[1:]
now = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
run = PipelineRun(run_id, 'raw-1', None, None, PipelinePhase.QUEUED, PipelineCounts(), now, now, None, None)
try:
    NewsPipelineStorage(root).write_run(run)
except Exception as error:
    print(type(error).__name__)
    raise
"""
    environment = {**os.environ, "PYTHONPATH": str(repo_backend)}
    first = subprocess.run([sys.executable, "-c", script, str(tmp_path), "run-1"], env=environment, capture_output=True, text=True)
    second = subprocess.run([sys.executable, "-c", script, str(tmp_path), "run-2"], env=environment, capture_output=True, text=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert "authority is active" in second.stderr


def test_real_subprocess_recovery_interrupts_stale_run(tmp_path):
    storage = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    storage.write_run(pipeline_run(phase=PipelinePhase.QUEUED))
    authority = json.loads(storage._authority_path.read_text(encoding="utf-8"))
    authority["expires_at"] = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    storage._authority_path.write_text(json.dumps(authority), encoding="utf-8")
    repo_backend = Path(__file__).resolve().parents[1]
    script = """
import sys
from news_pipeline import NewsPipelineStorage
print(NewsPipelineStorage(sys.argv[1]).recover_incomplete_runs())
"""
    environment = {**os.environ, "PYTHONPATH": str(repo_backend)}
    recovered = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=environment, capture_output=True, text=True)

    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout.strip() == "1"
    assert NewsPipelineStorage(tmp_path).load_run("run-1").phase is PipelinePhase.INTERRUPTED


def test_real_subprocess_phase_cas_cannot_overwrite_changed_state(tmp_path):
    storage = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    storage.write_run(pipeline_run(phase=PipelinePhase.QUEUED))
    authority = json.loads(storage._authority_path.read_text(encoding="utf-8"))
    authority["expires_at"] = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    storage._authority_path.write_text(json.dumps(authority), encoding="utf-8")
    repo_backend = Path(__file__).resolve().parents[1]
    script = """
import sys
from datetime import datetime, timezone
from news_pipeline import NewsPipelineStorage, PipelineCounts, PipelinePhase, PipelineRun
now = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
run = PipelineRun('run-1', 'raw-1', None, None, PipelinePhase.FETCHING, PipelineCounts(), now, now, None, 'old')
NewsPipelineStorage(sys.argv[1]).write_run(run, expected_phase=PipelinePhase.FETCHING)
"""
    environment = {**os.environ, "PYTHONPATH": str(repo_backend)}
    attempted = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=environment, capture_output=True, text=True)

    assert attempted.returncode != 0
    assert "expected current phase does not match" in attempted.stderr
    assert NewsPipelineStorage(tmp_path).load_run("run-1").phase is PipelinePhase.QUEUED
