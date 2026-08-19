from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from evidence_verification.models import EvidenceEvent, EvidenceSnapshot, StatusTransition, VerificationStatus
from evidence_verification.storage import EvidenceStorage, evidence_snapshot_from_document, snapshot_document


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def evidence_event(*, status: VerificationStatus, history_hours: tuple[int, ...]) -> EvidenceEvent:
    history = []
    previous = None
    sequence = [status] if len(history_hours) == 1 else [VerificationStatus.UNVERIFIED, status]
    for hour, current in zip(history_hours, sequence, strict=True):
        history.append(StatusTransition(previous, current, NOW.replace(hour=hour), f"reason-{current.value}"))
        previous = current
    return EvidenceEvent(
        event_id="a" * 20,
        title="事件",
        summary="摘要",
        category="company",
        related_tags=(),
        published_at=NOW,
        core_claim="事件",
        verification_status=status,
        verification_reason=f"reason-{status.value}",
        verified_at=NOW.replace(hour=history_hours[-1]),
        evidence_as_of=NOW,
        status_history=tuple(history),
    )


def test_default_root_is_isolated_below_vr_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("VR_DATA_DIR", str(tmp_path / "data"))
    assert EvidenceStorage().root == tmp_path / "data" / "evidence-verification" / "v1"


def test_publish_uses_atomic_replace_and_round_trips_snapshot(monkeypatch, tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    snapshot = EvidenceSnapshot(snapshot_id="a" * 20, generated_at=NOW, events=())
    replacements = []
    real_replace = __import__("os").replace

    def record_replace(source, destination):
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr("evidence_verification.storage.os.replace", record_replace)

    storage.publish(snapshot)

    assert [destination.name for _, destination in replacements] == ["current.json", "last-refresh.json"]
    assert storage.load_current() == snapshot
    assert not list(storage.root.glob("*.tmp"))


def test_failure_marker_never_overwrites_last_successful_snapshot(tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    snapshot = EvidenceSnapshot(snapshot_id="a" * 20, generated_at=NOW, events=())
    storage.publish(snapshot)
    before = storage.current_path.read_bytes()

    storage.record_failure("upstream unavailable")

    assert storage.current_path.read_bytes() == before
    marker = json.loads(storage.last_refresh_path.read_text(encoding="utf-8"))
    assert marker == {
        "status": "failed",
        "attempted_at": NOW.isoformat(),
        "last_successful_refresh_at": NOW.isoformat(),
        "error": "upstream unavailable",
    }


def test_status_history_is_append_only_across_publishes(tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    first_event = evidence_event(status=VerificationStatus.UNVERIFIED, history_hours=(12,))
    second_event = evidence_event(status=VerificationStatus.VERIFIED, history_hours=(12, 13))

    storage.publish(EvidenceSnapshot("a" * 20, NOW, (first_event,)))
    storage.publish(EvidenceSnapshot("b" * 20, NOW.replace(hour=13), (second_event,)))

    lines = storage.history_path(NOW).read_text(encoding="utf-8").splitlines()
    documents = [json.loads(line) for line in lines]
    assert [(row["from_status"], row["to_status"]) for row in documents] == [
        (None, "unverified"),
        ("unverified", "verified"),
    ]


def test_history_ledger_failure_after_current_publish_does_not_relabel_completed_snapshot(monkeypatch, tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    snapshot = EvidenceSnapshot(
        "a" * 20,
        NOW,
        (evidence_event(status=VerificationStatus.VERIFIED, history_hours=(12,)),),
    )
    monkeypatch.setattr(storage, "_append_transitions", lambda *_: (_ for _ in ()).throw(OSError("history denied")))

    storage.publish(snapshot)

    assert storage.load_current() == snapshot
    marker = storage.load_last_refresh()
    assert marker["status"] == "completed"
    assert marker["history_status"] == "failed"
    assert marker["history_error"] == "OSError"


def test_legacy_evidence_document_uses_snapshot_id_as_explicit_compatibility_identity(tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    snapshot = EvidenceSnapshot(snapshot_id="legacy-snapshot", generated_at=NOW, events=())
    document = snapshot_document(snapshot)
    document.pop("raw_snapshot_id", None)
    document.pop("recovery_metadata", None)
    storage.root.mkdir(parents=True)
    storage.current_path.write_text(json.dumps(document), encoding="utf-8")

    recovered = storage.load_current()

    assert recovered.raw_snapshot_id == "legacy-snapshot"
    assert recovered.recovery_metadata == {"legacy_identity": True}


def test_evidence_document_preserves_allocated_raw_snapshot_identity():
    snapshot = EvidenceSnapshot(
        snapshot_id="evidence-raw-1",
        raw_snapshot_id="raw-1",
        generated_at=NOW,
        events=(),
    )

    assert snapshot_document(snapshot)["raw_snapshot_id"] == "raw-1"


def test_evidence_parser_rejects_future_schema_and_non_builtin_identity_values():
    snapshot = EvidenceSnapshot(
        snapshot_id="evidence-raw-1",
        raw_snapshot_id="raw-1",
        generated_at=NOW,
        events=(),
    )
    document = snapshot_document(snapshot)

    with pytest.raises(ValueError):
        evidence_snapshot_from_document({**document, "schema_version": 2})

    class HostileString(str):
        def __str__(self):
            raise AssertionError("must not coerce custom values")

    hostile = dict(document)
    hostile["snapshot_id"] = HostileString("evidence-raw-1")
    with pytest.raises(ValueError):
        evidence_snapshot_from_document(hostile)


def test_evidence_storage_rejects_duplicate_key_disk_json_before_legacy_recovery(tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    snapshot = EvidenceSnapshot(snapshot_id="evidence-raw-1", raw_snapshot_id="raw-1", generated_at=NOW, events=())
    document = json.dumps(snapshot_document(snapshot), ensure_ascii=False)
    storage.root.mkdir(parents=True)
    storage.current_path.write_text('{"snapshot_id":"forged",' + document[1:], encoding="utf-8")

    assert storage.load_current() is None
