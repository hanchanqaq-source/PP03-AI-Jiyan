from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from evidence_verification.models import (
    EvidenceEvent,
    EvidenceSnapshot,
    FieldVerificationStatus,
    KeyField,
    VerificationStatus,
)
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
    event = EvidenceEvent(
        event_id="a" * 20,
        title="交易所公告：星河科技建设存储算力中心",
        summary="星河科技披露建设存储算力中心。",
        category="company",
        related_tags=(),
        published_at=NOW,
        core_claim="星河科技建设存储算力中心",
        verification_status=VerificationStatus.VERIFIED,
        verification_reason="官方公告支持",
        verified_at=NOW,
        evidence_as_of=NOW,
    )
    return EvidenceSnapshot(
        snapshot_id=f"evidence-{raw_snapshot_id}",
        raw_snapshot_id=raw_snapshot_id,
        generated_at=NOW,
        events=(event,),
    )


def trusted_snapshot(raw_snapshot_id: str) -> TrustedSnapshot:
    return TrustedSnapshot(
        raw_snapshot_id=raw_snapshot_id,
        published_at=NOW,
        events=(canonical_market_event(verification_status="verified"),),
    )


def canonical_market_event(*, verification_status: str | None = None) -> dict[str, object]:
    return {
        "event_id": "a" * 20,
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
            "title": "公告",
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
        "verification_status": verification_status,
        "verification_reason": "官方公告支持" if verification_status else None,
        "verified_at": NOW.isoformat() if verification_status else None,
        "verified_key_fields": [],
    }


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
    storage.close()

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
    with pytest.raises(ValueError, match="writer is active"):
        other.recover_incomplete_runs()
    assert other.load_run("run-1").phase is PipelinePhase.QUEUED

    active.close()
    stale = NewsPipelineStorage(tmp_path, now=lambda: NOW + timedelta(seconds=31))
    stale.recover_incomplete_runs()
    assert stale.load_run("run-1").phase is PipelinePhase.INTERRUPTED


def test_second_storage_instance_cannot_overwrite_an_active_private_writer(tmp_path):
    writer = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    queued = pipeline_run(phase=PipelinePhase.QUEUED)
    writer.write_run(queued)

    other = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    with pytest.raises(ValueError, match="writer is active"):
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

    storage.close()
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
    assert storage.load_trusted("new") is None


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

    with pytest.raises(OSError, match="^storage_error$"):
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
    storage.close()
    current_time[0] = NOW + timedelta(seconds=31)
    restarted = NewsPipelineStorage(tmp_path, now=lambda: current_time[0])
    restarted.recover_incomplete_runs()

    with pytest.raises(ValueError, match="closed"):
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
    with pytest.raises(OSError, match="^storage_error$"):
        storage.write_raw(raw_snapshot("raw-1"))

    assert created and not created[0].exists()


def test_real_subprocess_writers_use_cross_process_cas(tmp_path):
    repo_backend = Path(__file__).resolve().parents[1]
    script = """
import sys, time
from pathlib import Path
from datetime import datetime, timezone
from news_pipeline import NewsPipelineStorage, PipelineCounts, PipelinePhase, PipelineRun
root, run_id, barrier = sys.argv[1:]
now = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
run = PipelineRun(run_id, 'raw-1', None, None, PipelinePhase.QUEUED, PipelineCounts(), now, now, None, None)
barrier = Path(barrier)
(barrier / f'{run_id}.ready').write_text('ready')
while not (barrier / 'go').exists():
    time.sleep(0.005)
try:
    storage = NewsPipelineStorage(root)
    storage.write_run(run)
    print('won', flush=True)
    time.sleep(0.5)
except Exception as error:
    print(type(error).__name__, flush=True)
    raise
"""
    environment = {**os.environ, "PYTHONPATH": str(repo_backend)}
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    commands = [[sys.executable, "-c", script, str(tmp_path), run_id, str(barrier)] for run_id in ("run-1", "run-2")]
    processes = [subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
    deadline = __import__("time").monotonic() + 5
    while len(list(barrier.glob("*.ready"))) < 2 and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.01)
    assert len(list(barrier.glob("*.ready"))) == 2
    (barrier / "go").write_text("go")
    results = [process.communicate(timeout=10) + (process.returncode,) for process in processes]

    assert sorted(result[2] for result in results) == [0, 1]
    assert sum("won" in result[0] for result in results) == 1
    assert sum("pipeline writer is active" in result[1] for result in results) == 1


def test_real_subprocess_recovery_interrupts_stale_run(tmp_path):
    repo_backend = Path(__file__).resolve().parents[1]
    ready = tmp_path / "holder.ready"
    holder_script = """
import sys, time
from pathlib import Path
from datetime import datetime, timezone
from news_pipeline import NewsPipelineStorage, PipelineCounts, PipelinePhase, PipelineRun
root, ready = sys.argv[1:]
now = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
storage = NewsPipelineStorage(root)
storage.write_run(PipelineRun('run-1', 'raw-1', None, None, PipelinePhase.QUEUED, PipelineCounts(), now, now, None, None))
Path(ready).write_text('ready')
while True:
    time.sleep(0.05)
"""
    recover_script = """
import sys
from news_pipeline import NewsPipelineStorage
print(NewsPipelineStorage(sys.argv[1]).recover_incomplete_runs())
"""
    environment = {**os.environ, "PYTHONPATH": str(repo_backend)}
    holder = subprocess.Popen([sys.executable, "-c", holder_script, str(tmp_path), str(ready)], env=environment)
    deadline = __import__("time").monotonic() + 5
    while not ready.exists() and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.01)
    assert ready.exists()
    racing = subprocess.run([sys.executable, "-c", recover_script, str(tmp_path)], env=environment, capture_output=True, text=True)
    assert racing.returncode != 0
    assert "pipeline writer is active" in racing.stderr
    holder.terminate()
    holder.wait(timeout=10)
    recovered = subprocess.run([sys.executable, "-c", recover_script, str(tmp_path)], env=environment, capture_output=True, text=True)

    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout.strip() == "1"
    assert NewsPipelineStorage(tmp_path).load_run("run-1").phase is PipelinePhase.INTERRUPTED


def test_real_subprocess_phase_cas_cannot_overwrite_changed_state(tmp_path):
    storage = NewsPipelineStorage(tmp_path, now=lambda: NOW)
    storage.write_run(pipeline_run(phase=PipelinePhase.QUEUED))
    storage.close()
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


def test_writer_capability_is_not_persisted_as_a_bearer_token(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_run(pipeline_run(phase=PipelinePhase.QUEUED))

    legacy_authority = tmp_path / ".news-pipeline-authority.json"
    assert not legacy_authority.exists()
    generation = json.loads((tmp_path / ".news-pipeline-generation.json").read_text(encoding="utf-8"))
    assert generation == {"schema_version": 1, "generation": 1}


def test_missing_pointer_with_prior_trusted_artifacts_fails_closed(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("old"))
    storage.write_evidence(evidence_snapshot("old"))
    storage.publish_trusted(trusted_snapshot("old"))
    storage.current_pointer_path.unlink()
    storage.write_raw(raw_snapshot("new"))
    storage.write_evidence(evidence_snapshot("new"))

    with pytest.raises(OSError, match="storage_corrupt"):
        storage.publish_trusted(trusted_snapshot("new"))

    assert storage.load_trusted("old") == trusted_snapshot("old")
    assert not storage.current_pointer_path.exists()


def test_initial_orphan_trusted_artifact_retries_only_with_durable_intent(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    storage.write_evidence(evidence_snapshot("raw-1"))
    original = storage._write_current_pointer

    def fail_pointer(*_args, **_kwargs):
        raise OSError("storage_error")

    monkeypatch.setattr(storage, "_write_current_pointer", fail_pointer)
    with pytest.raises(OSError, match="storage_error"):
        storage.publish_trusted(trusted_snapshot("raw-1"))
    monkeypatch.setattr(storage, "_write_current_pointer", original)

    with pytest.raises(OSError, match="storage_corrupt"):
        storage.load_current_trusted()
    storage.publish_trusted(trusted_snapshot("raw-1"))
    assert storage.load_current_trusted() == trusted_snapshot("raw-1")


def test_loaded_run_rejects_non_allowlisted_redacted_error(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_run(pipeline_run(phase=PipelinePhase.QUEUED))
    path = storage.runs_root / "run-1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["redacted_error"] = "Authorization: Bearer secret C:\\private\\file"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert storage.load_run("run-1") is None


def test_raw_and_trusted_events_use_exact_canonical_schema(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    raw_event = canonical_market_event()
    raw = RawSnapshot("raw-1", NOW, (raw_event,))
    storage.write_raw(raw)
    assert storage.load_raw("raw-1") == raw

    forged = dict(raw_event)
    forged["future_field"] = "not-v1"
    with pytest.raises(ValueError, match="raw event schema"):
        storage.write_raw(RawSnapshot("raw-2", NOW, (forged,)))

    storage.write_evidence(evidence_snapshot("raw-1"))
    trusted_event = canonical_market_event(verification_status="verified")
    trusted = TrustedSnapshot("raw-1", NOW, (trusted_event,))
    storage.publish_trusted(trusted)
    assert storage.load_current_trusted() == trusted


def test_canonical_public_timestamps_preserve_aware_source_offsets(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    event = canonical_market_event()
    offset_time = "2026-08-20T16:00:00+08:00"
    event["published_at_first"] = offset_time
    event["published_at_latest"] = offset_time
    source = dict(event["sources"][0])
    source["published_at"] = offset_time
    source["fetched_at"] = offset_time
    event["sources"] = [source]
    snapshot = RawSnapshot("raw-offset", NOW, (event,))

    storage.write_raw(snapshot)

    assert storage.load_raw("raw-offset") == snapshot


def test_trusted_storage_rejects_untrusted_and_unknown_nested_event_fields(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    for raw_id in ("raw-1", "raw-2"):
        storage.write_raw(raw_snapshot(raw_id))
        storage.write_evidence(evidence_snapshot(raw_id))

    with pytest.raises(ValueError, match="trusted verification_status"):
        storage.publish_trusted(TrustedSnapshot("raw-1", NOW, (canonical_market_event(),)))

    forged = canonical_market_event(verification_status="verified")
    forged_source = dict(forged["sources"][0])
    forged_source["future_field"] = "not-v1"
    forged["sources"] = [forged_source]
    with pytest.raises(ValueError, match="source schema"):
        storage.publish_trusted(TrustedSnapshot("raw-2", NOW, (forged,)))


def test_tampered_raw_nested_schema_fails_closed_on_load(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(RawSnapshot("raw-1", NOW, (canonical_market_event(),)))
    path = storage.raw_root / "raw-1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["items"][0]["sources"][0]["future_field"] = "not-v1"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert storage.load_raw("raw-1") is None


def test_atomic_writer_closes_descriptor_when_fstat_fails_and_redacts_oserror(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    real_fstat = os.fstat
    observed: list[int] = []

    def fail_fstat(descriptor: int):
        observed.append(descriptor)
        raise OSError("C:\\private\\leak")

    with monkeypatch.context() as scoped:
        scoped.setattr("news_pipeline.storage.os.fstat", fail_fstat)
        with pytest.raises(OSError, match="^storage_error$"):
            storage.write_raw(raw_snapshot("raw-1"))

    assert observed
    with pytest.raises(OSError):
        real_fstat(observed[0])


def test_pipeline_reader_closes_descriptor_when_fstat_fails(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    real_fstat = os.fstat
    observed: list[int] = []

    def fail_fstat(descriptor: int):
        observed.append(descriptor)
        raise OSError("C:\\private\\read")

    with monkeypatch.context() as scoped:
        scoped.setattr("news_pipeline.storage.os.fstat", fail_fstat)
        assert storage.load_raw("raw-1") is None

    assert observed
    with pytest.raises(OSError):
        real_fstat(observed[-1])


def test_atomic_writer_does_not_double_close_a_reused_descriptor(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("survive", encoding="utf-8")
    reused: list[int] = []

    def fail_after_reuse(_source, _destination):
        reused.append(os.open(victim, os.O_RDONLY))
        raise OSError("D:\\private\\replace")

    monkeypatch.setattr(storage, "_replace_durable", fail_after_reuse)
    try:
        with pytest.raises(OSError, match="^storage_error$"):
            storage.write_raw(raw_snapshot("raw-1"))
        assert os.fstat(reused[0]).st_size == len("survive")
    finally:
        if reused:
            try:
                os.close(reused[0])
            except OSError:
                pass


def test_recovery_does_not_follow_symlinked_run_outside_storage_root(tmp_path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}.json"
    outside.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "outside",
        "raw_snapshot_id": "raw-1",
        "evidence_snapshot_id": None,
        "trusted_snapshot_id": None,
        "phase": "queued",
        "counts": {name: 0 for name in PipelineCounts.__dataclass_fields__},
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "redacted_error": None,
        "displayed_trusted_snapshot_id": None,
    }), encoding="utf-8")
    storage = NewsPipelineStorage(tmp_path)
    storage.runs_root.mkdir(parents=True)
    link = storage.runs_root / "outside.json"
    try:
        link.symlink_to(outside)
    except OSError:
        outside.unlink()
        pytest.skip("symlink creation is unavailable")

    try:
        assert storage.recover_incomplete_runs() == 0
        assert link.is_symlink()
        assert json.loads(outside.read_text(encoding="utf-8"))["phase"] == "queued"
    finally:
        link.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


def test_trusted_publish_rejects_empty_projection_before_any_publication_mutation(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    storage.write_evidence(evidence_snapshot("raw-1"))

    with pytest.raises(ValueError, match="trusted event"):
        storage.publish_trusted(TrustedSnapshot("raw-1", NOW, ()))

    assert not storage._publication_intent_path.exists()
    assert not storage._publication_complete_path.exists()
    assert not storage.current_pointer_path.exists()
    assert not storage.trusted_root.exists()


@pytest.mark.parametrize("evidence_status", [VerificationStatus.UNVERIFIED, VerificationStatus.CONFLICTING])
def test_trusted_publish_binds_event_id_and_status_to_same_evidence_snapshot(tmp_path, evidence_status):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    source = evidence_snapshot("raw-1")
    evidence_event = replace(source.events[0], verification_status=evidence_status)
    storage.write_evidence(replace(source, events=(evidence_event,)))
    trusted = trusted_snapshot("raw-1")

    with pytest.raises(ValueError, match="evidence"):
        storage.publish_trusted(trusted)

    assert not storage._publication_intent_path.exists()
    assert not storage.current_pointer_path.exists()


def test_trusted_publish_rejects_an_event_id_absent_from_same_evidence_snapshot(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    source = evidence_snapshot("raw-1")
    storage.write_evidence(replace(source, events=(replace(source.events[0], event_id="evidence-only"),)))

    with pytest.raises(ValueError, match="evidence"):
        storage.publish_trusted(trusted_snapshot("raw-1"))

    assert not storage._publication_intent_path.exists()
    assert not storage.current_pointer_path.exists()


def test_trusted_verified_key_fields_must_match_approved_evidence_fields(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    source = evidence_snapshot("raw-1")
    pending = KeyField(
        field_name="amount",
        raw_value="12亿元",
        normalized_value="1200000000",
        verification_status=FieldVerificationStatus.UNVERIFIED,
        evidence_ids=("official-1",),
        reason="尚无证据",
    )
    storage.write_evidence(replace(source, events=(replace(source.events[0], key_fields=(pending,)),)))
    event = canonical_market_event(verification_status="verified")
    event["verified_key_fields"] = [{
        "field_name": "amount",
        "raw_value": "12亿元",
        "normalized_value": "1200000000",
        "verification_status": "verified",
        "evidence_ids": ["official-1"],
        "reason": "self-labelled",
    }]

    with pytest.raises(ValueError, match="key field"):
        storage.publish_trusted(TrustedSnapshot("raw-1", NOW, (event,)))

    assert not storage._publication_intent_path.exists()
    assert not storage.current_pointer_path.exists()


def test_trusted_verified_key_fields_accept_exact_approved_evidence_projection(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    source = evidence_snapshot("raw-1")
    approved = KeyField(
        field_name="amount",
        raw_value="12亿元",
        normalized_value="1200000000",
        verification_status=FieldVerificationStatus.VERIFIED,
        evidence_ids=("official-1",),
        reason="官方公告支持",
    )
    storage.write_evidence(replace(source, events=(replace(source.events[0], key_fields=(approved,)),)))
    event = canonical_market_event(verification_status="verified")
    event["verified_key_fields"] = [{
        "field_name": approved.field_name,
        "raw_value": approved.raw_value,
        "normalized_value": approved.normalized_value,
        "verification_status": approved.verification_status.value,
        "evidence_ids": list(approved.evidence_ids),
        "reason": approved.reason,
    }]
    snapshot = TrustedSnapshot("raw-1", NOW, (event,))

    storage.publish_trusted(snapshot)

    assert storage.load_current_trusted() == snapshot


def test_prepared_record_failure_cannot_switch_the_current_pointer(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    for raw_id in ("old", "new"):
        storage.write_raw(raw_snapshot(raw_id))
        storage.write_evidence(evidence_snapshot(raw_id))
    storage.publish_trusted(trusted_snapshot("old"))

    monkeypatch.setattr(
        storage,
        "_write_publication_complete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage_error")),
    )

    with pytest.raises(OSError, match="storage_error"):
        storage.publish_trusted(trusted_snapshot("new"))

    assert storage.load_current_trusted() == trusted_snapshot("old")


def test_orphan_retry_requires_exact_matching_publication_intent(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    for raw_id in ("old", "new"):
        storage.write_raw(raw_snapshot(raw_id))
        storage.write_evidence(evidence_snapshot(raw_id))
    storage.publish_trusted(trusted_snapshot("old"))
    original = storage._write_current_pointer
    monkeypatch.setattr(
        storage,
        "_write_current_pointer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage_error")),
    )
    with pytest.raises(OSError, match="storage_error"):
        storage.publish_trusted(trusted_snapshot("new"))
    monkeypatch.setattr(storage, "_write_current_pointer", original)
    intent = json.loads(storage._publication_intent_path.read_text(encoding="utf-8"))
    intent["raw_snapshot_id"] = "different"
    storage._publication_intent_path.write_text(json.dumps(intent), encoding="utf-8")

    with pytest.raises((OSError, ValueError)):
        storage.publish_trusted(trusted_snapshot("new"))

    assert storage.load_current_trusted() == trusted_snapshot("old")


def test_orphan_retry_with_exact_intent_publishes_once(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    for raw_id in ("old", "new"):
        storage.write_raw(raw_snapshot(raw_id))
        storage.write_evidence(evidence_snapshot(raw_id))
    storage.publish_trusted(trusted_snapshot("old"))
    original = storage._write_current_pointer
    monkeypatch.setattr(
        storage,
        "_write_current_pointer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage_error")),
    )
    with pytest.raises(OSError, match="storage_error"):
        storage.publish_trusted(trusted_snapshot("new"))
    monkeypatch.setattr(storage, "_write_current_pointer", original)

    storage.publish_trusted(trusted_snapshot("new"))
    storage.publish_trusted(trusted_snapshot("new"))

    assert storage.load_current_trusted() == trusted_snapshot("new")
    pointer = json.loads(storage.current_pointer_path.read_text(encoding="utf-8"))
    assert pointer["generation"] == 2


def test_initial_orphan_retry_resumes_when_prepared_record_was_interrupted(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    storage.write_evidence(evidence_snapshot("raw-1"))
    original = storage._write_publication_complete
    monkeypatch.setattr(
        storage,
        "_write_publication_complete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage_error")),
    )
    with pytest.raises(OSError, match="storage_error"):
        storage.publish_trusted(trusted_snapshot("raw-1"))
    monkeypatch.setattr(storage, "_write_publication_complete", original)

    with pytest.raises(OSError, match="storage_corrupt"):
        storage.load_current_trusted()
    storage.publish_trusted(trusted_snapshot("raw-1"))

    assert storage.load_current_trusted() == trusted_snapshot("raw-1")


def test_trusted_publish_rejects_duplicate_verified_field_identity(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    source = evidence_snapshot("raw-1")
    approved = KeyField(
        field_name="amount",
        raw_value="12亿元",
        normalized_value="1200000000",
        verification_status=FieldVerificationStatus.VERIFIED,
        evidence_ids=("official-1",),
        reason="官方公告支持",
    )
    storage.write_evidence(replace(source, events=(replace(source.events[0], key_fields=(approved,)),)))
    event = canonical_market_event(verification_status="verified")
    field = {
        "field_name": approved.field_name,
        "raw_value": approved.raw_value,
        "normalized_value": approved.normalized_value,
        "verification_status": approved.verification_status.value,
        "evidence_ids": list(approved.evidence_ids),
        "reason": approved.reason,
    }
    event["verified_key_fields"] = [field, dict(field)]

    with pytest.raises(ValueError, match="key field"):
        storage.publish_trusted(TrustedSnapshot("raw-1", NOW, (event,)))

    assert not storage.current_pointer_path.exists()


def test_pointer_directory_sync_failure_occurs_before_the_final_replace(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    for raw_id in ("old", "new"):
        storage.write_raw(raw_snapshot(raw_id))
        storage.write_evidence(evidence_snapshot(raw_id))
    storage.publish_trusted(trusted_snapshot("old"))
    old_pointer = storage.current_pointer_path.read_bytes()
    real_sync = storage._sync_dir
    real_replace = storage._replace_final_commit

    def fail_only_pointer_parent(path):
        if path == storage.current_pointer_path.parent:
            raise OSError("storage_error")
        real_sync(path)

    def final_replace_with_failed_precommit(source, destination):
        monkeypatch.setattr(NewsPipelineStorage, "_sync_dir", staticmethod(fail_only_pointer_parent))
        try:
            real_replace(source, destination)
        finally:
            monkeypatch.setattr(NewsPipelineStorage, "_sync_dir", staticmethod(real_sync))

    monkeypatch.setattr(storage, "_replace_final_commit", final_replace_with_failed_precommit)

    with pytest.raises(OSError, match="storage_error"):
        storage.publish_trusted(trusted_snapshot("new"))

    assert storage.current_pointer_path.read_bytes() == old_pointer
    assert storage.load_current_trusted() == trusted_snapshot("old")


def test_pipeline_writer_fdopen_failure_never_closes_a_reused_descriptor(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("survive", encoding="utf-8")
    reused: list[int] = []
    real_fdopen = os.fdopen

    def close_reuse_then_fail(descriptor: int, *_args, **_kwargs):
        os.close(descriptor)
        reused.append(os.open(victim, os.O_RDONLY))
        assert reused[-1] == descriptor
        raise OSError("D:\\private\\fdopen")

    with monkeypatch.context() as scoped:
        scoped.setattr("news_pipeline.storage.os.fdopen", close_reuse_then_fail)
        with pytest.raises(OSError, match="^storage_error$"):
            storage.write_raw(raw_snapshot("raw-1"))

    try:
        assert os.fstat(reused[0]).st_size == len("survive")
    finally:
        if reused:
            os.close(reused[0])


def test_pipeline_reader_closes_descriptor_when_fdopen_fails(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    real_fstat = os.fstat
    observed: list[int] = []

    def fail_fdopen(descriptor: int, *_args, **_kwargs):
        observed.append(descriptor)
        raise OSError("C:\\private\\fdopen")

    with monkeypatch.context() as scoped:
        scoped.setattr("news_pipeline.storage.os.fdopen", fail_fdopen)
        assert storage.load_raw("raw-1") is None

    assert observed
    with pytest.raises(OSError):
        real_fstat(observed[-1])


def test_lock_open_rejects_preexisting_symlink_or_hardlink(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b"x")
    lock_path = root / ".news-pipeline.lock"
    try:
        os.link(outside, lock_path)
    except OSError:
        pytest.skip("hard-link creation is unavailable")

    with pytest.raises(OSError, match="storage_error"):
        NewsPipelineStorage(root).load_raw("raw-1")

    assert outside.read_bytes() == b"x"


def test_pipeline_reader_never_traverses_symlinked_artifact_parent(tmp_path):
    root = tmp_path / "store"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    document = {
        "schema_version": 1,
        "raw_snapshot_id": "raw-1",
        "collected_at": NOW.isoformat(),
        "items": [],
    }
    (outside / "raw-1.json").write_text(json.dumps(document), encoding="utf-8")
    try:
        (root / "raw").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    assert NewsPipelineStorage(root).load_raw("raw-1") is None


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork is unavailable")
def test_forked_child_cannot_use_or_close_parent_writer_capability(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("parent-1"))
    child = os.fork()
    if child == 0:
        try:
            try:
                storage.write_raw(raw_snapshot("child"))
            except ValueError:
                storage.close()
                os._exit(0)
            os._exit(2)
        except BaseException:
            os._exit(3)

    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    storage.write_raw(raw_snapshot("parent-2"))
    assert storage.load_raw("parent-2") == raw_snapshot("parent-2")


def test_abrupt_writer_exit_cleans_only_exact_owned_temp_residue(tmp_path):
    script = """
import os, sys
from datetime import datetime, timezone
from news_pipeline.models import RawSnapshot
from news_pipeline.storage import NewsPipelineStorage
root = sys.argv[1]
storage = NewsPipelineStorage(root)
storage._claim_writer_unlocked()
storage._replace_durable = lambda *_args: os._exit(23)
storage.write_raw(RawSnapshot('crashed', datetime(2026, 8, 20, tzinfo=timezone.utc), ()))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    crashed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env, capture_output=True, text=True, timeout=10)
    assert crashed.returncode == 23
    residue = list((tmp_path / "raw").glob(".crashed.json.*.tmp"))
    assert len(residue) == 1
    unknown = tmp_path / "raw" / ".unknown.keep.tmp"
    unknown.write_text("keep", encoding="utf-8")

    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("recovered"))

    assert not residue[0].exists()
    assert unknown.read_text(encoding="utf-8") == "keep"
