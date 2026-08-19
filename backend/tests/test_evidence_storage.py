from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from evidence_verification.models import (
    EvidenceEvent,
    EvidenceItem,
    EvidenceSnapshot,
    FieldVerificationStatus,
    KeyField,
    SourceRole,
    StatusTransition,
    VerificationStatus,
)
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

    monkeypatch.setattr("evidence_verification.storage._replace_durable", record_replace)

    storage.publish(snapshot)

    assert [destination.name for _, destination in replacements] == ["current.json", "last-refresh.json"]
    assert storage.load_current() == snapshot
    assert not list(storage.root.glob("*.tmp"))


def test_evidence_atomic_failure_cleans_only_its_own_temp_identity(monkeypatch, tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    snapshot = EvidenceSnapshot(snapshot_id="a" * 20, generated_at=NOW, events=())
    victim = tmp_path / "victim.json"
    victim.write_text("must survive", encoding="utf-8")
    real_replace = os.replace
    swapped = []

    def swap_then_fail(source, _destination):
        source_path = Path(source)
        source_path.unlink()
        real_replace(victim, source_path)
        swapped.append(source_path)
        raise OSError("replace failed")

    monkeypatch.setattr("evidence_verification.storage._replace_durable", swap_then_fail)

    with pytest.raises(OSError, match="replace failed"):
        storage.publish(snapshot)

    assert swapped[0].read_text(encoding="utf-8") == "must survive"


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


def rich_evidence_snapshot() -> EvidenceSnapshot:
    item = EvidenceItem(
        evidence_id="official-1",
        content_source="交易所",
        collector_source="交易所",
        canonical_url="https://example.test/notice/1",
        published_at=NOW,
        source_role=SourceRole.PRIMARY,
        origin_cluster="official",
        supports_claim=True,
        supports_fields=("amount",),
        is_official=True,
        title="公告",
        excerpt="必要摘录",
    )
    field = KeyField(
        field_name="amount",
        raw_value="12亿元",
        normalized_value="1200000000",
        verification_status=FieldVerificationStatus.VERIFIED,
        evidence_ids=("official-1",),
        reason="官方公告支持",
    )
    event = EvidenceEvent(
        event_id="event-1",
        title="建设算力中心",
        summary="建设算力中心",
        category="company",
        related_tags=(("company-1", "星河科技"),),
        published_at=NOW,
        core_claim="建设算力中心",
        verification_status=VerificationStatus.VERIFIED,
        verification_reason="官方公告支持",
        verified_at=NOW,
        evidence_as_of=NOW,
        key_fields=(field,),
        primary_evidence=(item,),
        status_history=(StatusTransition(None, VerificationStatus.VERIFIED, NOW, "官方公告支持"),),
    )
    return EvidenceSnapshot(
        snapshot_id="evidence-raw-1",
        raw_snapshot_id="raw-1",
        generated_at=NOW,
        events=(event,),
        recovery_metadata={"source": "deterministic"},
    )


@pytest.mark.parametrize(
    ("selector", "field"),
    [
        (lambda doc: doc["events"][0], "unexpected_event"),
        (lambda doc: doc["events"][0]["related_tags"][0], "unexpected_tag"),
        (lambda doc: doc["events"][0]["key_fields"][0], "unexpected_field"),
        (lambda doc: doc["events"][0]["primary_evidence"][0], "unexpected_evidence"),
        (lambda doc: doc["events"][0]["status_history"][0], "unexpected_transition"),
    ],
)
def test_evidence_parser_rejects_unknown_nested_fields(selector, field):
    document = snapshot_document(rich_evidence_snapshot())
    selector(document)[field] = "forged"

    with pytest.raises(ValueError, match="schema"):
        evidence_snapshot_from_document(document)


def test_evidence_parser_never_drops_or_coerces_invalid_nested_rows():
    document = snapshot_document(rich_evidence_snapshot())
    document["events"].append("forged")

    with pytest.raises(ValueError, match="event"):
        evidence_snapshot_from_document(document)


def test_evidence_writer_reader_preserves_metadata_and_aware_datetimes_exactly():
    snapshot = rich_evidence_snapshot()

    assert evidence_snapshot_from_document(snapshot_document(snapshot)) == snapshot


def test_evidence_writer_rejects_naive_datetimes_before_persistence(tmp_path):
    snapshot = EvidenceSnapshot(
        snapshot_id="evidence-raw-1",
        raw_snapshot_id="raw-1",
        generated_at=NOW.replace(tzinfo=None),
        events=(),
    )
    storage = EvidenceStorage(root=tmp_path / "evidence")

    with pytest.raises(ValueError, match="timezone"):
        storage.publish(snapshot)
    assert not storage.current_path.exists()


@pytest.mark.parametrize("field", ["verified_at", "evidence_as_of"])
def test_evidence_writer_rejects_naive_nested_event_datetimes(field):
    snapshot = rich_evidence_snapshot()
    event = snapshot.events[0]
    values = {name: getattr(event, name) for name in event.__dataclass_fields__}
    values[field] = NOW.replace(tzinfo=None)
    invalid = EvidenceSnapshot(
        snapshot_id=snapshot.snapshot_id,
        raw_snapshot_id=snapshot.raw_snapshot_id,
        generated_at=snapshot.generated_at,
        events=(EvidenceEvent(**values),),
        recovery_metadata=dict(snapshot.recovery_metadata),
    )

    with pytest.raises(ValueError, match="timezone"):
        snapshot_document(invalid)


def test_evidence_writer_rejects_naive_transition_datetime():
    snapshot = rich_evidence_snapshot()
    event = snapshot.events[0]
    transition = event.status_history[0]
    invalid_transition = StatusTransition(
        transition.from_status,
        transition.to_status,
        transition.changed_at.replace(tzinfo=None),
        transition.reason,
    )
    values = {name: getattr(event, name) for name in event.__dataclass_fields__}
    values["status_history"] = (invalid_transition,)
    invalid = EvidenceSnapshot(
        snapshot_id=snapshot.snapshot_id,
        raw_snapshot_id=snapshot.raw_snapshot_id,
        generated_at=snapshot.generated_at,
        events=(EvidenceEvent(**values),),
        recovery_metadata=dict(snapshot.recovery_metadata),
    )

    with pytest.raises(ValueError, match="timezone"):
        snapshot_document(invalid)
