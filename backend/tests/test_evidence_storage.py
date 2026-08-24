from __future__ import annotations

from dataclasses import replace
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


def repeated_evidence_snapshot(*, event_count: int, summary_size: int) -> EvidenceSnapshot:
    base = evidence_event(status=VerificationStatus.UNVERIFIED, history_hours=(12,))
    return EvidenceSnapshot(
        snapshot_id="s" * 20,
        generated_at=NOW,
        events=tuple(
            replace(base, event_id=f"{index:020d}", summary="x" * summary_size)
            for index in range(event_count)
        ),
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


def test_publish_accepts_canonical_snapshot_when_compact_json_fits_one_mib(tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    snapshot = repeated_evidence_snapshot(event_count=450, summary_size=1_600)
    document = snapshot_document(snapshot)
    compact_payload = (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    pretty_payload = (
        json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    assert len(compact_payload) <= 1_048_576 < len(pretty_payload)

    storage.publish(snapshot)

    assert storage.current_path.read_bytes() == compact_payload
    assert storage.load_current() == snapshot


def test_publish_rejects_snapshot_when_compact_json_exceeds_one_mib(tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    snapshot = repeated_evidence_snapshot(event_count=500, summary_size=1_600)
    compact_payload = (
        json.dumps(
            snapshot_document(snapshot), ensure_ascii=False, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")
    assert len(compact_payload) > 1_048_576

    with pytest.raises(ValueError, match="^evidence document is too large$"):
        storage.publish(snapshot)

    assert not storage.current_path.exists()


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

    with pytest.raises(OSError, match="^storage_error$"):
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


def test_evidence_publication_timestamps_preserve_aware_source_offsets():
    snapshot = rich_evidence_snapshot()
    offset = timezone(__import__("datetime").timedelta(hours=8))
    event = snapshot.events[0]
    item = replace(event.primary_evidence[0], published_at=NOW.astimezone(offset))
    shifted = replace(snapshot, events=(replace(event, published_at=NOW.astimezone(offset), primary_evidence=(item,)),))

    assert evidence_snapshot_from_document(snapshot_document(shifted)) == shifted


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


@pytest.mark.parametrize("field", ["generated_at", "verified_at", "evidence_as_of", "changed_at"])
def test_required_evidence_datetimes_validate_before_any_storage_mutation(tmp_path, field):
    snapshot = rich_evidence_snapshot()
    event = snapshot.events[0]
    if field == "generated_at":
        invalid = replace(snapshot, generated_at=None)
    elif field == "changed_at":
        invalid_transition = replace(event.status_history[0], changed_at=None)
        invalid = replace(snapshot, events=(replace(event, status_history=(invalid_transition,)),))
    else:
        invalid = replace(snapshot, events=(replace(event, **{field: None}),))

    direct = EvidenceStorage(root=tmp_path / "direct")
    with pytest.raises(ValueError, match="timestamp"):
        direct.publish(invalid)
    assert not direct.current_path.exists()
    assert not direct.last_refresh_path.exists()

    pipeline = __import__("news_pipeline.storage", fromlist=["NewsPipelineStorage"]).NewsPipelineStorage(tmp_path / "pipeline")
    with pytest.raises(ValueError, match="timestamp"):
        pipeline.write_evidence(invalid)
    assert not pipeline.evidence_root.exists()
    assert not (pipeline.root / ".news-pipeline-generation.json").exists()


@pytest.mark.parametrize(
    "metadata",
    [
        {"future": "unsupported"},
        {"legacy_identity": "true"},
        {"source": object()},
        {"recovered_at": "not-a-time"},
    ],
)
def test_recovery_metadata_uses_an_explicit_bounded_schema(metadata):
    snapshot = replace(rich_evidence_snapshot(), recovery_metadata=metadata)

    with pytest.raises(ValueError, match="recovery metadata"):
        snapshot_document(snapshot)


def test_evidence_immutable_retry_compares_the_canonical_projected_document(tmp_path):
    snapshot = rich_evidence_snapshot()
    event = snapshot.events[0]
    pending = replace(
        event.key_fields[0],
        raw_value="12亿元",
        verification_status=FieldVerificationStatus.UNVERIFIED,
    )
    long_evidence = replace(event.primary_evidence[0], excerpt="x" * 2_000)
    projected = replace(
        snapshot,
        events=(replace(
            event,
            title="建设算力中心 12亿元",
            summary="项目投资 12亿元",
            key_fields=(pending,),
            primary_evidence=(long_evidence,),
        ),),
    )
    storage = __import__("news_pipeline.storage", fromlist=["NewsPipelineStorage"]).NewsPipelineStorage(tmp_path)
    models = __import__("news_intelligence.models", fromlist=["MarketNewsEvent"])
    pipeline_models = __import__("news_pipeline.models", fromlist=["RawSnapshot"])
    raw_event = models.MarketNewsEvent(
        event_id=projected.events[0].event_id,
        title=projected.events[0].title,
        summary=projected.events[0].summary,
        category=projected.events[0].category,
        published_at_first=projected.events[0].published_at,
        published_at_latest=projected.events[0].published_at,
        sources=[],
        related_tags=[],
        original_links=[],
        data_status="realtime",
        tag_evidence=[],
    ).to_dict()
    storage.write_raw(pipeline_models.RawSnapshot("raw-1", NOW, (raw_event,)))

    storage.write_evidence(projected)
    storage.write_evidence(projected)

    assert storage.load_evidence("raw-1") is not None


def test_evidence_atomic_writer_closes_fstat_failure_and_normalizes_oserror(tmp_path, monkeypatch):
    storage = EvidenceStorage(root=tmp_path / "evidence")
    real_fstat = os.fstat
    observed: list[int] = []

    def fail_fstat(descriptor: int):
        observed.append(descriptor)
        raise OSError("C:\\private\\evidence")

    with monkeypatch.context() as scoped:
        scoped.setattr("evidence_verification.storage.os.fstat", fail_fstat)
        with pytest.raises(OSError, match="^storage_error$"):
            storage.publish(EvidenceSnapshot("snapshot", NOW, ()))

    assert observed
    with pytest.raises(OSError):
        real_fstat(observed[0])


def test_evidence_reader_closes_descriptor_when_fstat_fails(tmp_path, monkeypatch):
    storage = EvidenceStorage(root=tmp_path / "evidence")
    storage.publish(EvidenceSnapshot("snapshot", NOW, ()))
    real_fstat = os.fstat
    observed: list[int] = []

    def fail_fstat(descriptor: int):
        observed.append(descriptor)
        raise OSError("C:\\private\\read")

    with monkeypatch.context() as scoped:
        scoped.setattr("evidence_verification.storage.os.fstat", fail_fstat)
        assert storage.load_current() is None

    assert observed
    with pytest.raises(OSError):
        real_fstat(observed[-1])


def test_evidence_atomic_writer_does_not_double_close_reused_descriptor(tmp_path, monkeypatch):
    storage = EvidenceStorage(root=tmp_path / "evidence")
    victim = tmp_path / "victim.txt"
    victim.write_text("survive", encoding="utf-8")
    reused: list[int] = []

    def fail_after_reuse(_source, _destination):
        reused.append(os.open(victim, os.O_RDONLY))
        raise OSError("D:\\private\\replace")

    monkeypatch.setattr("evidence_verification.storage._replace_durable", fail_after_reuse)
    try:
        with pytest.raises(OSError, match="^storage_error$"):
            storage.publish(EvidenceSnapshot("snapshot", NOW, ()))
        assert os.fstat(reused[0]).st_size == len("survive")
    finally:
        if reused:
            try:
                os.close(reused[0])
            except OSError:
                pass


@pytest.mark.parametrize(
    "invalid_snapshot",
    [
        lambda snapshot: replace(snapshot, events=list(snapshot.events)),
        lambda snapshot: replace(
            snapshot,
            events=(replace(snapshot.events[0], verification_status="verified"),),
        ),
        lambda snapshot: replace(
            snapshot,
            events=(replace(
                snapshot.events[0],
                primary_evidence=(replace(snapshot.events[0].primary_evidence[0], supports_claim=1),),
            ),),
        ),
        lambda snapshot: replace(
            snapshot,
            events=(replace(snapshot.events[0], related_tags=list(snapshot.events[0].related_tags)),),
        ),
        lambda snapshot: replace(snapshot, generated_at="2026-08-20T00:00:00+00:00"),
    ],
    ids=["events-list", "status-string", "bool-integer", "tags-list", "timestamp-string"],
)
def test_evidence_publish_semantically_roundtrips_before_mutating_current(tmp_path, invalid_snapshot):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    valid = rich_evidence_snapshot()
    storage.publish(valid)
    old_current = storage.current_path.read_bytes()
    old_refresh = storage.last_refresh_path.read_bytes()

    with pytest.raises((TypeError, ValueError, AttributeError)):
        storage.publish(invalid_snapshot(valid))

    assert storage.current_path.read_bytes() == old_current
    assert storage.last_refresh_path.read_bytes() == old_refresh
    assert storage.load_current() == valid


def test_evidence_reader_closes_descriptor_when_fdopen_fails(tmp_path, monkeypatch):
    storage = EvidenceStorage(root=tmp_path / "evidence")
    storage.publish(EvidenceSnapshot("snapshot", NOW, ()))
    real_fstat = os.fstat
    observed: list[int] = []

    def fail_fdopen(descriptor: int, *_args, **_kwargs):
        observed.append(descriptor)
        raise OSError("C:\\private\\fdopen")

    with monkeypatch.context() as scoped:
        scoped.setattr("evidence_verification.storage.os.fdopen", fail_fdopen)
        assert storage.load_current() is None

    assert observed
    with pytest.raises(OSError):
        real_fstat(observed[-1])


def test_evidence_reader_rejects_a_symlinked_storage_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    snapshot = rich_evidence_snapshot()
    (outside / "current.json").write_text(
        json.dumps(snapshot_document(snapshot), ensure_ascii=False),
        encoding="utf-8",
    )
    linked_root = tmp_path / "linked"
    try:
        linked_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    assert EvidenceStorage(root=linked_root).load_current() is None


def test_evidence_writer_fdopen_failure_never_closes_a_reused_descriptor(tmp_path, monkeypatch):
    storage = EvidenceStorage(root=tmp_path / "evidence")
    victim = tmp_path / "victim.txt"
    victim.write_text("survive", encoding="utf-8")
    reused: list[int] = []

    def close_reuse_then_fail(descriptor: int, *_args, **_kwargs):
        os.close(descriptor)
        reused.append(os.open(victim, os.O_RDONLY))
        assert reused[-1] == descriptor
        raise OSError("D:\\private\\fdopen")

    with monkeypatch.context() as scoped:
        scoped.setattr("evidence_verification.storage.os.fdopen", close_reuse_then_fail)
        with pytest.raises(OSError, match="^storage_error$"):
            storage.publish(EvidenceSnapshot("snapshot", NOW, ()))

    try:
        assert os.fstat(reused[0]).st_size == len("survive")
    finally:
        if reused:
            os.close(reused[0])


def test_transition_append_closes_descriptor_once_when_fstat_fails(tmp_path, monkeypatch):
    storage = EvidenceStorage(root=tmp_path / "evidence")
    snapshot = EvidenceSnapshot(
        "snapshot",
        NOW,
        (evidence_event(status=VerificationStatus.UNVERIFIED, history_hours=(12,)),),
    )
    real_open, real_fstat, real_close = os.open, os.fstat, os.close
    opened: list[int] = []
    closed: list[int] = []

    def record_open(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def fail_owned_fstat(descriptor):
        if descriptor in opened:
            raise OSError("C:\\private\\history")
        return real_fstat(descriptor)

    def record_close(descriptor):
        if descriptor in opened:
            closed.append(descriptor)
        return real_close(descriptor)

    with monkeypatch.context() as scoped:
        scoped.setattr("evidence_verification.storage.os.open", record_open)
        scoped.setattr("evidence_verification.storage.os.fstat", fail_owned_fstat)
        scoped.setattr("evidence_verification.storage.os.close", record_close)
        with pytest.raises(OSError, match="^storage_error$"):
            storage._append_transitions(snapshot, None)

    assert opened and closed == [opened[0]]
    with pytest.raises(OSError):
        real_fstat(opened[0])


def test_transition_append_fdopen_failure_does_not_close_reused_descriptor(tmp_path, monkeypatch):
    storage = EvidenceStorage(root=tmp_path / "evidence")
    snapshot = EvidenceSnapshot(
        "snapshot",
        NOW,
        (evidence_event(status=VerificationStatus.UNVERIFIED, history_hours=(12,)),),
    )
    victim = tmp_path / "victim.txt"
    victim.write_text("survive", encoding="utf-8")
    reused: list[int] = []

    def close_reuse_then_fail(descriptor: int, *_args, **_kwargs):
        os.close(descriptor)
        reused.append(os.open(victim, os.O_RDONLY))
        assert reused[-1] == descriptor
        raise OSError("D:\\private\\history-fdopen")

    with monkeypatch.context() as scoped:
        scoped.setattr("evidence_verification.storage.os.fdopen", close_reuse_then_fail)
        with pytest.raises(OSError, match="^storage_error$"):
            storage._append_transitions(snapshot, None)

    try:
        assert os.fstat(reused[0]).st_size == len("survive")
    finally:
        if reused:
            os.close(reused[0])


@pytest.mark.parametrize("failure_stage", ["write", "fsync"])
def test_transition_append_write_and_fsync_failures_close_once_and_redact(
    failure_stage,
    tmp_path,
    monkeypatch,
):
    storage = EvidenceStorage(root=tmp_path / "evidence")
    snapshot = EvidenceSnapshot(
        "snapshot",
        NOW,
        (evidence_event(status=VerificationStatus.UNVERIFIED, history_hours=(12,)),),
    )
    real_fdopen, real_fsync = os.fdopen, os.fsync
    close_counts: list[int] = []
    owned_descriptors: list[int] = []

    class FailingHandle:
        def __init__(self, descriptor, *args, **kwargs):
            self.handle = real_fdopen(descriptor, *args, **kwargs)
            owned_descriptors.append(descriptor)
            self.close_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def write(self, value):
            if failure_stage == "write":
                raise OSError("C:\\private\\history-write")
            return self.handle.write(value)

        def flush(self):
            return self.handle.flush()

        def fileno(self):
            return self.handle.fileno()

        def close(self):
            self.close_count += 1
            close_counts.append(self.close_count)
            self.handle.close()

    def fail_owned_fsync(descriptor):
        if failure_stage == "fsync" and descriptor in owned_descriptors:
            raise OSError("D:\\private\\history-fsync")
        return real_fsync(descriptor)

    with monkeypatch.context() as scoped:
        scoped.setattr("evidence_verification.storage.os.fdopen", FailingHandle)
        scoped.setattr("evidence_verification.storage.os.fsync", fail_owned_fsync)
        with pytest.raises(OSError, match="^storage_error$"):
            storage._append_transitions(snapshot, None)

    assert close_counts == [1]
    with pytest.raises(OSError):
        os.fstat(owned_descriptors[0])
