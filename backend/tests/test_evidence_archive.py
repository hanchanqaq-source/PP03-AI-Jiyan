from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
import errno
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import quote

import pytest

import evidence_verification.archive as archive_module
from evidence_verification.archive import EvidenceArchive
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
from evidence_verification.service import EvidenceVerificationService
from evidence_verification.storage import EvidenceStorage
from evidence_verification.verifier import verify_event
from news_intelligence.models import NewsSourceItem


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class ExplodingOffset(tzinfo):
    def utcoffset(self, _value):
        raise RuntimeError("private timezone failure")


def evidence_item(
    evidence_id: str,
    *,
    canonical_url: str | None = None,
    excerpt: str = "公开证据摘要",
    published_at: datetime | None = NOW,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        content_source="official.example.com",
        collector_source="collector.example",
        canonical_url=canonical_url or f"https://official.example.com/{evidence_id}",
        published_at=published_at,
        source_role=SourceRole.PRIMARY,
        origin_cluster="publisher:official.example",
        supports_claim=True,
        is_official=True,
        title="星河科技正式公告",
        excerpt=excerpt,
    )


def event(
    event_id: str = "a" * 20,
    *,
    status: VerificationStatus = VerificationStatus.VERIFIED,
    published_at: datetime | None = NOW,
    history: tuple[StatusTransition, ...] | None = None,
    primary_evidence: tuple[EvidenceItem, ...] = (),
    independent_evidence: tuple[EvidenceItem, ...] = (),
    syndicated_copies: tuple[EvidenceItem, ...] = (),
    contradicting_evidence: tuple[EvidenceItem, ...] = (),
    key_fields: tuple[KeyField, ...] = (),
    title: str = "星河科技公告建设算力中心",
    summary: str = "公开摘要",
) -> EvidenceEvent:
    if history is None:
        history = (StatusTransition(None, status, NOW, f"reason-{status.value}"),)
    return EvidenceEvent(
        event_id=event_id,
        title=title,
        summary=summary,
        category="company",
        related_tags=(("semiconductor", "半导体"),),
        published_at=published_at,
        core_claim="星河科技建设算力中心",
        verification_status=status,
        verification_reason=history[-1].reason,
        verified_at=NOW,
        evidence_as_of=NOW,
        key_fields=key_fields,
        primary_evidence=primary_evidence,
        independent_evidence=independent_evidence,
        syndicated_copies=syndicated_copies,
        contradicting_evidence=contradicting_evidence,
        status_history=history,
    )


def snapshot(
    selected: EvidenceEvent,
    *,
    snapshot_id: str = "e" * 20,
    raw_snapshot_id: str = "r" * 20,
    generated_at: datetime = NOW,
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=snapshot_id,
        raw_snapshot_id=raw_snapshot_id,
        generated_at=generated_at,
        events=(selected,),
    )


def _causal_recovery_snapshots(
    event_id: str = "a" * 20,
) -> tuple[EvidenceSnapshot, EvidenceSnapshot]:
    verified_at = NOW - timedelta(hours=2)
    disproved_at = NOW - timedelta(hours=1)
    verified_transition = StatusTransition(
        None,
        VerificationStatus.VERIFIED,
        verified_at,
        "official-support",
    )
    disproved_transition = StatusTransition(
        VerificationStatus.VERIFIED,
        VerificationStatus.DISPROVED,
        disproved_at,
        "official-disproof",
    )
    base_evidence = evidence_item(f"evidence-{event_id}", published_at=verified_at)
    verified = replace(
        event(
            event_id,
            status=VerificationStatus.VERIFIED,
            published_at=verified_at,
            history=(verified_transition,),
            primary_evidence=(base_evidence,),
        ),
        verified_at=verified_at,
        evidence_as_of=verified_at,
    )
    disproved = replace(
        verified,
        verification_status=VerificationStatus.DISPROVED,
        verification_reason="official-disproof",
        verified_at=disproved_at,
        evidence_as_of=disproved_at,
        status_history=(verified_transition, disproved_transition),
    )
    return (
        snapshot(
            verified,
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
            generated_at=verified_at,
        ),
        snapshot(
            disproved,
            snapshot_id="3" * 20,
            raw_snapshot_id="4" * 20,
            generated_at=disproved_at,
        ),
    )


def test_service_archives_field_only_evidence_without_upgrading_core_claim(tmp_path):
    def source(name: str, host: str, title: str, summary: str) -> NewsSourceItem:
        return NewsSourceItem(
            source_name=name,
            source_url=f"https://{host}/feed",
            original_url=f"https://{host}/article",
            published_at=NOW,
            fetched_at=NOW,
            title=title,
            summary=summary,
            language="zh-CN",
            region="CN",
            track_key="semi",
            track_name="半导体",
            category="company",
            normalized_title=title,
            tokens=frozenset(title),
            anchors=frozenset({"项目"}),
            related_tags=(("semiconductor", "半导体"),),
            text_related_tags=(("semiconductor", "半导体"),),
            source_domain=host,
            data_status="cache",
        )

    sources = [
        source(
            "Core publisher",
            "core-news.com",
            "星河科技建设算力中心进度达50%",
            "项目进度达50%",
        ),
        source(
            "Field publisher",
            "field-news.com",
            "行业统计项目进度达50%",
            "完成率为50%",
        ),
    ]
    selected = SimpleNamespace(
        event_id="field-closure-event1",
        title=sources[0].title,
        summary=sources[0].summary,
        category="company",
        published_at_first=NOW,
        published_at_latest=NOW,
        related_tags=[{"id": "semiconductor", "name": "半导体"}],
        sources=sources,
    )
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    archive = EvidenceArchive(storage.root, now=lambda: NOW)
    service = EvidenceVerificationService(
        storage=storage,
        archive=archive,
        event_loader=lambda: [selected],
        now=lambda: NOW,
    )

    refreshed = service.refresh()
    verified = refreshed.events[0]
    archived = archive.get(selected.event_id)
    percentage = next(row for row in archived["key_fields"] if row["field_name"] == "percentage")
    available = {
        row["evidence_id"]
        for collection_name in (
            "primary_evidence",
            "independent_evidence",
            "syndicated_copies",
            "contradicting_evidence",
        )
        for row in archived[collection_name]
    }

    assert verified.verification_status == VerificationStatus.UNVERIFIED
    assert archived["verification_status"] == "unverified"
    assert percentage["verification_status"] == "corroborated"
    assert len(percentage["evidence_ids"]) == 2
    assert set(percentage["evidence_ids"]) <= available
    assert storage.load_current() == refreshed


@pytest.mark.parametrize("failed_write", (1, 2, 3, 4, 5))
def test_archive_batch_fault_never_exposes_only_the_older_causal_lineage(
    tmp_path,
    monkeypatch,
    failed_write,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    verified, disproved = _causal_recovery_snapshots()
    other_event = replace(
        disproved.events[0],
        event_id="b" * 20,
        published_at=NOW - timedelta(days=2),
        primary_evidence=(evidence_item("other", published_at=NOW - timedelta(days=2)),),
    )
    other = snapshot(
        other_event,
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
        generated_at=disproved.generated_at,
    )
    real_write = archive._atomic_write
    writes = 0

    def fail_selected_write(path, payload, maximum):
        nonlocal writes
        writes += 1
        if writes == failed_write:
            raise OSError("simulated batch write failure")
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", fail_selected_write)

    with pytest.raises(OSError):
        archive.upsert_many((verified, disproved, other))

    recovered = {
        row["event_id"]: row
        for row in EvidenceArchive(tmp_path, now=lambda: NOW).query(90)
    }
    assert not recovered or set(recovered) == {"a" * 20, "b" * 20}
    if recovered:
        assert recovered["a" * 20]["verification_status"] == "disproved"
        assert len(recovered["a" * 20]["snapshot_history"]) == 2
        assert [row["to_status"] for row in recovered["a" * 20]["status_history"]] == [
            "verified",
            "disproved",
        ]
        assert len(recovered["b" * 20]["snapshot_history"]) == 1


def test_archive_batch_commits_multi_event_multi_bucket_and_retry_idempotently(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    first, second = _causal_recovery_snapshots("a" * 20)
    other_event = replace(
        second.events[0],
        event_id="b" * 20,
        published_at=NOW - timedelta(days=2),
        primary_evidence=(evidence_item("other", published_at=NOW - timedelta(days=2)),),
    )
    other = snapshot(
        other_event,
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
        generated_at=second.generated_at,
    )

    archive.upsert_many((first, second, other))
    archive.upsert_many((first, second, other))

    rows = {row["event_id"]: row for row in archive.query(90)}
    assert set(rows) == {"a" * 20, "b" * 20}
    assert rows["a" * 20]["verification_status"] == "disproved"
    assert len(rows["a" * 20]["snapshot_history"]) == 2
    assert len(rows["b" * 20]["snapshot_history"]) == 1


def test_archive_batch_is_serialized_across_concurrent_writers(tmp_path):
    verified, disproved = _causal_recovery_snapshots("a" * 20)
    other_verified, other_disproved = _causal_recovery_snapshots("b" * 20)
    other_verified = replace(
        other_verified,
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
    )
    other_disproved = replace(
        other_disproved,
        snapshot_id="7" * 20,
        raw_snapshot_id="8" * 20,
    )
    first = EvidenceArchive(tmp_path, now=lambda: NOW)
    second = EvidenceArchive(tmp_path, now=lambda: NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(first.upsert_many, (verified, disproved)),
            executor.submit(second.upsert_many, (other_verified, other_disproved)),
        )
        for future in futures:
            future.result(timeout=30)

    rows = {row["event_id"]: row for row in EvidenceArchive(tmp_path, now=lambda: NOW).query(90)}
    assert set(rows) == {"a" * 20, "b" * 20}
    assert {row["verification_status"] for row in rows.values()} == {"disproved"}
    assert {len(row["snapshot_history"]) for row in rows.values()} == {2}


def test_archive_batch_stops_consuming_input_at_the_combined_snapshot_budget(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    monkeypatch.setattr(archive_module, "_MAX_SNAPSHOT_BYTES", 8_000)
    consumed = 0

    def selected_snapshots():
        nonlocal consumed
        for index in range(10):
            consumed += 1
            yield snapshot(
                event(f"{index:020d}", title="x" * 4_000),
                snapshot_id=f"s{index:019d}",
                raw_snapshot_id=f"r{index:019d}",
            )
        raise RuntimeError("private generator detail")

    with pytest.raises(ValueError, match="archive snapshot budget exceeded"):
        archive.upsert_many(selected_snapshots())

    assert consumed < 10
    assert archive.count() == 0


def test_archive_batch_rejects_the_combined_raw_title_budget_before_projection(
    tmp_path,
    monkeypatch,
):
    import evidence_verification.storage as storage_module

    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    trusted_text_calls = 0
    real_trusted_text = storage_module.trusted_event_text

    def observe_trusted_text(selected, value):
        nonlocal trusted_text_calls
        trusted_text_calls += 1
        return real_trusted_text(selected, value)

    monkeypatch.setattr(storage_module, "trusted_event_text", observe_trusted_text)
    selected = tuple(
        snapshot(
            event(f"{index:020x}", title="x" * 8_000),
            snapshot_id=f"s{index:019d}",
            raw_snapshot_id=f"r{index:019d}",
        )
        for index in range(2_500)
    )

    with pytest.raises(ValueError, match="archive snapshot budget exceeded"):
        archive.upsert_many(selected)

    assert trusted_text_calls == 0
    assert not archive.state_path.exists()


def test_archive_rejects_duplicate_source_event_before_projection(tmp_path, monkeypatch):
    import evidence_verification.storage as storage_module

    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    duplicate = event("a" * 20)
    selected = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW,
        events=(duplicate, duplicate),
    )
    trusted_text_calls = 0
    real_trusted_text = storage_module.trusted_event_text

    def observe_trusted_text(event_value, text):
        nonlocal trusted_text_calls
        trusted_text_calls += 1
        return real_trusted_text(event_value, text)

    monkeypatch.setattr(storage_module, "trusted_event_text", observe_trusted_text)

    with pytest.raises(ValueError, match="duplicate event identities"):
        archive.upsert_many((selected,))

    assert trusted_text_calls == 0
    assert not archive.state_path.exists()


def test_archive_expected_raw_event_set_completes_a_legal_missing_sibling(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    first = snapshot(
        event("a" * 20),
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
    )
    second = snapshot(
        event("b" * 20),
        snapshot_id=first.snapshot_id,
        raw_snapshot_id=first.raw_snapshot_id,
        generated_at=first.generated_at,
    )
    archive.upsert(first)

    archive.upsert_many(
        (second,),
        expected_raw_event_sets={first.raw_snapshot_id: ("a" * 20, "b" * 20)},
    )

    assert {row["event_id"] for row in archive.query(90)} == {"a" * 20, "b" * 20}


def test_archive_raw_conflict_with_existing_projection_has_zero_transaction_writes(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    first = event("a" * 20)
    second = event("b" * 20)
    original = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW,
        events=(first, second),
    )
    archive.upsert(original)
    state_before = archive.state_path.read_bytes()
    tampered = snapshot(
        replace(second, summary="篡改后的摘要"),
        snapshot_id=original.snapshot_id,
        raw_snapshot_id=original.raw_snapshot_id,
        generated_at=original.generated_at,
    )
    writes = 0
    real_write = archive._atomic_write

    def observe_write(path, payload, maximum):
        nonlocal writes
        writes += 1
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", observe_write)

    with pytest.raises(ValueError, match="lineage"):
        archive.upsert_many(
            (tampered,),
            expected_raw_event_sets={original.raw_snapshot_id: ("a" * 20, "b" * 20)},
        )

    assert writes == 0
    assert archive.state_path.read_bytes() == state_before
    assert archive.get(second.event_id)["summary"] == second.summary


def test_archive_serializes_competing_evidence_identities_for_one_raw_snapshot(tmp_path):
    first = EvidenceArchive(tmp_path, now=lambda: NOW)
    second = EvidenceArchive(tmp_path, now=lambda: NOW)
    candidate_a = snapshot(
        event("a" * 20),
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
    )
    candidate_b = snapshot(
        event("b" * 20),
        snapshot_id="t" * 20,
        raw_snapshot_id=candidate_a.raw_snapshot_id,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(first.upsert, candidate_a),
            executor.submit(second.upsert, candidate_b),
        )
        outcomes = []
        for future in futures:
            try:
                future.result(timeout=30)
            except ValueError:
                outcomes.append("rejected")
            else:
                outcomes.append("committed")

    assert sorted(outcomes) == ["committed", "rejected"]
    assert len(EvidenceArchive(tmp_path, now=lambda: NOW).query(90)) == 1


def test_archive_serializes_competing_raw_authorities_across_real_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes = [
        context.Process(
            target=_competing_raw_upsert_in_child,
            args=(str(tmp_path), event_id, snapshot_id, start),
        )
        for event_id, snapshot_id in (
            ("a" * 20, "s" * 20),
            ("b" * 20, "t" * 20),
        )
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(30)

    assert sorted(process.exitcode for process in processes) == [0, 79]
    assert len(EvidenceArchive(tmp_path, now=lambda: NOW).query(90)) == 1


def test_archive_real_process_crash_keeps_raw_siblings_all_or_old_and_retryable(tmp_path):
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash_raw_sibling_batch_before_index_replace,
        args=(str(tmp_path),),
    )
    process.start()
    process.join(30)

    assert process.exitcode == 74
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    observed = {row["event_id"] for row in archive.query(90)}
    assert observed in (set(), {"a" * 20, "b" * 20})

    selected = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW,
        events=(event("a" * 20), event("b" * 20)),
    )
    archive.upsert_many(
        (selected,),
        expected_raw_event_sets={selected.raw_snapshot_id: ("a" * 20, "b" * 20)},
    )
    assert {row["event_id"] for row in archive.query(90)} == {"a" * 20, "b" * 20}


def _legacy_v1_row(row: dict[str, object]) -> dict[str, object]:
    legacy = json.loads(json.dumps(row, ensure_ascii=False))
    legacy["schema_version"] = 1
    legacy.pop("content_digest")
    legacy.pop("raw_input_digest")
    for lineage in legacy["snapshot_history"]:
        lineage.pop("content_digest")
        lineage.pop("raw_input_digest")
    return legacy


def _downgrade_authority_to_legacy_state(archive: EvidenceArchive, schema_version: int = 1) -> None:
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    bucket_digests = {
        path.name: archive._payload_digest(path.read_bytes())
        for path in sorted(archive.archive_root.glob("*.jsonl"))
        if path.stat().st_size > 0
    }
    legacy = {
        "schema_version": schema_version,
        "generation": state["generation"],
        "phase": state["phase"],
        "transaction_id": state["transaction_id"],
        "index_digest": state["target_index_digest"],
        "bucket_digests": bucket_digests,
    }
    archive.state_path.write_text(
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _refresh_finalized_bucket_manifest(archive: EvidenceArchive) -> None:
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    state["target_bucket_digests"] = {
        path.name: archive._payload_digest(path.read_bytes())
        for path in sorted(archive.archive_root.glob("*.jsonl"))
    }
    archive.state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _set_journal_mtime(archive: EvidenceArchive, when: datetime) -> None:
    timestamp_ns = int(when.timestamp() * 1_000_000_000)
    os.utime(archive.journal_path, ns=(timestamp_ns, timestamp_ns))


def _rewrite_pending_journal_as_v2(archive: EvidenceArchive) -> dict[str, object]:
    prepared = json.loads(archive.state_path.read_text(encoding="utf-8"))
    document = {
        "schema_version": 2,
        "transaction_id": "",
        "base_generation": prepared["base_generation"],
        "target_generation": prepared["target_generation"],
        "base_index_digest": prepared["base_index_digest"],
        "target_index_digest": prepared["target_index_digest"],
        "base_bucket_digests": prepared["base_bucket_digests"],
        "target_bucket_digests": prepared["target_bucket_digests"],
        "rows": prepared["rows"],
    }
    document["transaction_id"] = archive._journal_transaction_id(
        base_generation=document["base_generation"],
        target_generation=document["target_generation"],
        base_index_digest=document["base_index_digest"],
        target_index_digest=document["target_index_digest"],
        base_bucket_digests=document["base_bucket_digests"],
        target_bucket_digests=document["target_bucket_digests"],
        rows=document["rows"],
    )
    archive.journal_path.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _set_journal_mtime(archive, archive._now())
    archive.state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generation": prepared["base_generation"],
                "phase": "finalized",
                "transaction_id": "0" * 64,
                "index_digest": prepared["base_index_digest"],
                "bucket_digests": {},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )
    return document


def _journal_only_document(
    archive: EvidenceArchive,
    selected: EvidenceSnapshot,
    schema_version: int,
) -> dict[str, object]:
    row = archive_module._archive_document(
        selected,
        archive_module.event_document(selected.events[0]),
        NOW,
    )
    if schema_version == 1:
        return {"schema_version": 1, "rows": [_legacy_v1_row(row)]}

    bucket_name = archive_module._bucket_name(row)
    target_index = {row["event_id"]: bucket_name}
    base_bucket_digests = {bucket_name: archive_module._MISSING_DIGEST}
    target_bucket_digests = {
        bucket_name: archive._payload_digest(archive._bucket_payload(bucket_name, [row]))
    }
    target_index_digest = archive._payload_digest(archive._index_payload(target_index))
    document: dict[str, object] = {
        "schema_version": schema_version,
        "transaction_id": "",
        "base_generation": 0,
        "target_generation": 1,
        "base_index_digest": archive_module._MISSING_DIGEST,
        "target_index_digest": target_index_digest,
        "base_bucket_digests": base_bucket_digests,
        "target_bucket_digests": target_bucket_digests,
        "rows": [row],
    }
    cutoff = (NOW - timedelta(days=90)).isoformat() if schema_version == 3 else None
    if schema_version == 3:
        document["cutoff"] = cutoff
        document["target_index"] = target_index
    document["transaction_id"] = archive._journal_transaction_id(
        base_generation=0,
        target_generation=1,
        base_index_digest=archive_module._MISSING_DIGEST,
        target_index_digest=target_index_digest,
        base_bucket_digests=base_bucket_digests,
        target_bucket_digests=target_bucket_digests,
        rows=[row],
        cutoff=cutoff,
        target_index=target_index if schema_version == 3 else None,
    )
    return document


def _write_bucket_for_test(
    archive: EvidenceArchive,
    name: str,
    rows: list[dict[str, object]],
) -> None:
    archive._atomic_write(
        archive.archive_root / name,
        archive._bucket_payload(name, rows),
        archive_module._MAX_BUCKET_BYTES,
    )


def _write_historical_v1_finalized_archive(
    archive: EvidenceArchive,
    selected_snapshots: tuple[EvidenceSnapshot, ...],
) -> dict[str, object]:
    """Write the real schema-v1 shape: no synthetic bucket manifest."""
    with archive._process_lock():
        pass
    rows: list[dict[str, object]] = []
    for selected in selected_snapshots:
        rows.append(archive_module._archive_document(
            selected,
            archive_module.event_document(selected.events[0]),
            selected.generated_at,
        ))
    buckets: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        buckets.setdefault(archive_module._bucket_name(row), []).append(row)
    bucket_bytes: dict[str, bytes] = {}
    for name, bucket_rows in buckets.items():
        payload = b"".join(
            json.dumps(
                _legacy_v1_row(row),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            for row in sorted(bucket_rows, key=lambda item: item["event_id"])
        )
        (archive.archive_root / name).write_bytes(payload)
        bucket_bytes[name] = payload
    physical_index = {
        row["event_id"]: archive_module._bucket_name(row)
        for row in rows
    }
    index_bytes = archive._index_payload(physical_index)
    archive.index_path.write_bytes(index_bytes)
    legacy_state = {
        "schema_version": 1,
        "generation": 1,
        "phase": "finalized",
        "transaction_id": "1" * 64,
        "index_digest": archive._payload_digest(index_bytes),
        "bucket_digests": {},
    }
    state_bytes = (
        json.dumps(legacy_state, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    archive.state_path.write_bytes(state_bytes)
    return {
        "rows": rows,
        "bucket_bytes": bucket_bytes,
        "index_bytes": index_bytes,
        "state_bytes": state_bytes,
    }


def _upsert_in_child(root: str, event_id: str) -> None:
    raw_snapshot_id = ("1" if event_id.startswith("1") else "2") * 20
    EvidenceArchive(root, now=lambda: NOW).upsert(snapshot(
        event(event_id),
        snapshot_id=event_id,
        raw_snapshot_id=raw_snapshot_id,
    ))


def _competing_raw_upsert_in_child(
    root: str,
    event_id: str,
    snapshot_id: str,
    start: object,
) -> None:
    start.wait(20)
    try:
        EvidenceArchive(root, now=lambda: NOW).upsert(snapshot(
            event(event_id),
            snapshot_id=snapshot_id,
            raw_snapshot_id="r" * 20,
        ))
    except ValueError:
        os._exit(79)


def _crash_raw_sibling_batch_before_index_replace(root: str) -> None:
    archive = EvidenceArchive(root, now=lambda: NOW)
    real_replace = archive_module._replace_durable

    def exit_before_index_replace(source: Path, destination: Path) -> None:
        if destination == archive.index_path:
            os._exit(74)
        real_replace(source, destination)

    archive_module._replace_durable = exit_before_index_replace
    selected = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW,
        events=(event("a" * 20), event("b" * 20)),
    )
    archive.upsert_many(
        (selected,),
        expected_raw_event_sets={selected.raw_snapshot_id: ("a" * 20, "b" * 20)},
    )


def _crash_after_archive_temp_fsync(root: str) -> None:
    def exit_before_replace(_source: Path, _destination: Path) -> None:
        os._exit(73)

    archive_module._replace_durable = exit_before_replace
    EvidenceArchive(root, now=lambda: NOW).upsert(snapshot(
        event("c" * 20),
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
    ))
    os._exit(74)


def _hold_archive_lock(root: str, ready, seconds: float) -> None:
    archive = EvidenceArchive(root, now=lambda: NOW)
    with archive._process_lock():
        ready.set()
        time.sleep(seconds)


class _SplitlinesForbiddenBytes(bytes):
    def splitlines(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("bucket payload allocated splitlines before row-budget rejection")


def test_archive_deduplicates_by_event_id_and_preserves_disproof_history_and_lineage(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    verified = StatusTransition(None, VerificationStatus.VERIFIED, NOW - timedelta(hours=1), "official support")
    disproved = StatusTransition(
        VerificationStatus.VERIFIED,
        VerificationStatus.DISPROVED,
        NOW,
        "official denial",
    )

    archive.upsert(snapshot(
        event(status=VerificationStatus.VERIFIED, history=(verified,)),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))
    archive.upsert(snapshot(
        event(status=VerificationStatus.DISPROVED, history=(verified, disproved)),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
    ))

    rows = archive.query(days=90)
    assert len(rows) == 1
    assert rows[0]["verification_status"] == "disproved"
    assert [row["to_status"] for row in rows[0]["status_history"]] == ["verified", "disproved"]
    assert rows[0]["evidence_snapshot_id"] == "3" * 20
    assert rows[0]["raw_snapshot_id"] == "4" * 20
    assert [
        {
            key: value
            for key, value in lineage.items()
            if key not in {"content_digest", "raw_input_digest"}
        }
        for lineage in rows[0]["snapshot_history"]
    ] == [
        {
            "evidence_snapshot_id": "1" * 20,
            "raw_snapshot_id": "2" * 20,
            "generated_at": NOW.isoformat(),
        },
        {
            "evidence_snapshot_id": "3" * 20,
            "raw_snapshot_id": "4" * 20,
            "generated_at": NOW.isoformat(),
        },
    ]
    assert all(
        len(lineage["content_digest"]) == 64
        and set(lineage["content_digest"]) <= set("0123456789abcdef")
        for lineage in rows[0]["snapshot_history"]
    )


def test_archive_does_not_store_full_article_body(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event(
        summary="y" * 8_000,
        primary_evidence=(evidence_item("proof", excerpt="x" * 10_000),),
    )))

    stored = next((tmp_path / "archive").glob("*.jsonl")).read_text(encoding="utf-8")
    assert "x" * 5_000 not in stored
    assert "y" * 5_000 not in stored
    assert len(archive.get("a" * 20)["summary"]) == 1_200
    assert len(archive.get("a" * 20)["primary_evidence"][0]["excerpt"]) == 1_200


def test_archive_merges_evidence_by_id_or_url_and_key_fields_by_value_identity(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    shared_url = "https://official.example.com/shared"
    archive.upsert(snapshot(event(
        primary_evidence=(evidence_item("old-proof", canonical_url=shared_url),),
        key_fields=(KeyField(
            "money", "12亿元", "CNY:1200000000", FieldVerificationStatus.VERIFIED, ("old-proof",), "official",
        ),),
    )))
    archive.upsert(snapshot(event(
        status=VerificationStatus.CONFLICTING,
        history=(
            StatusTransition(None, VerificationStatus.VERIFIED, NOW, "reason-verified"),
            StatusTransition(
                VerificationStatus.VERIFIED,
                VerificationStatus.CONFLICTING,
                NOW,
                "reason-conflicting",
            ),
        ),
        primary_evidence=(
            evidence_item("new-proof", canonical_url=shared_url),
            evidence_item("second-proof"),
        ),
        key_fields=(KeyField(
            "money", "15亿元", "CNY:1500000000", FieldVerificationStatus.CONFLICTING, ("second-proof",), "conflict",
        ),),
        title="星河科技更新公告",
        summary="更新摘要",
    ), snapshot_id="f" * 20, raw_snapshot_id="b" * 20))

    row = archive.get("a" * 20)
    assert row["title"] == "星河科技更新公告"
    assert row["summary"] == "更新摘要"
    assert len(row["primary_evidence"]) == 2
    assert {item["canonical_url"] for item in row["primary_evidence"]} == {
        shared_url,
        "https://official.example.com/second-proof",
    }
    assert [
        (field["field_name"], field["normalized_value"], field["verification_status"])
        for field in row["key_fields"]
    ] == [
        ("money", "CNY:1500000000", "conflicting"),
        ("money", "CNY:1200000000", "verified"),
    ]


def test_archive_query_accepts_exact_windows_and_known_statuses_and_sorts_descending(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    for offset, status in ((0, VerificationStatus.VERIFIED), (2, VerificationStatus.DISPROVED), (89, VerificationStatus.CONFLICTING)):
        selected = _timed_event(
            f"{offset + 1:020x}",
            NOW - timedelta(days=offset),
            status=status,
        )
        archive.upsert(snapshot(
            selected,
            snapshot_id=f"{offset + 11:020x}",
            raw_snapshot_id=f"{offset + 21:020x}",
            generated_at=NOW - timedelta(days=offset),
        ))

    assert [row["event_id"] for row in archive.query(days=90)] == [f"{value:020x}" for value in (1, 3, 90)]
    assert [row["verification_status"] for row in archive.query(days=3, status="disproved")] == ["disproved"]
    for days in (1, 3, 7, 30, 90):
        assert isinstance(archive.query(days=days), list)
    for invalid in (0, 2, 91, True, "90"):
        with pytest.raises(ValueError, match="days"):
            archive.query(days=invalid)
    for invalid_status in ("unknown", "VERIFIED", "", 1):
        with pytest.raises(ValueError, match="status"):
            archive.query(days=90, status=invalid_status)


def test_archive_finalized_manifest_skips_one_bounded_malformed_row_append(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    bucket = tmp_path / "archive" / "2026-08-20.jsonl"
    malformed = json.loads(bucket.read_text(encoding="utf-8"))
    malformed["title"] = 42
    with bucket.open("ab") as handle:
        handle.write(
            json.dumps(malformed, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        )

    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    assert archive.last_diagnostics["skipped_corrupt_rows"] == 1
    assert 0 <= archive.last_diagnostics["scanned_files"] <= 90
    assert set(archive.last_diagnostics) == {
        "scanned_files", "skipped_files", "scanned_rows", "skipped_corrupt_rows", "duplicate_rows",
    }


def test_archive_query_stops_before_reading_past_the_global_scan_budget(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    monkeypatch.setattr(archive_module, "_MAX_SCAN_BYTES", 32)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_get_count_and_ninety_day_boundary_are_truthful(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("1" * 20, published_at=NOW - timedelta(days=90))))
    archive.upsert(snapshot(
        replace(
            event("2" * 20, published_at=NOW - timedelta(days=90, seconds=1)),
            verified_at=NOW - timedelta(days=90, seconds=1),
            evidence_as_of=NOW - timedelta(days=90, seconds=1),
            status_history=(StatusTransition(
                None,
                VerificationStatus.VERIFIED,
                NOW - timedelta(days=90, seconds=1),
                "reason-verified",
            ),),
        ),
        snapshot_id="2" * 20,
        raw_snapshot_id="3" * 20,
        generated_at=NOW - timedelta(days=90, seconds=1),
    ))

    assert archive.count() == 1
    assert archive.get("1" * 20)["event_id"] == "1" * 20
    assert archive.get("2" * 20) is None
    assert archive.get("missing") is None


def test_archive_atomic_rewrite_failure_preserves_previous_bucket(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    bucket = tmp_path / "archive" / "2026-08-20.jsonl"
    before = bucket.read_bytes()
    real_replace = archive_module._replace_durable

    def fail_bucket(source: Path, destination: Path) -> None:
        if destination == bucket:
            raise OSError("simulated")
        real_replace(source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_bucket)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(snapshot(
            event(
                status=VerificationStatus.DISPROVED,
                history=(
                    StatusTransition(None, VerificationStatus.VERIFIED, NOW, "reason-verified"),
                    StatusTransition(
                        VerificationStatus.VERIFIED,
                        VerificationStatus.DISPROVED,
                        NOW,
                        "reason-disproved",
                    ),
                ),
            ),
            snapshot_id="f" * 20,
            raw_snapshot_id="q" * 20,
        ))

    assert bucket.read_bytes() == before


def test_archive_index_io_failure_leaves_merged_journal_queryable_and_retryable(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    updated = snapshot(
        event(
            status=VerificationStatus.DISPROVED,
            history=(
                StatusTransition(None, VerificationStatus.VERIFIED, NOW, "reason-verified"),
                StatusTransition(
                    VerificationStatus.VERIFIED,
                    VerificationStatus.DISPROVED,
                    NOW,
                    "reason-disproved",
                ),
            ),
        ),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
    )
    real_replace = archive_module._replace_durable

    def fail_index(source: Path, destination: Path) -> None:
        if destination == archive.index_path:
            raise OSError("simulated index failure")
        real_replace(source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_index)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(updated)

    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "prepared"

    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)
    assert archive.get("a" * 20)["verification_status"] == "disproved"
    archive.upsert(updated)
    assert not archive.journal_path.exists()
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "finalized"
    assert archive.get("a" * 20)["verification_status"] == "disproved"


def test_archive_concurrent_process_upserts_do_not_lose_bucket_rows(tmp_path):
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_upsert_in_child, args=(str(tmp_path), event_id))
        for event_id in ("1" * 20, "2" * 20)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0

    assert {row["event_id"] for row in EvidenceArchive(tmp_path, now=lambda: NOW).query(days=90)} == {
        "1" * 20,
        "2" * 20,
    }


def test_archive_rejects_naive_times_before_mutation_and_keeps_unknown_temp(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    unknown_temp = tmp_path / "archive" / ".2026-08-20.jsonl.user.tmp"
    unknown_temp.parent.mkdir(parents=True)
    unknown_temp.write_text("user-owned", encoding="utf-8")
    invalid = replace(snapshot(event()), generated_at=NOW.replace(tzinfo=None))

    with pytest.raises(ValueError, match="timezone"):
        archive.upsert(invalid)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.count()
    assert unknown_temp.read_text(encoding="utf-8") == "user-owned"


def test_archive_skips_symlink_bucket_without_following_it(tmp_path):
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps({"secret": "outside"}), encoding="utf-8")
    bucket = archive_root / "2026-08-20.jsonl"
    try:
        bucket.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available")

    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    assert archive.query(days=90) == []
    assert archive.last_diagnostics["skipped_files"] == 1
    assert outside.read_text(encoding="utf-8") == json.dumps({"secret": "outside"})


def test_archive_detects_dangling_symlink_bucket_without_following_it(tmp_path):
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    bucket = archive_root / "2026-08-20.jsonl"
    try:
        bucket.symlink_to(tmp_path / "missing-outside.jsonl")
    except OSError:
        pytest.skip("symlink creation is not available")

    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    assert archive.query(days=90) == []
    assert archive.last_diagnostics["skipped_files"] == 1


def test_archive_query_never_uses_following_exists_for_bucket_candidates(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    candidate = tmp_path / "archive" / "2026-08-20.jsonl"
    real_exists = Path.exists

    def guarded_exists(path: Path) -> bool:
        if path == candidate:
            raise AssertionError("following existence check reached archive bucket")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", guarded_exists)

    assert archive.query(days=1) == []


def _timed_event(
    event_id: str,
    observed_at: datetime,
    *,
    status: VerificationStatus = VerificationStatus.VERIFIED,
) -> EvidenceEvent:
    return replace(
        event(event_id, status=status, published_at=observed_at),
        verified_at=observed_at,
        evidence_as_of=observed_at,
        status_history=(StatusTransition(None, status, observed_at, f"reason-{status.value}"),),
    )


def test_archive_cross_bucket_target_failure_keeps_previous_history_queryable(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    previous_time = NOW - timedelta(days=1)
    archive.upsert(snapshot(
        _timed_event("c" * 20, previous_time),
        generated_at=previous_time,
    ))
    previous_bucket = tmp_path / "archive" / "2026-08-19.jsonl"
    target_bucket = tmp_path / "archive" / "2026-08-20.jsonl"
    previous_bytes = previous_bucket.read_bytes()
    real_replace = archive_module._replace_durable

    def fail_target(source: Path, destination: Path) -> None:
        if destination == target_bucket:
            raise OSError("simulated target failure")
        real_replace(source, destination)

    updated_event = replace(
        _timed_event("c" * 20, NOW, status=VerificationStatus.DISPROVED),
        status_history=(
            StatusTransition(None, VerificationStatus.VERIFIED, previous_time, "reason-verified"),
            StatusTransition(
                VerificationStatus.VERIFIED,
                VerificationStatus.DISPROVED,
                NOW,
                "reason-disproved",
            ),
        ),
    )
    updated = snapshot(
        updated_event,
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
    )
    monkeypatch.setattr(archive_module, "_replace_durable", fail_target)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(updated)

    assert previous_bucket.read_bytes() == previous_bytes
    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)
    retained = archive.get("c" * 20)
    assert retained is not None
    assert retained["verification_status"] == "disproved"
    assert [row["to_status"] for row in retained["status_history"]] == ["verified", "disproved"]

    archive.upsert(updated)
    assert not archive.journal_path.exists()
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "finalized"
    assert archive.get("c" * 20)["snapshot_history"][-1]["evidence_snapshot_id"] == "f" * 20


def test_archive_finalized_manifest_rejects_cross_bucket_remnant_injection(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    previous_time = NOW - timedelta(days=1)
    archive.upsert(snapshot(
        _timed_event("d" * 20, previous_time),
        generated_at=previous_time,
    ))
    old_bucket = tmp_path / "archive" / "2026-08-19.jsonl"
    old_bytes = old_bucket.read_bytes()
    updated = replace(
        _timed_event("d" * 20, NOW, status=VerificationStatus.DISPROVED),
        status_history=(
            StatusTransition(None, VerificationStatus.VERIFIED, previous_time, "reason-verified"),
            StatusTransition(
                VerificationStatus.VERIFIED,
                VerificationStatus.DISPROVED,
                NOW,
                "reason-disproved",
            ),
        ),
    )
    archive.upsert(snapshot(
        updated,
        snapshot_id="f" * 20,
        raw_snapshot_id="9" * 20,
    ))
    old_bucket.write_bytes(old_bytes)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90, status="verified")


def test_archive_preflights_index_capacity_before_any_bucket_mutation(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("1" * 20)))
    target = tmp_path / "archive" / "2026-08-19.jsonl"
    monkeypatch.setattr(archive_module, "_MAX_INDEX_EVENTS", 1)

    with pytest.raises(ValueError, match="index"):
        archive.upsert(snapshot(
            _timed_event("2" * 20, NOW - timedelta(days=1)),
            snapshot_id="2" * 20,
            raw_snapshot_id="3" * 20,
            generated_at=NOW - timedelta(days=1),
        ))

    assert not target.exists()
    assert archive.get("2" * 20) is None


def test_archive_index_supports_six_events_per_source_day_for_ninety_days(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    expected = {
        f"{number:020d}": f"{(NOW - timedelta(days=number % 90)).date().isoformat()}.jsonl"
        for number in range(108 * 6 * 90)
    }

    archive._write_index(expected)

    assert archive._read_index() == expected
    assert len(archive._index_payload(expected)) <= archive_module._MAX_INDEX_BYTES


def test_archive_index_prunes_out_of_window_identities_before_capacity_check(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    old_time = NOW - timedelta(days=91)
    archive.upsert(snapshot(
        _timed_event("1" * 20, old_time),
        generated_at=old_time,
    ))
    monkeypatch.setattr(archive_module, "_MAX_INDEX_EVENTS", 1)

    archive.upsert(snapshot(event("2" * 20), snapshot_id="2" * 20, raw_snapshot_id="3" * 20))

    assert archive._read_index() == {"2" * 20: "2026-08-20.jsonl"}


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "javascript:alert(1)",
        "https://user:secret@official.example.com/proof",
        "https://official.example.com/proof#private-fragment",
        "http://127.0.0.1/proof",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/proof",
        "https://printer.local/proof",
        "https://official.example.com/proof?api_key=do-not-store",
        "https://official.example.com/proof?next=https%3A%2F%2Fother.example.com%2F%3Ftoken%3Ddo-not-store",
        "https://official.example.com/proof?next=https%253A%252F%252Fother.example.com%252F%253Fsignature%253Ddo-not-store",
    ),
)
def test_archive_rejects_unsafe_or_sensitive_evidence_urls_without_persisting_them(
    tmp_path,
    unsafe_url,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    selected = event(primary_evidence=(evidence_item("unsafe", canonical_url=unsafe_url),))

    with pytest.raises(ValueError) as raised:
        archive.upsert(snapshot(selected))

    assert "do-not-store" not in str(raised.value)
    assert not (tmp_path / "archive").exists()


def test_archive_allows_an_explicitly_empty_evidence_url(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    item = replace(evidence_item("empty"), canonical_url="")

    archive.upsert(snapshot(event(primary_evidence=(item,))))

    assert archive.get("a" * 20)["primary_evidence"][0]["canonical_url"] == ""


@pytest.mark.parametrize("limit_name", ("_MAX_SNAPSHOT_BYTES", "_MAX_SNAPSHOT_NODES", "_MAX_SNAPSHOT_RESOURCES"))
def test_archive_preflights_whole_snapshot_budgets_before_creating_storage(tmp_path, monkeypatch, limit_name):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    monkeypatch.setattr(archive_module, limit_name, 1, raising=False)
    selected = event(primary_evidence=(evidence_item("budget-proof"),))

    with pytest.raises(ValueError, match="snapshot"):
        archive.upsert(snapshot(selected))

    assert not (tmp_path / "archive").exists()


def test_archive_reads_and_rewrites_each_affected_bucket_once_per_snapshot(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    selected = snapshot(event("1" * 20))
    selected = replace(selected, events=(event("1" * 20), event("2" * 20)))
    calls: list[str] = []
    real_read_bucket = archive._read_bucket

    def counted_read(name, diagnostics, **kwargs):
        calls.append(name)
        return real_read_bucket(name, diagnostics, **kwargs)

    monkeypatch.setattr(archive, "_read_bucket", counted_read)

    archive.upsert(selected)

    assert calls.count("2026-08-20.jsonl") == 1


def test_archive_preserves_more_than_256_lineages_within_the_window(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    initial_time = NOW - timedelta(minutes=256)
    archive.upsert(snapshot(
        _timed_event("e" * 20, initial_time),
        generated_at=initial_time,
    ))
    bucket = tmp_path / "archive" / "2026-08-20.jsonl"
    row = json.loads(bucket.read_text(encoding="utf-8"))
    history = [
        {
            "evidence_snapshot_id": f"{number:020x}",
            "raw_snapshot_id": f"{number + 1000:020x}",
            "generated_at": (initial_time + timedelta(minutes=number)).isoformat(),
            "content_digest": f"{number:064x}",
            "raw_input_digest": f"{number + 256:064x}",
        }
        for number in range(256)
    ]
    latest = history[-1]
    row["snapshot_history"] = history
    row["evidence_snapshot_id"] = latest["evidence_snapshot_id"]
    row["raw_snapshot_id"] = latest["raw_snapshot_id"]
    row["snapshot_generated_at"] = latest["generated_at"]
    row["last_updated_at"] = latest["generated_at"]
    row["raw_input_digest"] = latest["raw_input_digest"]
    bucket.write_text(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    _refresh_finalized_bucket_manifest(archive)

    corrected = replace(
        _timed_event("e" * 20, NOW, status=VerificationStatus.CORRECTED),
        status_history=(
            StatusTransition(None, VerificationStatus.VERIFIED, initial_time, "reason-verified"),
            StatusTransition(
                VerificationStatus.VERIFIED,
                VerificationStatus.CORRECTED,
                NOW,
                "reason-corrected",
            ),
        ),
    )
    archive.upsert(snapshot(
        corrected,
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
    ))

    archived = archive.get("e" * 20)
    assert len(archived["snapshot_history"]) == 257
    assert archived["snapshot_history"][0] == history[0]


def test_archive_rejects_future_and_noncausal_times_before_mutation(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    future = NOW + timedelta(minutes=6)
    future_item = replace(evidence_item("future"), published_at=future)
    selected = replace(
        event(primary_evidence=(future_item,)),
        published_at=future,
        verified_at=future,
        evidence_as_of=future,
        status_history=(
            StatusTransition(None, VerificationStatus.VERIFIED, NOW, "later"),
            StatusTransition(VerificationStatus.VERIFIED, VerificationStatus.CORRECTED, NOW - timedelta(minutes=1), "earlier"),
        ),
    )

    with pytest.raises(ValueError, match="time"):
        archive.upsert(snapshot(selected, generated_at=future))

    assert not (tmp_path / "archive").exists()


def test_archive_finalized_manifest_rejects_future_row_injection(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event(status=VerificationStatus.VERIFIED)))
    bucket = tmp_path / "archive" / "2026-08-20.jsonl"
    future_snapshot = snapshot(
        event(status=VerificationStatus.DISPROVED),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
        generated_at=NOW + timedelta(days=1),
    )
    future_row = archive_module._archive_document(
        future_snapshot,
        archive_module.event_document(future_snapshot.events[0]),
        NOW,
    )
    with bucket.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(future_row, ensure_ascii=False, separators=(",", ":")) + "\n")

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_scan_budget_uses_bytes_read_from_the_same_nofollow_descriptor(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    bucket = tmp_path / "archive" / "2026-08-20.jsonl"
    original_size = bucket.stat().st_size
    real_open = os.open
    raced = False

    def racing_open(path, flags, *args, **kwargs):
        nonlocal raced
        if Path(path) == bucket and not raced:
            raced = True
            with bucket.open("ab") as handle:
                handle.write(b"                ")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(archive_module, "_MAX_SCAN_BYTES", original_size + 1)
    monkeypatch.setattr(os, "open", racing_open)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_read_only_queries_do_not_create_storage_or_lock_files(tmp_path):
    root = tmp_path / "missing-root"
    archive = EvidenceArchive(root, now=lambda: NOW)

    assert archive.query(days=90) == []
    assert archive.get("missing") is None
    assert archive.count() == 0
    assert not root.exists()


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "http://2130706433/proof",
        "http://0x7f000001/proof",
        "http://0177.0.0.1/proof",
        "http://127.1/proof",
        "http://[::ffff:127.0.0.1]/proof",
        "http://[64:ff9b::7f00:1]/proof",
        "http://[64:ff9b:1::a00:1]/proof",
        "http://[2002:7f00:1::]/proof",
        "http://[2001:0000:4136:e378:8000:63bf:3fff:fdd2]/proof",
        "https://official.example.com/proof?xApiKey=secret",
        "https://official.example.com/proof?mytoken=secret",
        "https://official.example.com/proof?client-Authorization=secret",
    ),
)
def test_archive_rejects_alternate_private_hosts_and_compound_secret_names_before_storage(
    tmp_path,
    unsafe_url,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item("unsafe", canonical_url=unsafe_url),))))

    assert not (tmp_path / "archive").exists()


def test_archive_rejects_query_that_does_not_stabilize_within_decode_budget(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    nested = "token=secret"
    for _ in range(12):
        nested = quote(nested, safe="")
    unsafe_url = f"https://official.example.com/proof?next={nested}"

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item("unsafe", canonical_url=unsafe_url),))))

    assert not (tmp_path / "archive").exists()


def test_archive_canonicalizes_public_ipv6_with_brackets_and_round_trips(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    public_ipv6 = "https://[2606:4700:4700:0:0:0:0:1111]:443/proof"

    archive.upsert(snapshot(event(primary_evidence=(evidence_item("ipv6", canonical_url=public_ipv6),))))

    stored_url = archive.get("a" * 20)["primary_evidence"][0]["canonical_url"]
    assert stored_url == "https://[2606:4700:4700::1111]/proof"
    assert archive_module._archive_public_url(stored_url) == stored_url


def test_archive_rejects_event_times_that_are_not_causal_to_snapshot_before_storage(tmp_path):
    snapshot_time = NOW - timedelta(days=1)
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    selected = replace(
        event(primary_evidence=(evidence_item("future-of-snapshot"),)),
        verification_reason="late",
        status_history=(StatusTransition(None, VerificationStatus.VERIFIED, NOW, "late"),),
    )

    with pytest.raises(ValueError, match="time"):
        archive.upsert(snapshot(selected, generated_at=snapshot_time))

    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize(
    "selected",
    (
        replace(
            event(),
            status_history=(
                StatusTransition(None, VerificationStatus.VERIFIED, NOW - timedelta(minutes=2), "start"),
                StatusTransition(None, VerificationStatus.DISPROVED, NOW - timedelta(minutes=1), "broken"),
            ),
        ),
        replace(
            event(status=VerificationStatus.DISPROVED),
            status_history=(StatusTransition(None, VerificationStatus.VERIFIED, NOW, "wrong final"),),
        ),
    ),
)
def test_archive_rejects_noncontinuous_or_wrong_final_status_history_before_storage(tmp_path, selected):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="status history"):
        archive.upsert(snapshot(selected))

    assert not (tmp_path / "archive").exists()


def _same_time_competing_snapshots() -> tuple[EvidenceSnapshot, EvidenceSnapshot]:
    first_transition = StatusTransition(
        None,
        VerificationStatus.VERIFIED,
        NOW - timedelta(hours=2),
        "official support",
    )
    final_transition = StatusTransition(
        VerificationStatus.VERIFIED,
        VerificationStatus.DISPROVED,
        NOW - timedelta(hours=1),
        "official denial",
    )
    verified = replace(
        event(
            status=VerificationStatus.VERIFIED,
            history=(first_transition,),
            title="old title",
            summary="old summary",
            primary_evidence=(replace(evidence_item("shared"), title="old evidence"),),
            key_fields=(KeyField(
                "amount", "old", "old", FieldVerificationStatus.VERIFIED, ("shared",), "old",
            ),),
        ),
        related_tags=(("shared-tag", "old tag"),),
    )
    disproved = replace(
        event(
            status=VerificationStatus.DISPROVED,
            history=(first_transition, final_transition),
            title="winning title",
            summary="winning summary",
            primary_evidence=(replace(evidence_item("shared"), title="winning evidence"),),
            key_fields=(KeyField(
                "amount", "winning", "winning", FieldVerificationStatus.VERIFIED, ("shared",), "winning",
            ),),
        ),
        related_tags=(("shared-tag", "winning tag"),),
    )
    return (
        snapshot(verified, snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
        snapshot(disproved, snapshot_id="f" * 20, raw_snapshot_id="e" * 20),
    )


def test_archive_same_timestamp_total_order_is_replay_stable_and_keeps_winning_content(tmp_path):
    verified, disproved = _same_time_competing_snapshots()
    forward = EvidenceArchive(tmp_path / "forward", now=lambda: NOW)
    reverse = EvidenceArchive(tmp_path / "reverse", now=lambda: NOW)
    for selected in (verified, disproved, verified):
        forward.upsert(selected)
    for selected in (disproved, verified, disproved):
        reverse.upsert(selected)

    forward_row = forward.get("a" * 20)
    reverse_row = reverse.get("a" * 20)
    assert forward_row == reverse_row
    assert forward_row["verification_status"] == "disproved"
    assert forward_row["title"] == "winning title"
    assert forward_row["related_tags"] == [{"id": "shared-tag", "name": "winning tag"}]
    assert forward_row["key_fields"][0]["normalized_value"] == "winning"
    assert forward_row["primary_evidence"][0]["title"] == "winning evidence"
    assert forward_row["evidence_snapshot_id"] == "f" * 20


def test_archive_older_snapshot_cannot_overwrite_newer_collections_or_current_fields(tmp_path):
    verified, disproved = _same_time_competing_snapshots()
    verified = replace(verified, generated_at=NOW - timedelta(minutes=1))
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    archive.upsert(disproved)
    archive.upsert(verified)

    row = archive.get("a" * 20)
    assert row["verification_status"] == "disproved"
    assert row["title"] == "winning title"
    assert row["related_tags"][0]["name"] == "winning tag"
    assert row["key_fields"][0]["normalized_value"] == "winning"
    assert row["primary_evidence"][0]["title"] == "winning evidence"


@pytest.mark.parametrize("journal_payload", (b"{not-json", b'{"schema_version":999,"rows":[]}\n'))
def test_archive_corrupt_or_unknown_journal_fails_closed_instead_of_serving_old_bucket(
    tmp_path,
    journal_payload,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _downgrade_authority_to_legacy_state(archive)
    archive.journal_path.write_bytes(journal_payload)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.get("a" * 20)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.count()


def test_archive_oversized_journal_fails_closed(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _downgrade_authority_to_legacy_state(archive)
    archive.journal_path.write_bytes(b"x" * 65)
    monkeypatch.setattr(archive_module, "_MAX_JOURNAL_BYTES", 64)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_future_journal_fails_closed(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    row = archive.get("a" * 20)
    _downgrade_authority_to_legacy_state(archive)
    future = (NOW + timedelta(minutes=6)).isoformat()
    row["snapshot_generated_at"] = future
    row["last_updated_at"] = future
    row["snapshot_history"][-1]["generated_at"] = future
    archive.journal_path.write_text(
        json.dumps({"schema_version": 1, "rows": [row]}, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


@pytest.mark.parametrize("duplicate", (False, True))
def test_archive_journal_requires_unique_event_ids_and_canonical_order(tmp_path, duplicate):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("b" * 20)))
    first = archive.get("b" * 20)
    _downgrade_authority_to_legacy_state(archive)
    second_snapshot = snapshot(
        event("a" * 20),
        snapshot_id="2" * 20,
        raw_snapshot_id="3" * 20,
    )
    second = archive_module._archive_document(
        second_snapshot,
        archive_module.event_document(second_snapshot.events[0]),
        NOW,
    )
    rows = [first, first] if duplicate else [first, second]
    archive.journal_path.write_text(
        json.dumps({"schema_version": 1, "rows": rows}, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_recovers_authoritative_inline_prepared_state_before_query(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    verified, disproved = _same_time_competing_snapshots()
    archive.upsert(verified)
    real_replace = archive_module._replace_durable

    def fail_bucket(source: Path, destination: Path) -> None:
        if destination.name.endswith(".jsonl"):
            raise OSError("simulated bucket crash")
        real_replace(source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_bucket)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(disproved)
    prepared = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert prepared["phase"] == "prepared"
    assert prepared["rows"][0]["verification_status"] == "disproved"

    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)
    rows = archive.query(days=90)

    assert [row["verification_status"] for row in rows] == ["disproved"]
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "finalized"


def test_archive_rejects_tampered_prepared_state_that_would_regress_a_bucket_row(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(_timed_event("a" * 20, NOW)))
    real_replace = archive_module._replace_durable

    def fail_bucket(source: Path, destination: Path) -> None:
        if destination.name.endswith(".jsonl"):
            raise OSError("simulated bucket crash")
        real_replace(source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_bucket)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20))
    prepared = json.loads(archive.state_path.read_text(encoding="utf-8"))
    prepared["rows"][0]["published_at"] = (NOW - timedelta(days=91)).isoformat()
    archive.state_path.write_text(json.dumps(prepared, separators=(",", ":")) + "\n", encoding="utf-8")

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_snapshot_budget_short_circuits_before_serializing_whole_rows(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    monkeypatch.setattr(archive_module, "_MAX_SNAPSHOT_BYTES", 1)
    real_dumps = json.dumps

    def guarded_dumps(value, *args, **kwargs):
        if type(value) is dict and "event_id" in value:
            raise AssertionError("whole archive row serialized before budget rejection")
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(archive_module.json, "dumps", guarded_dumps)

    with pytest.raises(ValueError, match="snapshot"):
        archive.upsert(snapshot(event()))

    assert not (tmp_path / "archive").exists()


def test_archive_bucket_mutation_groups_out_of_order_removals_and_additions_in_one_pass():
    rows = [
        {"event_id": "c"},
        {"event_id": "a"},
        {"event_id": "b"},
    ]
    planned = archive_module._apply_bucket_mutations(
        {"2026-08-20.jsonl": rows},
        {"2026-08-20.jsonl": {"a", "c"}},
        {"2026-08-20.jsonl": [{"event_id": "d"}, {"event_id": "a"}]},
    )

    assert [row["event_id"] for row in planned["2026-08-20.jsonl"]] == ["a", "b", "d"]


@pytest.mark.parametrize("changed_field", ("st_size", "st_mtime_ns", "st_ctime_ns"))
def test_archive_same_descriptor_read_detects_size_or_timestamp_change(tmp_path, monkeypatch, changed_field):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    real_fstat = os.fstat
    calls = 0

    def racing_fstat(descriptor):
        nonlocal calls
        current = real_fstat(descriptor)
        calls += 1
        if calls != 2:
            return current
        values = {
            name: getattr(current, name)
            for name in (
                "st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
            )
        }
        values[changed_field] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(os, "fstat", racing_fstat)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_reader_waits_for_the_cross_process_archive_transaction_lock(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_hold_archive_lock, args=(str(tmp_path), ready, 0.5))
    process.start()
    assert ready.wait(10)

    started = time.monotonic()
    rows = archive.query(days=90)
    elapsed = time.monotonic() - started
    process.join(10)

    assert process.exitcode == 0
    assert [row["event_id"] for row in rows] == ["a" * 20]
    assert elapsed >= 0.3


def test_archive_scan_budget_fails_closed_instead_of_omitting_unrelated_bucket_rows(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    state_size = archive.state_path.stat().st_size
    index_size = archive.index_path.stat().st_size
    bucket_size = (archive.archive_root / "2026-08-20.jsonl").stat().st_size
    required = state_size + index_size + bucket_size

    monkeypatch.setattr(archive_module, "_MAX_SCAN_BYTES", required - 1)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    monkeypatch.setattr(archive_module, "_MAX_SCAN_BYTES", required)
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20}


def test_archive_public_url_keeps_reserved_and_repeated_path_semantics_distinct():
    values = (
        "https://official.example.com/a%2fb",
        "https://official.example.com/a/b",
        "https://official.example.com/a//b",
    )

    canonical = [archive_module._archive_public_url(value) for value in values]

    assert canonical == [
        "https://official.example.com/a%2Fb",
        "https://official.example.com/a/b",
        "https://official.example.com/a//b",
    ]
    assert len(set(canonical)) == 3
    assert [archive_module._archive_public_url(value) for value in canonical] == canonical


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://[2606:4700:4700::1111%25eth0]/proof",
        "https://[::192.0.2.1]/proof",
    ),
)
def test_archive_rejects_scoped_or_deprecated_embedded_ipv4_hosts(tmp_path, unsafe_url):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item("unsafe", canonical_url=unsafe_url),))))

    assert not archive.archive_root.exists()


def test_archive_evidence_dedupe_remaps_key_field_references_to_retained_ids(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    shared_url = "https://official.example.com/shared-proof"
    archive.upsert(snapshot(event(
        primary_evidence=(evidence_item("old-proof", canonical_url=shared_url),),
        key_fields=(KeyField(
            "amount",
            "old",
            "old",
            FieldVerificationStatus.VERIFIED,
            ("old-proof",),
            "official",
        ),),
    )))
    archive.upsert(snapshot(
        event(primary_evidence=(evidence_item("new-proof", canonical_url=shared_url),)),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
    ))

    row = archive.get("a" * 20)
    retained_ids = {
        item["evidence_id"]
        for collection in archive_module._EVIDENCE_COLLECTION_KEYS
        for item in row[collection]
    }
    referenced_ids = {
        evidence_id
        for field in row["key_fields"]
        for evidence_id in field["evidence_ids"]
    }
    assert referenced_ids
    assert referenced_ids <= retained_ids


def test_archive_rejects_key_field_references_without_retained_evidence(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    selected = event(key_fields=(KeyField(
        "amount",
        "12",
        "12",
        FieldVerificationStatus.VERIFIED,
        ("missing-proof",),
        "official",
    ),))

    with pytest.raises(ValueError, match="evidence"):
        archive.upsert(snapshot(selected))

    assert not archive.archive_root.exists()


def test_archive_rejects_different_content_for_an_existing_lineage_identity(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    first = snapshot(event(title="immutable title"))
    conflicting = snapshot(event(title="different title"))
    archive.upsert(first)

    with pytest.raises(ValueError, match="lineage"):
        archive.upsert(conflicting)

    assert archive.get("a" * 20)["title"] == "immutable title"


@pytest.mark.parametrize(
    "selected",
    (
        replace(event(), verification_reason="does not match final transition"),
        replace(
            event(),
            verified_at=NOW - timedelta(minutes=2),
            status_history=(StatusTransition(
                None,
                VerificationStatus.VERIFIED,
                NOW - timedelta(minutes=1),
                "reason-verified",
            ),),
        ),
        replace(
            event(primary_evidence=(evidence_item("later-proof"),)),
            evidence_as_of=NOW - timedelta(minutes=1),
        ),
        replace(
            event(),
            verification_reason="",
            status_history=(StatusTransition(None, VerificationStatus.VERIFIED, NOW, ""),),
        ),
    ),
)
def test_archive_rejects_noncausal_status_reason_and_evidence_times(tmp_path, selected):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="archive"):
        archive.upsert(snapshot(selected))

    assert not archive.archive_root.exists()


def test_archive_accepts_reappearance_as_a_new_rooted_status_chain_and_replays_stably(tmp_path):
    first_time = NOW - timedelta(days=2)
    omitted_time = NOW - timedelta(days=1)
    first = snapshot(
        _timed_event("r" * 20, first_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=first_time,
    )
    omitted = EvidenceSnapshot(
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=omitted_time,
        events=(),
    )
    reappeared_event = replace(
        _timed_event("r" * 20, NOW, status=VerificationStatus.DISPROVED),
        verification_reason="new official denial",
        status_history=(StatusTransition(
            None,
            VerificationStatus.DISPROVED,
            NOW,
            "new official denial",
        ),),
    )
    reappeared = snapshot(
        reappeared_event,
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
    )
    forward = EvidenceArchive(tmp_path / "forward", now=lambda: NOW)
    replay = EvidenceArchive(tmp_path / "replay", now=lambda: NOW)

    for selected in (first, omitted, reappeared, first):
        forward.upsert(selected)
    for selected in (reappeared, omitted, first, reappeared):
        replay.upsert(selected)

    forward_row = forward.get("r" * 20)
    replay_row = replay.get("r" * 20)
    assert forward_row == replay_row
    assert forward_row["verification_status"] == "disproved"
    assert [row["from_status"] for row in forward_row["status_history"]] == [None, None]
    assert [row["to_status"] for row in forward_row["status_history"]] == ["verified", "disproved"]


def test_archive_journal_rejects_excessive_structure_before_json_materialization(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _downgrade_authority_to_legacy_state(archive)
    archive.journal_path.write_bytes(
        b'{"schema_version":1,"rows":' + (b"[" * 20) + (b"]" * 20) + b"}\n"
    )
    parsed = False
    real_parse = archive._parse_json

    def forbidden_parse(raw):
        nonlocal parsed
        if raw.startswith(b'{"schema_version":1,"rows":'):
            parsed = True
            raise AssertionError("journal reached json.loads before structural preflight")
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", forbidden_parse)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)
    assert parsed is False


def test_archive_rejects_in_memory_resource_exhaustion_before_snapshot_document_copy(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    selected = snapshot(event(primary_evidence=(evidence_item("proof"),)))
    materialized = False

    def forbidden_materialization(_snapshot):
        nonlocal materialized
        materialized = True
        raise AssertionError("validated_snapshot_document called before resource preflight")

    monkeypatch.setattr(archive_module, "_MAX_SNAPSHOT_RESOURCES", 1)
    monkeypatch.setattr(archive_module, "validated_snapshot_document", forbidden_materialization)

    with pytest.raises(ValueError, match="snapshot"):
        archive.upsert(selected)
    assert materialized is False
    assert not archive.archive_root.exists()


@pytest.mark.parametrize("duplicate", (False, True))
def test_archive_bucket_requires_exact_sorted_unique_event_ids(tmp_path, duplicate):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    second_snapshot = snapshot(
        event("b" * 20),
        snapshot_id="2" * 20,
        raw_snapshot_id="3" * 20,
    )
    second = archive_module._archive_document(
        second_snapshot,
        archive_module.event_document(second_snapshot.events[0]),
        NOW,
    )
    first = archive.get("a" * 20)
    rows = [first, first] if duplicate else [second, first]
    bucket = archive.archive_root / "2026-08-20.jsonl"
    bucket.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_revalidates_merged_projection_and_keeps_all_distinct_evidence(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    first_time = NOW - timedelta(minutes=1)
    replayed_event_time = NOW - timedelta(minutes=10)
    reason = "verified by official evidence"
    first = replace(
        event(
            published_at=first_time,
            history=(StatusTransition(None, VerificationStatus.VERIFIED, first_time, reason),),
            primary_evidence=(evidence_item("newer-proof", published_at=first_time),),
        ),
        verification_reason=reason,
        verified_at=first_time,
        evidence_as_of=first_time,
    )
    replayed = replace(
        event(
            published_at=replayed_event_time,
            history=(StatusTransition(None, VerificationStatus.VERIFIED, replayed_event_time, reason),),
            primary_evidence=(evidence_item("older-proof", published_at=replayed_event_time),),
        ),
        verification_reason=reason,
        verified_at=replayed_event_time,
        evidence_as_of=replayed_event_time,
    )

    archive.upsert(snapshot(
        first,
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=first_time,
    ))
    archive.upsert(snapshot(
        replayed,
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=NOW,
    ))

    row = archive.get("a" * 20)
    assert row is not None
    assert {
        item["evidence_id"]
        for collection in archive_module._EVIDENCE_COLLECTION_KEYS
        for item in row[collection]
    } == {"newer-proof", "older-proof"}
    assert datetime.fromisoformat(row["evidence_as_of"]) >= first_time
    assert datetime.fromisoformat(row["verified_at"]) >= datetime.fromisoformat(row["evidence_as_of"])
    archive_module._validate_temporal_row(row, NOW)
    assert archive_module._archive_from_document(row) == row


def test_archive_merge_preserves_first_archive_time_across_later_valid_snapshots(tmp_path):
    first_time = NOW - timedelta(days=1)
    clock = [first_time]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    archive.upsert(snapshot(
        _timed_event("t" * 20, first_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=first_time,
    ))
    clock[0] = NOW

    archive.upsert(snapshot(
        _timed_event("t" * 20, NOW),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=NOW,
    ))

    row = archive.get("t" * 20)
    assert row is not None
    assert row["archived_at"] == first_time.isoformat()
    assert row["last_updated_at"] == NOW.isoformat()
    archive_module._validate_temporal_row(row, NOW)


@pytest.mark.parametrize(
    ("primary", "contradicting"),
    (
        (
            evidence_item("same-id", canonical_url="https://official.example.com/first"),
            evidence_item("same-id", canonical_url="https://official.example.com/second"),
        ),
        (
            evidence_item("first-id", canonical_url="https://official.example.com/same"),
            evidence_item("second-id", canonical_url="https://official.example.com/same"),
        ),
    ),
)
def test_archive_rejects_ambiguous_evidence_identity_across_collections_before_storage(
    tmp_path,
    primary,
    contradicting,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="evidence"):
        archive.upsert(snapshot(event(
            primary_evidence=(primary,),
            contradicting_evidence=(contradicting,),
        )))

    assert not archive.archive_root.exists()


@pytest.mark.parametrize(
    ("source_role", "is_official", "base_collection"),
    (
        (SourceRole.INDEPENDENT, False, "independent_evidence"),
        (SourceRole.PRIMARY, True, "primary_evidence"),
    ),
)
def test_archive_normalizes_exact_verifier_overlap_without_changing_input_authority(
    tmp_path,
    source_role,
    is_official,
    base_collection,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    source_now = NOW.astimezone(timezone(timedelta(hours=8)))
    raw_event = SimpleNamespace(
        event_id="a" * 20,
        title="星河科技公告建设算力中心",
        summary="星河科技将建设算力中心，项目投资12亿元。",
        category="company",
        published_at_first=source_now,
        published_at_latest=source_now,
        related_tags=[{"id": "semiconductor", "name": "半导体"}],
    )
    first = EvidenceItem(
        evidence_id="source-one",
        content_source="one.public-example.com",
        collector_source="one-feed.public-example.com",
        canonical_url="https://one.public-example.com/report",
        published_at=source_now,
        source_role=source_role,
        origin_cluster="publisher:one.public-example.com",
        is_official=is_official,
        title="星河科技公告建设算力中心",
        excerpt="项目投资12亿元。",
    )
    second = replace(
        first,
        evidence_id="source-two",
        content_source="two.public-example.com",
        collector_source="two-feed.public-example.com",
        canonical_url="https://two.public-example.com/report",
        origin_cluster="publisher:two.public-example.com",
        excerpt="项目投资15亿元。",
    )
    verified = verify_event(raw_event, [first, second], previous=None, now=NOW)
    selected = snapshot(verified)
    raw_input_digest = archive_module._raw_input_event_digest(verified)

    assert verified.verification_status is VerificationStatus.CONFLICTING
    base_items = getattr(verified, base_collection)
    assert {item.evidence_id for item in base_items} == {
        "source-one",
        "source-two",
    }
    assert verified.contradicting_evidence == base_items

    archive.upsert(selected)
    first_row = archive.get(verified.event_id)
    assert first_row is not None
    first_content_digest = first_row["content_digest"]
    archive.upsert(selected)
    retried = archive.get(verified.event_id)

    assert retried is not None
    assert retried["verification_status"] == "conflicting"
    expected_counts = {
        "primary_evidence": 0,
        "independent_evidence": 0,
        "syndicated_copies": 0,
        "contradicting_evidence": 2,
    }
    assert {
        collection_name: len(retried[collection_name])
        for collection_name in archive_module._EVIDENCE_COLLECTION_KEYS
    } == expected_counts
    assert retried["independent_evidence"] == []
    assert [item["evidence_id"] for item in retried["contradicting_evidence"]] == [
        "source-one",
        "source-two",
    ]
    assert {
        (item["source_role"], item["origin_cluster"])
        for item in retried["contradicting_evidence"]
    } == {
        (source_role.value, "publisher:one.public-example.com"),
        (source_role.value, "publisher:two.public-example.com"),
    }
    retained_ids = {
        item["evidence_id"]
        for collection_name in archive_module._EVIDENCE_COLLECTION_KEYS
        for item in retried[collection_name]
    }
    referenced_ids = {
        evidence_id
        for field in retried["key_fields"]
        for evidence_id in field["evidence_ids"]
    }
    assert retained_ids == {"source-one", "source-two"}
    assert referenced_ids == retained_ids
    assert retried["raw_input_digest"] == raw_input_digest
    assert retried["snapshot_history"][0]["raw_input_digest"] == raw_input_digest
    assert retried["published_at"] == NOW.isoformat()
    assert retried["evidence_as_of"] == NOW.isoformat()
    assert {
        item["published_at"] for item in retried["contradicting_evidence"]
    } == {NOW.isoformat()}
    assert retried["content_digest"] == first_content_digest
    assert len(retried["snapshot_history"]) == 1


def test_archive_canonicalizes_all_projected_aware_times_without_changing_instants(
    tmp_path,
):
    canonical_instant = NOW - timedelta(minutes=1, microseconds=876_544)
    published = canonical_instant.astimezone(timezone(timedelta(hours=8)))
    evidence_time = canonical_instant.astimezone(timezone(timedelta(hours=5, minutes=30)))
    verified = canonical_instant.astimezone(timezone(-timedelta(hours=4)))
    changed = canonical_instant.astimezone(timezone(timedelta(hours=9)))
    selected = replace(
        event(
            published_at=published,
            history=(StatusTransition(
                None,
                VerificationStatus.VERIFIED,
                changed,
                "verified across mixed offsets",
            ),),
            primary_evidence=(evidence_item("mixed-offset-proof", published_at=evidence_time),),
        ),
        verified_at=verified,
        evidence_as_of=evidence_time,
    )
    selected_snapshot = snapshot(selected)
    raw_input_digest = archive_module._raw_input_event_digest(selected)
    archive = EvidenceArchive(tmp_path / "offset", now=lambda: NOW)

    archive.upsert(selected_snapshot)
    row = archive.get(selected.event_id)

    assert row is not None
    expected = canonical_instant.isoformat()
    assert row["published_at"] == expected
    assert row["verified_at"] == expected
    assert row["evidence_as_of"] == expected
    assert row["primary_evidence"][0]["published_at"] == expected
    assert row["status_history"][0]["changed_at"] == expected
    assert row["raw_input_digest"] == raw_input_digest
    assert row["snapshot_history"][0]["raw_input_digest"] == raw_input_digest

    utc_selected = replace(
        selected,
        published_at=canonical_instant,
        verified_at=canonical_instant,
        evidence_as_of=canonical_instant,
        primary_evidence=(replace(
            selected.primary_evidence[0],
            published_at=canonical_instant,
        ),),
        status_history=(replace(
            selected.status_history[0],
            changed_at=canonical_instant,
        ),),
    )
    utc_archive = EvidenceArchive(tmp_path / "utc", now=lambda: NOW)
    utc_archive.upsert(snapshot(utc_selected))
    utc_row = utc_archive.get(utc_selected.event_id)

    assert utc_row is not None
    assert utc_row["content_digest"] == row["content_digest"]
    assert utc_row["raw_input_digest"] != row["raw_input_digest"]


def test_archive_preserves_ref_order_until_material_union_then_stays_idempotent(
    tmp_path,
):
    initial_ids = ("zeta-10", "Alpha-2")
    selected = event(
        primary_evidence=tuple(evidence_item(evidence_id) for evidence_id in initial_ids),
        key_fields=(KeyField(
            "amount",
            "12",
            "12",
            FieldVerificationStatus.VERIFIED,
            initial_ids,
            "official",
        ),),
    )
    selected_snapshot = snapshot(selected)
    raw_input_digest = archive_module._raw_input_event_digest(selected)
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    archive.upsert(selected_snapshot)
    first = archive.get(selected.event_id)
    assert first is not None
    assert first["key_fields"][0]["evidence_ids"] == list(initial_ids)

    expanded = replace(
        selected,
        primary_evidence=selected.primary_evidence + (evidence_item("10-proof"),),
        key_fields=(replace(
            selected.key_fields[0],
            evidence_ids=("10-proof", "zeta-10"),
            reason="new official evidence",
        ),),
    )
    expanded_snapshot = snapshot(
        expanded,
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
        generated_at=NOW + timedelta(minutes=1),
    )
    archive.upsert(expanded_snapshot)
    expanded_row = archive.get(selected.event_id)
    assert expanded_row is not None
    expanded_digest = expanded_row["content_digest"]

    archive.upsert(expanded_snapshot)
    retried = archive.get(selected.event_id)

    assert retried is not None
    assert retried["key_fields"][0]["evidence_ids"] == [
        "10-proof",
        "Alpha-2",
        "zeta-10",
    ]
    assert retried["snapshot_history"][0]["raw_input_digest"] == raw_input_digest
    assert retried["content_digest"] == expanded_digest
    assert len(retried["snapshot_history"]) == 2


def test_archive_rejects_duplicate_key_field_references_before_storage(tmp_path):
    selected = event(
        primary_evidence=(evidence_item("duplicate-reference"),),
        key_fields=(KeyField(
            "amount",
            "12",
            "12",
            FieldVerificationStatus.VERIFIED,
            ("duplicate-reference", "duplicate-reference"),
            "official",
        ),),
    )
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="key-field evidence reference"):
        archive.upsert(snapshot(selected))

    assert not archive.archive_root.exists()


def test_archive_rejects_duplicate_refs_hidden_by_same_identity_union_before_storage(
    tmp_path,
):
    evidence_ids = ("dup-a", "dup-b")
    base = KeyField(
        "amount",
        "12",
        "12",
        FieldVerificationStatus.VERIFIED,
        (evidence_ids[0], evidence_ids[0]),
        "official",
    )
    selected = event(
        primary_evidence=tuple(
            evidence_item(evidence_id) for evidence_id in evidence_ids
        ),
        key_fields=(
            base,
            replace(base, evidence_ids=(evidence_ids[1],)),
        ),
    )
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="key-field evidence reference"):
        archive.upsert(snapshot(selected))

    assert not archive.archive_root.exists()


def test_archive_exact_retry_preserves_distinct_values_with_same_field_status(tmp_path):
    proof = evidence_item("multi-value-proof")
    fields = tuple(
        KeyField(
            "money",
            raw_value,
            normalized_value,
            FieldVerificationStatus.CONFLICTING,
            (proof.evidence_id,),
            "conflicting values",
        )
        for raw_value, normalized_value in (
            ("12亿元", "CNY:1200000000"),
            ("15亿元", "CNY:1500000000"),
            ("18亿元", "CNY:1800000000"),
        )
    )
    selected = snapshot(event(
        primary_evidence=(proof,),
        key_fields=fields,
    ))
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    archive.upsert(selected)
    first = archive.get("a" * 20)
    assert first is not None
    first_digest = first["content_digest"]
    archive.upsert(selected)
    retried = archive.get("a" * 20)

    assert retried is not None
    assert [
        (field["field_name"], field["raw_value"], field["normalized_value"])
        for field in retried["key_fields"]
    ] == [
        (field.field_name, field.raw_value, field.normalized_value)
        for field in fields
    ]
    assert retried["content_digest"] == first_digest
    assert retried["snapshot_history"][0]["content_digest"] == first_digest
    assert len(retried["snapshot_history"]) == 1


def test_archive_first_projection_unions_same_value_identity_refs_idempotently(tmp_path):
    refs = ("zeta-proof", "Alpha-proof")
    base = KeyField(
        "money",
        "12亿元",
        "CNY:1200000000",
        FieldVerificationStatus.VERIFIED,
        (refs[0],),
        "official",
    )
    selected_event = event(
        primary_evidence=tuple(evidence_item(evidence_id) for evidence_id in sorted(refs)),
        key_fields=(base, replace(base, evidence_ids=(refs[1],))),
    )
    selected = snapshot(selected_event)
    raw_input_digest = archive_module._raw_input_event_digest(selected_event)
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    archive.upsert(selected)
    first = archive.get("a" * 20)

    assert first is not None
    assert len(first["key_fields"]) == 1
    assert first["key_fields"][0]["evidence_ids"] == ["Alpha-proof", "zeta-proof"]
    assert first["raw_input_digest"] == raw_input_digest
    assert first["snapshot_history"][0]["raw_input_digest"] == raw_input_digest
    archive.upsert(selected)
    retried = archive.get("a" * 20)

    assert retried == first
    assert retried["snapshot_history"][0]["content_digest"] == first["content_digest"]
    assert len(retried["snapshot_history"]) == 1


@pytest.mark.parametrize(
    "changed_field",
    (
        KeyField(
            "money",
            "120000万元",
            "CNY:1200000000",
            FieldVerificationStatus.VERIFIED,
            ("identity-proof",),
            "official",
        ),
        KeyField(
            "money",
            "12亿元",
            "CNY:1200000000.01",
            FieldVerificationStatus.VERIFIED,
            ("identity-proof",),
            "official",
        ),
    ),
)
def test_archive_raw_and_normalized_values_are_independent_identity_dimensions(
    tmp_path,
    changed_field,
):
    base = KeyField(
        "money",
        "12亿元",
        "CNY:1200000000",
        FieldVerificationStatus.VERIFIED,
        ("identity-proof",),
        "official",
    )
    selected = snapshot(event(
        primary_evidence=(evidence_item("identity-proof"),),
        key_fields=(base, changed_field),
    ))
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    archive.upsert(selected)
    archive.upsert(selected)
    row = archive.get("a" * 20)

    assert row is not None
    assert len(row["key_fields"]) == 2
    assert {
        (field["field_name"], field["raw_value"], field["normalized_value"])
        for field in row["key_fields"]
    } == {
        (base.field_name, base.raw_value, base.normalized_value),
        (
            changed_field.field_name,
            changed_field.raw_value,
            changed_field.normalized_value,
        ),
    }


def _legacy_duplicate_key_field_snapshot(
    *,
    recovery_metadata=None,
):
    refs = ("zeta-legacy-proof", "Alpha-legacy-proof")
    base = KeyField(
        "money",
        "12亿元",
        "CNY:1200000000",
        FieldVerificationStatus.VERIFIED,
        (refs[0],),
        "official",
    )
    selected = snapshot(
        event(
            primary_evidence=tuple(
                evidence_item(evidence_id) for evidence_id in sorted(refs)
            ),
            key_fields=(base, replace(base, evidence_ids=(refs[1],))),
        ),
        generated_at=NOW - timedelta(minutes=3),
    )
    return replace(selected, recovery_metadata=recovery_metadata or {})


def _write_legacy_duplicate_key_field_projection(
    archive,
    selected,
    monkeypatch,
):
    real_normalize = archive_module._normalize_archive_projection

    def preserve_legacy_duplicate_rows(row):
        normalized = real_normalize(row)
        normalized["key_fields"] = [dict(field) for field in row["key_fields"]]
        return normalized

    monkeypatch.setattr(
        archive_module,
        "_normalize_archive_projection",
        preserve_legacy_duplicate_rows,
    )
    archive.upsert(selected)
    historical = archive.get("a" * 20)
    monkeypatch.setattr(
        archive_module,
        "_normalize_archive_projection",
        real_normalize,
    )
    assert historical is not None
    assert len(historical["key_fields"]) == 2
    return historical


def test_archive_legacy_duplicate_key_fields_exact_current_retry_is_noop(
    tmp_path,
    monkeypatch,
):
    selected = _legacy_duplicate_key_field_snapshot()
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    historical = _write_legacy_duplicate_key_field_projection(
        archive,
        selected,
        monkeypatch,
    )
    original_raw_digest = archive_module._raw_input_event_digest(selected.events[0])

    archive.upsert(selected)
    retried = archive.get("a" * 20)

    assert historical["raw_input_digest"] == original_raw_digest
    assert historical["snapshot_history"][0]["raw_input_digest"] == original_raw_digest
    assert retried == historical


@pytest.mark.parametrize(
    ("initial_recovery", "retry_recovery", "expected_source", "expected_time"),
    (
        (
            None,
            {
                "source": "evidence_current",
                "recovered_at": (NOW - timedelta(minutes=1)).isoformat(),
                "recovery_status": "cache_recovered",
                "source_snapshot_id": "source-snapshot",
            },
            "evidence_current",
            NOW - timedelta(minutes=1),
        ),
        (
            {
                "source": "evidence_current",
                "recovered_at": (NOW - timedelta(minutes=1)).isoformat(),
                "recovery_status": "cache_recovered",
                "source_snapshot_id": "source-snapshot",
            },
            {
                "source": "evidence_history",
                "recovered_at": (NOW - timedelta(minutes=2)).isoformat(),
                "recovery_status": "cache_recovered",
                "source_snapshot_id": "source-snapshot",
            },
            "evidence_current+evidence_history",
            NOW - timedelta(minutes=2),
        ),
    ),
)
def test_archive_legacy_duplicate_key_fields_retry_merges_only_recovery(
    tmp_path,
    monkeypatch,
    initial_recovery,
    retry_recovery,
    expected_source,
    expected_time,
):
    selected = _legacy_duplicate_key_field_snapshot(
        recovery_metadata=initial_recovery,
    )
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    historical = _write_legacy_duplicate_key_field_projection(
        archive,
        selected,
        monkeypatch,
    )
    incoming = replace(selected, recovery_metadata=retry_recovery)

    archive.upsert(incoming)
    retried = archive.get("a" * 20)

    assert retried is not None
    assert retried["key_fields"] == historical["key_fields"]
    assert retried["content_digest"] == historical["content_digest"]
    assert retried["raw_input_digest"] == historical["raw_input_digest"]
    assert retried["snapshot_history"][0]["content_digest"] == (
        historical["snapshot_history"][0]["content_digest"]
    )
    assert retried["snapshot_history"][0]["raw_input_digest"] == (
        historical["snapshot_history"][0]["raw_input_digest"]
    )
    assert retried["snapshot_history"][0]["recovery"] == {
        "source": expected_source,
        "status": "cache_recovered",
        "recovered_at": expected_time.isoformat(),
        "source_snapshot_id": "source-snapshot",
    }


def test_archive_legacy_duplicate_key_fields_retry_rejects_recovery_conflict_zero_write(
    tmp_path,
    monkeypatch,
):
    initial_recovery = {
        "source": "evidence_current",
        "recovered_at": (NOW - timedelta(minutes=1)).isoformat(),
        "recovery_status": "cache_recovered",
        "source_snapshot_id": "source-a",
    }
    selected = _legacy_duplicate_key_field_snapshot(
        recovery_metadata=initial_recovery,
    )
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    historical = _write_legacy_duplicate_key_field_projection(
        archive,
        selected,
        monkeypatch,
    )
    writes = []
    real_write = archive._atomic_write

    def observe_write(path, payload, maximum):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", observe_write)
    conflicting = replace(
        selected,
        recovery_metadata={
            **initial_recovery,
            "source_snapshot_id": "source-b",
        },
    )

    with pytest.raises(ValueError, match="lineage|recovery"):
        archive.upsert(conflicting)

    assert writes == []
    assert archive.get("a" * 20) == historical


def test_archive_legacy_duplicate_key_fields_retry_rejects_non_key_change_with_fake_raw(
    tmp_path,
    monkeypatch,
):
    selected = _legacy_duplicate_key_field_snapshot()
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    historical = _write_legacy_duplicate_key_field_projection(
        archive,
        selected,
        monkeypatch,
    )
    writes = []
    real_write = archive._atomic_write

    def observe_write(path, payload, maximum):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", observe_write)
    monkeypatch.setattr(
        archive_module,
        "_raw_input_event_digest",
        lambda _event: historical["raw_input_digest"],
    )
    changed = replace(
        selected,
        events=(replace(selected.events[0], title="different public title"),),
    )

    with pytest.raises(ValueError, match="lineage content mismatch"):
        archive.upsert(changed)

    assert writes == []
    assert archive.get("a" * 20) == historical


def test_archive_key_field_value_identity_takes_winner_status_reason_and_unions_refs(
    tmp_path,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW + timedelta(minutes=2))
    old_proof = evidence_item("old-value-proof")
    new_proof = evidence_item("new-value-proof")
    old_field = KeyField(
        "money",
        "12亿元",
        "CNY:1200000000",
        FieldVerificationStatus.VERIFIED,
        (old_proof.evidence_id,),
        "old official reason",
    )
    new_field = replace(
        old_field,
        verification_status=FieldVerificationStatus.CONFLICTING,
        evidence_ids=(new_proof.evidence_id,),
        reason="latest conflicting reason",
    )
    archive.upsert(snapshot(event(
        primary_evidence=(old_proof,),
        key_fields=(old_field,),
    )))
    archive.upsert(snapshot(
        event(
            primary_evidence=(new_proof,),
            key_fields=(new_field,),
        ),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
        generated_at=NOW + timedelta(minutes=1),
    ))

    row = archive.get("a" * 20)

    assert row is not None
    assert len(row["key_fields"]) == 1
    assert row["key_fields"][0] == {
        "field_name": "money",
        "raw_value": "12亿元",
        "normalized_value": "CNY:1200000000",
        "verification_status": "conflicting",
        "evidence_ids": ["new-value-proof", "old-value-proof"],
        "reason": "latest conflicting reason",
    }


def test_archive_different_key_field_values_coexist_across_snapshots(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW + timedelta(minutes=2))
    proof = evidence_item("coexisting-values-proof")
    first_field = KeyField(
        "money",
        "12亿元",
        "CNY:1200000000",
        FieldVerificationStatus.CONFLICTING,
        (proof.evidence_id,),
        "conflict",
    )
    second_field = replace(
        first_field,
        raw_value="15亿元",
        normalized_value="CNY:1500000000",
    )
    archive.upsert(snapshot(event(
        primary_evidence=(proof,),
        key_fields=(first_field,),
    )))
    archive.upsert(snapshot(
        event(
            primary_evidence=(proof,),
            key_fields=(second_field,),
        ),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
        generated_at=NOW + timedelta(minutes=1),
    ))

    row = archive.get("a" * 20)

    assert row is not None
    assert [field["normalized_value"] for field in row["key_fields"]] == [
        "CNY:1500000000",
        "CNY:1200000000",
    ]


def test_archive_rejects_one_snapshot_with_conflicting_rows_for_same_value_identity(
    tmp_path,
):
    proof = evidence_item("ambiguous-field-proof")
    base = KeyField(
        "money",
        "12亿元",
        "CNY:1200000000",
        FieldVerificationStatus.VERIFIED,
        (proof.evidence_id,),
        "verified",
    )
    selected = snapshot(event(
        primary_evidence=(proof,),
        key_fields=(
            base,
            replace(
                base,
                verification_status=FieldVerificationStatus.CONFLICTING,
                reason="conflicting",
            ),
        ),
    ))
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="key.field"):
        archive.upsert(selected)

    assert not archive.archive_root.exists()


def test_archive_schema_v3_unsorted_refs_exact_retry_preserves_lineage(
    tmp_path,
    monkeypatch,
):
    refs = ("zeta-proof", "alpha-proof")
    selected = snapshot(event(
        primary_evidence=tuple(
            evidence_item(evidence_id) for evidence_id in sorted(refs)
        ),
        key_fields=(KeyField(
            "amount",
            "12",
            "12",
            FieldVerificationStatus.VERIFIED,
            refs,
            "official",
        ),),
    ))
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    real_normalize = archive_module._normalize_archive_projection

    def preserve_historical_ref_order(row):
        normalized = real_normalize(row)
        normalized["key_fields"] = [
            {
                **field,
                "evidence_ids": list(row["key_fields"][index]["evidence_ids"]),
            }
            for index, field in enumerate(normalized["key_fields"])
        ]
        return normalized

    monkeypatch.setattr(
        archive_module,
        "_normalize_archive_projection",
        preserve_historical_ref_order,
    )
    archive.upsert(selected)
    historical = archive.get("a" * 20)
    assert historical is not None
    assert historical["key_fields"][0]["evidence_ids"] == list(refs)
    historical_digest = historical["content_digest"]
    monkeypatch.setattr(
        archive_module,
        "_normalize_archive_projection",
        real_normalize,
    )

    archive.upsert(selected)
    retried = archive.get("a" * 20)

    assert retried is not None
    assert retried["key_fields"][0]["evidence_ids"] == list(refs)
    assert retried["content_digest"] == historical_digest
    assert retried["snapshot_history"][0]["content_digest"] == historical_digest
    assert len(retried["snapshot_history"]) == 1


def test_archive_material_alias_remap_sorts_refs_without_losing_value_identity(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW + timedelta(minutes=2))
    urls = (
        "https://official.example.com/alias-one",
        "https://official.example.com/alias-two",
    )
    old_items = (
        evidence_item("a-canonical", canonical_url=urls[0]),
        evidence_item("b-canonical", canonical_url=urls[1]),
    )
    new_items = (
        evidence_item("z-new", canonical_url=urls[0]),
        evidence_item("y-new", canonical_url=urls[1]),
    )
    old_field = KeyField(
        "amount",
        "12",
        "12",
        FieldVerificationStatus.VERIFIED,
        (),
        "official",
    )
    archive.upsert(snapshot(event(
        primary_evidence=old_items,
        key_fields=(old_field,),
    )))
    archive.upsert(snapshot(
        event(
            primary_evidence=new_items,
            key_fields=(replace(
                old_field,
                evidence_ids=("z-new", "y-new"),
            ),),
        ),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
        generated_at=NOW + timedelta(minutes=1),
    ))

    row = archive.get("a" * 20)

    assert row is not None
    assert row["key_fields"][0]["evidence_ids"] == ["a-canonical", "b-canonical"]


def test_archive_exact_retry_repairs_historical_collapsed_key_field_values(
    tmp_path,
    monkeypatch,
):
    proof = evidence_item("repair-proof")
    fields = tuple(
        KeyField(
            "money",
            raw_value,
            normalized_value,
            FieldVerificationStatus.CONFLICTING,
            (proof.evidence_id,),
            "conflict",
        )
        for raw_value, normalized_value in (
            ("12亿元", "CNY:1200000000"),
            ("15亿元", "CNY:1500000000"),
            ("18亿元", "CNY:1800000000"),
        )
    )
    selected = snapshot(event(
        primary_evidence=(proof,),
        key_fields=fields,
    ))
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(selected)
    original = archive.get("a" * 20)
    assert original is not None
    original_lineage_digest = original["snapshot_history"][0]["content_digest"]
    original_raw_digest = original["raw_input_digest"]
    real_key_field_merge = archive_module._merge_key_fields
    real_archive_merge = archive_module._merge_archive_rows

    def historical_collapse(loser, winner):
        merged = {}
        references = {}
        for field in (*loser, *winner):
            identity = (field["field_name"], field["verification_status"])
            merged[identity] = dict(field)
            references.setdefault(identity, set()).update(field["evidence_ids"])
        for identity, field in merged.items():
            field["evidence_ids"] = sorted(references[identity])
        return list(merged.values())

    def collapse_only_during_historical_archive_merge(previous, incoming, **kwargs):
        monkeypatch.setattr(archive_module, "_merge_key_fields", historical_collapse)
        try:
            return real_archive_merge(previous, incoming, **kwargs)
        finally:
            monkeypatch.setattr(
                archive_module,
                "_merge_key_fields",
                real_key_field_merge,
            )

    monkeypatch.setattr(
        archive_module,
        "_merge_archive_rows",
        collapse_only_during_historical_archive_merge,
    )
    archive.upsert(selected)
    collapsed = archive.get("a" * 20)
    assert collapsed is not None
    assert len(collapsed["key_fields"]) == 1
    monkeypatch.setattr(
        archive_module,
        "_merge_archive_rows",
        real_archive_merge,
    )

    archive.upsert(selected)
    repaired = archive.get("a" * 20)
    assert repaired is not None
    repaired_digest = repaired["content_digest"]
    archive.upsert(selected)
    retried = archive.get("a" * 20)

    assert retried is not None
    assert {
        (field["field_name"], field["raw_value"], field["normalized_value"])
        for field in retried["key_fields"]
    } == {
        (field.field_name, field.raw_value, field.normalized_value)
        for field in fields
    }
    assert retried["content_digest"] == repaired_digest
    assert retried["snapshot_history"][0]["content_digest"] == original_lineage_digest
    assert retried["raw_input_digest"] == original_raw_digest
    assert retried["snapshot_history"][0]["raw_input_digest"] == original_raw_digest
    assert len(retried["snapshot_history"]) == 1


def test_archive_canonicalizes_recovery_provenance_time_from_aware_offset(tmp_path):
    recovered_at = (NOW - timedelta(minutes=2)).astimezone(
        timezone(timedelta(hours=8))
    )
    selected = replace(
        snapshot(event(), generated_at=NOW - timedelta(minutes=3)),
        recovery_metadata={
            "source": "evidence_current",
            "recovered_at": recovered_at.isoformat(),
            "recovery_status": "cache_recovered",
            "source_snapshot_id": "source-snapshot",
        },
    )
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    archive.upsert(selected)
    row = archive.get("a" * 20)

    assert row is not None
    assert row["snapshot_history"][0]["recovery"]["recovered_at"] == (
        NOW - timedelta(minutes=2)
    ).isoformat()


@pytest.mark.parametrize(
    "invalid_time",
    (
        NOW.replace(tzinfo=None),
        datetime.max.replace(tzinfo=timezone(-timedelta(hours=23, minutes=59))),
        NOW.replace(tzinfo=ExplodingOffset()),
    ),
)
def test_archive_rejects_noncanonicalizable_projected_times_without_storage(
    tmp_path,
    invalid_time,
):
    selected = replace(event(), published_at=invalid_time)
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="timestamp|timezone"):
        archive.upsert(snapshot(selected))

    assert not archive.archive_root.exists()


def test_archive_rejects_exact_copy_claiming_two_base_roles_without_storage(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    duplicated = evidence_item("base-role-conflict")

    with pytest.raises(ValueError, match="evidence"):
        archive.upsert(snapshot(event(
            primary_evidence=(duplicated,),
            independent_evidence=(duplicated,),
        )))

    assert not archive.archive_root.exists()


@pytest.mark.parametrize(
    ("primary", "independent", "contradicting"),
    (
        (
            (evidence_item("third-role-copy"),),
            (evidence_item("third-role-copy"),),
            (evidence_item("third-role-copy"),),
        ),
        (
            (evidence_item("repeated-role-copy"), evidence_item("repeated-role-copy")),
            (),
            (evidence_item("repeated-role-copy"),),
        ),
        (
            (evidence_item("changed-role-copy"),),
            (),
            (replace(evidence_item("changed-role-copy"), title="different authority"),),
        ),
        (
            (),
            (evidence_item("role-mismatch"),),
            (evidence_item("role-mismatch"),),
        ),
    ),
)
def test_archive_rejects_noncanonical_role_copy_shapes_without_storage(
    tmp_path,
    primary,
    independent,
    contradicting,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="evidence"):
        archive.upsert(snapshot(event(
            primary_evidence=primary,
            independent_evidence=independent,
            contradicting_evidence=contradicting,
        )))

    assert not archive.archive_root.exists()


def test_archive_rejects_duplicate_evidence_identity_within_one_collection_before_storage(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    duplicated = evidence_item("duplicate-proof")

    with pytest.raises(ValueError, match="evidence"):
        archive.upsert(snapshot(event(primary_evidence=(duplicated, duplicated))))

    assert not archive.archive_root.exists()


def test_archive_rejects_cross_collection_identity_conflict_during_merge_without_mutation(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event(
        primary_evidence=(evidence_item(
            "shared-proof",
            canonical_url="https://official.example.com/original-proof",
        ),),
        key_fields=(KeyField(
            "amount",
            "12",
            "12",
            FieldVerificationStatus.VERIFIED,
            ("shared-proof",),
            "official",
        ),),
    )))
    before = archive.get("a" * 20)
    conflicting = event(
        status=VerificationStatus.CONFLICTING,
        history=(
            StatusTransition(None, VerificationStatus.VERIFIED, NOW, "verified evidence"),
            StatusTransition(
                VerificationStatus.VERIFIED,
                VerificationStatus.CONFLICTING,
                NOW,
                "conflicting evidence",
            ),
        ),
        contradicting_evidence=(evidence_item(
            "shared-proof",
            canonical_url="https://official.example.com/different-proof",
        ),),
        key_fields=(KeyField(
            "amount",
            "unknown",
            "unknown",
            FieldVerificationStatus.CONFLICTING,
            ("shared-proof",),
            "conflict",
        ),),
    )

    with pytest.raises(ValueError, match="evidence"):
        archive.upsert(snapshot(
            conflicting,
            snapshot_id="5" * 20,
            raw_snapshot_id="6" * 20,
        ))

    assert archive.get("a" * 20) == before


def test_archive_merge_preserves_historical_key_field_evidence_references(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event(
        primary_evidence=(evidence_item("old-proof"),),
        key_fields=(KeyField(
            "amount",
            "12",
            "12",
            FieldVerificationStatus.VERIFIED,
            ("old-proof",),
            "first official evidence",
        ),),
    )))
    archive.upsert(snapshot(
        event(
            primary_evidence=(evidence_item("new-proof"),),
            key_fields=(KeyField(
                "amount",
                "12",
                "12",
                FieldVerificationStatus.VERIFIED,
                ("new-proof",),
                "new official evidence",
            ),),
        ),
        snapshot_id="7" * 20,
        raw_snapshot_id="8" * 20,
    ))

    row = archive.get("a" * 20)
    amount = next(field for field in row["key_fields"] if field["field_name"] == "amount")
    assert set(amount["evidence_ids"]) == {"old-proof", "new-proof"}


def test_archive_reads_v1_bucket_rewrites_v3_and_fails_closed_on_exact_retry(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    original = snapshot(event(primary_evidence=(evidence_item("legacy-proof"),)))
    archive.upsert(original)
    row = archive.get("a" * 20)
    bucket = archive.archive_root / "2026-08-20.jsonl"
    bucket.write_text(
        json.dumps(_legacy_v1_row(row), ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _downgrade_authority_to_legacy_state(archive)

    recovered = archive.query(days=90)
    assert len(recovered) == 1
    assert recovered[0]["schema_version"] == 3
    assert len(recovered[0]["content_digest"]) == 64
    assert all(len(lineage["content_digest"]) == 64 for lineage in recovered[0]["snapshot_history"])
    assert all(lineage["legacy_v1"] is True for lineage in recovered[0]["snapshot_history"])

    with pytest.raises(archive_module.ArchiveRawAuthorityError, match="lineage"):
        archive.upsert(original)
    persisted = json.loads(bucket.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 3
    assert len(persisted["content_digest"]) == 64
    assert len(persisted["raw_input_digest"]) == 64


def test_archive_v1_compatibility_requires_an_exact_integer_schema_version(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    legacy = _legacy_v1_row(archive.get("a" * 20))
    legacy["schema_version"] = True

    with pytest.raises(ValueError, match="schema"):
        archive_module._archive_from_document(legacy)

    archive.journal_path.write_text(
        json.dumps(
            {"schema_version": True, "rows": [archive.get("a" * 20)]},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )
    _downgrade_authority_to_legacy_state(archive)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_reads_strict_v1_rows_from_committed_journal(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event(primary_evidence=(evidence_item("legacy-journal-proof"),))))
    row = archive.get("a" * 20)
    _downgrade_authority_to_legacy_state(archive)
    archive.journal_path.write_text(
        json.dumps(
            {"schema_version": 1, "rows": [_legacy_v1_row(row)]},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )

    recovered = archive.query(days=90)
    assert len(recovered) == 1
    assert recovered[0]["schema_version"] == 3
    assert len(recovered[0]["content_digest"]) == 64


def test_archive_v1_multi_lineage_migration_rejects_unprovable_historical_replay(tmp_path):
    first_time = NOW - timedelta(days=1)
    clock = [first_time]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    first_snapshot = snapshot(
        _timed_event("m" * 20, first_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=first_time,
    )
    archive.upsert(first_snapshot)
    clock[0] = NOW
    archive.upsert(snapshot(
        replace(_timed_event("m" * 20, NOW), title="updated public title"),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=NOW,
    ))
    bucket = archive.archive_root / "2026-08-20.jsonl"
    row = archive.get("m" * 20)
    bucket.write_text(
        json.dumps(_legacy_v1_row(row), ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _downgrade_authority_to_legacy_state(archive)

    with pytest.raises(ValueError, match="lineage"):
        archive.upsert(first_snapshot)

    replayed = archive.get("m" * 20)
    assert replayed is not None
    assert len(replayed["snapshot_history"]) == 2
    assert all(lineage["legacy_v1"] is True for lineage in replayed["snapshot_history"])
    assert all(lineage["legacy_unverifiable"] is True for lineage in replayed["snapshot_history"])
    assert replayed["title"] == "updated public title"


def test_archive_rejects_unknown_future_bucket_schema_instead_of_hiding_history(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    bucket = archive.archive_root / "2026-08-20.jsonl"
    row = json.loads(bucket.read_text(encoding="utf-8"))
    row["schema_version"] = 4
    bucket.write_text(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_character_budget_short_circuits_before_json_string_copy(monkeypatch):
    def forbidden_json_copy(*_args, **_kwargs):
        raise AssertionError("oversized string reached json.dumps")

    monkeypatch.setattr(archive_module.json, "dumps", forbidden_json_copy)

    with pytest.raises(ValueError, match="budget"):
        archive_module._input_metrics(
            "x" * 1_024,
            maximum_bytes=32,
            maximum_nodes=10,
        )


def test_archive_rejects_arbitrarily_large_text_without_copying_its_utf8_payload():
    oversized = "汉" * 1_048_577
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="snapshot text"):
            archive_module._input_metrics(
                oversized,
                maximum_bytes=16_777_216,
                maximum_nodes=10,
            )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 524_288


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://[v1.fe80]/proof",
        "https://[vF.a:b]/proof",
    ),
)
def test_archive_rejects_ipvfuture_authority_instead_of_rewriting_it_as_dns(tmp_path, unsafe_url):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item(
            "ipvfuture",
            canonical_url=unsafe_url,
        ),))))

    assert not archive.archive_root.exists()


@pytest.mark.parametrize("operation", ("query", "get", "count"))
def test_archive_wrong_utc_bucket_fails_closed_for_every_reader(tmp_path, operation):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    row = archive.get("a" * 20)
    wrong_bucket = archive.archive_root / "2026-08-19.jsonl"
    wrong_bucket.write_text(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        if operation == "query":
            archive.query(days=90)
        elif operation == "get":
            archive.get("a" * 20)
        else:
            archive.count()


@pytest.mark.parametrize("location", ("root", "archive"))
def test_archive_existing_non_directory_storage_is_not_treated_as_empty(tmp_path, location):
    root = tmp_path / "store"
    if location == "root":
        root.write_text("not a directory", encoding="utf-8")
    else:
        root.mkdir()
        (root / "archive").write_text("not a directory", encoding="utf-8")
    archive = EvidenceArchive(root, now=lambda: NOW)

    with pytest.raises(OSError, match="storage"):
        archive.query(days=90)


def test_archive_existing_symlink_directory_is_not_treated_as_empty(tmp_path):
    root = tmp_path / "store"
    target = tmp_path / "target"
    root.mkdir()
    target.mkdir()
    try:
        os.symlink(target, root / "archive", target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable on this platform")
    archive = EvidenceArchive(root, now=lambda: NOW)

    with pytest.raises(OSError, match="storage"):
        archive.query(days=90)


@pytest.mark.parametrize("operation", ("query", "get", "count"))
@pytest.mark.parametrize(
    "corruption",
    ("oversized_line", "too_many_rows", "oversized_bucket", "unordered", "duplicate"),
)
def test_archive_bucket_structural_corruption_fails_closed_for_every_reader(
    tmp_path,
    operation,
    corruption,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    bucket = archive.archive_root / "2026-08-20.jsonl"
    first = archive.get("a" * 20)
    second_snapshot = snapshot(
        event("b" * 20),
        snapshot_id="7" * 20,
        raw_snapshot_id="8" * 20,
    )
    second = archive_module._archive_document(
        second_snapshot,
        archive_module.event_document(second_snapshot.events[0]),
        NOW,
    )
    encoded_first = json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    encoded_second = json.dumps(second, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if corruption == "oversized_line":
        bucket.write_bytes(encoded_first + b'"' + (b"x" * 1_048_577) + b'"\n')
    elif corruption == "too_many_rows":
        bucket.write_bytes(b"{}\n" * 4_097)
    elif corruption == "oversized_bucket":
        with bucket.open("wb") as handle:
            handle.seek(33_554_432)
            handle.write(b"\n")
    elif corruption == "unordered":
        bucket.write_bytes(encoded_second + encoded_first)
    else:
        bucket.write_bytes(encoded_first + encoded_first)

    with pytest.raises(OSError, match="storage_corrupt"):
        if operation == "query":
            archive.query(days=90)
        elif operation == "get":
            archive.get("a" * 20)
        else:
            archive.count()


def test_archive_rejects_replayed_stale_inline_prepared_state(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    disproved = snapshot(
        event(
            status=VerificationStatus.DISPROVED,
            history=(
                StatusTransition(None, VerificationStatus.VERIFIED, NOW, "reason-verified"),
                StatusTransition(
                    VerificationStatus.VERIFIED,
                    VerificationStatus.DISPROVED,
                    NOW,
                    "reason-disproved",
                ),
            ),
        ),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
    )
    real_replace = archive_module._replace_durable

    def fail_first_bucket(source: Path, destination: Path) -> None:
        if destination.name.endswith(".jsonl"):
            raise OSError("capture transaction journal")
        real_replace(source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_first_bucket)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(disproved)
    stale_prepared = archive.state_path.read_bytes()

    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)
    assert archive.get("a" * 20)["verification_status"] == "disproved"
    archive.upsert(snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20))
    assert archive.get("a" * 20)["verification_status"] == "disproved"

    archive.state_path.write_bytes(stale_prepared)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_keeps_one_manifest_bound_finalized_state_across_transactions(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    archive.upsert(snapshot(event()))
    first = json.loads(archive.state_path.read_text(encoding="utf-8"))

    archive.upsert(snapshot(
        event("b" * 20),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))

    second = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert first["transaction_id"] != second["transaction_id"]
    assert second["phase"] == "finalized"
    assert second["generation"] == first["generation"] + 1
    assert second["target_bucket_digests"]
    assert not archive.journal_path.exists()
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}


def test_archive_v1_lineage_digest_binds_the_actual_event_projection():
    first = archive_module._archive_document(
        snapshot(event(title="first public title")),
        archive_module.event_document(event(title="first public title")),
        NOW,
    )
    changed = archive_module._archive_document(
        snapshot(event(title="different public title")),
        archive_module.event_document(event(title="different public title")),
        NOW,
    )
    first_v1 = archive_module._archive_from_document(_legacy_v1_row(first))
    changed_v1 = archive_module._archive_from_document(_legacy_v1_row(changed))

    assert first_v1["snapshot_history"][0]["content_digest"] != changed_v1["snapshot_history"][0]["content_digest"]
    with pytest.raises(ValueError, match="lineage"):
        archive_module._merge_archive_rows(first_v1, changed_v1)


def test_archive_v1_multi_lineage_marks_each_unverifiable_historical_projection(tmp_path):
    first_time = NOW - timedelta(days=1)
    clock = [first_time]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    archive.upsert(snapshot(
        _timed_event("m" * 20, first_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=first_time,
    ))
    clock[0] = NOW
    archive.upsert(snapshot(
        replace(_timed_event("m" * 20, NOW), title="updated public title"),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=NOW,
    ))
    migrated = archive_module._archive_from_document(_legacy_v1_row(archive.get("m" * 20)))

    assert len(migrated["snapshot_history"]) == 2
    assert all(lineage["legacy_unverifiable"] is True for lineage in migrated["snapshot_history"])
    assert archive_module._archive_from_document(migrated) == migrated


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://official.example.com/proof?key=secret",
        "https://official.example.com/proof?credential=secret",
        "https://official.example.com/proof?bearer=secret",
        "https://official.example.com/proof?clientCredential=secret",
        "https://official.example.com/proof?bearerToken=secret",
        "https://official.example.com/proof?api%255Fkey=secret",
        "https://official.example.com/proof?next=credential%253Dsecret",
    ),
)
def test_archive_rejects_exact_camel_encoded_and_nested_sensitive_query_names(tmp_path, unsafe_url):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item(
            "unsafe-query",
            canonical_url=unsafe_url,
        ),))))

    assert not archive.archive_root.exists()


def test_archive_sensitive_query_detection_does_not_reject_monkey_or_turnkey(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    public_url = "https://official.example.com/proof?monkey=capuchin&turnkey=ready"

    archive.upsert(snapshot(event(primary_evidence=(evidence_item(
        "benign-query",
        canonical_url=public_url,
    ),))))

    assert archive.get("a" * 20)["primary_evidence"][0]["canonical_url"] == public_url


def test_archive_rejects_cyclic_evidence_id_url_aliases_without_losing_key_field_refs(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    url_one = "https://official.example.com/proof-one"
    url_two = "https://official.example.com/proof-two"
    archive.upsert(snapshot(event(
        primary_evidence=(
            evidence_item("proof-a", canonical_url=url_one),
            evidence_item("proof-b", canonical_url=url_two),
        ),
        key_fields=(
            KeyField("amount-a", "1", "1", FieldVerificationStatus.VERIFIED, ("proof-a",), "official"),
            KeyField("amount-b", "2", "2", FieldVerificationStatus.VERIFIED, ("proof-b",), "official"),
        ),
    )))
    before = archive.get("a" * 20)

    with pytest.raises(ValueError, match="evidence"):
        archive.upsert(snapshot(
            event(
                primary_evidence=(
                    evidence_item("proof-a", canonical_url=url_two),
                    evidence_item("proof-b", canonical_url=url_one),
                ),
                key_fields=(
                    KeyField("amount-a", "1", "1", FieldVerificationStatus.VERIFIED, ("proof-a",), "official"),
                    KeyField("amount-b", "2", "2", FieldVerificationStatus.VERIFIED, ("proof-b",), "official"),
                ),
            ),
            snapshot_id="5" * 20,
            raw_snapshot_id="6" * 20,
        ))

    assert archive.get("a" * 20) == before


@pytest.mark.parametrize("second_failure", ("prepared", "bucket", "index", "finalized"))
def test_archive_recovers_committed_target_before_starting_another_transaction_after_repeated_crashes(
    tmp_path,
    monkeypatch,
    second_failure,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    real_replace = archive_module._replace_durable
    first_update = snapshot(
        event(
            status=VerificationStatus.DISPROVED,
            history=(
                StatusTransition(None, VerificationStatus.VERIFIED, NOW, "reason-verified"),
                StatusTransition(
                    VerificationStatus.VERIFIED,
                    VerificationStatus.DISPROVED,
                    NOW,
                    "reason-disproved",
                ),
            ),
        ),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
    )

    def fail_first_finalized_state(source: Path, destination: Path) -> None:
        if destination == archive.state_path:
            payload = json.loads(source.read_text(encoding="utf-8"))
            if payload["phase"] == "finalized" and payload["generation"] == 2:
                raise OSError("simulated crash before finalized state")
        real_replace(source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_first_finalized_state)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(first_update)

    pending = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert pending["phase"] == "prepared"
    assert pending["generation"] == 2

    second_snapshot = snapshot(
        event("b" * 20),
        snapshot_id="9" * 20,
        raw_snapshot_id="8" * 20,
    )

    def fail_second_transaction(source: Path, destination: Path) -> None:
        if second_failure == "prepared" and destination == archive.state_path:
            payload = json.loads(source.read_text(encoding="utf-8"))
            if payload["phase"] == "prepared" and payload["generation"] == 3:
                raise OSError("simulated second prepared-state crash")
        if second_failure == "bucket" and destination.name.endswith(".jsonl"):
            raise OSError("simulated second bucket crash")
        if second_failure == "index" and destination == archive.index_path:
            raise OSError("simulated second index crash")
        if second_failure == "finalized" and destination == archive.state_path:
            payload = json.loads(source.read_text(encoding="utf-8"))
            if payload["phase"] == "finalized" and payload["generation"] == 3:
                raise OSError("simulated second finalized-state crash")
        real_replace(source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_second_transaction)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(second_snapshot)

    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)
    archive.upsert(second_snapshot)
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}
    assert archive.get("a" * 20)["verification_status"] == "disproved"


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://official.example.com/proof?payload=%7B%22apiKey%22%3A%22SECRET%22%7D",
        "https://official.example.com/proof?payload=%255B%257B%2522authorizationToken%2522%253A%2522SECRET%2522%257D%255D",
        "https://official.example.com/proof?payload=%7B%22meta%22%3A%5B%22token%3DSECRET%22%5D%7D",
        "https://official.example.com/proof?payload=%7B%22meta%22%3A%22clientCredential%3A%20SECRET%22%7D",
    ),
)
def test_archive_rejects_sensitive_names_nested_inside_bounded_json_query_payloads_before_storage(
    tmp_path,
    unsafe_url,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item(
            "nested-secret",
            canonical_url=unsafe_url,
        ),))))

    assert not archive.archive_root.exists()


def test_archive_nested_json_secret_detection_allows_monkey_and_turnkey_keys(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    public_url = (
        "https://official.example.com/proof?payload="
        "%7B%22monkey%22%3A%22capuchin%22%2C%22turnkey%22%3A%22ready%22%7D"
    )

    archive.upsert(snapshot(event(primary_evidence=(evidence_item(
        "nested-benign",
        canonical_url=public_url,
    ),))))

    assert archive.get("a" * 20)["primary_evidence"][0]["canonical_url"] == public_url


def test_archive_reclassifies_one_global_evidence_identity_into_the_newer_snapshot_role(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    shared_url = "https://official.example.com/reclassified-proof"
    archive.upsert(snapshot(event(
        primary_evidence=(evidence_item("z-old-proof", canonical_url=shared_url),),
        key_fields=(KeyField(
            "amount",
            "1",
            "1",
            FieldVerificationStatus.VERIFIED,
            ("z-old-proof",),
            "official",
        ),),
    )))

    archive.upsert(snapshot(
        event(
            independent_evidence=(evidence_item("a-new-proof", canonical_url=shared_url),),
            key_fields=(KeyField(
                "amount",
                "1",
                "1",
                FieldVerificationStatus.VERIFIED,
                ("a-new-proof",),
                "official",
            ),),
        ),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
    ))

    row = archive.get("a" * 20)
    assert row["primary_evidence"] == []
    assert [item["evidence_id"] for item in row["independent_evidence"]] == ["a-new-proof"]
    amount = next(field for field in row["key_fields"] if field["field_name"] == "amount")
    assert amount["evidence_ids"] == ["a-new-proof"]


def test_archive_rejects_cross_collection_id_url_swap_without_mutating_history(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    url_a = "https://official.example.com/cross-role-a"
    url_b = "https://official.example.com/cross-role-b"
    archive.upsert(snapshot(event(
        primary_evidence=(evidence_item("proof-a", canonical_url=url_a),),
        independent_evidence=(evidence_item("proof-b", canonical_url=url_b),),
    )))
    before = archive.get("a" * 20)

    with pytest.raises(ValueError, match="evidence"):
        archive.upsert(snapshot(
            event(
                independent_evidence=(evidence_item("proof-a", canonical_url=url_b),),
                contradicting_evidence=(evidence_item("proof-b", canonical_url=url_a),),
            ),
            snapshot_id="f" * 20,
            raw_snapshot_id="d" * 20,
        ))

    assert archive.get("a" * 20) == before


def test_archive_v1_journal_rejects_historical_subset_instead_of_merging_it_as_no_change(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    verified = snapshot(event(primary_evidence=(evidence_item("first-proof"),)))
    archive.upsert(verified)
    historical = archive.get("a" * 20)
    archive.upsert(snapshot(
        event(
            status=VerificationStatus.DISPROVED,
            history=(
                StatusTransition(None, VerificationStatus.VERIFIED, NOW, "reason-verified"),
                StatusTransition(
                    VerificationStatus.VERIFIED,
                    VerificationStatus.DISPROVED,
                    NOW,
                    "reason-disproved",
                ),
            ),
            primary_evidence=(evidence_item("first-proof"),),
            contradicting_evidence=(evidence_item("later-disproof"),),
        ),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
    ))
    _downgrade_authority_to_legacy_state(archive)
    archive.journal_path.write_text(
        json.dumps(
            {"schema_version": 1, "rows": [_legacy_v1_row(historical)]},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_v1_exact_replay_is_validated_outside_the_requested_query_window(tmp_path):
    archived_time = NOW - timedelta(days=30)
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(
        _timed_event("o" * 20, archived_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=archived_time,
    ))
    persisted = archive.get("o" * 20)
    _downgrade_authority_to_legacy_state(archive)
    archive.journal_path.write_text(
        json.dumps(
            {"schema_version": 1, "rows": [_legacy_v1_row(persisted)]},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )

    assert archive.query(days=1) == []


@pytest.mark.parametrize("nonexact_case", ("absent", "newer", "conflicting"))
def test_archive_v1_journal_rejects_every_nonexact_replay_without_mutating_persisted_rows(
    tmp_path,
    nonexact_case,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event(primary_evidence=(evidence_item("persisted-proof"),))))
    persisted = archive.get("a" * 20)
    bucket = archive.archive_root / "2026-08-20.jsonl"
    before = bucket.read_bytes()
    _downgrade_authority_to_legacy_state(archive)
    if nonexact_case == "absent":
        selected = snapshot(
            event("b" * 20, primary_evidence=(evidence_item("absent-proof"),)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        )
        journal_row = archive_module._archive_document(
            selected,
            archive_module.event_document(selected.events[0]),
            NOW,
        )
        legacy = _legacy_v1_row(journal_row)
    elif nonexact_case == "newer":
        selected = snapshot(
            event(
                status=VerificationStatus.DISPROVED,
                history=(
                    StatusTransition(None, VerificationStatus.VERIFIED, NOW, "reason-verified"),
                    StatusTransition(
                        VerificationStatus.VERIFIED,
                        VerificationStatus.DISPROVED,
                        NOW,
                        "reason-disproved",
                    ),
                ),
                primary_evidence=(evidence_item("persisted-proof"),),
                contradicting_evidence=(evidence_item("newer-disproof"),),
            ),
            snapshot_id="f" * 20,
            raw_snapshot_id="d" * 20,
        )
        journal_row = archive_module._archive_document(
            selected,
            archive_module.event_document(selected.events[0]),
            NOW,
        )
        legacy = _legacy_v1_row(journal_row)
    else:
        legacy = _legacy_v1_row(persisted)
        legacy["title"] = "conflicting historical title"

    archive.journal_path.write_text(
        json.dumps(
            {"schema_version": 1, "rows": [legacy]},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert bucket.read_bytes() == before


def _rewrite_finalized_index(archive: EvidenceArchive, events: dict[str, str]) -> None:
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    archive._write_index(events)
    state["target_index"] = dict(sorted(events.items()))
    state["target_index_digest"] = archive._index_digest()
    archive.state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("operation", ("query", "get", "count", "upsert"))
@pytest.mark.parametrize("corruption", ("missing_bucket", "missing_row", "unmapped_row"))
def test_archive_cross_validates_finalized_index_bucket_and_event_before_every_operation(
    tmp_path,
    operation,
    corruption,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    event_id = "a" * 20
    missing_bucket = "2026-08-19.jsonl"
    if corruption == "missing_bucket":
        _rewrite_finalized_index(archive, {event_id: missing_bucket})
    elif corruption == "missing_row":
        _write_bucket_for_test(archive, missing_bucket, [])
        _rewrite_finalized_index(archive, {event_id: missing_bucket})
    else:
        _rewrite_finalized_index(archive, {})

    with pytest.raises(OSError, match="storage_corrupt"):
        if operation == "query":
            archive.query(days=90)
        elif operation == "get":
            archive.get(event_id)
        elif operation == "count":
            archive.count()
        else:
            archive.upsert(snapshot(
                event("b" * 20),
                snapshot_id="1" * 20,
                raw_snapshot_id="2" * 20,
            ))


def _leave_inline_state_before_bucket(
    archive: EvidenceArchive,
    monkeypatch,
    selected: EvidenceSnapshot,
) -> None:
    real_replace = archive_module._replace_durable

    def fail_bucket(source: Path, destination: Path) -> None:
        if destination.name.endswith(".jsonl"):
            raise OSError("simulated bucket crash")
        real_replace(source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_bucket)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(selected)
    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)


def test_archive_inline_prepared_state_binds_original_cutoff_and_complete_target_index(tmp_path, monkeypatch):
    old_time = NOW - timedelta(days=89, hours=23)
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(
        _timed_event("o" * 20, old_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=old_time,
    ))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("n" * 20), snapshot_id="3" * 20, raw_snapshot_id="4" * 20),
    )

    prepared = json.loads(archive.state_path.read_text(encoding="utf-8"))

    assert prepared["cutoff"] == (NOW - timedelta(days=90)).isoformat()
    assert prepared["target_index"] == {
        "n" * 20: "2026-08-20.jsonl",
        "o" * 20: old_time.date().isoformat() + ".jsonl",
    }


def test_archive_v2_native_journal_rejects_unindexed_canonical_bucket_row(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    _rewrite_pending_journal_as_v2(archive)
    stray_time = NOW - timedelta(days=1)
    stray_snapshot = snapshot(
        _timed_event("c" * 20, stray_time),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=stray_time,
    )
    stray = archive_module._archive_document(
        stray_snapshot,
        archive_module.event_document(stray_snapshot.events[0]),
        NOW,
    )
    _write_bucket_for_test(archive, archive_module._bucket_name(stray), [stray])
    legacy_state_before = archive.state_path.read_bytes()

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert archive.state_path.read_bytes() == legacy_state_before


@pytest.mark.parametrize("budget_dimension", ("bytes", "rows", "nodes", "files"))
def test_archive_inline_recovery_shares_state_scan_budget_without_reset(
    tmp_path,
    monkeypatch,
    budget_dimension,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    state_before = archive.state_path.read_bytes()
    bucket_path = next(archive.archive_root.glob("*.jsonl"))
    state_document = archive._parse_json(state_before)
    bucket_document = archive_module._archive_from_document(
        archive._parse_json(bucket_path.read_bytes().splitlines()[0])
    )
    _state_bytes, state_nodes = archive_module._json_metrics(
        state_document,
        maximum_bytes=archive_module._MAX_STATE_BYTES,
        maximum_nodes=archive_module._MAX_SCAN_NODES,
    )
    _bucket_bytes, bucket_nodes = archive_module._json_metrics(
        bucket_document,
        maximum_bytes=archive_module._MAX_ROW_BYTES,
        maximum_nodes=archive_module._MAX_SCAN_NODES,
    )
    limits = {
        "bytes": len(state_before) + bucket_path.stat().st_size - 1,
        "rows": len(state_document["rows"]) + 1 - 1,
        "nodes": state_nodes + bucket_nodes - 1,
        "files": 1,
    }
    constants = {
        "bytes": "_MAX_SCAN_BYTES",
        "rows": "_MAX_SCAN_ROWS",
        "nodes": "_MAX_SCAN_NODES",
        "files": "_MAX_SCAN_FILES",
    }
    monkeypatch.setattr(archive_module, constants[budget_dimension], limits[budget_dimension])

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(
            event("c" * 20),
            snapshot_id="3" * 20,
            raw_snapshot_id="4" * 20,
        ))

    assert archive.state_path.read_bytes() == state_before


def test_archive_recovers_with_transaction_cutoff_then_next_transaction_prunes_normally(
    tmp_path,
    monkeypatch,
):
    old_time = NOW - timedelta(days=89, hours=23)
    clock = [NOW]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    archive.upsert(snapshot(
        _timed_event("o" * 20, old_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=old_time,
    ))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("n" * 20), snapshot_id="3" * 20, raw_snapshot_id="4" * 20),
    )
    clock[0] = NOW + timedelta(days=2)
    archive.upsert(snapshot(
        _timed_event("f" * 20, clock[0]),
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
        generated_at=clock[0],
    ))

    assert archive._read_index() == {
        "f" * 20: clock[0].date().isoformat() + ".jsonl",
        "n" * 20: NOW.date().isoformat() + ".jsonl",
    }
    assert {row["event_id"] for row in archive.query(days=90)} == {"f" * 20, "n" * 20}
    assert not archive.journal_path.exists()


def test_archive_consumes_exact_v1_journal_before_starting_native_transaction(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    persisted = archive.get("a" * 20)
    _downgrade_authority_to_legacy_state(archive)
    archive.journal_path.write_text(
        json.dumps(
            {"schema_version": 1, "rows": [_legacy_v1_row(persisted)]},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )
    observed_existing_journal: list[bool] = []
    real_atomic_write = archive._atomic_write

    def observe_native_journal(path: Path, payload: bytes, maximum: int) -> tuple[int, int]:
        if path == archive.journal_path:
            observed_existing_journal.append(archive._entry_present(path))
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", observe_native_journal)
    archive.upsert(snapshot(
        event("b" * 20),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))

    assert observed_existing_journal == []
    assert json.loads(archive.journal_path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["schema_version"] == 4
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}


def test_archive_preserves_foreign_transaction_replaced_after_prepared_state(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    archive.journal_path.write_bytes(b'{"owner":"foreign-before"}\n')
    real_atomic_write = archive._atomic_write
    foreign_after = b'{"owner":"foreign-after-prepared"}\n'

    def replace_journal_after_prepared_state(path: Path, payload: bytes, maximum: int):
        identity = real_atomic_write(path, payload, maximum)
        if path == archive.state_path and json.loads(payload)["phase"] == "prepared":
            replacement = archive.archive_root / "replacement-transaction.json"
            replacement.write_bytes(foreign_after)
            os.replace(replacement, archive.journal_path)
        return identity

    monkeypatch.setattr(archive, "_atomic_write", replace_journal_after_prepared_state)
    archive.upsert(snapshot(
        event("b" * 20),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))

    assert archive.journal_path.read_bytes() == foreign_after
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "finalized"


def test_archive_preserves_foreign_transaction_replaced_after_finalized_state(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    archive.journal_path.write_bytes(b'{"owner":"foreign-before"}\n')
    real_atomic_write = archive._atomic_write
    foreign_payload = b'{"owner":"foreign","must_survive":true}\n'

    def replace_after_finalized(path: Path, payload: bytes, maximum: int):
        identity = real_atomic_write(path, payload, maximum)
        if (
            path == archive.state_path
            and json.loads(payload)["phase"] == "finalized"
            and json.loads(payload)["generation"] == 2
        ):
            replacement = archive.archive_root / "foreign-transaction.json"
            replacement.write_bytes(foreign_payload)
            os.replace(replacement, archive.journal_path)
        return identity

    monkeypatch.setattr(archive, "_atomic_write", replace_after_finalized)
    archive.upsert(snapshot(
        event("b" * 20),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))

    assert archive.journal_path.read_bytes() == foreign_payload
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://official.example.com/proof?secretkey=SECRET",
        "https://official.example.com/proof?authkey=SECRET",
        "https://official.example.com/proof?sessionkey=SECRET",
        "https://official.example.com/proof?subscriptionkey=SECRET",
        "https://official.example.com/proof?secret%255Fkey=SECRET",
        "https://official.example.com/proof?payload="
        + quote(json.dumps(json.dumps({"secretkey": "SECRET"}), separators=(",", ":"))),
        "https://official.example.com/proof?payload="
        + quote(json.dumps(json.dumps(json.dumps({"sessionKey": "SECRET"})), separators=(",", ":"))),
    ),
)
def test_archive_rejects_compound_and_json_string_wrapped_secret_names_before_storage(
    tmp_path,
    unsafe_url,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item(
            "wrapped-secret",
            canonical_url=unsafe_url,
        ),))))

    assert not archive.archive_root.exists()


def test_archive_allows_json_string_wrapped_monkey_and_turnkey_names(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    inner = json.dumps({"monkey": "capuchin", "turnkey": "ready"}, separators=(",", ":"))
    wrapped = json.dumps(inner, separators=(",", ":"))
    public_url = "https://official.example.com/proof?payload=" + quote(wrapped)

    archive.upsert(snapshot(event(primary_evidence=(evidence_item(
        "wrapped-benign",
        canonical_url=public_url,
    ),))))

    assert archive.get("a" * 20)["primary_evidence"][0]["canonical_url"] == public_url


@pytest.mark.parametrize("json_scalar", ("1.25", "123456789012345678901234567890"))
def test_archive_allows_bounded_valid_json_scalars_without_sensitive_names(tmp_path, json_scalar):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    public_url = "https://official.example.com/proof?payload=" + json_scalar

    archive.upsert(snapshot(event(primary_evidence=(evidence_item(
        "scalar-benign",
        canonical_url=public_url,
    ),))))

    assert archive.get("a" * 20)["primary_evidence"][0]["canonical_url"] == public_url


def test_archive_new_role_wins_without_erasing_nonempty_public_evidence_metadata(tmp_path):
    clock = [NOW]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    shared_url = "https://official.example.com/stable-proof"
    original_published_at = NOW - timedelta(hours=1)
    archive.upsert(snapshot(event(
        primary_evidence=(replace(
            evidence_item("stable-proof", canonical_url=shared_url),
            content_source="original.publisher.example",
            collector_source="original-collector.example",
            published_at=original_published_at,
            origin_cluster="publisher:original.publisher.example",
            title="original public title",
            excerpt="original public excerpt",
        ),),
        key_fields=(KeyField(
            "amount",
            "1",
            "1",
            FieldVerificationStatus.VERIFIED,
            ("stable-proof",),
            "official",
        ),),
    )))
    clock[0] = NOW + timedelta(minutes=1)
    empty_new_metadata = replace(
        evidence_item("stable-proof", canonical_url=shared_url),
        content_source="",
        collector_source="",
        canonical_url="",
        published_at=None,
        origin_cluster="",
        title="",
        excerpt="",
        source_role=SourceRole.INDEPENDENT,
        supports_claim=False,
        supports_fields=("amount",),
        contradicts_claim=True,
        is_official=False,
    )
    archive.upsert(snapshot(
        replace(
            _timed_event("a" * 20, clock[0]),
            independent_evidence=(empty_new_metadata,),
            key_fields=(KeyField(
                "amount",
                "1",
                "1",
                FieldVerificationStatus.VERIFIED,
                ("stable-proof",),
                "official",
            ),),
        ),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
        generated_at=clock[0],
    ))

    row = archive.get("a" * 20)
    assert row["primary_evidence"] == []
    retained = row["independent_evidence"][0]
    assert retained["canonical_url"] == shared_url
    assert retained["content_source"] == "original.publisher.example"
    assert retained["collector_source"] == "original-collector.example"
    assert retained["published_at"] == original_published_at.isoformat()
    assert retained["origin_cluster"] == "publisher:original.publisher.example"
    assert retained["title"] == "original public title"
    assert retained["excerpt"] == "original public excerpt"
    assert retained["source_role"] == "independent"
    assert retained["supports_claim"] is False
    assert retained["supports_fields"] == ["amount"]
    assert retained["contradicts_claim"] is True
    assert retained["is_official"] is False
    assert row["key_fields"][0]["evidence_ids"] == ["stable-proof"]
    assert len(row["snapshot_history"]) == 2


def test_archive_rejects_thirty_thousand_distinct_index_bucket_candidates_before_bucket_io(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    start = NOW.date() - timedelta(days=29_999)
    oversized_index = {
        f"{number:020x}": f"{(start + timedelta(days=number)).isoformat()}.jsonl"
        for number in range(30_000)
    }
    _rewrite_finalized_index(archive, oversized_index)

    def forbid_bucket_read(*_args, **_kwargs):
        raise AssertionError("bucket path accessed before candidate bound")

    monkeypatch.setattr(archive, "_read_bucket", forbid_bucket_read)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_rejects_legacy_journal_bucket_candidates_before_any_bucket_path_access(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _downgrade_authority_to_legacy_state(archive)
    start = NOW - timedelta(days=archive_module._MAX_ARCHIVE_FILES)
    rows: list[dict[str, object]] = []
    for number in range(archive_module._MAX_ARCHIVE_FILES + 1):
        observed_at = start + timedelta(days=number)
        selected = snapshot(
            _timed_event(f"{number:020x}", observed_at),
            snapshot_id=f"{number + 1_000:020x}",
            raw_snapshot_id=f"{number + 2_000:020x}",
            generated_at=observed_at,
        )
        rows.append(_legacy_v1_row(archive_module._archive_document(
            selected,
            archive_module.event_document(selected.events[0]),
            NOW,
        )))
    archive.journal_path.write_text(
        json.dumps(
            {"schema_version": 1, "rows": rows},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )

    def forbid_bucket_read(*_args, **_kwargs):
        raise AssertionError("bucket path accessed before legacy candidate bound")

    monkeypatch.setattr(archive, "_read_bucket", forbid_bucket_read)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_native_transactions_never_replace_or_require_foreign_transaction_path(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.archive_root.mkdir(parents=True)
    foreign = b'{"owner":"foreign","must_survive":true}\n'
    archive.journal_path.write_bytes(foreign)

    archive.upsert(snapshot(event()))

    assert archive.journal_path.read_bytes() == foreign
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 4
    assert state["phase"] == "finalized"
    assert archive.get("a" * 20)["event_id"] == "a" * 20


def test_archive_foreign_transaction_replacement_during_finalization_is_irrelevant_and_preserved(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    first_foreign = b'{"owner":"foreign-one"}\n'
    second_foreign = b'{"owner":"foreign-two"}\n'
    archive.journal_path.write_bytes(first_foreign)
    real_atomic_write = archive._atomic_write
    replaced = False

    def replace_foreign_after_prepared(path: Path, payload: bytes, maximum: int):
        nonlocal replaced
        identity = real_atomic_write(path, payload, maximum)
        if path == archive.state_path and json.loads(payload)["phase"] == "prepared":
            archive.journal_path.write_bytes(second_foreign)
            replaced = True
        return identity

    monkeypatch.setattr(archive, "_atomic_write", replace_foreign_after_prepared)
    archive.upsert(snapshot(
        event("b" * 20),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))

    assert replaced is True
    assert archive.journal_path.read_bytes() == second_foreign
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}


def test_archive_finalized_state_is_authoritative_after_transaction_path_is_deleted(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    if archive.journal_path.exists():
        archive.journal_path.unlink()

    assert archive.get("a" * 20)["verification_status"] == "verified"
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "finalized"
    assert state["target_bucket_digests"]


def test_archive_recovers_inline_prepared_state_without_transaction_path(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    real_replace = archive_module._replace_durable

    def fail_finalized_state(source: Path, destination: Path) -> None:
        if destination == archive.state_path:
            payload = json.loads(source.read_text(encoding="utf-8"))
            if payload["phase"] == "finalized" and payload["generation"] == 2:
                raise OSError("simulated crash before finalized state")
        real_replace(source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_finalized_state)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(snapshot(
            event("b" * 20),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))

    prepared = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert prepared["phase"] == "prepared"
    assert prepared["rows"]
    if archive.journal_path.exists():
        archive.journal_path.unlink()

    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)
    archive.upsert(snapshot(
        event("c" * 20),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
    ))

    assert {row["event_id"] for row in archive.query(days=90)} == {
        "a" * 20,
        "b" * 20,
        "c" * 20,
    }


@pytest.mark.parametrize("field", ("target_bucket_digests", "target_index"))
def test_archive_finalized_state_requires_complete_authoritative_target_manifests(tmp_path, field):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    state.pop(field)
    archive.state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_rejects_partial_provenance_tuple_instead_of_constructing_mixed_origin(tmp_path):
    clock = [NOW]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    stable = replace(
        evidence_item("stable-proof"),
        content_source="publisher-one.example",
        collector_source="collector-one.example",
        published_at=NOW - timedelta(hours=1),
        origin_cluster="publisher:one",
    )
    archive.upsert(snapshot(event(primary_evidence=(stable,))))
    partial = replace(
        stable,
        content_source="publisher-two.example",
        collector_source="",
        published_at=NOW,
        origin_cluster="publisher:two",
        title="new title",
    )
    clock[0] = NOW + timedelta(minutes=1)

    with pytest.raises(ValueError, match="provenance"):
        archive.upsert(snapshot(
            replace(_timed_event("a" * 20, clock[0]), primary_evidence=(partial,)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
            generated_at=clock[0],
        ))

    retained = archive.get("a" * 20)["primary_evidence"][0]
    assert (
        retained["canonical_url"],
        retained["published_at"],
        retained["content_source"],
        retained["collector_source"],
        retained["origin_cluster"],
    ) == (
        stable.canonical_url,
        stable.published_at.isoformat(),
        stable.content_source,
        stable.collector_source,
        stable.origin_cluster,
    )


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://official.example.com/proof?apiKey2=SECRET",
        "https://official.example.com/proof?token2=SECRET",
        "https://official.example.com/proof?subscriptionKey2026=SECRET",
        "https://official.example.com/proof?payload=%257B%2522apiKey2%2522%253A%2522SECRET%2522%257D",
        "https://official.example.com/proof?apiKey2v3=SECRET",
        "https://official.example.com/proof?token2v3=SECRET",
        "https://official.example.com/proof?subscriptionKey2026v2=SECRET",
        "https://official.example.com/proof?payload=%25257B%252522apiKey2v3%252522%25253A%252522SECRET%252522%25257D",
    ),
)
def test_archive_rejects_version_suffixed_secret_names_at_all_nested_decode_layers(tmp_path, unsafe_url):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item(
            "versioned-secret",
            canonical_url=unsafe_url,
        ),))))

    assert not archive.archive_root.exists()


@pytest.mark.parametrize("legacy_schema", (1, 2, 3))
def test_archive_migrates_legacy_state_versions_without_losing_completed_rows(tmp_path, legacy_schema):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _downgrade_authority_to_legacy_state(archive, legacy_schema)

    archive.upsert(snapshot(
        event("b" * 20),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))

    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 4
    assert state["phase"] == "finalized"
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}


@pytest.mark.parametrize("journal_schema", (2, 3))
def test_archive_migrates_pending_legacy_native_journals_once_into_inline_state(
    tmp_path,
    monkeypatch,
    journal_schema,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    if journal_schema == 2:
        legacy_journal = _rewrite_pending_journal_as_v2(archive)
    else:
        prepared = json.loads(archive.state_path.read_text(encoding="utf-8"))
        legacy_journal = {
            "schema_version": 3,
            "transaction_id": prepared["transaction_id"],
            "base_generation": prepared["base_generation"],
            "target_generation": prepared["target_generation"],
            "base_index_digest": prepared["base_index_digest"],
            "target_index_digest": prepared["target_index_digest"],
            "base_bucket_digests": prepared["base_bucket_digests"],
            "target_bucket_digests": prepared["target_bucket_digests"],
            "cutoff": prepared["cutoff"],
            "target_index": prepared["target_index"],
            "rows": prepared["rows"],
        }
        archive.journal_path.write_text(
            json.dumps(legacy_journal, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        archive.state_path.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "generation": prepared["base_generation"],
                    "phase": "finalized",
                    "transaction_id": "0" * 64,
                    "index_digest": prepared["base_index_digest"],
                    "bucket_digests": {},
                },
                separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
    legacy_bytes = archive.journal_path.read_bytes()

    rows = archive.query(days=90)

    assert {row["event_id"] for row in rows} == {"a" * 20, "b" * 20}
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["schema_version"] == 4
    assert archive.journal_path.read_bytes() == legacy_bytes
    assert legacy_journal["schema_version"] == journal_schema


def test_archive_unknown_future_state_schema_fails_closed_without_touching_transaction_path(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    state["schema_version"] = 999
    archive.state_path.write_text(json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8")
    foreign = b'{"owner":"foreign"}\n'
    archive.journal_path.write_bytes(foreign)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert archive.journal_path.read_bytes() == foreign


def test_archive_complete_new_provenance_tuple_wins_as_one_source_identity(tmp_path):
    clock = [NOW]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    first = replace(
        evidence_item("stable-proof", canonical_url="https://one.example.com/proof"),
        content_source="one.example.com",
        collector_source="collector-one",
        published_at=NOW - timedelta(hours=1),
        origin_cluster="publisher:one",
    )
    archive.upsert(snapshot(event(primary_evidence=(first,))))
    clock[0] = NOW + timedelta(minutes=1)
    second = replace(
        first,
        content_source="two.example",
        collector_source="collector-two",
        published_at=clock[0],
        origin_cluster="publisher:two",
        title="new public title",
    )
    archive.upsert(snapshot(
        replace(_timed_event("a" * 20, clock[0]), primary_evidence=(second,)),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=clock[0],
    ))

    retained = archive.get("a" * 20)["primary_evidence"][0]
    assert (
        retained["canonical_url"],
        retained["published_at"],
        retained["content_source"],
        retained["collector_source"],
        retained["origin_cluster"],
    ) == (
        "https://one.example.com/proof",
        clock[0].isoformat(),
        "two.example",
        "collector-two",
        "publisher:two",
    )


@pytest.mark.parametrize("name", ("monkey2", "turnkey2026", "monkey2v3", "turnkey2026v2"))
def test_archive_versioned_benign_query_names_remain_public(tmp_path, name):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    public_url = f"https://official.example.com/proof?{name}=allowed"

    archive.upsert(snapshot(event(primary_evidence=(evidence_item(
        "benign-versioned-name",
        canonical_url=public_url,
    ),))))

    assert archive.get("a" * 20)["primary_evidence"][0]["canonical_url"] == public_url


@pytest.mark.parametrize("journal_schema", (1, 2, 3))
def test_archive_recovers_strict_journal_only_first_transaction_before_native_upsert(
    tmp_path,
    journal_schema,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    legacy_time = NOW - timedelta(hours=1)
    legacy_snapshot = snapshot(
        _timed_event("l" * 20, legacy_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=legacy_time,
    )
    journal = _journal_only_document(archive, legacy_snapshot, journal_schema)
    archive.archive_root.mkdir(parents=True)
    archive.journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    if journal_schema == 2:
        _set_journal_mtime(archive, NOW)
    original_journal = archive.journal_path.read_bytes()

    archive.upsert(snapshot(
        event("n" * 20),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
    ))

    assert {row["event_id"] for row in archive.query(days=90)} == {"l" * 20, "n" * 20}
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["schema_version"] == 4
    assert archive.journal_path.read_bytes() == original_journal


@pytest.mark.parametrize(
    "foreign",
    (
        b"{not-json\n",
        b'{"owner":"foreign"}\n',
        b'{"schema_version":999,"rows":[]}\n',
    ),
)
def test_archive_ignores_nonlegacy_foreign_transaction_on_first_native_upsert(tmp_path, foreign):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.archive_root.mkdir(parents=True)
    archive.journal_path.write_bytes(foreign)

    archive.upsert(snapshot(event()))

    assert archive.journal_path.read_bytes() == foreign
    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]


def test_archive_finalized_manifest_excludes_already_expired_bucket(tmp_path):
    expired_time = NOW - timedelta(days=91)
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)

    archive.upsert(snapshot(
        _timed_event("o" * 20, expired_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=expired_time,
    ))

    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "finalized"
    assert state["target_index"] == {}
    assert expired_time.date().isoformat() + ".jsonl" not in state["target_bucket_digests"]


def test_archive_query_survives_cleanup_of_bucket_that_aged_out_of_retention(tmp_path):
    clock = [NOW]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    old_time = NOW - timedelta(days=89)
    archive.upsert(snapshot(
        _timed_event("o" * 20, old_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=old_time,
    ))
    archive.upsert(snapshot(
        event("n" * 20),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
    ))
    clock[0] = NOW + timedelta(days=2)
    (archive.archive_root / f"{old_time.date().isoformat()}.jsonl").unlink()

    assert [row["event_id"] for row in archive.query(days=90)] == ["n" * 20]
    assert archive.count() == 1


def _legacy_finalized_state_from_v4(
    archive: EvidenceArchive,
    *,
    index_digest: str | None = None,
    bucket_digests: dict[str, str] | None = None,
) -> dict[str, object]:
    current = json.loads(archive.state_path.read_text(encoding="utf-8"))
    return {
        "schema_version": 3,
        "generation": current["generation"],
        "phase": "finalized",
        "transaction_id": current["transaction_id"],
        "index_digest": index_digest or current["target_index_digest"],
        "bucket_digests": current["target_bucket_digests"] if bucket_digests is None else bucket_digests,
    }


@pytest.mark.parametrize("authority", ("index_digest", "bucket_manifest"))
def test_archive_legacy_finalized_state_validates_declared_physical_authority(tmp_path, authority):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    current = json.loads(archive.state_path.read_text(encoding="utf-8"))
    wrong_digest = "f" * 64
    if wrong_digest == current["target_index_digest"]:
        wrong_digest = "e" * 64
    if authority == "index_digest":
        legacy = _legacy_finalized_state_from_v4(archive, index_digest=wrong_digest)
    else:
        bucket_name = next(iter(current["target_bucket_digests"]))
        legacy = _legacy_finalized_state_from_v4(
            archive,
            bucket_digests={bucket_name: wrong_digest},
        )
    archive.state_path.write_text(
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_legacy_finalized_state_rejects_injected_row_and_index(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    legacy = _legacy_finalized_state_from_v4(archive)
    persisted = archive.get("a" * 20)
    injected_snapshot = snapshot(
        event("b" * 20),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    )
    injected = archive_module._archive_document(
        injected_snapshot,
        archive_module.event_document(injected_snapshot.events[0]),
        NOW,
    )
    bucket_name = archive_module._bucket_name(injected)
    _write_bucket_for_test(archive, bucket_name, [persisted, injected])
    archive._write_index({"a" * 20: bucket_name, "b" * 20: bucket_name})
    archive.state_path.write_text(
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_scandir_stops_after_bounded_total_entries_before_sorting(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive._prepare()
    yielded = 0
    total = archive_module._MAX_ARCHIVE_FILES + 100
    nofollow_stats = 0
    real_stat = Path.stat

    class ForeignEntry:
        def __init__(self, number: int) -> None:
            self.name = f"foreign-{number}"

    class ManyForeignEntries:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            nonlocal yielded
            for number in range(total):
                yielded += 1
                yield ForeignEntry(number)

    def count_nofollow_stat(path: Path, *args, **kwargs):
        nonlocal nofollow_stats
        if path.parent == archive.archive_root and path.name.startswith("foreign-"):
            nofollow_stats += 1
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(archive_module.os, "scandir", lambda _path: ManyForeignEntries())
    monkeypatch.setattr(Path, "stat", count_nofollow_stat)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive._bucket_names()

    # One sentinel entry may be fetched to prove the bounded scan is over-cap,
    # but no more than the configured 520 candidates may reach no-follow stat.
    assert yielded <= archive_module._MAX_ARCHIVE_DIRECTORY_SCAN_ENTRIES + 1
    assert nofollow_stats <= archive_module._MAX_ARCHIVE_DIRECTORY_SCAN_ENTRIES


def test_archive_state_cutoff_overflow_is_normalized_to_storage_corrupt(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    state["cutoff"] = "9999-12-31T23:59:59.999999+00:00"
    archive.state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_legacy_journal_cutoff_overflow_is_normalized_to_storage_corrupt(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    current = json.loads(archive.state_path.read_text(encoding="utf-8"))
    legacy_state = _legacy_finalized_state_from_v4(archive, bucket_digests={})
    archive.state_path.write_text(
        json.dumps(legacy_state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    journal = {
        "schema_version": 3,
        "transaction_id": "0" * 64,
        "base_generation": current["generation"],
        "target_generation": current["generation"] + 1,
        "base_index_digest": current["target_index_digest"],
        "target_index_digest": current["target_index_digest"],
        "base_bucket_digests": current["target_bucket_digests"],
        "target_bucket_digests": current["target_bucket_digests"],
        "cutoff": "9999-12-31T23:59:59.999999+00:00",
        "target_index": current["target_index"],
        "rows": [],
    }
    archive.journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_state_clock_skew_overflow_is_normalized_to_storage_corrupt(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    maximum = datetime.max.replace(tzinfo=timezone.utc)
    state["cutoff"] = (maximum - timedelta(days=90)).isoformat()
    archive.state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    archive._now = lambda: maximum

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_empty_reader_does_not_overflow_after_maximum_utc_day(tmp_path):
    archive = EvidenceArchive(
        tmp_path,
        now=lambda: datetime.max.replace(tzinfo=timezone.utc),
    )
    archive.archive_root.mkdir(parents=True)

    assert archive.query(days=1) == []


def test_archive_manifest_keeps_nonempty_cutoff_date_bucket_after_exact_row_expires(tmp_path):
    clock = [NOW]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    boundary_time = NOW - timedelta(days=89, hours=23, minutes=59)
    boundary_bucket = f"{boundary_time.date().isoformat()}.jsonl"
    archive.upsert(snapshot(
        _timed_event("b" * 20, boundary_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=boundary_time,
    ))

    clock[0] = NOW + timedelta(minutes=2)
    archive.upsert(EvidenceSnapshot(
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=clock[0],
        events=(),
    ))

    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["target_index"] == {}
    assert set(state["target_bucket_digests"]) == {boundary_bucket}
    assert archive.query(days=90) == []

    clock[0] = NOW + timedelta(hours=12, minutes=1)
    (archive.archive_root / boundary_bucket).unlink()
    assert archive.query(days=90) == []


def test_archive_future_skew_bucket_is_manifested_without_becoming_queryable_early(tmp_path):
    transaction_time = NOW.replace(hour=23, minute=59)
    clock = [transaction_time]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    future_time = transaction_time + timedelta(minutes=2)
    future_bucket = f"{future_time.date().isoformat()}.jsonl"

    archive.upsert(snapshot(
        _timed_event("f" * 20, future_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=transaction_time,
    ))

    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["target_index"] == {}
    assert set(state["target_bucket_digests"]) == {future_bucket}
    assert archive.query(days=90) == []

    clock[0] = transaction_time + timedelta(minutes=3)
    assert [row["event_id"] for row in archive.query(days=90)] == ["f" * 20]


def test_archive_legacy_finalized_manifest_must_declare_every_active_canonical_bucket(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    old_time = NOW - timedelta(days=1)
    archive.upsert(snapshot(
        _timed_event("o" * 20, old_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=old_time,
    ))
    archive.upsert(snapshot(
        event("n" * 20),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
    ))
    current = json.loads(archive.state_path.read_text(encoding="utf-8"))
    current_bucket = f"{NOW.date().isoformat()}.jsonl"
    legacy = _legacy_finalized_state_from_v4(
        archive,
        bucket_digests={current_bucket: current["target_bucket_digests"][current_bucket]},
    )
    archive.state_path.write_text(
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_legacy_finalized_migration_ignores_cleaned_whole_date_bucket(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    expired_time = NOW - timedelta(days=91)
    archive.upsert(snapshot(
        _timed_event("o" * 20, expired_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=expired_time,
    ))
    archive.upsert(snapshot(
        event("n" * 20),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
    ))
    expired_bucket = f"{expired_time.date().isoformat()}.jsonl"
    expired_path = archive.archive_root / expired_bucket
    expired_digest = archive._payload_digest(expired_path.read_bytes())
    current = json.loads(archive.state_path.read_text(encoding="utf-8"))
    legacy = _legacy_finalized_state_from_v4(
        archive,
        bucket_digests={**current["target_bucket_digests"], expired_bucket: expired_digest},
    )
    archive.state_path.write_text(
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    expired_path.unlink()

    assert [row["event_id"] for row in archive.query(days=90)] == ["n" * 20]
    migrated = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert expired_bucket not in migrated["target_bucket_digests"]


def test_archive_migrates_empty_v1_first_transaction_as_a_strict_noop(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.archive_root.mkdir(parents=True)
    journal = b'{"schema_version":1,"rows":[]}\n'
    archive.journal_path.write_bytes(journal)

    archive.upsert(snapshot(event("n" * 20)))

    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 4
    assert state["phase"] == "finalized"
    assert state["target_index"] == {"n" * 20: f"{NOW.date().isoformat()}.jsonl"}
    assert [row["event_id"] for row in archive.query(days=90)] == ["n" * 20]
    assert archive.journal_path.read_bytes() == journal


@pytest.mark.parametrize("schema_version", (1, 2, 3))
def test_archive_ignores_schema_numbered_foreign_owner_transaction_before_first_upsert(
    tmp_path,
    schema_version,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.archive_root.mkdir(parents=True)
    foreign = json.dumps(
        {"schema_version": schema_version, "owner": "foreign"},
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    archive.journal_path.write_bytes(foreign)

    archive.upsert(snapshot(event()))

    assert archive.journal_path.read_bytes() == foreign
    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]


def test_archive_malformed_exact_legacy_transaction_still_fails_closed(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.archive_root.mkdir(parents=True)
    malformed = b'{"schema_version":1,"rows":"not-a-list"}\n'
    archive.journal_path.write_bytes(malformed)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(event()))

    assert archive.journal_path.read_bytes() == malformed
    assert not archive.state_path.exists()


def test_archive_rejects_query_name_that_exceeds_fixed_suffix_normalization_rounds(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    over_budget_name = "monkey" + "2v3" * 20
    public_url = f"https://official.example.com/proof?{over_budget_name}=allowed"

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item(
            "suffix-budget",
            canonical_url=public_url,
        ),))))

    assert not archive.archive_root.exists()


def test_archive_rejects_oversized_nested_json_query_key_before_storage(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    payload = quote(json.dumps({"x" * 129: "allowed"}, separators=(",", ":")))
    public_url = f"https://official.example.com/proof?payload={payload}"

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item(
            "query-key-budget",
            canonical_url=public_url,
        ),))))

    assert not archive.archive_root.exists()


def _empty_v2_journal(archive: EvidenceArchive) -> dict[str, object]:
    target_index_digest = archive._payload_digest(archive._index_payload({}))
    document: dict[str, object] = {
        "schema_version": 2,
        "transaction_id": "",
        "base_generation": 0,
        "target_generation": 1,
        "base_index_digest": archive_module._MISSING_DIGEST,
        "target_index_digest": target_index_digest,
        "base_bucket_digests": {},
        "target_bucket_digests": {},
        "rows": [],
    }
    document["transaction_id"] = archive._journal_transaction_id(
        base_generation=0,
        target_generation=1,
        base_index_digest=archive_module._MISSING_DIGEST,
        target_index_digest=target_index_digest,
        base_bucket_digests={},
        target_bucket_digests={},
        rows=[],
    )
    return document


def test_archive_recovers_v2_journal_only_transaction_with_its_original_time_projection(
    tmp_path,
    monkeypatch,
):
    clock = [NOW]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    original = snapshot(
        _timed_event("o" * 20, NOW),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=NOW,
    )
    journal = _journal_only_document(archive, original, 2)
    archive.archive_root.mkdir(parents=True)
    archive.journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _set_journal_mtime(archive, NOW)

    recovered_cutoffs: list[datetime] = []
    real_recover = archive._recover_prepared_authority

    def record_recovery(state, **kwargs):
        recovered_cutoffs.append(state["cutoff"])
        return real_recover(state, **kwargs)

    monkeypatch.setattr(archive, "_recover_prepared_authority", record_recovery)
    clock[0] = NOW + timedelta(days=91)
    archive.upsert(snapshot(
        _timed_event("n" * 20, clock[0]),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=clock[0],
    ))

    assert recovered_cutoffs[0] == NOW - timedelta(days=90)
    native = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert native["cutoff"] == (clock[0] - timedelta(days=90)).isoformat()
    assert native["target_index"] == {"n" * 20: f"{clock[0].date().isoformat()}.jsonl"}
    assert [row["event_id"] for row in archive.query(days=90)] == ["n" * 20]


def test_archive_recovers_empty_v2_journal_only_transaction_as_noop(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.archive_root.mkdir(parents=True)
    document = _empty_v2_journal(archive)
    archive.journal_path.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _set_journal_mtime(archive, NOW)

    archive.upsert(snapshot(event("n" * 20)))

    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 4
    assert state["target_index"] == {"n" * 20: f"{NOW.date().isoformat()}.jsonl"}
    assert [row["event_id"] for row in archive.query(days=90)] == ["n" * 20]


def test_archive_v2_journal_only_recovery_rejects_target_index_digest_mismatch(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    document = _empty_v2_journal(archive)
    document["target_index_digest"] = "f" * 64
    document["transaction_id"] = archive._journal_transaction_id(
        base_generation=document["base_generation"],
        target_generation=document["target_generation"],
        base_index_digest=document["base_index_digest"],
        target_index_digest=document["target_index_digest"],
        base_bucket_digests=document["base_bucket_digests"],
        target_bucket_digests=document["target_bucket_digests"],
        rows=document["rows"],
    )
    archive.archive_root.mkdir(parents=True)
    archive.journal_path.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _set_journal_mtime(archive, NOW)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(event("n" * 20)))

    assert not archive.state_path.exists()


@pytest.mark.parametrize("fault_phase", ("prepared", "bucket", "index", "finalized"))
def test_archive_legacy_migration_persists_recoverable_v4_state_before_physical_projection(
    tmp_path,
    monkeypatch,
    fault_phase,
):
    current = snapshot(
        _timed_event("c" * 20, NOW),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=NOW,
    )
    expired_time = NOW - timedelta(days=91)
    expired = snapshot(
        _timed_event("o" * 20, expired_time),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=expired_time,
    )
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    legacy = _write_historical_v1_finalized_archive(archive, (expired, current))
    real_atomic_write = archive._atomic_write
    observed_phases: list[str] = []
    prepared_documents: list[dict[str, object]] = []

    def fail_selected_phase(path: Path, payload: bytes, maximum: int):
        selected_phase: str | None = None
        state_document: dict[str, object] | None = None
        if path == archive.state_path:
            state_document = json.loads(payload)
            selected_phase = state_document["phase"]
        elif path == archive.index_path:
            selected_phase = "index"
        elif path.suffix == ".jsonl":
            selected_phase = "bucket"
        if selected_phase == "prepared" and state_document is not None:
            prepared_documents.append(state_document)
        if selected_phase == fault_phase:
            observed_phases.append(selected_phase)
            raise OSError(f"simulated {selected_phase} migration fault")
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", fail_selected_phase)
    with pytest.raises(OSError):
        archive.query(days=90)

    assert observed_phases == [fault_phase]
    if fault_phase == "prepared":
        assert archive.state_path.read_bytes() == legacy["state_bytes"]
        assert archive.index_path.read_bytes() == legacy["index_bytes"]
        assert {
            path.name: path.read_bytes()
            for path in archive.archive_root.glob("*.jsonl")
        } == legacy["bucket_bytes"]
    else:
        pending = json.loads(archive.state_path.read_text(encoding="utf-8"))
        assert pending["schema_version"] == 4
        assert pending["phase"] == "prepared"
        assert set(pending) == archive_module._PREPARED_STATE_KEYS
        assert pending["base_generation"] == 1
        assert pending["target_generation"] == 2
        assert pending["base_bucket_digests"]
        assert pending["target_bucket_digests"]
        assert pending["rows"]
        assert pending["cutoff"] == (NOW - timedelta(days=90)).isoformat()
    assert prepared_documents

    monkeypatch.setattr(archive, "_atomic_write", real_atomic_write)
    assert [row["event_id"] for row in archive.query(days=90)] == ["c" * 20]
    finalized = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert finalized["schema_version"] == 4
    assert finalized["phase"] == "finalized"
    assert finalized["target_index"] == {
        "c" * 20: f"{NOW.date().isoformat()}.jsonl",
    }
    assert archive._payload_digest(archive.index_path.read_bytes()) == finalized["target_index_digest"]
    assert {
        name: archive._payload_digest((archive.archive_root / name).read_bytes())
        for name in finalized["target_bucket_digests"]
    } == finalized["target_bucket_digests"]


def test_archive_migrates_historical_v1_finalized_empty_manifest_with_explicit_unverifiable_truth(
    tmp_path,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    _write_historical_v1_finalized_archive(archive, (snapshot(event()),))

    rows = archive.query(days=90)

    assert [row["event_id"] for row in rows] == ["a" * 20]
    assert all(
        lineage.get("legacy_v1") is True
        and lineage.get("legacy_unverifiable") is True
        for lineage in rows[0]["snapshot_history"]
    )
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 4
    assert state["phase"] == "finalized"
    assert state["target_bucket_digests"] == {
        name: archive._payload_digest((archive.archive_root / name).read_bytes())
        for name in state["target_bucket_digests"]
    }


def test_archive_legacy_migration_preflights_mutation_budget_before_prepared_state(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    legacy = _write_historical_v1_finalized_archive(archive, (snapshot(event()),))
    monkeypatch.setattr(archive_module, "_MAX_MUTATION_BYTES", 1)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert archive.state_path.read_bytes() == legacy["state_bytes"]
    assert archive.index_path.read_bytes() == legacy["index_bytes"]
    assert {
        path.name: path.read_bytes()
        for path in archive.archive_root.glob("*.jsonl")
    } == legacy["bucket_bytes"]


@pytest.mark.parametrize(
    "corruption",
    (
        "state_keys",
        "index_digest",
        "index_projection",
        "bucket_schema",
        "bucket_identity",
        "unindexed_corrupt_row",
    ),
)
def test_archive_historical_v1_empty_manifest_still_requires_exact_physical_projection(
    tmp_path,
    corruption,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    legacy = _write_historical_v1_finalized_archive(archive, (snapshot(event()),))
    state = json.loads(legacy["state_bytes"])
    row = _legacy_v1_row(legacy["rows"][0])
    bucket_name = archive_module._bucket_name(legacy["rows"][0])
    if corruption == "state_keys":
        state["unexpected"] = True
    elif corruption == "index_digest":
        state["index_digest"] = "f" * 64
    elif corruption == "index_projection":
        index_bytes = archive._index_payload({"b" * 20: bucket_name})
        archive.index_path.write_bytes(index_bytes)
        state["index_digest"] = archive._payload_digest(index_bytes)
    elif corruption == "bucket_schema":
        row["schema_version"] = 999
        (archive.archive_root / bucket_name).write_text(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif corruption == "bucket_identity":
        row["event_id"] = "b" * 20
        (archive.archive_root / bucket_name).write_text(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    else:
        row.pop("title")
        index_bytes = archive._index_payload({})
        archive.index_path.write_bytes(index_bytes)
        state["index_digest"] = archive._payload_digest(index_bytes)
        (archive.archive_root / bucket_name).write_text(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    archive.state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_schema_v1_nonempty_manifest_remains_strictly_digest_bound(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    legacy = _write_historical_v1_finalized_archive(archive, (snapshot(event()),))
    state = json.loads(legacy["state_bytes"])
    bucket_name = next(iter(legacy["bucket_bytes"]))
    state["bucket_digests"] = {bucket_name: "f" * 64}
    archive.state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_v2_cutoff_uses_latest_common_snapshot_lineage_not_earliest_archive_time(
    tmp_path,
    monkeypatch,
):
    transaction_start = NOW
    update_time = NOW + timedelta(days=1)
    clock = [transaction_start]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    future_time = transaction_start + timedelta(minutes=2)
    archive.upsert(snapshot(
        _timed_event("s" * 20, future_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=transaction_start,
    ))
    clock[0] = update_time
    updated = replace(
        _timed_event("s" * 20, update_time, status=VerificationStatus.DISPROVED),
        status_history=(
            StatusTransition(None, VerificationStatus.VERIFIED, future_time, "reason-verified"),
            StatusTransition(
                VerificationStatus.VERIFIED,
                VerificationStatus.DISPROVED,
                update_time,
                "reason-disproved",
            ),
        ),
    )
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            updated,
            snapshot_id="3" * 20,
            raw_snapshot_id="4" * 20,
            generated_at=update_time,
        ),
    )
    journal = _rewrite_pending_journal_as_v2(archive)
    assert journal["rows"][0]["archived_at"] == transaction_start.isoformat()
    assert journal["rows"][0]["snapshot_generated_at"] == update_time.isoformat()
    recovered_cutoffs: list[datetime] = []
    real_recover = archive._recover_prepared_authority

    def record_recovery(state, **kwargs):
        recovered_cutoffs.append(state["cutoff"])
        return real_recover(state, **kwargs)

    monkeypatch.setattr(archive, "_recover_prepared_authority", record_recovery)
    clock[0] = update_time + timedelta(days=91)
    archive.upsert(snapshot(
        _timed_event("n" * 20, clock[0]),
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
        generated_at=clock[0],
    ))

    assert recovered_cutoffs[0] == update_time - timedelta(days=90)
    assert [row["event_id"] for row in archive.query(days=90)] == ["n" * 20]


def test_archive_v2_target_index_prunes_expired_unrelated_base_ids_before_journal_rows(
    tmp_path,
    monkeypatch,
):
    base_time = NOW - timedelta(days=2)
    expired_time = base_time - timedelta(days=89)
    clock = [base_time]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    archive.upsert(snapshot(
        _timed_event("o" * 20, expired_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=expired_time,
    ))
    assert archive._read_index() == {
        "o" * 20: f"{expired_time.date().isoformat()}.jsonl",
    }
    clock[0] = NOW
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            _timed_event("n" * 20, NOW),
            snapshot_id="3" * 20,
            raw_snapshot_id="4" * 20,
            generated_at=NOW,
        ),
    )
    journal = _rewrite_pending_journal_as_v2(archive)
    expected_target = {"n" * 20: f"{NOW.date().isoformat()}.jsonl"}
    assert journal["target_index_digest"] == archive._payload_digest(
        archive._index_payload(expected_target)
    )

    assert [row["event_id"] for row in archive.query(days=90)] == ["n" * 20]
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "finalized"
    assert state["target_index"] == expected_target
    assert archive._read_index() == expected_target


def test_archive_v2_recovery_uses_journal_mtime_for_old_import_and_newer_base(
    tmp_path,
    monkeypatch,
):
    transaction_time = NOW
    clock = [transaction_time]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    newer_base_time = transaction_time - timedelta(days=1)
    archive.upsert(snapshot(
        _timed_event("b" * 20, newer_base_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=newer_base_time,
    ))
    imported_time = transaction_time - timedelta(days=30)
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            _timed_event("i" * 20, imported_time),
            snapshot_id="3" * 20,
            raw_snapshot_id="4" * 20,
            generated_at=imported_time,
        ),
    )
    journal = _rewrite_pending_journal_as_v2(archive)
    transaction_ns = int(transaction_time.timestamp() * 1_000_000_000)
    os.utime(archive.journal_path, ns=(transaction_ns, transaction_ns))
    expected_original_target = {
        "b" * 20: f"{newer_base_time.date().isoformat()}.jsonl",
        "i" * 20: f"{imported_time.date().isoformat()}.jsonl",
    }
    assert journal["target_index_digest"] == archive._payload_digest(
        archive._index_payload(expected_original_target)
    )

    recovered_states: list[dict[str, object]] = []
    real_recover = archive._recover_prepared_authority

    def record_recovery(state, **kwargs):
        recovered_states.append(state)
        return real_recover(state, **kwargs)

    monkeypatch.setattr(archive, "_recover_prepared_authority", record_recovery)
    clock[0] = transaction_time + timedelta(days=91)
    archive.upsert(snapshot(
        _timed_event("n" * 20, clock[0]),
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
        generated_at=clock[0],
    ))

    assert recovered_states[0]["cutoff"] == transaction_time - timedelta(days=90)
    assert recovered_states[0]["target_index"] == expected_original_target
    assert [row["event_id"] for row in archive.query(days=90)] == ["n" * 20]


def test_archive_v2_recovery_falls_back_to_unique_event_window_when_mtime_moved(
    tmp_path,
    monkeypatch,
):
    transaction_time = NOW
    clock = [transaction_time]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    newer_base_time = transaction_time - timedelta(days=1)
    archive.upsert(snapshot(
        _timed_event("b" * 20, newer_base_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=newer_base_time,
    ))
    imported_time = transaction_time - timedelta(days=30)
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            _timed_event("i" * 20, imported_time),
            snapshot_id="3" * 20,
            raw_snapshot_id="4" * 20,
            generated_at=imported_time,
        ),
    )
    journal = _rewrite_pending_journal_as_v2(archive)
    moved_time = transaction_time + timedelta(days=91)
    moved_ns = int(moved_time.timestamp() * 1_000_000_000)
    os.utime(archive.journal_path, ns=(moved_ns, moved_ns))
    assert journal["target_index_digest"] == archive._payload_digest(
        archive._index_payload({
            "b" * 20: f"{newer_base_time.date().isoformat()}.jsonl",
            "i" * 20: f"{imported_time.date().isoformat()}.jsonl",
        })
    )

    recovered_states: list[dict[str, object]] = []
    real_recover = archive._recover_prepared_authority

    def record_recovery(state, **kwargs):
        recovered_states.append(state)
        return real_recover(state, **kwargs)

    monkeypatch.setattr(archive, "_recover_prepared_authority", record_recovery)
    clock[0] = moved_time
    archive.upsert(snapshot(
        _timed_event("r" * 20, moved_time),
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
        generated_at=moved_time,
    ))

    assert recovered_states[0]["target_index"] == {
        "b" * 20: f"{newer_base_time.date().isoformat()}.jsonl",
        "i" * 20: f"{imported_time.date().isoformat()}.jsonl",
    }
    assert recovered_states[0]["cutoff"] == (
        transaction_time - archive_module._MAX_CLOCK_SKEW - timedelta(days=90)
    )
    assert [row["event_id"] for row in archive.query(days=90)] == ["r" * 20]


def test_archive_first_publication_interleave_rechecks_lock_instead_of_scanning_unlocked(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "store"
    (root / "archive").mkdir(parents=True)
    reader = EvidenceArchive(root, now=lambda: NOW)
    writer = EvidenceArchive(root, now=lambda: NOW)
    real_open = reader._open_existing_lock
    open_calls = 0

    def publish_between_lock_probe_and_empty_decision():
        nonlocal open_calls
        open_calls += 1
        handle = real_open()
        if open_calls == 1:
            assert handle is None
            writer.upsert(snapshot(event("f" * 20)))
            return None
        return handle

    monkeypatch.setattr(reader, "_open_existing_lock", publish_between_lock_probe_and_empty_decision)

    rows = reader.query(days=90)

    assert [row["event_id"] for row in rows] == ["f" * 20]
    assert open_calls == 2


def test_archive_first_publication_multiprocess_race_rechecks_created_lock(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "store"
    (root / "archive").mkdir(parents=True)
    reader = EvidenceArchive(root, now=lambda: NOW)
    real_open = reader._open_existing_lock
    context = multiprocessing.get_context("spawn")
    process: multiprocessing.Process | None = None
    open_calls = 0

    def publish_in_child_after_missing_lock():
        nonlocal open_calls, process
        open_calls += 1
        handle = real_open()
        if open_calls == 1:
            assert handle is None
            process = context.Process(target=_upsert_in_child, args=(str(root), "m" * 20))
            process.start()
            process.join(15)
            assert process.exitcode == 0
            return None
        return handle

    monkeypatch.setattr(reader, "_open_existing_lock", publish_in_child_after_missing_lock)

    rows = reader.query(days=90)

    assert process is not None and process.exitcode == 0
    assert [row["event_id"] for row in rows] == ["m" * 20]
    assert open_calls == 2


def test_archive_empty_reader_linearizes_once_without_creating_a_lock(tmp_path, monkeypatch):
    root = tmp_path / "store"
    (root / "archive").mkdir(parents=True)
    archive = EvidenceArchive(root, now=lambda: NOW)
    real_open = archive._open_existing_lock
    open_calls = 0

    def count_open_calls():
        nonlocal open_calls
        open_calls += 1
        return real_open()

    monkeypatch.setattr(archive, "_open_existing_lock", count_open_calls)

    assert archive.query(days=90) == []
    assert open_calls == 1
    assert not archive.lock_path.exists()


def test_archive_legacy_migration_caches_journal_and_charges_final_state_read(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            event("b" * 20),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ),
    )
    _rewrite_pending_journal_as_v2(archive)
    reads: dict[Path, int] = {}
    real_read = archive._read_bytes

    def count_reads(path: Path, maximum: int, **kwargs):
        reads[path] = reads.get(path, 0) + 1
        return real_read(path, maximum, **kwargs)

    monkeypatch.setattr(archive, "_read_bytes", count_reads)

    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}
    assert reads[archive.journal_path] == 1
    assert reads[archive.state_path] == 2
    assert archive.last_diagnostics["scanned_files"] >= 4


def test_archive_legacy_migration_fails_closed_when_final_state_read_exceeds_shared_budget(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    with archive._process_lock():
        pass
    archive.journal_path.write_text(
        json.dumps(_empty_v2_journal(archive), ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _set_journal_mtime(archive, NOW)
    monkeypatch.setattr(archive_module, "_MAX_SCAN_FILES", 1)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_v2_mtime_within_authenticated_clock_skew_is_causal(tmp_path, monkeypatch):
    clock = [NOW]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    imported_time = NOW - timedelta(days=30)
    row_contract_time = NOW + timedelta(minutes=4)
    selected = snapshot(
        replace(
            event(
                "i" * 20,
                published_at=imported_time,
                history=(StatusTransition(
                    None,
                    VerificationStatus.VERIFIED,
                    row_contract_time,
                    "reason-verified",
                ),),
            ),
            verified_at=row_contract_time,
            evidence_as_of=row_contract_time,
        ),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=NOW,
    )
    journal = _journal_only_document(archive, selected, 2)
    archive.archive_root.mkdir(parents=True)
    archive.journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _set_journal_mtime(archive, NOW + timedelta(minutes=1))

    recovered_states: list[dict[str, object]] = []
    real_recover = archive._recover_prepared_authority

    def record_recovery(state, **kwargs):
        recovered_states.append(state)
        return real_recover(state, **kwargs)

    monkeypatch.setattr(archive, "_recover_prepared_authority", record_recovery)
    clock[0] = NOW + timedelta(days=91)
    archive.upsert(snapshot(
        _timed_event("n" * 20, clock[0]),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=clock[0],
    ))

    assert recovered_states[0]["cutoff"] == NOW + timedelta(minutes=1) - timedelta(days=90)
    assert recovered_states[0]["target_index"] == {
        "i" * 20: f"{imported_time.date().isoformat()}.jsonl",
    }


def test_archive_v2_fallback_includes_pruned_physical_rows_with_bounded_work(
    tmp_path,
    monkeypatch,
):
    expired_time = NOW - timedelta(days=91)
    newer_base_time = NOW - timedelta(days=1)
    imported_time = NOW - timedelta(days=30)
    clock = [NOW - timedelta(days=2)]
    archive = EvidenceArchive(tmp_path, now=lambda: clock[0])
    archive.upsert(snapshot(
        _timed_event("o" * 20, expired_time),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=expired_time,
    ))
    clock[0] = newer_base_time
    archive.upsert(snapshot(
        _timed_event("b" * 20, newer_base_time),
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=newer_base_time,
    ))
    clock[0] = NOW
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            _timed_event("i" * 20, imported_time),
            snapshot_id="5" * 20,
            raw_snapshot_id="6" * 20,
            generated_at=NOW,
        ),
    )
    journal = _rewrite_pending_journal_as_v2(archive)
    expected_target = {
        "b" * 20: f"{newer_base_time.date().isoformat()}.jsonl",
        "i" * 20: f"{imported_time.date().isoformat()}.jsonl",
    }
    assert journal["target_index_digest"] == archive._payload_digest(
        archive._index_payload(expected_target)
    )
    imported_row = journal["rows"][0]
    _write_bucket_for_test(
        archive,
        archive_module._bucket_name(imported_row),
        [imported_row],
    )
    archive._write_index(expected_target)
    _set_journal_mtime(archive, NOW + timedelta(days=91))
    monkeypatch.setattr(archive_module, "_MAX_V2_CUTOFF_CANDIDATES", 8)

    recovered_states: list[dict[str, object]] = []
    real_recover = archive._recover_prepared_authority

    def record_recovery(state, **kwargs):
        recovered_states.append(state)
        return real_recover(state, **kwargs)

    monkeypatch.setattr(archive, "_recover_prepared_authority", record_recovery)
    clock[0] = NOW + timedelta(days=91)
    archive.upsert(snapshot(
        _timed_event("n" * 20, clock[0]),
        snapshot_id="7" * 20,
        raw_snapshot_id="8" * 20,
        generated_at=clock[0],
    ))

    assert recovered_states[0]["target_index"] == expected_target
    assert recovered_states[0]["cutoff"] == (
        NOW - archive_module._MAX_CLOCK_SKEW - timedelta(days=90)
    )
    assert [row["event_id"] for row in archive.query(days=90)] == ["n" * 20]


def test_archive_v2_fallback_derives_large_known_target_without_candidate_enumeration(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW + timedelta(days=91))
    physical_rows: dict[str, dict[str, object]] = {}
    locations: dict[str, set[str]] = {}
    for offset in range(20):
        event_time = NOW - timedelta(days=120 - (offset * 2))
        event_id = f"{offset:020d}"
        selected = snapshot(
            _timed_event(event_id, event_time),
            snapshot_id=f"{offset + 100:020d}",
            raw_snapshot_id=f"{offset + 200:020d}",
            generated_at=event_time,
        )
        row = archive_module._archive_document(
            selected,
            archive_module.event_document(selected.events[0]),
            event_time,
        )
        physical_rows[event_id] = row
        locations[event_id] = {archive_module._bucket_name(row)}

    imported_time = NOW - timedelta(days=100)
    generated_at = NOW - timedelta(days=30)
    imported = snapshot(
        _timed_event("j" * 20, imported_time),
        snapshot_id="7" * 20,
        raw_snapshot_id="8" * 20,
        generated_at=generated_at,
    )
    journal_row = archive_module._archive_document(
        imported,
        archive_module.event_document(imported.events[0]),
        generated_at,
    )
    target_index = {
        event_id: archive_module._bucket_name(row)
        for event_id, row in physical_rows.items()
        if NOW - timedelta(days=90) <= archive_module._event_time(row) <= NOW
    }
    target_digest = archive._payload_digest(archive._index_payload(target_index))
    journal = {
        "legacy": False,
        "native_schema_version": 2,
        "rows": [journal_row],
        "base_index_digest": archive_module._MISSING_DIGEST,
        "target_index_digest": target_digest,
        "transaction_mtime": NOW + timedelta(days=200),
    }
    monkeypatch.setattr(archive_module, "_MAX_V2_CUTOFF_CANDIDATES", 8)

    cutoff, recovered_target = archive._resolve_v2_transaction_projection(
        journal,
        target_index,
        physical_rows=physical_rows,
        locations=locations,
        physical_index_digest=target_digest,
        now=NOW + timedelta(days=91),
    )

    assert recovered_target == target_index
    assert {
        event_id: archive_module._bucket_name(row)
        for event_id, row in {**physical_rows, journal_row["event_id"]: journal_row}.items()
        if cutoff <= archive_module._event_time(row) <= cutoff + timedelta(days=90)
    } == target_index


@pytest.mark.parametrize("budget_dimension", ("bytes", "files"))
def test_archive_state_budget_is_rejected_before_payload_io(
    tmp_path,
    monkeypatch,
    budget_dimension,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("s" * 20)))
    state_size = archive.state_path.stat().st_size
    if budget_dimension == "bytes":
        monkeypatch.setattr(archive_module, "_MAX_SCAN_BYTES", state_size - 1)
    else:
        monkeypatch.setattr(archive_module, "_MAX_SCAN_FILES", 0)
    reads: list[Path] = []
    real_read = archive._read_bytes

    def record_reads(path: Path, maximum: int, **kwargs):
        if path == archive.state_path:
            reads.append(path)
        return real_read(path, maximum, **kwargs)

    monkeypatch.setattr(archive, "_read_bytes", record_reads)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert reads == []


def test_archive_prepared_state_row_budget_is_rejected_before_json_materialization(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    state_bytes = archive.state_path.read_bytes()
    monkeypatch.setattr(archive_module, "_MAX_SCAN_ROWS", 0)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_state_parse(raw: bytes):
        nonlocal parse_calls
        if raw == state_bytes:
            parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_state_parse)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert parse_calls == 0


def test_archive_duplicate_state_rows_cannot_bypass_preparse_row_budget(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    original = archive.state_path.read_bytes()
    state_bytes = b'{"rows":[],' + original[1:]
    archive.state_path.write_bytes(state_bytes)
    monkeypatch.setattr(archive_module, "_MAX_SCAN_ROWS", 0)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_state_parse(raw: bytes):
        nonlocal parse_calls
        if raw == state_bytes:
            parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_state_parse)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert parse_calls == 0


def test_archive_journal_budget_is_rejected_before_payload_io(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    with archive._process_lock():
        pass
    archive.journal_path.write_text(
        json.dumps(_empty_v2_journal(archive), ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _set_journal_mtime(archive, NOW)
    monkeypatch.setattr(
        archive_module,
        "_MAX_SCAN_BYTES",
        archive.journal_path.stat().st_size - 1,
    )
    reads: list[Path] = []
    real_read = archive._read_bytes

    def record_reads(path: Path, maximum: int, **kwargs):
        if path == archive.journal_path:
            reads.append(path)
        return real_read(path, maximum, **kwargs)

    monkeypatch.setattr(archive, "_read_bytes", record_reads)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert reads == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_archive_first_publication_skips_canonical_symlink_before_legacy_authority(tmp_path):
    root = tmp_path / "store"
    archive_root = root / "archive"
    archive_root.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"outside":"must-not-be-read"}\n', encoding="utf-8")
    os.symlink(outside, archive_root / "2026-08-20.jsonl")
    archive = EvidenceArchive(root, now=lambda: NOW)

    assert archive.query(days=90) == []
    assert not archive.lock_path.exists()


def test_archive_query_skips_one_unmanifested_future_schema_row_with_diagnostic(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    with archive._process_lock():
        pass
    selected = snapshot(event("v" * 20))
    valid = archive_module._archive_document(
        selected,
        archive_module.event_document(selected.events[0]),
        NOW,
    )
    future = json.loads(json.dumps(valid, ensure_ascii=False))
    future["event_id"] = "z" * 20
    future["schema_version"] = 99
    bucket = archive.archive_root / "2026-08-20.jsonl"
    bucket.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            for row in (_legacy_v1_row(valid), future)
        ) + "\n",
        encoding="utf-8",
    )

    rows = archive.query(days=90)

    assert [row["event_id"] for row in rows] == ["v" * 20]
    assert archive.last_diagnostics["skipped_corrupt_rows"] == 1


@pytest.mark.parametrize("authority_kind", ("v4_prepared", "v2_journal"))
def test_archive_target_index_cannot_substitute_a_missing_physical_target_row(
    tmp_path,
    monkeypatch,
    authority_kind,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            event("b" * 20),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ),
    )
    prepared = json.loads(archive.state_path.read_text(encoding="utf-8"))
    if authority_kind == "v2_journal":
        _rewrite_pending_journal_as_v2(archive)
    archive._write_index(prepared["target_index"])
    before_state = archive.state_path.read_bytes()
    before_index = archive.index_path.read_bytes()
    before_buckets = {
        path.name: path.read_bytes()
        for path in sorted(archive.archive_root.glob("*.jsonl"))
    }
    writes: list[Path] = []
    real_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert writes == []
    assert archive.state_path.read_bytes() == before_state
    assert archive.index_path.read_bytes() == before_index
    assert {
        path.name: path.read_bytes()
        for path in sorted(archive.archive_root.glob("*.jsonl"))
    } == before_buckets


def _write_empty_v2_noop_over_current_authority(
    archive: EvidenceArchive,
    *,
    transaction_time: datetime,
) -> None:
    current = json.loads(archive.state_path.read_text(encoding="utf-8"))
    _downgrade_authority_to_legacy_state(archive, 2)
    document = {
        "schema_version": 2,
        "transaction_id": "",
        "base_generation": current["generation"],
        "target_generation": current["generation"] + 1,
        "base_index_digest": current["target_index_digest"],
        "target_index_digest": current["target_index_digest"],
        "base_bucket_digests": current["target_bucket_digests"],
        "target_bucket_digests": current["target_bucket_digests"],
        "rows": [],
    }
    document["transaction_id"] = archive._journal_transaction_id(
        base_generation=document["base_generation"],
        target_generation=document["target_generation"],
        base_index_digest=document["base_index_digest"],
        target_index_digest=document["target_index_digest"],
        base_bucket_digests=document["base_bucket_digests"],
        target_bucket_digests=document["target_bucket_digests"],
        rows=[],
    )
    archive.journal_path.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _set_journal_mtime(archive, transaction_time)


def test_archive_empty_v2_noop_authenticates_nonempty_base_with_clock_skew(tmp_path):
    boundary_time = NOW - timedelta(days=90) + timedelta(minutes=2)
    authenticated_time = NOW + timedelta(minutes=4)
    selected = snapshot(
        replace(
            event(
                "n" * 20,
                published_at=boundary_time,
                history=(StatusTransition(
                    None,
                    VerificationStatus.VERIFIED,
                    authenticated_time,
                    "reason-verified",
                ),),
            ),
            verified_at=authenticated_time,
            evidence_as_of=authenticated_time,
        ),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=authenticated_time,
    )
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(selected)
    _write_empty_v2_noop_over_current_authority(archive, transaction_time=NOW)

    rows = archive.query(days=90)

    assert [row["event_id"] for row in rows] == ["n" * 20]
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 4
    assert state["phase"] == "finalized"
    assert state["cutoff"] == (NOW - timedelta(days=90)).isoformat()


def test_archive_empty_v2_noop_falls_back_at_full_projection_causal_skew(
    tmp_path,
):
    authenticated_time = NOW + timedelta(minutes=4)
    selected = snapshot(
        replace(
            event(
                "n" * 20,
                published_at=NOW - timedelta(days=1),
                history=(StatusTransition(
                    None,
                    VerificationStatus.VERIFIED,
                    authenticated_time,
                    "reason-verified",
                ),),
            ),
            verified_at=authenticated_time,
            evidence_as_of=authenticated_time,
        ),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=authenticated_time,
    )
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(selected)
    _write_empty_v2_noop_over_current_authority(
        archive,
        transaction_time=NOW - timedelta(minutes=2),
    )
    rows = archive.query(days=90)

    assert [row["event_id"] for row in rows] == ["n" * 20]
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["cutoff"] == (
        authenticated_time - archive_module._MAX_CLOCK_SKEW - timedelta(days=90)
    ).isoformat()


@pytest.mark.parametrize("corrupt_kind", ("malformed_json", "future_schema"))
def test_archive_native_manifest_skips_one_bounded_corrupt_row_by_valid_projection(
    tmp_path,
    corrupt_kind,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    bucket = archive.archive_root / "2026-08-20.jsonl"
    if corrupt_kind == "malformed_json":
        corrupt = b'{"schema_version":2,"broken":}\n'
    else:
        future = json.loads(bucket.read_text(encoding="utf-8"))
        future["event_id"] = "z" * 20
        future["schema_version"] = 99
        corrupt = json.dumps(future, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    with bucket.open("ab") as handle:
        handle.write(corrupt)

    rows = archive.query(days=90)

    assert [row["event_id"] for row in rows] == ["a" * 20]
    assert archive.last_diagnostics["skipped_corrupt_rows"] == 1


@pytest.mark.parametrize("corrupt_kind", ("malformed_json", "future_schema"))
def test_archive_unmanifested_corrupt_only_bucket_is_skipped_without_new_authority(
    tmp_path,
    corrupt_kind,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    before_state = archive.state_path.read_bytes()
    unmanifested = archive.archive_root / "2026-08-19.jsonl"
    if corrupt_kind == "malformed_json":
        payload = b'{"schema_version":2,"broken":}\n'
    else:
        valid_bucket = archive.archive_root / "2026-08-20.jsonl"
        future = json.loads(valid_bucket.read_text(encoding="utf-8"))
        future["event_id"] = "z" * 20
        future["schema_version"] = 99
        payload = json.dumps(future, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    unmanifested.write_bytes(payload)

    rows = archive.query(days=90)

    assert [row["event_id"] for row in rows] == ["a" * 20]
    assert archive.last_diagnostics["skipped_corrupt_rows"] == 1
    assert archive.state_path.read_bytes() == before_state
    state = json.loads(before_state)
    assert "2026-08-19.jsonl" not in state["target_bucket_digests"]


def test_archive_corrupt_only_first_bucket_does_not_create_v4_authority(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    with archive._process_lock():
        pass
    corrupt_bucket = archive.archive_root / "2026-08-20.jsonl"
    corrupt_bucket.write_bytes(b'{"schema_version":99}\n')

    assert archive.query(days=90) == []
    assert archive.last_diagnostics["skipped_corrupt_rows"] == 1
    assert not archive.state_path.exists()
    assert not archive.index_path.exists()


@pytest.mark.parametrize("authority_kind", ("v4_prepared", "v2_journal"))
def test_archive_recovery_accepts_target_valid_projection_with_one_corrupt_row(
    tmp_path,
    monkeypatch,
    authority_kind,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            event("b" * 20),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ),
    )
    prepared = json.loads(archive.state_path.read_text(encoding="utf-8"))
    target_name = prepared["target_index"]["b" * 20]
    physical_rows = archive._read_bucket(target_name, archive._diagnostics())
    target_rows = sorted(
        physical_rows + prepared["rows"],
        key=lambda row: row["event_id"],
    )
    target_payload = archive._bucket_payload(target_name, target_rows)
    future = json.loads(json.dumps(target_rows[-1], ensure_ascii=False))
    future["event_id"] = "z" * 20
    future["schema_version"] = 99
    corrupt = json.dumps(future, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    (archive.archive_root / target_name).write_bytes(target_payload + corrupt)
    if authority_kind == "v2_journal":
        _rewrite_pending_journal_as_v2(archive)

    rows = archive.query(days=90)

    assert {row["event_id"] for row in rows} == {"a" * 20, "b" * 20}
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 4
    assert state["phase"] == "finalized"


def test_archive_legacy_manifest_migration_skips_one_corrupt_row_by_v1_projection(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    bucket = archive.archive_root / "2026-08-20.jsonl"
    native = json.loads(bucket.read_text(encoding="utf-8"))
    legacy = _legacy_v1_row(native)
    bucket.write_text(
        json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _downgrade_authority_to_legacy_state(archive, 1)
    with bucket.open("ab") as handle:
        handle.write(b'{"schema_version":99}\n')

    rows = archive.query(days=90)

    assert [row["event_id"] for row in rows] == ["a" * 20]
    assert archive.last_diagnostics["skipped_corrupt_rows"] == 1
    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 4
    assert state["phase"] == "finalized"


@pytest.mark.parametrize(
    "payload",
    (
        b'{"schema_version":2,"broken":}\n',
        b'{"schema_version":99}\n',
    ),
)
def test_archive_corrupt_row_node_budget_is_checked_before_json_materialization(
    tmp_path,
    monkeypatch,
    payload,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    with archive._process_lock():
        pass
    name = "2026-08-20.jsonl"
    (archive.archive_root / name).write_bytes(payload)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_parse(raw: bytes):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_parse)
    budget = {"bytes": len(payload), "rows": 1, "nodes": 0, "files": 1}

    with pytest.raises(OSError, match="storage_corrupt"):
        archive._read_bucket(
            name,
            archive._diagnostics(),
            budget=budget,
            fail_on_budget=True,
            strict_rows=False,
        )

    assert parse_calls == 0
    assert budget["rows"] == 0


def test_archive_corrupt_row_cannot_turn_preflight_node_exhaustion_into_a_skip(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    with archive._process_lock():
        pass
    name = "2026-08-20.jsonl"
    payload = (
        json.dumps(
            {"schema_version": 99, "padding": list(range(32))},
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    (archive.archive_root / name).write_bytes(payload)
    budget = {"bytes": len(payload), "rows": 1, "nodes": 1, "files": 1}

    with pytest.raises(OSError, match="storage_corrupt"):
        archive._read_bucket(
            name,
            archive._diagnostics(),
            budget=budget,
            fail_on_budget=True,
            strict_rows=True,
            expected_digests={archive._payload_digest(b"")},
        )


@pytest.mark.parametrize("remaining_rows", (0, 8))
@pytest.mark.parametrize(
    "payload",
    (
        b'[{"rows":[]}]\n',
        b'{"schema_version":4,"rows":{}}\n',
        b'{"rows":[],"rows":[]}\n',
    ),
)
def test_archive_malformed_state_rows_shape_is_rejected_before_parse_for_any_row_budget(
    tmp_path,
    monkeypatch,
    payload,
    remaining_rows,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    with archive._process_lock():
        pass
    archive.state_path.write_bytes(payload)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_parse(raw: bytes):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_parse)
    budget = {"bytes": len(payload), "rows": remaining_rows, "nodes": 64, "files": 1}

    with pytest.raises(OSError, match="storage_corrupt"):
        archive._read_authority_state(NOW, budget=budget)

    assert parse_calls == 0


@pytest.mark.parametrize("remaining_rows", (0, 8))
@pytest.mark.parametrize(
    "payload",
    (
        b'[{"rows":[]}]\n',
        b'{"schema_version":2,"rows":{}}\n',
        b'{"schema_version":2,"rows":[],"rows":[]}\n',
    ),
)
def test_archive_malformed_journal_rows_shape_is_rejected_before_parse_for_any_row_budget(
    tmp_path,
    monkeypatch,
    payload,
    remaining_rows,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    with archive._process_lock():
        pass
    archive.journal_path.write_bytes(payload)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_parse(raw: bytes):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_parse)
    budget = {"bytes": len(payload), "rows": remaining_rows, "nodes": 64, "files": 1}

    with pytest.raises(OSError, match="storage_corrupt"):
        archive._read_journal(
            NOW,
            archive._diagnostics(),
            budget=budget,
        )

    assert parse_calls == 0


@pytest.mark.parametrize(
    ("physical_rows", "remaining_rows"),
    (
        (archive_module._MAX_BUCKET_ROWS + 1, archive_module._MAX_SCAN_ROWS),
        (2, 1),
    ),
)
def test_archive_bucket_rejects_row_budget_without_splitlines_allocation(
    tmp_path,
    monkeypatch,
    physical_rows,
    remaining_rows,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    payload = _SplitlinesForbiddenBytes(b"{}\n" * physical_rows)
    monkeypatch.setattr(archive, "_read_bytes", lambda *_args, **_kwargs: payload)
    budget = {
        "bytes": len(payload),
        "rows": remaining_rows,
        "nodes": archive_module._MAX_SCAN_NODES,
        "files": 1,
    }

    with pytest.raises(OSError, match="storage_corrupt"):
        archive._read_bucket(
            "2026-08-20.jsonl",
            archive._diagnostics(),
            budget=budget,
            fail_on_budget=True,
        )


@pytest.mark.parametrize("authority_kind", ("state", "journal"))
def test_archive_authority_preparse_bounds_top_level_key_before_json_materialization(
    tmp_path,
    monkeypatch,
    authority_kind,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    with archive._process_lock():
        pass
    payload = b'{"' + (b"x" * 129) + b'":0,"rows":[]}\n'
    path = archive.state_path if authority_kind == "state" else archive.journal_path
    path.write_bytes(payload)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_parse(raw: bytes):
        nonlocal parse_calls
        if raw == payload:
            parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_parse)
    budget = {"bytes": len(payload), "rows": 8, "nodes": 256, "files": 1}

    with pytest.raises(OSError, match="storage_corrupt"):
        if authority_kind == "state":
            archive._read_authority_state(NOW, budget=budget)
        else:
            archive._read_journal(NOW, archive._diagnostics(), budget=budget)

    assert parse_calls == 0


@pytest.mark.parametrize(
    "payload",
    (
        b'[{"events":{}}]\n',
        b'{"schema_version":1,"events":[]}\n',
        b'{"schema_version":1,"events":{},"events":{}}\n',
    ),
)
def test_archive_index_shape_is_rejected_before_json_materialization(
    tmp_path,
    monkeypatch,
    payload,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    archive.index_path.write_bytes(payload)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_parse(raw: bytes):
        nonlocal parse_calls
        if raw == payload:
            parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_parse)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert parse_calls == 0


def test_archive_index_entry_capacity_is_rejected_before_json_materialization(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    payload = (
        json.dumps(
            {
                "schema_version": 1,
                "events": {
                    "a" * 20: "2026-08-20.jsonl",
                    "b" * 20: "2026-08-20.jsonl",
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    archive.index_path.write_bytes(payload)
    monkeypatch.setattr(archive_module, "_MAX_INDEX_EVENTS", 1)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_parse(raw: bytes):
        nonlocal parse_calls
        if raw == payload:
            parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_parse)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert parse_calls == 0


@pytest.mark.parametrize("dimension", ("files", "bytes", "nodes", "rows"))
def test_archive_index_budget_exhaustion_fails_before_payload_io(
    tmp_path,
    monkeypatch,
    dimension,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    real_read_state = archive._read_authority_state

    def exhaust_after_state(*args, **kwargs):
        state = real_read_state(*args, **kwargs)
        kwargs["budget"][dimension] = 0
        return state

    index_reads: list[Path] = []
    real_read_bytes = archive._read_bytes

    def record_index_read(path: Path, maximum: int, **kwargs):
        if path == archive.index_path:
            index_reads.append(path)
        return real_read_bytes(path, maximum, **kwargs)

    monkeypatch.setattr(archive, "_read_authority_state", exhaust_after_state)
    monkeypatch.setattr(archive, "_read_bytes", record_index_read)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert index_reads == []


def test_archive_index_token_budget_is_rejected_before_json_materialization(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    payload = archive.index_path.read_bytes()
    real_read_state = archive._read_authority_state

    def leave_two_nodes_after_state(*args, **kwargs):
        state = real_read_state(*args, **kwargs)
        kwargs["budget"]["nodes"] = 2
        return state

    parse_calls = 0
    real_parse = archive._parse_json

    def count_parse(raw: bytes):
        nonlocal parse_calls
        if raw == payload:
            parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_read_authority_state", leave_two_nodes_after_state)
    monkeypatch.setattr(archive, "_parse_json", count_parse)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert parse_calls == 0


def test_archive_index_entries_consume_the_shared_row_budget(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    real_read_state = archive._read_authority_state

    def leave_one_row_after_state(*args, **kwargs):
        state = real_read_state(*args, **kwargs)
        kwargs["budget"]["rows"] = 1
        return state

    monkeypatch.setattr(archive, "_read_authority_state", leave_one_row_after_state)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_legacy_migration_reads_physical_index_once(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _downgrade_authority_to_legacy_state(archive)
    diagnostics = archive._diagnostics()
    budget = archive_module._new_scan_budget()
    legacy_state = archive._read_authority_state(
        NOW,
        diagnostics=diagnostics,
        budget=budget,
    )
    index_reads = 0
    real_read_bytes = archive._read_bytes

    def count_index_reads(path: Path, maximum: int, **kwargs):
        nonlocal index_reads
        if path == archive.index_path:
            index_reads += 1
        return real_read_bytes(path, maximum, **kwargs)

    monkeypatch.setattr(archive, "_read_bytes", count_index_reads)

    with archive._process_lock():
        migrated = archive._migrate_legacy_authority(
            legacy_state,
            now=NOW,
            diagnostics=diagnostics,
            budget=budget,
        )

    assert migrated is not None and migrated["phase"] == "finalized"
    assert index_reads == 1


def test_archive_prepared_recovery_reads_physical_index_once(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    diagnostics = archive._diagnostics()
    budget = archive_module._new_scan_budget()
    prepared = archive._read_authority_state(NOW, diagnostics=diagnostics, budget=budget)
    index_reads = 0
    real_read_bytes = archive._read_bytes

    def count_index_reads(path: Path, maximum: int, **kwargs):
        nonlocal index_reads
        if path == archive.index_path:
            index_reads += 1
        return real_read_bytes(path, maximum, **kwargs)

    monkeypatch.setattr(archive, "_read_bytes", count_index_reads)

    with archive._process_lock():
        archive._recover_prepared_authority(
            prepared,
            now=NOW,
            diagnostics=diagnostics,
            budget=budget,
        )

    assert index_reads == 1


def test_archive_prepared_recovery_rejects_same_bytes_index_identity_replacement(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    diagnostics = archive._diagnostics()
    budget = archive_module._new_scan_budget()
    prepared = archive._read_authority_state(NOW, diagnostics=diagnostics, budget=budget)
    real_plan = archive._plan_prepared_authority

    def replace_index_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        replacement = archive.archive_root / "replacement-index.json"
        replacement.write_bytes(archive.index_path.read_bytes())
        os.replace(replacement, archive.index_path)
        return plan

    monkeypatch.setattr(archive, "_plan_prepared_authority", replace_index_after_plan)

    with archive._process_lock(), pytest.raises(OSError, match="storage_corrupt"):
        archive._recover_prepared_authority(
            prepared,
            now=NOW,
            diagnostics=diagnostics,
            budget=budget,
        )

    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "prepared"


@pytest.mark.parametrize(
    "payload",
    (
        b'{"schema_version":1,"schema_version":1,"events":{}}\n',
        b'{"schema_version":1,"events":{},"extra":"x"}\n',
        b'{"schema_version":"1","events":{}}\n',
        b'{"schema_version":1.0,"events":{}}\n',
        b'{"schema_version":1,"events":{"aaaaaaaaaaaaaaaaaaaa":null}}\n',
        b'{"schema_version":1,"events":{"aaaaaaaaaaaaaaaaaaaa":7}}\n',
        b'{"schema_version":1,"events":{"aaaaaaaaaaaaaaaaaaaa":[]}}\n',
        b'{"schema_version":1,"events":{"aaaaaaaaaaaaaaaaaaaa":{}}}\n',
        (
            b'{"schema_version":1,"events":{"aaaaaaaaaaaaaaaaaaaa":"2026-08-20.jsonl",'
            b'"\\u0061aaaaaaaaaaaaaaaaaaa":"2026-08-20.jsonl"}}\n'
        ),
        b'{"schema_version":1,"events":{"aaaaaaaaaaaaaaaaaaaa":"\xff"}}\n',
    ),
)
def test_archive_index_preparser_rejects_noncanonical_flat_schema_before_json_materialization(
    tmp_path,
    monkeypatch,
    payload,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    archive.index_path.write_bytes(payload)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_parse(raw: bytes):
        nonlocal parse_calls
        if raw == payload:
            parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_parse)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert parse_calls == 0


def test_archive_index_preparser_accepts_escaped_utf8_flat_strings_and_charges_exact_budget(
    tmp_path,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive._prepare()
    payload = (
        '{"\\u0073chema_version":1,"\\u0065vents":'
        '{"éééééééééééééééééééé":"2026-08-20\\u002ejsonl"}}\n'
    ).encode("utf-8")
    archive.index_path.write_bytes(payload)
    budget = {
        "bytes": len(payload),
        "rows": 1,
        "nodes": 7,
        "files": 1,
    }

    record = archive._read_index_record(budget=budget)

    assert record is not None
    assert record["events"] == {"é" * 20: "2026-08-20.jsonl"}
    assert budget == {"bytes": 0, "rows": 0, "nodes": 0, "files": 0}


def _changed_stat_result(metadata, **changes):
    fields = {
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
        "st_size": metadata.st_size,
        "st_mtime_ns": metadata.st_mtime_ns,
        "st_ctime_ns": metadata.st_ctime_ns,
        "st_nlink": metadata.st_nlink,
        "st_mode": metadata.st_mode,
        "st_reparse_tag": getattr(metadata, "st_reparse_tag", 0),
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize(
    ("field", "mutate"),
    (
        ("st_dev", lambda value: value + 1),
        ("st_ino", lambda value: value + 1),
        ("st_size", lambda value: value + 1),
        ("st_mtime_ns", lambda value: value + 1),
        ("st_ctime_ns", lambda value: value + 1),
        ("st_nlink", lambda value: value + 1),
        ("st_mode", lambda value: value ^ 0o200),
        ("st_reparse_tag", lambda value: value + 1),
    ),
)
def test_archive_index_cas_rejects_every_final_path_signature_change(
    tmp_path,
    monkeypatch,
    field,
    mutate,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    record = archive._read_index_record(budget=archive_module._new_scan_budget())
    assert record is not None
    real_stat = Path.stat
    index_stat_calls = 0

    def changed_final_stat(path: Path, *args, **kwargs):
        nonlocal index_stat_calls
        metadata = real_stat(path, *args, **kwargs)
        if path == archive.index_path:
            index_stat_calls += 1
            if index_stat_calls == 2:
                return _changed_stat_result(
                    metadata,
                    **{field: mutate(getattr(metadata, field, 0))},
                )
        return metadata

    monkeypatch.setattr(Path, "stat", changed_final_stat)

    assert archive._index_record_is_current(record) is False


def test_archive_index_cas_rechecks_open_descriptor_after_final_path_lstat(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    record = archive._read_index_record(budget=archive_module._new_scan_budget())
    assert record is not None
    real_fstat = os.fstat
    fstat_calls = 0

    def changed_second_fstat(descriptor):
        nonlocal fstat_calls
        metadata = real_fstat(descriptor)
        fstat_calls += 1
        if fstat_calls == 2:
            return _changed_stat_result(metadata, st_mtime_ns=metadata.st_mtime_ns + 1)
        return metadata

    monkeypatch.setattr(os, "fstat", changed_second_fstat)

    assert archive._index_record_is_current(record) is False
    assert fstat_calls >= 2


def test_archive_native_upsert_rejects_index_identity_change_before_any_mutation(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    real_prepared_payload = archive._prepared_authority_payload
    replaced = False

    def replace_index_before_mutation(*args, **kwargs):
        nonlocal replaced
        payload = real_prepared_payload(*args, **kwargs)
        if not replaced:
            replacement = archive.archive_root / "replacement-index.json"
            replacement.write_bytes(archive.index_path.read_bytes())
            os.replace(replacement, archive.index_path)
            replaced = True
        return payload

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_prepared_authority_payload", replace_index_before_mutation)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(
            snapshot(
                event("b" * 20),
                snapshot_id="1" * 20,
                raw_snapshot_id="2" * 20,
            )
        )

    assert writes == []


def test_archive_legacy_migration_rejects_index_identity_change_before_prepared_state_write(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    _downgrade_authority_to_legacy_state(archive)
    diagnostics = archive._diagnostics()
    budget = archive_module._new_scan_budget()
    legacy_state = archive._read_authority_state(
        NOW,
        diagnostics=diagnostics,
        budget=budget,
    )
    real_plan = archive._plan_prepared_authority

    def replace_index_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        replacement = archive.archive_root / "replacement-index.json"
        replacement.write_bytes(archive.index_path.read_bytes())
        os.replace(replacement, archive.index_path)
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", replace_index_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with archive._process_lock(), pytest.raises(OSError, match="storage_corrupt"):
        archive._migrate_legacy_authority(
            legacy_state,
            now=NOW,
            diagnostics=diagnostics,
            budget=budget,
        )

    assert writes == []


@pytest.mark.parametrize(
    "payload",
    (
        b'{"schema_version":1,"events":{"\\u0x12aaaaaaaaaaaaaaaa":"2026-08-20.jsonl"}}\n',
        b'{"schema_version":1,"events":{"aaaaaaaaaaaaaaaaaaaa":"2026-08-20\\u0x2ejsonl"}}\n',
    ),
)
def test_archive_index_preparser_rejects_nonhex_unicode_escape_before_json_materialization(
    tmp_path,
    monkeypatch,
    payload,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    archive.index_path.write_bytes(payload)
    parse_calls = 0
    real_parse = archive._parse_json

    def count_parse(raw: bytes):
        nonlocal parse_calls
        if raw == payload:
            parse_calls += 1
        return real_parse(raw)

    monkeypatch.setattr(archive, "_parse_json", count_parse)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert parse_calls == 0


@pytest.mark.parametrize(
    ("field", "mutate"),
    (
        ("st_dev", lambda value: value + 1),
        ("st_ino", lambda value: value + 1),
        ("st_size", lambda value: value + 1),
        ("st_mtime_ns", lambda value: value + 1),
        ("st_ctime_ns", lambda value: value + 1),
        ("st_nlink", lambda value: value + 1),
        ("st_mode", lambda value: value ^ 0o200),
        ("st_reparse_tag", lambda value: value + 1),
    ),
)
def test_archive_index_cas_binds_complete_open_descriptor_signature_to_record(
    tmp_path,
    monkeypatch,
    field,
    mutate,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    record = archive._read_index_record(budget=archive_module._new_scan_budget())
    assert record is not None
    real_fstat = os.fstat

    def changed_descriptor_stat(descriptor):
        metadata = real_fstat(descriptor)
        return _changed_stat_result(
            metadata,
            **{field: mutate(getattr(metadata, field, 0))},
        )

    monkeypatch.setattr(os, "fstat", changed_descriptor_stat)

    assert archive._index_record_is_current(record) is False


def _rewrite_pending_journal_as_native_schema(
    archive: EvidenceArchive,
    schema_version: int,
) -> bytes:
    prepared = json.loads(archive.state_path.read_text(encoding="utf-8"))
    if schema_version == 2:
        document = _rewrite_pending_journal_as_v2(archive)
    else:
        document = {
            "schema_version": 3,
            "transaction_id": prepared["transaction_id"],
            "base_generation": prepared["base_generation"],
            "target_generation": prepared["target_generation"],
            "base_index_digest": prepared["base_index_digest"],
            "target_index_digest": prepared["target_index_digest"],
            "base_bucket_digests": prepared["base_bucket_digests"],
            "target_bucket_digests": prepared["target_bucket_digests"],
            "cutoff": prepared["cutoff"],
            "target_index": prepared["target_index"],
            "rows": prepared["rows"],
        }
        archive.journal_path.write_text(
            json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        archive.state_path.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "generation": prepared["base_generation"],
                    "phase": "finalized",
                    "transaction_id": "0" * 64,
                    "index_digest": prepared["base_index_digest"],
                    "bucket_digests": {},
                },
                separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
    assert document["schema_version"] == schema_version
    return archive.journal_path.read_bytes()


@pytest.mark.parametrize("schema_version", (2, 3))
def test_archive_native_legacy_journal_migration_rejects_absent_to_present_index_before_state_write(
    tmp_path,
    monkeypatch,
    schema_version,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    selected = snapshot(event("j" * 20))
    with archive._process_lock():
        pass
    journal = _journal_only_document(archive, selected, schema_version)
    archive.journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _set_journal_mtime(archive, NOW)
    journal_bytes = archive.journal_path.read_bytes()
    real_plan = archive._plan_prepared_authority

    def install_index_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        archive.index_path.write_bytes(archive._index_payload({}))
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", install_index_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert writes == []
    assert not archive.state_path.exists()
    assert archive.journal_path.read_bytes() == journal_bytes
    monkeypatch.setattr(archive, "_plan_prepared_authority", real_plan)
    monkeypatch.setattr(archive, "_atomic_write", real_atomic_write)
    archive.index_path.unlink()
    assert [row["event_id"] for row in archive.query(days=90)] == ["j" * 20]
    assert archive.journal_path.read_bytes() == journal_bytes


@pytest.mark.parametrize("schema_version", (2, 3))
@pytest.mark.parametrize("mutation", ("same_bytes_new_inode", "same_inode_metadata"))
def test_archive_native_legacy_journal_migration_rechecks_index_identity_before_state_write(
    tmp_path,
    monkeypatch,
    schema_version,
    mutation,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    journal_bytes = _rewrite_pending_journal_as_native_schema(archive, schema_version)
    state_bytes = archive.state_path.read_bytes()
    index_bytes = archive.index_path.read_bytes()
    real_plan = archive._plan_prepared_authority

    def mutate_index_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        if mutation == "same_bytes_new_inode":
            replacement = archive.archive_root / "replacement-index.json"
            replacement.write_bytes(index_bytes)
            os.replace(replacement, archive.index_path)
        else:
            metadata = archive.index_path.stat(follow_symlinks=False)
            os.utime(
                archive.index_path,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000),
            )
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", mutate_index_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert writes == []
    assert archive.state_path.read_bytes() == state_bytes
    assert archive.index_path.read_bytes() == index_bytes
    assert archive.journal_path.read_bytes() == journal_bytes
    monkeypatch.setattr(archive, "_plan_prepared_authority", real_plan)
    monkeypatch.setattr(archive, "_atomic_write", real_atomic_write)
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}
    assert archive.journal_path.read_bytes() == journal_bytes


def _install_unsafe_bucket_entry(bucket: Path, outside: Path, kind: str) -> None:
    if kind == "hardlink":
        os.link(outside, bucket)
    else:
        os.symlink(outside, bucket)


@pytest.mark.parametrize(
    "kind",
    (
        "hardlink",
        pytest.param("symlink", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX symlink case")),
    ),
)
def test_archive_read_only_scan_skips_unsafe_canonical_bucket_with_diagnostic(
    tmp_path,
    monkeypatch,
    kind,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.archive_root.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside_bytes = b'{"outside":"foreign"}\n'
    outside.write_bytes(outside_bytes)
    bucket = archive.archive_root / "2026-08-20.jsonl"
    _install_unsafe_bucket_entry(bucket, outside, kind)

    def reject_payload_read(*args, **kwargs):
        raise AssertionError("unsafe canonical bucket reached payload read")

    monkeypatch.setattr(archive, "_read_bytes", reject_payload_read)

    assert archive.query(days=90) == []
    assert archive.last_diagnostics["skipped_files"] == 1
    assert outside.read_bytes() == outside_bytes
    assert not archive.lock_path.exists()


def test_archive_read_only_scan_does_not_count_a_disappeared_candidate_as_unsafe(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive._prepare()
    bucket = archive.archive_root / "2026-08-20.jsonl"
    bucket.write_bytes(b"\n")
    diagnostics = archive._diagnostics()
    real_safe_file = archive._safe_file

    def disappear_before_lstat(path: Path):
        if path == bucket:
            bucket.unlink()
            return None
        return real_safe_file(path)

    monkeypatch.setattr(archive, "_safe_file", disappear_before_lstat)

    assert archive._bucket_names(diagnostics=diagnostics, prepare=False) == []
    assert diagnostics["skipped_files"] == 0


@pytest.mark.parametrize(
    "kind",
    (
        "hardlink",
        pytest.param("symlink", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX symlink case")),
    ),
)
def test_archive_upsert_preflights_every_target_bucket_before_any_mutation(
    tmp_path,
    monkeypatch,
    kind,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.archive_root.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside_bytes = b'{"outside":"foreign"}\n'
    outside.write_bytes(outside_bytes)
    unsafe_bucket = archive.archive_root / "2026-08-20.jsonl"
    _install_unsafe_bucket_entry(unsafe_bucket, outside, kind)
    earlier = NOW - timedelta(days=1)
    selected = EvidenceSnapshot(
        snapshot_id="e" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW,
        events=(
            event("a" * 20, published_at=earlier),
            event("b" * 20, published_at=NOW),
        ),
    )
    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_(?:error|corrupt)"):
        archive.upsert(selected)

    assert writes == []
    assert not archive.state_path.exists()
    assert not archive.index_path.exists()
    assert not (archive.archive_root / "2026-08-19.jsonl").exists()
    assert outside.read_bytes() == outside_bytes


@pytest.mark.parametrize(
    "kind",
    (
        "hardlink",
        pytest.param("symlink", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX symlink case")),
    ),
)
def test_archive_legacy_migration_preflights_affected_bucket_identity_before_state_write(
    tmp_path,
    monkeypatch,
    kind,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    legacy = _write_historical_v1_finalized_archive(archive, (snapshot(event("a" * 20)),))
    bucket = archive.archive_root / "2026-08-20.jsonl"
    bucket_bytes = bucket.read_bytes()
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(bucket_bytes)
    state_bytes = archive.state_path.read_bytes()
    index_bytes = archive.index_path.read_bytes()
    diagnostics = archive._diagnostics()
    budget = archive_module._new_scan_budget()
    legacy_state = archive._read_authority_state(
        NOW,
        diagnostics=diagnostics,
        budget=budget,
    )
    real_plan = archive._plan_prepared_authority

    def replace_bucket_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        bucket.unlink()
        _install_unsafe_bucket_entry(bucket, outside, kind)
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", replace_bucket_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with archive._process_lock(), pytest.raises(OSError, match="storage_(?:error|corrupt)"):
        archive._migrate_legacy_authority(
            legacy_state,
            now=NOW,
            diagnostics=diagnostics,
            budget=budget,
        )

    assert writes == []
    assert archive.state_path.read_bytes() == state_bytes
    assert archive.index_path.read_bytes() == index_bytes
    assert outside.read_bytes() == bucket_bytes
    assert legacy["state_bytes"] == state_bytes
    monkeypatch.setattr(archive, "_plan_prepared_authority", real_plan)
    monkeypatch.setattr(archive, "_atomic_write", real_atomic_write)
    bucket.unlink()
    bucket.write_bytes(bucket_bytes)
    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    assert outside.read_bytes() == bucket_bytes


def _replace_path_with_same_bytes(path: Path) -> None:
    replacement = path.with_name(f".{path.name}.read-authority-replacement")
    replacement.write_bytes(path.read_bytes())
    os.replace(replacement, path)


def _mutate_read_authority_path(
    path: Path,
    mutation: str,
    *,
    outside: Path,
) -> bytes | None:
    if mutation == "absent_to_present":
        assert not path.exists()
        foreign = b'{"foreign":"must-not-be-overwritten"}\n'
        path.write_bytes(foreign)
        return foreign
    if mutation == "same_bytes_new_inode":
        original = path.read_bytes()
        _replace_path_with_same_bytes(path)
        return original
    if mutation == "same_inode_metadata":
        original = path.read_bytes()
        metadata = path.stat(follow_symlinks=False)
        os.utime(
            path,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000),
        )
        return original
    if mutation == "disappear":
        path.unlink()
        return None
    foreign = b'{"outside":"must-not-be-overwritten"}\n'
    outside.write_bytes(foreign)
    path.unlink()
    _install_unsafe_bucket_entry(path, outside, mutation)
    return foreign


def _next_prepared_state_payload(
    archive: EvidenceArchive,
    selected: EvidenceSnapshot,
) -> bytes:
    finalized = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert finalized["phase"] == "finalized"
    row = archive_module._archive_document(
        selected,
        archive_module.event_document(selected.events[0]),
        selected.generated_at,
    )
    bucket_name = archive_module._bucket_name(row)
    diagnostics = archive._diagnostics()
    rows = archive._read_bucket(
        bucket_name,
        diagnostics,
        validation_now=selected.generated_at,
    )
    target_rows = sorted(rows + [row], key=lambda item: item["event_id"])
    target_index = dict(finalized["target_index"])
    target_index[row["event_id"]] = bucket_name
    base_bucket_digests = dict(finalized["target_bucket_digests"])
    base_bucket_digests.setdefault(bucket_name, archive_module._MISSING_DIGEST)
    target_bucket_digests = dict(base_bucket_digests)
    target_bucket_digests[bucket_name] = archive._payload_digest(
        archive._bucket_payload(bucket_name, target_rows)
    )
    target_index_digest = archive._payload_digest(archive._index_payload(target_index))
    return archive._prepared_authority_payload(
        base_generation=finalized["generation"],
        target_generation=finalized["generation"] + 1,
        cutoff=NOW - timedelta(days=90),
        base_index_digest=finalized["target_index_digest"],
        target_index_digest=target_index_digest,
        base_bucket_digests=base_bucket_digests,
        target_bucket_digests=target_bucket_digests,
        target_index=target_index,
        rows=[row],
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "absent_to_present",
        "same_bytes_new_inode",
        "same_inode_metadata",
        "disappear",
        "hardlink",
        pytest.param(
            "symlink",
            marks=pytest.mark.skipif(os.name == "nt", reason="POSIX symlink case"),
        ),
    ),
)
def test_archive_native_upsert_binds_bucket_read_authority_before_first_mutation(
    tmp_path,
    monkeypatch,
    mutation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_time = NOW - timedelta(days=1) if mutation == "absent_to_present" else NOW
    bucket = archive.archive_root / f"{target_time.date().isoformat()}.jsonl"
    state_bytes = archive.state_path.read_bytes()
    index_bytes = archive.index_path.read_bytes()
    original_bucket_bytes = None if not bucket.exists() else bucket.read_bytes()
    outside = tmp_path / f"native-{mutation}-outside.jsonl"
    real_prepared_payload = archive._prepared_authority_payload
    injected = False
    expected_after_mutation: bytes | None = None

    def mutate_after_plan(*args, **kwargs):
        nonlocal injected, expected_after_mutation
        payload = real_prepared_payload(*args, **kwargs)
        if not injected:
            expected_after_mutation = _mutate_read_authority_path(
                bucket,
                mutation,
                outside=outside,
            )
            injected = True
        return payload

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_prepared_authority_payload", mutate_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_(?:error|corrupt)"):
        archive.upsert(
            snapshot(
                event("b" * 20, published_at=target_time),
                snapshot_id="1" * 20,
                raw_snapshot_id="2" * 20,
            )
        )

    assert writes == []
    assert archive.state_path.read_bytes() == state_bytes
    assert archive.index_path.read_bytes() == index_bytes
    if mutation == "disappear":
        assert not bucket.exists()
    elif mutation in {"hardlink", "symlink"}:
        assert outside.read_bytes() == expected_after_mutation
    else:
        assert bucket.read_bytes() == expected_after_mutation
    if original_bucket_bytes is not None and mutation == "same_inode_metadata":
        assert bucket.read_bytes() == original_bucket_bytes


@pytest.mark.parametrize(
    "mutation",
    (
        "absent_to_present",
        "same_bytes_new_inode",
        "same_inode_metadata",
        "disappear",
        "hardlink",
        pytest.param(
            "symlink",
            marks=pytest.mark.skipif(os.name == "nt", reason="POSIX symlink case"),
        ),
    ),
)
def test_archive_prepared_recovery_binds_bucket_read_authority_before_first_mutation(
    tmp_path,
    monkeypatch,
    mutation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_time = NOW - timedelta(days=1) if mutation == "absent_to_present" else NOW
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            event("b" * 20, published_at=target_time),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ),
    )
    bucket = archive.archive_root / f"{target_time.date().isoformat()}.jsonl"
    state_bytes = archive.state_path.read_bytes()
    index_bytes = archive.index_path.read_bytes()
    outside = tmp_path / f"prepared-{mutation}-outside.jsonl"
    real_plan = archive._plan_prepared_authority
    expected_after_mutation: bytes | None = None

    def mutate_after_plan(*args, **kwargs):
        nonlocal expected_after_mutation
        plan = real_plan(*args, **kwargs)
        expected_after_mutation = _mutate_read_authority_path(
            bucket,
            mutation,
            outside=outside,
        )
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", mutate_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_(?:error|corrupt)"):
        archive.query(days=90)

    assert writes == []
    assert archive.state_path.read_bytes() == state_bytes
    assert archive.index_path.read_bytes() == index_bytes
    if mutation == "disappear":
        assert not bucket.exists()
    elif mutation in {"hardlink", "symlink"}:
        assert outside.read_bytes() == expected_after_mutation
    else:
        assert bucket.read_bytes() == expected_after_mutation


@pytest.mark.parametrize("schema_version", (2, 3))
@pytest.mark.parametrize(
    "mutation",
    (
        "same_bytes_new_inode",
        "same_inode_metadata",
        "disappear",
        "hardlink",
        pytest.param(
            "symlink",
            marks=pytest.mark.skipif(os.name == "nt", reason="POSIX symlink case"),
        ),
    ),
)
def test_archive_native_legacy_journal_binds_bucket_read_authority_before_state_write(
    tmp_path,
    monkeypatch,
    schema_version,
    mutation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    journal_bytes = _rewrite_pending_journal_as_native_schema(archive, schema_version)
    state_bytes = archive.state_path.read_bytes()
    index_bytes = archive.index_path.read_bytes()
    bucket = archive.archive_root / "2026-08-20.jsonl"
    outside = tmp_path / f"legacy-native-{schema_version}-{mutation}.jsonl"
    real_plan = archive._plan_prepared_authority
    expected_after_mutation: bytes | None = None

    def mutate_after_plan(*args, **kwargs):
        nonlocal expected_after_mutation
        plan = real_plan(*args, **kwargs)
        expected_after_mutation = _mutate_read_authority_path(
            bucket,
            mutation,
            outside=outside,
        )
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", mutate_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_(?:error|corrupt)"):
        archive.query(days=90)

    assert writes == []
    assert archive.state_path.read_bytes() == state_bytes
    assert archive.index_path.read_bytes() == index_bytes
    assert archive.journal_path.read_bytes() == journal_bytes
    if mutation == "disappear":
        assert not bucket.exists()
    elif mutation in {"hardlink", "symlink"}:
        assert outside.read_bytes() == expected_after_mutation
    else:
        assert bucket.read_bytes() == expected_after_mutation


@pytest.mark.parametrize(
    "mutation",
    (
        "same_bytes_new_inode",
        "same_inode_metadata",
        "disappear",
        "hardlink",
        pytest.param(
            "symlink",
            marks=pytest.mark.skipif(os.name == "nt", reason="POSIX symlink case"),
        ),
    ),
)
def test_archive_v1_general_migration_binds_bucket_read_authority_before_state_write(
    tmp_path,
    monkeypatch,
    mutation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    _write_historical_v1_finalized_archive(archive, (snapshot(event("a" * 20)),))
    state_bytes = archive.state_path.read_bytes()
    index_bytes = archive.index_path.read_bytes()
    bucket = archive.archive_root / "2026-08-20.jsonl"
    outside = tmp_path / f"legacy-v1-{mutation}.jsonl"
    real_plan = archive._plan_prepared_authority
    expected_after_mutation: bytes | None = None

    def mutate_after_plan(*args, **kwargs):
        nonlocal expected_after_mutation
        plan = real_plan(*args, **kwargs)
        expected_after_mutation = _mutate_read_authority_path(
            bucket,
            mutation,
            outside=outside,
        )
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", mutate_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_(?:error|corrupt)"):
        archive.query(days=90)

    assert writes == []
    assert archive.state_path.read_bytes() == state_bytes
    assert archive.index_path.read_bytes() == index_bytes
    if mutation == "disappear":
        assert not bucket.exists()
    elif mutation in {"hardlink", "symlink"}:
        assert outside.read_bytes() == expected_after_mutation
    else:
        assert bucket.read_bytes() == expected_after_mutation


def test_archive_first_v1_journal_binds_absent_bucket_read_authority_before_state_write(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    with archive._process_lock():
        pass
    journal = _journal_only_document(archive, snapshot(event("j" * 20)), 1)
    archive.journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    journal_bytes = archive.journal_path.read_bytes()
    bucket = archive.archive_root / "2026-08-20.jsonl"
    foreign = b'{"foreign":"first-v1-must-not-be-overwritten"}\n'
    real_plan = archive._plan_prepared_authority

    def install_foreign_bucket_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        bucket.write_bytes(foreign)
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", install_foreign_bucket_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_(?:error|corrupt)"):
        archive.query(days=90)

    assert writes == []
    assert not archive.state_path.exists()
    assert not archive.index_path.exists()
    assert archive.journal_path.read_bytes() == journal_bytes
    assert bucket.read_bytes() == foreign


@pytest.mark.parametrize(
    "mutation",
    ("same_bytes_new_inode", "same_inode_metadata", "disappear"),
)
def test_archive_native_upsert_binds_state_read_authority_before_first_mutation(
    tmp_path,
    monkeypatch,
    mutation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    state_bytes = archive.state_path.read_bytes()
    index_bytes = archive.index_path.read_bytes()
    bucket_bytes = (archive.archive_root / "2026-08-20.jsonl").read_bytes()
    outside = tmp_path / f"state-{mutation}.json"
    real_prepared_payload = archive._prepared_authority_payload

    def mutate_state_after_plan(*args, **kwargs):
        payload = real_prepared_payload(*args, **kwargs)
        _mutate_read_authority_path(
            archive.state_path,
            mutation,
            outside=outside,
        )
        return payload

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_prepared_authority_payload", mutate_state_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(
            snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20)
        )

    assert writes == []
    assert archive.index_path.read_bytes() == index_bytes
    assert (archive.archive_root / "2026-08-20.jsonl").read_bytes() == bucket_bytes
    if mutation == "disappear":
        assert not archive.state_path.exists()
    else:
        assert archive.state_path.read_bytes() == state_bytes


def test_archive_native_first_publication_binds_absent_state_authority(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    foreign = b'{"foreign":"state-authority"}\n'
    real_prepared_payload = archive._prepared_authority_payload

    def install_state_after_plan(*args, **kwargs):
        payload = real_prepared_payload(*args, **kwargs)
        archive.state_path.write_bytes(foreign)
        return payload

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_prepared_authority_payload", install_state_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(event("a" * 20)))

    assert writes == []
    assert archive.state_path.read_bytes() == foreign
    assert not archive.index_path.exists()
    assert not (archive.archive_root / "2026-08-20.jsonl").exists()


def test_archive_native_upsert_does_not_overwrite_newer_valid_prepared_state(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    newer_prepared = _next_prepared_state_payload(
        archive,
        snapshot(event("c" * 20), snapshot_id="3" * 20, raw_snapshot_id="4" * 20),
    )
    index_bytes = archive.index_path.read_bytes()
    bucket_bytes = (archive.archive_root / "2026-08-20.jsonl").read_bytes()
    real_prepared_payload = archive._prepared_authority_payload
    injected = False

    def install_newer_prepared_after_plan(*args, **kwargs):
        nonlocal injected
        payload = real_prepared_payload(*args, **kwargs)
        if not injected:
            replacement = archive.archive_root / ".newer-prepared-state.json"
            replacement.write_bytes(newer_prepared)
            os.replace(replacement, archive.state_path)
            injected = True
        return payload

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_prepared_authority_payload", install_newer_prepared_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(
            snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20)
        )

    assert writes == []
    assert archive.state_path.read_bytes() == newer_prepared
    assert archive.index_path.read_bytes() == index_bytes
    assert (archive.archive_root / "2026-08-20.jsonl").read_bytes() == bucket_bytes
    monkeypatch.setattr(archive, "_prepared_authority_payload", real_prepared_payload)
    monkeypatch.setattr(archive, "_atomic_write", real_atomic_write)
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "c" * 20}


def test_archive_prepared_recovery_does_not_overwrite_newer_valid_prepared_state(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    newer_prepared = _next_prepared_state_payload(
        archive,
        snapshot(event("c" * 20), snapshot_id="3" * 20, raw_snapshot_id="4" * 20),
    )
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    index_bytes = archive.index_path.read_bytes()
    bucket_bytes = (archive.archive_root / "2026-08-20.jsonl").read_bytes()
    real_plan = archive._plan_prepared_authority

    def install_newer_prepared_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        replacement = archive.archive_root / ".newer-recovery-state.json"
        replacement.write_bytes(newer_prepared)
        os.replace(replacement, archive.state_path)
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", install_newer_prepared_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert writes == []
    assert archive.state_path.read_bytes() == newer_prepared
    assert archive.index_path.read_bytes() == index_bytes
    assert (archive.archive_root / "2026-08-20.jsonl").read_bytes() == bucket_bytes
    monkeypatch.setattr(archive, "_plan_prepared_authority", real_plan)
    monkeypatch.setattr(archive, "_atomic_write", real_atomic_write)
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "c" * 20}


@pytest.mark.parametrize("schema_version", (1, 2, 3))
def test_archive_legacy_migration_binds_state_read_authority_before_prepared_write(
    tmp_path,
    monkeypatch,
    schema_version,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    if schema_version == 1:
        _write_historical_v1_finalized_archive(archive, (snapshot(event("a" * 20)),))
    else:
        archive.upsert(snapshot(event("a" * 20)))
        _leave_inline_state_before_bucket(
            archive,
            monkeypatch,
            snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
        )
        _rewrite_pending_journal_as_native_schema(archive, schema_version)
    state_bytes = archive.state_path.read_bytes()
    index_bytes = archive.index_path.read_bytes()
    real_plan = archive._plan_prepared_authority

    def replace_state_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        _replace_path_with_same_bytes(archive.state_path)
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", replace_state_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert writes == []
    assert archive.state_path.read_bytes() == state_bytes
    assert archive.index_path.read_bytes() == index_bytes


def _mutate_journal_read_authority(path: Path, mutation: str) -> bytes | None:
    if mutation == "absent_to_present":
        assert not path.exists()
        replacement = b'{"schema_version":99,"owner":"foreign-later"}\n'
        path.write_bytes(replacement)
        return replacement
    if mutation == "disappear":
        path.unlink()
        return None
    if mutation == "same_bytes_new_inode":
        original = path.read_bytes()
        _replace_path_with_same_bytes(path)
        return original
    if mutation == "same_inode_metadata":
        original = path.read_bytes()
        metadata = path.stat(follow_symlinks=False)
        os.utime(
            path,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000),
        )
        return original
    assert mutation == "same_length_content"
    changed = bytearray(path.read_bytes())
    offset = next(
        index
        for index, value in enumerate(changed)
        if value in b"abcdef"
    )
    changed[offset] = ord("f") if changed[offset] != ord("f") else ord("e")
    path.write_bytes(changed)
    return bytes(changed)


@pytest.mark.parametrize(
    "mutation",
    (
        "absent_to_present",
        "same_bytes_new_inode",
        "same_inode_metadata",
        "same_length_content",
        "disappear",
    ),
)
def test_archive_first_native_takeover_binds_transaction_read_record_before_state_write(
    tmp_path,
    monkeypatch,
    mutation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.archive_root.mkdir(parents=True)
    if mutation != "absent_to_present":
        archive.journal_path.write_bytes(
            b'{"schema_version":99,"owner":"foreign-before"}\n'
        )
    real_prepared_payload = archive._prepared_authority_payload
    expected_journal: bytes | None = None

    def mutate_transaction_after_plan(*args, **kwargs):
        nonlocal expected_journal
        payload = real_prepared_payload(*args, **kwargs)
        expected_journal = _mutate_journal_read_authority(
            archive.journal_path,
            mutation,
        )
        return payload

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_prepared_authority_payload", mutate_transaction_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(event("a" * 20)))

    assert writes == []
    assert not archive.state_path.exists()
    assert not archive.index_path.exists()
    assert not (archive.archive_root / "2026-08-20.jsonl").exists()
    if expected_journal is None:
        assert not archive.journal_path.exists()
    else:
        assert archive.journal_path.read_bytes() == expected_journal


@pytest.mark.parametrize("schema_version", (1, 2, 3))
@pytest.mark.parametrize(
    "mutation",
    (
        "same_bytes_new_inode",
        "same_inode_metadata",
        "same_length_content",
        "disappear",
    ),
)
def test_archive_legacy_journal_migration_binds_transaction_read_record_before_first_write(
    tmp_path,
    monkeypatch,
    schema_version,
    mutation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    with archive._process_lock():
        pass
    journal = _journal_only_document(
        archive,
        snapshot(event("j" * 20)),
        schema_version,
    )
    archive.journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    if schema_version == 2:
        _set_journal_mtime(archive, NOW)
    real_plan = archive._plan_prepared_authority
    expected_journal: bytes | None = None

    def mutate_transaction_after_plan(*args, **kwargs):
        nonlocal expected_journal
        plan = real_plan(*args, **kwargs)
        expected_journal = _mutate_journal_read_authority(
            archive.journal_path,
            mutation,
        )
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", mutate_transaction_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert writes == []
    assert not archive.state_path.exists()
    assert not archive.index_path.exists()
    assert not (archive.archive_root / "2026-08-20.jsonl").exists()
    if expected_journal is None:
        assert not archive.journal_path.exists()
    else:
        assert archive.journal_path.read_bytes() == expected_journal


@pytest.mark.parametrize("schema_version", (1, 2, 3))
def test_archive_legacy_journal_record_remains_bound_after_prepared_state_write(
    tmp_path,
    monkeypatch,
    schema_version,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    with archive._process_lock():
        pass
    journal = _journal_only_document(
        archive,
        snapshot(event("j" * 20)),
        schema_version,
    )
    archive.journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    if schema_version == 2:
        _set_journal_mtime(archive, NOW)
    real_atomic_write = archive._atomic_write
    changed_journal: bytes | None = None

    def mutate_transaction_after_prepared(path: Path, payload: bytes, maximum: int):
        nonlocal changed_journal
        result = real_atomic_write(path, payload, maximum)
        if (
            path == archive.state_path
            and json.loads(payload)["phase"] == "prepared"
            and changed_journal is None
        ):
            changed_journal = _mutate_journal_read_authority(
                archive.journal_path,
                "same_length_content",
            )
        return result

    monkeypatch.setattr(archive, "_atomic_write", mutate_transaction_after_prepared)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert changed_journal is not None
    assert archive.journal_path.read_bytes() == changed_journal
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "prepared"
    assert not archive.index_path.exists()
    assert not (archive.archive_root / "2026-08-20.jsonl").exists()


def test_archive_general_migration_binds_absent_transaction_before_state_write(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    legacy = _write_historical_v1_finalized_archive(
        archive,
        (snapshot(event("a" * 20)),),
    )
    state_before = archive.state_path.read_bytes()
    index_before = archive.index_path.read_bytes()
    bucket = archive.archive_root / "2026-08-20.jsonl"
    bucket_before = bucket.read_bytes()
    foreign = b'{"schema_version":99,"owner":"foreign-later"}\n'
    real_plan = archive._plan_prepared_authority

    def install_transaction_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        archive.journal_path.write_bytes(foreign)
        return plan

    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_plan_prepared_authority", install_transaction_after_plan)
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert writes == []
    assert archive.state_path.read_bytes() == state_before == legacy["state_bytes"]
    assert archive.index_path.read_bytes() == index_before
    assert bucket.read_bytes() == bucket_before
    assert archive.journal_path.read_bytes() == foreign


def test_archive_index_cas_rejects_same_length_content_with_current_metadata(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    record = archive._read_index_record(budget=archive_module._new_scan_budget())
    assert record is not None
    original_digest = record["digest"]
    changed = bytearray(archive.index_path.read_bytes())
    offset = changed.index(ord("a"))
    changed[offset] = ord("b")
    archive.index_path.write_bytes(changed)
    current = archive._capture_current_read_record(
        archive.index_path,
        archive_module._MAX_INDEX_BYTES,
        semantic_digest=original_digest,
    )
    assert current is not None
    record["metadata"] = current["metadata"]
    record["descriptor_metadata"] = current["descriptor_metadata"]
    if "read_record" in record:
        record["read_record"] = dict(record["read_record"])
        record["read_record"]["metadata"] = current["metadata"]
        record["read_record"]["descriptor_metadata"] = current["descriptor_metadata"]

    assert archive._index_record_is_current(record) is False


def test_archive_commit_record_hashing_uses_a_shared_byte_budget_before_mutation(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    state_before = archive.state_path.read_bytes()
    index_before = archive.index_path.read_bytes()
    bucket = archive.archive_root / "2026-08-20.jsonl"
    bucket_before = bucket.read_bytes()
    monkeypatch.setattr(archive_module, "_MAX_CAS_BYTES", 0, raising=False)
    writes: list[Path] = []
    real_atomic_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_atomic_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(
            event("b" * 20),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))

    assert writes == []
    assert archive.state_path.read_bytes() == state_before
    assert archive.index_path.read_bytes() == index_before
    assert bucket.read_bytes() == bucket_before


def _two_bucket_snapshot() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id="e" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW,
        events=(
            event("a" * 20, published_at=NOW - timedelta(days=1)),
            event("b" * 20, published_at=NOW),
        ),
    )


@pytest.mark.parametrize("operation", ("native", "recovery"))
def test_archive_multi_bucket_commit_rechecks_later_target_before_overwrite(
    tmp_path,
    monkeypatch,
    operation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    selected = _two_bucket_snapshot()
    if operation == "recovery":
        _leave_inline_state_before_bucket(archive, monkeypatch, selected)
    second_bucket = archive.archive_root / "2026-08-20.jsonl"
    foreign = b'{"foreign":"later-target-must-survive"}\n'
    real_atomic_write = archive._atomic_write
    installed = False
    writes: list[Path] = []

    def install_foreign_after_first_bucket(path: Path, payload: bytes, maximum: int):
        nonlocal installed
        result = real_atomic_write(path, payload, maximum)
        writes.append(path)
        if path.name == "2026-08-19.jsonl" and not installed:
            second_bucket.write_bytes(foreign)
            installed = True
        return result

    monkeypatch.setattr(archive, "_atomic_write", install_foreign_after_first_bucket)

    with pytest.raises(OSError, match="storage_corrupt"):
        if operation == "native":
            archive.upsert(selected)
        else:
            archive.query(days=90)

    assert installed is True
    assert second_bucket.read_bytes() == foreign
    assert second_bucket not in writes
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "prepared"
    assert not archive.index_path.exists()


@pytest.mark.parametrize("operation", ("native", "recovery"))
def test_archive_commit_rechecks_written_buckets_before_final_state(
    tmp_path,
    monkeypatch,
    operation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    selected = _two_bucket_snapshot()
    if operation == "recovery":
        _leave_inline_state_before_bucket(archive, monkeypatch, selected)
    target = archive.archive_root / "2026-08-19.jsonl"
    foreign = b'{"foreign":"post-index-must-survive"}\n'
    real_atomic_write = archive._atomic_write
    injected = False

    def replace_bucket_after_index(path: Path, payload: bytes, maximum: int):
        nonlocal injected
        result = real_atomic_write(path, payload, maximum)
        if path == archive.index_path and not injected:
            target.write_bytes(foreign)
            injected = True
        return result

    monkeypatch.setattr(archive, "_atomic_write", replace_bucket_after_index)

    with pytest.raises(OSError, match="storage_corrupt"):
        if operation == "native":
            archive.upsert(selected)
        else:
            archive.query(days=90)

    assert injected is True
    assert target.read_bytes() == foreign
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "prepared"


@pytest.mark.skipif(os.name == "nt", reason="POSIX lock path binding")
def test_archive_writer_rechecks_lock_path_binding_after_os_lock(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    checks = 0

    def staged_binding(_handle):
        nonlocal checks
        checks += 1
        return checks == 1

    monkeypatch.setattr(
        archive,
        "_lock_path_binds_handle",
        staged_binding,
        raising=False,
    )

    with pytest.raises(OSError, match="storage_error"):
        with archive._process_lock():
            pass

    assert checks == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX dual-inode lock race")
def test_archive_posix_rejects_lock_path_rebound_after_flock(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    real_try_lock = archive._try_lock
    replacement = tmp_path / "replacement.lock"
    replacement.write_bytes(b"replacement")
    rebound = False

    def rebind_after_flock(handle):
        nonlocal rebound
        acquired = real_try_lock(handle)
        if acquired and not rebound:
            os.replace(replacement, archive.lock_path)
            rebound = True
        return acquired

    monkeypatch.setattr(archive, "_try_lock", rebind_after_flock)

    with pytest.raises(OSError, match="storage_error"):
        with archive._process_lock():
            pass

    assert rebound is True


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "/proof/api_key=placeholder",
        "/proof/access-token%3Dplaceholder",
        "/proof/payload%3D%257B%2522client_secret%2522%253A%2522placeholder%2522%257D",
        "/proof/%252574oken%25253Dplaceholder",
    ),
)
def test_archive_rejects_sensitive_assignments_in_public_url_path_before_storage(
    tmp_path,
    unsafe_path,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    public_url = "https://official.example.com" + unsafe_path

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(primary_evidence=(evidence_item(
            "path-sensitive",
            canonical_url=public_url,
        ),))))

    assert not archive.archive_root.exists()


@pytest.mark.parametrize(
    "safe_path",
    (
        "/research/tokenization/article",
        "/proof/monkey=capuchin",
        "/proof/session-notes",
        "/proof/a%2Fb",
    ),
)
def test_archive_allows_benign_public_url_path_material(tmp_path, safe_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    public_url = "https://official.example.com" + safe_path

    archive.upsert(snapshot(event(primary_evidence=(evidence_item(
        "path-benign",
        canonical_url=public_url,
    ),))))

    assert archive.get("a" * 20)["primary_evidence"][0]["canonical_url"] == public_url


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "http://224.0.0.1/proof",
        "http://239.255.255.250/proof",
        "https://[ff02::1]/proof",
        "https://[ff0e::1]/proof",
        "https://[fec0::1]/proof",
        "https://[::]/proof",
    ),
)
def test_archive_rejects_non_public_unicast_literal_hosts_before_storage(
    tmp_path,
    unsafe_url,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)

    with pytest.raises(ValueError, match="URL"):
        archive.upsert(snapshot(event(
            primary_evidence=(evidence_item("unsafe", canonical_url=unsafe_url),),
        )))

    assert not archive.archive_root.exists()


@pytest.mark.parametrize(
    "public_url",
    (
        "https://8.8.8.8/proof",
        "https://[2606:4700:4700::1111]/proof",
        "https://official.example.com/proof",
    ),
)
def test_archive_keeps_public_unicast_and_benign_hosts(public_url):
    assert archive_module._archive_public_url(public_url) == public_url


def _archive_projection_bytes(archive: EvidenceArchive) -> dict[str, bytes]:
    if not archive.archive_root.exists():
        return {}
    return {
        path.relative_to(archive.archive_root).as_posix(): path.read_bytes()
        for path in sorted(archive.archive_root.rglob("*"))
        if path.is_file() and path != archive.lock_path
    }


@pytest.mark.parametrize(
    ("dimension", "constant_name"),
    (
        ("bytes", "_MAX_SCAN_BYTES"),
        ("rows", "_MAX_SCAN_ROWS"),
        ("nodes", "_MAX_SCAN_NODES"),
        ("files", "_MAX_SCAN_FILES"),
    ),
)
def test_archive_preflights_complete_target_scan_budget_before_prepared_state(
    tmp_path,
    monkeypatch,
    dimension,
    constant_name,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    before = _archive_projection_bytes(archive)
    before_rows = archive.query(days=90)

    initial_budget = archive_module._new_scan_budget()
    remaining = dict(initial_budget)
    diagnostics = archive._diagnostics()
    state = archive._read_authority_state(
        NOW,
        diagnostics=diagnostics,
        budget=remaining,
    )
    assert state is not None
    archive._validate_finalized_authority(
        state,
        now=NOW,
        diagnostics=diagnostics,
        budget=remaining,
    )
    base_consumption = initial_budget[dimension] - remaining[dimension]
    assert base_consumption > 0

    original_limit = getattr(archive_module, constant_name)
    monkeypatch.setattr(archive_module, constant_name, base_consumption)
    writes: list[Path] = []
    real_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(
            event("b" * 20, published_at=NOW - timedelta(days=1)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))

    assert writes == []
    assert _archive_projection_bytes(archive) == before
    monkeypatch.setattr(archive_module, constant_name, original_limit)
    assert archive.query(days=90) == before_rows


@pytest.mark.parametrize(
    ("dimension", "constant_name"),
    (
        ("bytes", "_MAX_CAS_BYTES"),
        ("files", "_MAX_CAS_FILES"),
    ),
)
def test_archive_preflights_scaled_cas_work_before_first_state_write(
    tmp_path,
    monkeypatch,
    dimension,
    constant_name,
):
    selected = _two_bucket_snapshot()
    probe = EvidenceArchive(tmp_path / "probe", now=lambda: NOW)
    _leave_inline_state_before_bucket(probe, monkeypatch, selected)
    prepared_size = probe.state_path.stat().st_size

    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    limit = prepared_size * 4 if dimension == "bytes" else 4
    monkeypatch.setattr(archive_module, constant_name, limit)
    writes: list[Path] = []
    real_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(selected)

    assert writes == []
    assert not archive.state_path.exists()
    assert not archive.index_path.exists()
    assert not list(archive.archive_root.glob("*.jsonl"))


def test_archive_default_cas_budget_commits_2400_events_across_ninety_buckets(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    summary = "公开摘要" + "证据" * 300
    selected = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="w" * 20,
        generated_at=NOW,
        events=tuple(
            event(
                f"{number:020x}",
                published_at=NOW - timedelta(days=number % 90),
                summary=summary,
            )
            for number in range(2_400)
        ),
    )

    archive.upsert(selected)

    state = json.loads(archive.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "finalized"
    assert archive.count() == 2_400
    assert len(list(archive.archive_root.glob("*.jsonl"))) == 90


@pytest.mark.parametrize("corrupt_kind", ("malformed_json", "future_schema"))
def test_archive_rejects_corrupt_only_target_bucket_before_prepared_state(
    tmp_path,
    monkeypatch,
    corrupt_kind,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target = archive.archive_root / "2026-08-19.jsonl"
    if corrupt_kind == "malformed_json":
        payload = b'{"schema_version":2,"broken":}\n'
    else:
        source = json.loads(
            (archive.archive_root / "2026-08-20.jsonl").read_text(encoding="utf-8")
        )
        source["event_id"] = "z" * 20
        source["schema_version"] = 99
        payload = (
            json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
    target.write_bytes(payload)
    before = _archive_projection_bytes(archive)
    writes: list[Path] = []
    real_write = archive._atomic_write

    def record_write(path: Path, encoded: bytes, maximum: int):
        writes.append(path)
        return real_write(path, encoded, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(
            event("b" * 20, published_at=NOW - timedelta(days=1)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))

    assert writes == []
    assert _archive_projection_bytes(archive) == before
    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]


def _fill_safe_canonical_bucket_capacity(
    archive: EvidenceArchive,
    total: int,
    *,
    excluded_names: set[str] | None = None,
) -> list[Path]:
    excluded = set() if excluded_names is None else set(excluded_names)
    existing = {path.name for path in archive.archive_root.glob("*.jsonl")}
    created: list[Path] = []
    cursor = datetime(2000, 1, 1, tzinfo=timezone.utc).date()
    while len(existing) < total:
        name = f"{cursor.isoformat()}.jsonl"
        cursor += timedelta(days=1)
        if name in excluded or name in existing:
            continue
        path = archive.archive_root / name
        path.write_bytes(b"")
        existing.add(name)
        created.append(path)
    assert len(existing) == total
    return created


def test_archive_rejects_512_to_513_bucket_union_before_any_native_mutation(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_name = "2026-08-19.jsonl"
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES,
        excluded_names={target_name},
    )
    before = _archive_projection_bytes(archive)
    before_rows = archive.query(days=90)
    writes: list[Path] = []
    real_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(
            event("b" * 20, published_at=NOW - timedelta(days=1)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))

    assert writes == []
    assert _archive_projection_bytes(archive) == before
    monkeypatch.setattr(archive, "_atomic_write", real_write)
    assert archive.query(days=90) == before_rows


def test_archive_allows_511_to_512_bucket_union(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_name = "2026-08-19.jsonl"
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES - 1,
        excluded_names={target_name},
    )

    archive.upsert(snapshot(
        event("b" * 20, published_at=NOW - timedelta(days=1)),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))

    assert len(archive._bucket_names()) == archive_module._MAX_ARCHIVE_FILES
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}


def test_archive_same_bucket_update_does_not_consume_another_capacity_slot(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _fill_safe_canonical_bucket_capacity(archive, archive_module._MAX_ARCHIVE_FILES)

    archive.upsert(snapshot(
        event("b" * 20),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))

    assert len(archive._bucket_names()) == archive_module._MAX_ARCHIVE_FILES
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}


def test_archive_expired_physical_bucket_still_consumes_capacity_until_cleanup(
    tmp_path,
    monkeypatch,
):
    clock = [NOW - timedelta(days=100)]
    archive = EvidenceArchive(tmp_path / "store", now=lambda: clock[0])
    old_time = clock[0]
    archive.upsert(snapshot(
        _timed_event("a" * 20, old_time),
        generated_at=old_time,
    ))
    target_name = "2026-08-20.jsonl"
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES,
        excluded_names={target_name},
    )
    clock[0] = NOW
    before = _archive_projection_bytes(archive)
    writes: list[Path] = []
    real_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(
            event("b" * 20),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))

    assert writes == []
    assert _archive_projection_bytes(archive) == before
    monkeypatch.setattr(archive, "_atomic_write", real_write)
    assert archive.query(days=90) == []


@pytest.mark.parametrize(
    "kind",
    (
        "hardlink",
        pytest.param("symlink", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX symlink case")),
    ),
)
def test_archive_bucket_capacity_ignores_unrelated_unsafe_canonical_entry(
    tmp_path,
    kind,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_name = "2026-08-19.jsonl"
    unsafe_name = "1999-12-31.jsonl"
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES - 1,
        excluded_names={target_name, unsafe_name},
    )
    outside = tmp_path / "outside.jsonl"
    outside_bytes = b'{"outside":"foreign"}\n'
    outside.write_bytes(outside_bytes)
    _install_unsafe_bucket_entry(archive.archive_root / unsafe_name, outside, kind)

    archive.upsert(snapshot(
        event("b" * 20, published_at=NOW - timedelta(days=1)),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))

    diagnostics = archive._diagnostics()
    assert len(archive._bucket_names(diagnostics=diagnostics)) == archive_module._MAX_ARCHIVE_FILES
    assert diagnostics["skipped_files"] == 1
    assert outside.read_bytes() == outside_bytes
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}


def test_archive_bucket_capacity_preflight_treats_disappeared_candidate_as_absent(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_name = "2026-08-19.jsonl"
    disappearing_name = "1999-12-31.jsonl"
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES - 1,
        excluded_names={target_name, disappearing_name},
    )
    archive.journal_path.write_bytes(b'{"owner":"foreign"}\n')
    disappearing = archive.archive_root / disappearing_name
    disappearing.write_bytes(b"")
    real_stat = Path.stat
    removed = False

    def disappear_before_nofollow_stat(path: Path, *args, **kwargs):
        nonlocal removed
        if path == disappearing and not removed:
            removed = True
            path.unlink()
            raise FileNotFoundError(str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", disappear_before_nofollow_stat)

    archive._preflight_archive_directory_entries([
        archive.state_path,
        archive.index_path,
        archive.archive_root / target_name,
    ])

    assert removed is True
    assert not disappearing.exists()


def test_archive_prepared_recovery_rejects_513th_bucket_before_any_write(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_name = "2026-08-19.jsonl"
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            event("b" * 20, published_at=NOW - timedelta(days=1)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ),
    )
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES,
        excluded_names={target_name},
    )
    before = _archive_projection_bytes(archive)
    writes: list[Path] = []
    real_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert writes == []
    assert _archive_projection_bytes(archive) == before


def test_archive_prepared_recovery_allows_511_to_512_bucket_union(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_name = "2026-08-19.jsonl"
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            event("b" * 20, published_at=NOW - timedelta(days=1)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ),
    )
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES - 1,
        excluded_names={target_name},
    )

    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}
    assert len(archive._bucket_names()) == archive_module._MAX_ARCHIVE_FILES


def test_archive_v1_general_migration_counts_prospective_bucket_before_state_write(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    legacy = _write_historical_v1_finalized_archive(archive, (snapshot(event("a" * 20)),))
    target_name = "2026-08-19.jsonl"
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES,
        excluded_names={target_name},
    )
    before = _archive_projection_bytes(archive)
    writes: list[Path] = []
    real_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(
            event("b" * 20, published_at=NOW - timedelta(days=1)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))

    assert writes == []
    assert _archive_projection_bytes(archive) == before
    assert archive.state_path.read_bytes() == legacy["state_bytes"]


def test_archive_v1_general_migration_is_queryable_with_512_existing_buckets(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    _write_historical_v1_finalized_archive(archive, (snapshot(event("a" * 20)),))
    _fill_safe_canonical_bucket_capacity(archive, archive_module._MAX_ARCHIVE_FILES)

    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    assert len(archive._bucket_names()) == archive_module._MAX_ARCHIVE_FILES


@pytest.mark.parametrize("schema_version", (2, 3))
def test_archive_native_legacy_journal_recovery_rejects_513th_bucket_before_state_write(
    tmp_path,
    monkeypatch,
    schema_version,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_name = "2026-08-19.jsonl"
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            event("b" * 20, published_at=NOW - timedelta(days=1)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ),
    )
    journal_bytes = _rewrite_pending_journal_as_native_schema(archive, schema_version)
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES,
        excluded_names={target_name},
    )
    before = _archive_projection_bytes(archive)
    writes: list[Path] = []
    real_write = archive._atomic_write

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert writes == []
    assert _archive_projection_bytes(archive) == before
    assert archive.journal_path.read_bytes() == journal_bytes


@pytest.mark.parametrize("schema_version", (2, 3))
def test_archive_native_legacy_journal_recovery_allows_511_to_512_bucket_union(
    tmp_path,
    monkeypatch,
    schema_version,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_name = "2026-08-19.jsonl"
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(
            event("b" * 20, published_at=NOW - timedelta(days=1)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ),
    )
    journal_bytes = _rewrite_pending_journal_as_native_schema(archive, schema_version)
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES - 1,
        excluded_names={target_name},
    )

    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}
    assert archive.journal_path.read_bytes() == journal_bytes
    assert len(archive._bucket_names()) == archive_module._MAX_ARCHIVE_FILES


def test_archive_capacity_commit_barrier_rejects_hardlink_becoming_safe_at_513(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    target_name = "2026-08-19.jsonl"
    unsafe_name = "1999-12-31.jsonl"
    _fill_safe_canonical_bucket_capacity(
        archive,
        archive_module._MAX_ARCHIVE_FILES - 1,
        excluded_names={target_name, unsafe_name},
    )
    outside = tmp_path / "outside-capacity.jsonl"
    outside.write_bytes(b"")
    unsafe_bucket = archive.archive_root / unsafe_name
    os.link(outside, unsafe_bucket)
    state_before = archive.state_path.read_bytes()
    index_before = archive.index_path.read_bytes()
    before_rows = archive.query(days=90)
    writes: list[Path] = []
    real_assert = archive._assert_prewrite_authority_is_current
    real_write = archive._atomic_write
    transitioned = False

    def make_hardlink_safe_after_authority_check(*args, **kwargs):
        nonlocal transitioned
        result = real_assert(*args, **kwargs)
        if not transitioned:
            outside.unlink()
            transitioned = True
        return result

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(
        archive,
        "_assert_prewrite_authority_is_current",
        make_hardlink_safe_after_authority_check,
    )
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(
            event("b" * 20, published_at=NOW - timedelta(days=1)),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))

    assert transitioned is True
    assert writes == []
    assert archive.state_path.read_bytes() == state_before
    assert archive.index_path.read_bytes() == index_before
    monkeypatch.setattr(archive, "_assert_prewrite_authority_is_current", real_assert)
    monkeypatch.setattr(archive, "_atomic_write", real_write)
    unsafe_bucket.unlink()
    assert archive.query(days=90) == before_rows


@pytest.mark.parametrize("transition", ("safe_to_unsafe", "disappear", "appear"))
def test_archive_rebinds_every_canonical_candidate_beside_first_native_mutation(
    tmp_path,
    monkeypatch,
    transition,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    current_bucket = archive.archive_root / "2026-08-20.jsonl"
    target_time = NOW - timedelta(days=1) if transition == "appear" else NOW
    target_bucket = archive.archive_root / f"{target_time.date().isoformat()}.jsonl"
    current_bytes = current_bucket.read_bytes()
    state_before = archive.state_path.read_bytes()
    index_before = archive.index_path.read_bytes()
    before_rows = archive.query(days=90)
    outside = tmp_path / f"classification-{transition}.jsonl"
    writes: list[Path] = []
    real_assert = archive._assert_prewrite_authority_is_current
    real_write = archive._atomic_write
    transitioned = False

    def change_classification_after_authority_check(*args, **kwargs):
        nonlocal transitioned
        result = real_assert(*args, **kwargs)
        if not transitioned:
            if transition == "safe_to_unsafe":
                os.link(current_bucket, outside)
            elif transition == "disappear":
                current_bucket.unlink()
            else:
                assert not target_bucket.exists()
                target_bucket.write_bytes(b"")
            transitioned = True
        return result

    def record_write(path: Path, payload: bytes, maximum: int):
        writes.append(path)
        return real_write(path, payload, maximum)

    monkeypatch.setattr(
        archive,
        "_assert_prewrite_authority_is_current",
        change_classification_after_authority_check,
    )
    monkeypatch.setattr(archive, "_atomic_write", record_write)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.upsert(snapshot(
            event("b" * 20, published_at=target_time),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))

    assert transitioned is True
    assert writes == []
    assert archive.state_path.read_bytes() == state_before
    assert archive.index_path.read_bytes() == index_before
    if transition == "safe_to_unsafe":
        outside.unlink()
    elif transition == "disappear":
        current_bucket.write_bytes(current_bytes)
    else:
        target_bucket.unlink()
    monkeypatch.setattr(archive, "_assert_prewrite_authority_is_current", real_assert)
    monkeypatch.setattr(archive, "_atomic_write", real_write)
    assert archive.query(days=90) == before_rows


def test_archive_query_preserves_real_crash_temp_while_enforcing_normal_entry_cap(
    tmp_path,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _fill_safe_canonical_bucket_capacity(archive, archive_module._MAX_ARCHIVE_FILES)
    foreign_journal = b'{"schema_version":4,"owner":"foreign"}\n'
    archive.journal_path.write_bytes(foreign_journal)
    assert len(list(archive.archive_root.iterdir())) == archive_module._MAX_ARCHIVE_DIRECTORY_ENTRIES

    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash_after_archive_temp_fsync,
        args=(str(archive.root),),
    )
    process.start()
    process.join(30)

    assert process.exitcode == 73
    owned_temps = list(archive.archive_root.glob(".state.json.*.tmp"))
    assert len(owned_temps) == 1
    crash_bytes = owned_temps[0].read_bytes()
    assert len(list(archive.archive_root.iterdir())) == archive_module._MAX_ARCHIVE_DIRECTORY_ENTRIES + 1
    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    assert owned_temps[0].read_bytes() == crash_bytes
    assert archive.journal_path.read_bytes() == foreign_journal


def test_archive_four_real_crash_temps_remain_usable_and_fifth_fails_closed(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    context = multiprocessing.get_context("spawn")
    residuals: dict[Path, bytes] = {}

    for crash_number in range(1, archive_module._MAX_TEMP_ARTIFACT_FILES + 1):
        process = context.Process(
            target=_crash_after_archive_temp_fsync,
            args=(str(archive.root),),
        )
        process.start()
        process.join(30)
        assert process.exitcode == 73
        current = sorted(archive.archive_root.glob(".state.json.*.tmp"))
        assert len(current) == crash_number
        residuals = {path: path.read_bytes() for path in current}
        assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
        assert {path: path.read_bytes() for path in residuals} == residuals

    fifth = context.Process(
        target=_crash_after_archive_temp_fsync,
        args=(str(archive.root),),
    )
    fifth.start()
    fifth.join(30)
    assert fifth.exitcode == 73
    all_residuals = {
        path: path.read_bytes()
        for path in archive.archive_root.glob(".state.json.*.tmp")
    }
    assert len(all_residuals) == archive_module._MAX_TEMP_ARTIFACT_FILES + 1

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert {path: path.read_bytes() for path in all_residuals} == all_residuals


@pytest.mark.parametrize("operation", ("query", "get", "count", "upsert"))
def test_archive_public_operations_preserve_exact_named_single_link_foreign_temp(
    tmp_path,
    operation,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    foreign_temp = archive.archive_root / ".state.json.abcdefgh.tmp"
    foreign_bytes = b"foreign-exact-name-must-survive"
    foreign_temp.write_bytes(foreign_bytes)

    if operation == "query":
        assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    elif operation == "get":
        assert archive.get("a" * 20)["event_id"] == "a" * 20
    elif operation == "count":
        assert archive.count() == 1
    else:
        archive.upsert(snapshot(
            event("b" * 20),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))
        assert {row["event_id"] for row in archive.query(days=90)} == {
            "a" * 20,
            "b" * 20,
        }

    assert foreign_temp.read_bytes() == foreign_bytes


@pytest.mark.parametrize("temp_count", (1, 2, 3, 4))
def test_archive_ignores_and_preserves_up_to_four_safe_temp_artifacts(tmp_path, temp_count):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    temp_names = (
        ".state.json.abcdefgh.tmp",
        ".index.json.bcdefghi.tmp",
        ".2026-08-20.jsonl.cdefghij.tmp",
        ".2026-08-19.jsonl.defghijk.tmp",
    )
    artifacts: dict[Path, bytes] = {}
    for index, name in enumerate(temp_names[:temp_count]):
        path = archive.archive_root / name
        payload = f"foreign-{index}".encode("ascii")
        path.write_bytes(payload)
        artifacts[path] = payload

    state_before = archive.state_path.read_bytes()
    index_before = archive.index_path.read_bytes()
    buckets_before = {
        path.name: path.read_bytes()
        for path in archive.archive_root.glob("*.jsonl")
    }

    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    assert archive.state_path.read_bytes() == state_before
    assert archive.index_path.read_bytes() == index_before
    assert {
        path.name: path.read_bytes()
        for path in archive.archive_root.glob("*.jsonl")
    } == buckets_before
    assert {path: path.read_bytes() for path in artifacts} == artifacts


def test_archive_four_safe_temp_artifacts_use_only_bounded_directory_headroom(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _fill_safe_canonical_bucket_capacity(archive, archive_module._MAX_ARCHIVE_FILES)
    foreign_journal = b'{"schema_version":4,"owner":"foreign"}\n'
    archive.journal_path.write_bytes(foreign_journal)
    temp_names = (
        ".state.json.abcdefgh.tmp",
        ".index.json.bcdefghi.tmp",
        ".2026-08-20.jsonl.cdefghij.tmp",
        ".2026-08-19.jsonl.defghijk.tmp",
    )
    artifacts = {
        archive.archive_root / name: f"foreign-{index}".encode("ascii")
        for index, name in enumerate(temp_names)
    }
    for path, payload in artifacts.items():
        path.write_bytes(payload)
    authority_before = {
        "state": archive.state_path.read_bytes(),
        "index": archive.index_path.read_bytes(),
        "buckets": {
            path.name: path.read_bytes()
            for path in archive.archive_root.glob("*.jsonl")
        },
    }

    assert len(list(archive.archive_root.iterdir())) == archive_module._MAX_ARCHIVE_DIRECTORY_SCAN_ENTRIES
    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    assert archive.state_path.read_bytes() == authority_before["state"]
    assert archive.index_path.read_bytes() == authority_before["index"]
    assert {
        path.name: path.read_bytes()
        for path in archive.archive_root.glob("*.jsonl")
    } == authority_before["buckets"]
    assert {path: path.read_bytes() for path in artifacts} == artifacts

    archive.upsert(snapshot(
        event("b" * 20),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    ))
    assert {row["event_id"] for row in archive.query(days=90)} == {
        "a" * 20,
        "b" * 20,
    }
    assert {path: path.read_bytes() for path in artifacts} == artifacts


@pytest.mark.parametrize("operation", ("query", "upsert"))
def test_archive_fifth_safe_temp_artifact_fails_closed_without_deletion(tmp_path, operation):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    temp_names = (
        ".state.json.abcdefgh.tmp",
        ".index.json.bcdefghi.tmp",
        ".2026-08-20.jsonl.cdefghij.tmp",
        ".2026-08-19.jsonl.defghijk.tmp",
        ".state.json.efghijkl.tmp",
    )
    artifacts = {}
    for index, name in enumerate(temp_names):
        path = archive.archive_root / name
        payload = f"foreign-{index}".encode("ascii")
        path.write_bytes(payload)
        artifacts[path] = payload

    with pytest.raises(OSError, match="storage_corrupt"):
        if operation == "query":
            archive.query(days=90)
        else:
            archive.upsert(snapshot(
                event("b" * 20),
                snapshot_id="1" * 20,
                raw_snapshot_id="2" * 20,
            ))

    assert {path: path.read_bytes() for path in artifacts} == artifacts


def test_archive_unknown_temp_name_fails_closed_without_deletion_below_capacity(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    unknown_temp = archive.archive_root / ".state.json.foreign.tmp"
    unknown_bytes = b"foreign-unknown-name-must-survive"
    unknown_temp.write_bytes(unknown_bytes)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert unknown_temp.read_bytes() == unknown_bytes


def test_archive_caught_atomic_failure_preserves_created_temp_artifact(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    real_replace = archive_module._replace_durable

    def fail_state(_source: Path, destination: Path) -> None:
        if destination == archive.state_path:
            raise OSError("simulated state write failure")
        real_replace(_source, destination)

    monkeypatch.setattr(archive_module, "_replace_durable", fail_state)

    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(snapshot(event("a" * 20)))

    residuals = list(archive.archive_root.glob(".state.json.*.tmp"))
    assert len(residuals) == 1
    residual_bytes = residuals[0].read_bytes()
    assert residual_bytes

    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)
    archive.upsert(snapshot(event("a" * 20)))
    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    assert residuals[0].read_bytes() == residual_bytes


def test_archive_unknown_temp_over_normal_entry_cap_fails_closed_without_deletion(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _fill_safe_canonical_bucket_capacity(archive, archive_module._MAX_ARCHIVE_FILES)
    archive.journal_path.write_bytes(b'{"schema_version":4,"owner":"foreign"}\n')
    unknown_temp = archive.archive_root / ".state.json.foreign.tmp"
    unknown_bytes = b"foreign-temp-must-survive"
    unknown_temp.write_bytes(unknown_bytes)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert unknown_temp.read_bytes() == unknown_bytes


@pytest.mark.parametrize(
    "kind",
    (
        "hardlink",
        pytest.param("symlink", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX symlink case")),
    ),
)
def test_archive_unsafe_owned_looking_temp_fails_closed_without_deletion(tmp_path, kind):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _fill_safe_canonical_bucket_capacity(archive, archive_module._MAX_ARCHIVE_FILES)
    archive.journal_path.write_bytes(b'{"schema_version":4,"owner":"foreign"}\n')
    outside = tmp_path / "foreign-owned-looking-temp"
    outside_bytes = b"foreign-hardlink-must-survive"
    outside.write_bytes(outside_bytes)
    owned_looking = archive.archive_root / ".state.json.abcdefgh.tmp"
    _install_unsafe_bucket_entry(owned_looking, outside, kind)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    assert owned_looking.read_bytes() == outside_bytes
    assert outside.read_bytes() == outside_bytes
    if kind == "hardlink":
        assert owned_looking.stat().st_nlink == 2
    else:
        assert owned_looking.is_symlink()


@pytest.mark.parametrize("schema_version", (2, 3))
def test_archive_native_legacy_migration_uses_fresh_postcommit_budget_at_512_buckets(
    tmp_path,
    monkeypatch,
    schema_version,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _leave_inline_state_before_bucket(
        archive,
        monkeypatch,
        snapshot(event("b" * 20), snapshot_id="1" * 20, raw_snapshot_id="2" * 20),
    )
    journal_bytes = _rewrite_pending_journal_as_native_schema(archive, schema_version)
    _fill_safe_canonical_bucket_capacity(archive, archive_module._MAX_ARCHIVE_FILES)

    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "finalized"
    assert archive.journal_path.read_bytes() == journal_bytes


def test_archive_v1_general_migration_uses_fresh_postcommit_budget_at_512_buckets(tmp_path):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    _write_historical_v1_finalized_archive(archive, (snapshot(event("a" * 20)),))
    _fill_safe_canonical_bucket_capacity(archive, archive_module._MAX_ARCHIVE_FILES)
    foreign_journal = b'{"schema_version":1,"owner":"foreign"}\n'
    archive.journal_path.write_bytes(foreign_journal)

    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "finalized"
    assert archive.journal_path.read_bytes() == foreign_journal


@pytest.mark.parametrize("operation", ("query", "upsert"))
@pytest.mark.parametrize("candidate_kind", ("ordinary", "temp_artifact"))
def test_archive_public_operation_treats_517th_candidate_vanishing_before_stat_as_absent(
    tmp_path,
    monkeypatch,
    operation,
    candidate_kind,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    archive.upsert(snapshot(event("a" * 20)))
    _fill_safe_canonical_bucket_capacity(archive, archive_module._MAX_ARCHIVE_FILES)
    archive.journal_path.write_bytes(b'{"schema_version":4,"owner":"foreign"}\n')
    candidate_name = (
        "foreign-vanishing-entry"
        if candidate_kind == "ordinary"
        else ".state.json.abcdefgh.tmp"
    )
    candidate = archive.archive_root / candidate_name
    candidate.write_bytes(b"vanishing")
    assert len(list(archive.archive_root.iterdir())) == (
        archive_module._MAX_ARCHIVE_DIRECTORY_ENTRIES + 1
    )

    real_scandir = os.scandir
    real_stat = Path.stat
    vanished = False
    candidate_stat_calls = 0

    class BareEnoentError(OSError):
        pass

    class CandidateLastEntries:
        def __enter__(self):
            with real_scandir(archive.archive_root) as entries:
                names = [entry.name for entry in entries]
            self._names = sorted(name for name in names if name != candidate_name)
            if candidate_name in names:
                self._names.append(candidate_name)
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter(SimpleNamespace(name=name) for name in self._names)

    def candidate_last_scandir(path):
        if Path(path) == archive.archive_root:
            return CandidateLastEntries()
        return real_scandir(path)

    def vanish_before_nofollow_stat(path: Path, *args, **kwargs):
        nonlocal vanished, candidate_stat_calls
        if path == candidate and not vanished:
            candidate_stat_calls += 1
            vanished = True
            candidate.unlink()
            if candidate_kind == "ordinary":
                raise FileNotFoundError(str(candidate))
            raise BareEnoentError(errno.ENOENT, "vanished", str(candidate))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(archive_module.os, "scandir", candidate_last_scandir)
    monkeypatch.setattr(Path, "stat", vanish_before_nofollow_stat)

    if operation == "query":
        assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]
    else:
        archive.upsert(snapshot(
            event("b" * 20),
            snapshot_id="1" * 20,
            raw_snapshot_id="2" * 20,
        ))
        assert {row["event_id"] for row in archive.query(days=90)} == {
            "a" * 20,
            "b" * 20,
        }

    assert vanished is True
    assert candidate_stat_calls == 1
    assert not candidate.exists()


def test_archive_first_v1_journal_uses_a_new_budget_after_recovery_phase(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path / "store", now=lambda: NOW)
    with archive._process_lock():
        pass
    journal = _journal_only_document(archive, snapshot(event("j" * 20)), 1)
    archive.journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    real_recover = archive._recover_prepared_authority

    def exhaust_finished_phase_budget(*args, **kwargs):
        result = real_recover(*args, **kwargs)
        kwargs["budget"]["files"] = 0
        return result

    monkeypatch.setattr(archive, "_recover_prepared_authority", exhaust_finished_phase_budget)

    assert [row["event_id"] for row in archive.query(days=90)] == ["j" * 20]
    assert json.loads(archive.state_path.read_text(encoding="utf-8"))["phase"] == "finalized"


def test_archive_raw_authority_binds_exact_untruncated_input_and_retry_is_idempotent(
    tmp_path,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW + timedelta(hours=1))
    shared_prefix = "公开摘要" + ("x" * 1_200)
    first = snapshot(event(summary=shared_prefix + "A"))
    changed_tail = snapshot(event(summary=shared_prefix + "B"))

    archive.upsert(first)
    archive.upsert(first)
    state_before = archive.state_path.read_bytes()

    with pytest.raises(ValueError, match="lineage|raw snapshot"):
        archive.upsert(changed_tail)

    assert archive.state_path.read_bytes() == state_before
    row = archive.get(first.events[0].event_id)
    assert row is not None
    assert row["summary"] == shared_prefix[:1_200]
    assert "raw_input_digest" in row
    assert len(row["raw_input_digest"]) == 64
    assert row["snapshot_history"][0]["raw_input_digest"] == row["raw_input_digest"]
    raw_text = b"".join(
        path.read_bytes()
        for path in archive.archive_root.iterdir()
        if path.is_file()
    )
    assert (shared_prefix + "A").encode("utf-8") not in raw_text


def test_archive_raw_input_digest_covers_every_authoritative_event_field_group():
    proof = evidence_item(
        "proof",
        canonical_url="https://official.example.com/proof?utm_source=first",
        excerpt="evidence body A",
    )
    amount = KeyField(
        "amount",
        "12亿元",
        "1200000000",
        FieldVerificationStatus.VERIFIED,
        ("proof",),
        "official amount",
    )
    base = event(primary_evidence=(proof,), key_fields=(amount,))
    transition = base.status_history[0]
    changed = {
        "title": replace(base, title=("t" * 500) + "tail-B"),
        "summary": replace(base, summary=("s" * 1_200) + "tail-B"),
        "core_claim": replace(base, core_claim=("c" * 1_200) + "tail-B"),
        "url": replace(
            base,
            primary_evidence=(replace(
                proof,
                canonical_url="https://official.example.com/proof?utm_source=second",
            ),),
        ),
        "evidence": replace(
            base,
            primary_evidence=(replace(proof, excerpt="evidence body B"),),
        ),
        "status": replace(base, verification_status=VerificationStatus.DISPROVED),
        "status_history": replace(
            base,
            status_history=(replace(transition, reason="different transition truth"),),
        ),
        "key_fields": replace(
            base,
            key_fields=(replace(amount, raw_value="12.00000001亿元"),),
        ),
    }

    original_digest = archive_module._raw_input_event_digest(base)
    assert len(original_digest) == 64
    assert {
        field_name
        for field_name, value in changed.items()
        if archive_module._raw_input_event_digest(value) == original_digest
    } == set()


def test_archive_current_schema_requires_top_and_lineage_raw_input_digests(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    row = json.loads(next(archive.archive_root.glob("*.jsonl")).read_text(encoding="utf-8"))

    missing_top = json.loads(json.dumps(row))
    missing_top.pop("raw_input_digest")
    with pytest.raises(ValueError, match="schema"):
        archive_module._archive_from_document(missing_top)

    missing_lineage = json.loads(json.dumps(row))
    missing_lineage["snapshot_history"][0].pop("raw_input_digest")
    with pytest.raises(ValueError, match="lineage schema"):
        archive_module._archive_from_document(missing_lineage)


def _component_isolation_case(tmp_path):
    operation_now = NOW + timedelta(hours=3)
    archive = EvidenceArchive(tmp_path, now=lambda: operation_now)
    old_time = NOW
    new_time = NOW + timedelta(hours=1)
    original_evidence = evidence_item(
        "shared-evidence",
        canonical_url="https://official.example.com/original",
        published_at=old_time,
    )
    original = replace(
        event("a" * 20, primary_evidence=(original_evidence,)),
        verified_at=old_time,
        evidence_as_of=old_time,
        status_history=(StatusTransition(
            None,
            VerificationStatus.VERIFIED,
            old_time,
            "reason-verified",
        ),),
    )
    archive.upsert(snapshot(
        original,
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
        generated_at=old_time,
    ))

    conflicting_evidence = replace(
        original_evidence,
        canonical_url="https://official.example.com/conflicting-alias",
        published_at=new_time,
    )
    conflicting = replace(
        original,
        primary_evidence=(conflicting_evidence,),
        verified_at=new_time,
        evidence_as_of=new_time,
    )
    sibling = replace(
        event("b" * 20),
        verified_at=new_time,
        evidence_as_of=new_time,
        status_history=(StatusTransition(
            None,
            VerificationStatus.VERIFIED,
            new_time,
            "reason-verified",
        ),),
    )
    conflicted_component = EvidenceSnapshot(
        snapshot_id="3" * 20,
        raw_snapshot_id="4" * 20,
        generated_at=new_time,
        events=(conflicting, sibling),
    )
    independent = snapshot(
        replace(
            event("c" * 20),
            verified_at=new_time,
            evidence_as_of=new_time,
            status_history=(StatusTransition(
                None,
                VerificationStatus.VERIFIED,
                new_time,
                "reason-verified",
            ),),
        ),
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
        generated_at=new_time,
    )
    return archive, conflicted_component, independent


def test_archive_component_merge_conflict_rejects_only_its_connected_raw_component(
    tmp_path,
):
    archive, conflicted_component, independent = _component_isolation_case(tmp_path)

    outcome = archive.upsert_many(
        (conflicted_component, independent),
        expected_raw_event_sets={
            conflicted_component.raw_snapshot_id: ("a" * 20, "b" * 20),
            independent.raw_snapshot_id: ("c" * 20,),
        },
        continue_on_raw_conflict=True,
    )

    assert type(outcome).__name__ == "ArchiveBatchOutcome"
    assert outcome.accepted_event_ids == frozenset({"c" * 20})
    assert outcome.rejected_event_ids == frozenset({"a" * 20, "b" * 20})
    assert outcome.reasons_by_event == {
        "a" * 20: "component_conflict",
        "b" * 20: "component_conflict",
    }
    rows = {row["event_id"]: row for row in archive.query(90)}
    assert set(rows) == {"a" * 20, "c" * 20}
    assert rows["a" * 20]["primary_evidence"][0]["canonical_url"].endswith(
        "/original"
    )


@pytest.mark.parametrize("failed_write", (1, 3, 4))
def test_archive_component_isolation_fault_keeps_rejected_component_old_and_retryable(
    tmp_path,
    monkeypatch,
    failed_write,
):
    archive, conflicted_component, independent = _component_isolation_case(tmp_path)
    real_write = archive._atomic_write
    writes = 0

    def fail_selected_write(path, payload, maximum):
        nonlocal writes
        writes += 1
        if writes == failed_write:
            raise OSError("simulated isolated component write failure")
        return real_write(path, payload, maximum)

    monkeypatch.setattr(archive, "_atomic_write", fail_selected_write)
    with pytest.raises(OSError):
        archive.upsert_many(
            (conflicted_component, independent),
            expected_raw_event_sets={
                conflicted_component.raw_snapshot_id: ("a" * 20, "b" * 20),
                independent.raw_snapshot_id: ("c" * 20,),
            },
            continue_on_raw_conflict=True,
        )

    observed = {
        row["event_id"]: row
        for row in EvidenceArchive(
            tmp_path,
            now=lambda: NOW + timedelta(hours=3),
        ).query(90)
    }
    assert set(observed) in ({"a" * 20}, {"a" * 20, "c" * 20})
    assert "b" * 20 not in observed
    assert observed["a" * 20]["primary_evidence"][0]["canonical_url"].endswith(
        "/original"
    )

    monkeypatch.setattr(archive, "_atomic_write", real_write)
    outcome = archive.upsert_many(
        (conflicted_component, independent),
        expected_raw_event_sets={
            conflicted_component.raw_snapshot_id: ("a" * 20, "b" * 20),
            independent.raw_snapshot_id: ("c" * 20,),
        },
        continue_on_raw_conflict=True,
    )
    assert outcome.accepted_event_ids == frozenset({"c" * 20})
    assert outcome.rejected_event_ids == frozenset({"a" * 20, "b" * 20})
    assert {
        row["event_id"]
        for row in EvidenceArchive(
            tmp_path,
            now=lambda: NOW + timedelta(hours=3),
        ).query(90)
    } == {"a" * 20, "c" * 20}


def test_archive_component_isolation_is_serialized_and_idempotent_across_writers(
    tmp_path,
):
    first, conflicted_component, independent = _component_isolation_case(tmp_path)
    second = EvidenceArchive(
        tmp_path,
        now=lambda: NOW + timedelta(hours=3),
    )
    kwargs = {
        "expected_raw_event_sets": {
            conflicted_component.raw_snapshot_id: ("a" * 20, "b" * 20),
            independent.raw_snapshot_id: ("c" * 20,),
        },
        "continue_on_raw_conflict": True,
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            future.result(timeout=30)
            for future in (
                executor.submit(
                    first.upsert_many,
                    (conflicted_component, independent),
                    **kwargs,
                ),
                executor.submit(
                    second.upsert_many,
                    (conflicted_component, independent),
                    **kwargs,
                ),
            )
        )

    assert all(
        outcome.accepted_event_ids == frozenset({"c" * 20})
        and outcome.rejected_event_ids == frozenset({"a" * 20, "b" * 20})
        for outcome in outcomes
    )
    rows = {
        row["event_id"]: row
        for row in EvidenceArchive(
            tmp_path,
            now=lambda: NOW + timedelta(hours=3),
        ).query(90)
    }
    assert set(rows) == {"a" * 20, "c" * 20}
    assert rows["a" * 20]["primary_evidence"][0]["canonical_url"].endswith(
        "/original"
    )


def test_archive_previous_schema_without_raw_digest_is_readable_but_cannot_authorize_retry(
    tmp_path,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW + timedelta(hours=1))
    selected = snapshot(event(summary="legacy exact input"))
    archive.upsert(selected)
    bucket = next(archive.archive_root.glob("*.jsonl"))
    row = json.loads(bucket.read_text(encoding="utf-8"))
    row["schema_version"] = 2
    row.pop("raw_input_digest")
    for lineage in row["snapshot_history"]:
        lineage.pop("raw_input_digest")
    bucket.write_text(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_finalized_bucket_manifest(archive)

    assert archive.get(selected.events[0].event_id) is not None
    state_before = archive.state_path.read_bytes()
    with pytest.raises(ValueError, match="lineage|raw snapshot"):
        archive.upsert(selected)
    assert archive.state_path.read_bytes() == state_before


def _schema_v2_merged_multi_lineage_row(tmp_path):
    operation_now = NOW + timedelta(hours=2)
    archive = EvidenceArchive(tmp_path, now=lambda: operation_now)
    verified, disproved = _causal_recovery_snapshots()
    replacement_evidence = evidence_item(
        "replacement-proof",
        canonical_url="https://official.example.com/replacement-proof",
        published_at=disproved.generated_at,
    )
    disproved = replace(
        disproved,
        events=(replace(
            disproved.events[0],
            primary_evidence=(replacement_evidence,),
        ),),
    )
    archive.upsert_many((verified, disproved))
    bucket = next(archive.archive_root.glob("*.jsonl"))
    current = json.loads(bucket.read_text(encoding="utf-8"))
    current_identity = (
        current["evidence_snapshot_id"],
        current["raw_snapshot_id"],
        current["snapshot_generated_at"],
    )
    current_lineage = next(
        lineage
        for lineage in current["snapshot_history"]
        if (
            lineage["evidence_snapshot_id"],
            lineage["raw_snapshot_id"],
            lineage["generated_at"],
        ) == current_identity
    )
    assert current["content_digest"] != current_lineage["content_digest"]
    previous = json.loads(json.dumps(current, ensure_ascii=False))
    previous["schema_version"] = 2
    previous.pop("raw_input_digest")
    for lineage in previous["snapshot_history"]:
        lineage.pop("raw_input_digest")
    bucket.write_text(
        json.dumps(previous, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_finalized_bucket_manifest(archive)
    return archive, bucket, previous, current_identity


def test_archive_schema_v2_merged_multi_lineage_uses_current_lineage_synthetic_raw_digest(
    tmp_path,
):
    archive, _bucket, previous, current_identity = _schema_v2_merged_multi_lineage_row(
        tmp_path
    )
    current_lineage = next(
        lineage
        for lineage in previous["snapshot_history"]
        if (
            lineage["evidence_snapshot_id"],
            lineage["raw_snapshot_id"],
            lineage["generated_at"],
        ) == current_identity
    )
    expected_raw_digest = hashlib.sha256(
        f"legacy-raw-input-v2:{current_lineage['content_digest']}".encode("ascii")
    ).hexdigest()

    parsed = archive_module._archive_from_document(previous)

    assert parsed["raw_input_digest"] == expected_raw_digest
    assert next(
        lineage["raw_input_digest"]
        for lineage in parsed["snapshot_history"]
        if (
            lineage["evidence_snapshot_id"],
            lineage["raw_snapshot_id"],
            lineage["generated_at"],
        ) == current_identity
    ) == expected_raw_digest
    assert {item["evidence_id"] for item in parsed["primary_evidence"]} == {
        "evidence-" + ("a" * 20),
        "replacement-proof",
    }
    assert [transition["to_status"] for transition in parsed["status_history"]] == [
        "verified",
        "disproved",
    ]

    queried = archive.query(90)
    assert [row["event_id"] for row in queried] == ["a" * 20]
    independent = snapshot(
        event(
            "c" * 20,
            published_at=NOW - timedelta(days=1),
            primary_evidence=(evidence_item(
                "independent-proof",
                published_at=NOW - timedelta(days=1),
            ),),
        ),
        snapshot_id="5" * 20,
        raw_snapshot_id="6" * 20,
        generated_at=NOW + timedelta(hours=1),
    )
    archive.upsert(independent)
    archive.upsert(independent)
    migrated = {row["event_id"]: row for row in archive.query(90)}
    assert set(migrated) == {"a" * 20, "c" * 20}
    assert len(migrated["a" * 20]["snapshot_history"]) == 2
    assert {item["evidence_id"] for item in migrated["a" * 20]["primary_evidence"]} == {
        "evidence-" + ("a" * 20),
        "replacement-proof",
    }


@pytest.mark.parametrize("corruption", ("missing", "duplicate", "mismatched-current"))
def test_archive_schema_v2_current_lineage_identity_is_fail_closed(tmp_path, corruption):
    _archive, _bucket, previous, current_identity = _schema_v2_merged_multi_lineage_row(
        tmp_path
    )
    current_index = next(
        index
        for index, lineage in enumerate(previous["snapshot_history"])
        if (
            lineage["evidence_snapshot_id"],
            lineage["raw_snapshot_id"],
            lineage["generated_at"],
        ) == current_identity
    )
    if corruption == "missing":
        previous["snapshot_history"].pop(current_index)
    elif corruption == "duplicate":
        previous["snapshot_history"].append(
            json.loads(json.dumps(previous["snapshot_history"][current_index]))
        )
    else:
        previous["raw_snapshot_id"] = "9" * 20

    with pytest.raises(ValueError, match="lineage"):
        archive_module._archive_from_document(previous)


def test_archive_schema_v3_still_requires_exact_current_raw_input_digest(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW + timedelta(hours=1))
    archive.upsert(snapshot(event()))
    row = json.loads(next(archive.archive_root.glob("*.jsonl")).read_text(encoding="utf-8"))
    row["raw_input_digest"] = "0" * 64

    with pytest.raises(ValueError, match="lineage"):
        archive_module._archive_from_document(row)
