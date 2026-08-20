from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path

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


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def evidence_item(
    evidence_id: str,
    *,
    canonical_url: str | None = None,
    excerpt: str = "公开证据摘要",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        content_source="official.example.com",
        collector_source="collector.example",
        canonical_url=canonical_url or f"https://official.example.com/{evidence_id}",
        published_at=NOW,
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
        verification_reason=f"reason-{status.value}",
        verified_at=NOW,
        evidence_as_of=NOW,
        key_fields=key_fields,
        primary_evidence=primary_evidence,
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


def _upsert_in_child(root: str, event_id: str) -> None:
    EvidenceArchive(root, now=lambda: NOW).upsert(snapshot(event(event_id)))


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
    assert rows[0]["snapshot_history"] == [
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


def test_archive_merges_evidence_by_id_or_url_and_key_fields_by_name_and_status(tmp_path):
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
    assert [(field["field_name"], field["verification_status"]) for field in row["key_fields"]] == [
        ("money", "verified"),
        ("money", "conflicting"),
    ]


def test_archive_query_accepts_exact_windows_and_known_statuses_and_sorts_descending(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    for offset, status in ((0, VerificationStatus.VERIFIED), (2, VerificationStatus.DISPROVED), (89, VerificationStatus.CONFLICTING)):
        selected = replace(
            event(f"{offset + 1:020x}", status=status, published_at=NOW - timedelta(days=offset)),
            verified_at=NOW - timedelta(days=offset),
            evidence_as_of=NOW - timedelta(days=offset),
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


def test_archive_skips_corrupt_oversized_and_unknown_rows_with_bounded_diagnostics(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    bucket = tmp_path / "archive" / "2026-08-20.jsonl"
    with bucket.open("ab") as handle:
        handle.write(b'{"schema_version":999,"event_id":"unknown"}\n')
        handle.write(b'{"junk":"' + (b"x" * 1_050_000) + b'"}\n')

    rows = archive.query(days=90)

    assert [row["event_id"] for row in rows] == ["a" * 20]
    assert archive.last_diagnostics["skipped_corrupt_rows"] == 2
    assert 0 <= archive.last_diagnostics["scanned_files"] <= 90
    assert set(archive.last_diagnostics) == {
        "scanned_files", "skipped_files", "scanned_rows", "skipped_corrupt_rows", "duplicate_rows",
    }


def test_archive_query_stops_before_reading_past_the_global_scan_budget(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    monkeypatch.setattr(archive_module, "_MAX_SCAN_BYTES", 32)

    assert archive.query(days=90) == []
    assert archive.last_diagnostics["skipped_files"] == 1
    assert archive.last_diagnostics["scanned_rows"] == 0


def test_archive_get_count_and_ninety_day_boundary_are_truthful(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("1" * 20, published_at=NOW - timedelta(days=90))))
    archive.upsert(snapshot(
        replace(
            event("2" * 20, published_at=NOW - timedelta(days=90, seconds=1)),
            verified_at=NOW - timedelta(days=90, seconds=1),
            evidence_as_of=NOW - timedelta(days=90, seconds=1),
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
        archive.upsert(snapshot(event(status=VerificationStatus.DISPROVED), snapshot_id="f" * 20))

    assert bucket.read_bytes() == before


def test_archive_index_io_failure_leaves_merged_journal_queryable_and_retryable(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    updated = snapshot(
        event(status=VerificationStatus.DISPROVED),
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

    assert archive.journal_path.is_file()
    assert archive.get("a" * 20)["verification_status"] == "disproved"

    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)
    archive.upsert(updated)
    assert not archive.journal_path.exists()
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

    assert archive.count() == 0
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

    updated = snapshot(
        _timed_event("c" * 20, NOW, status=VerificationStatus.DISPROVED),
        snapshot_id="f" * 20,
        raw_snapshot_id="d" * 20,
    )
    monkeypatch.setattr(archive_module, "_replace_durable", fail_target)
    with pytest.raises(OSError, match="storage_error"):
        archive.upsert(updated)

    assert previous_bucket.read_bytes() == previous_bytes
    retained = archive.get("c" * 20)
    assert retained is not None
    assert retained["verification_status"] == "disproved"
    assert [row["to_status"] for row in retained["status_history"]] == ["verified", "disproved"]

    monkeypatch.setattr(archive_module, "_replace_durable", real_replace)
    archive.upsert(updated)
    assert not archive.journal_path.exists()
    assert archive.get("c" * 20)["snapshot_history"][-1]["evidence_snapshot_id"] == "f" * 20


def test_archive_query_merges_cross_bucket_remnants_before_status_filtering(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    previous_time = NOW - timedelta(days=1)
    archive.upsert(snapshot(
        _timed_event("d" * 20, previous_time),
        generated_at=previous_time,
    ))
    old_bucket = tmp_path / "archive" / "2026-08-19.jsonl"
    old_bytes = old_bucket.read_bytes()
    archive.upsert(snapshot(
        _timed_event("d" * 20, NOW, status=VerificationStatus.DISPROVED),
        snapshot_id="f" * 20,
        raw_snapshot_id="9" * 20,
    ))
    old_bucket.write_bytes(old_bytes)

    assert archive.query(days=90, status="verified") == []
    assert [row["verification_status"] for row in archive.query(days=90, status="disproved")] == ["disproved"]


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


def test_archive_index_supports_one_identity_per_source_day_for_ninety_days(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    expected = {
        f"{number:020d}": f"{(NOW - timedelta(days=number % 90)).date().isoformat()}.jsonl"
        for number in range(108 * 90)
    }

    archive._write_index(expected)

    assert archive._read_index() == expected


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
        }
        for number in range(256)
    ]
    latest = history[-1]
    row["snapshot_history"] = history
    row["evidence_snapshot_id"] = latest["evidence_snapshot_id"]
    row["raw_snapshot_id"] = latest["raw_snapshot_id"]
    row["snapshot_generated_at"] = latest["generated_at"]
    row["last_updated_at"] = latest["generated_at"]
    bucket.write_text(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    archive.upsert(snapshot(
        _timed_event("e" * 20, NOW, status=VerificationStatus.CORRECTED),
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


def test_archive_skips_future_row_before_it_can_suppress_current_status(tmp_path):
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

    rows = archive.query(days=90)

    assert [row["verification_status"] for row in rows] == ["verified"]
    assert archive.last_diagnostics["skipped_corrupt_rows"] == 1


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

    assert archive.query(days=90) == []
    assert archive.last_diagnostics["skipped_files"] == 1


def test_archive_read_only_queries_do_not_create_storage_or_lock_files(tmp_path):
    root = tmp_path / "missing-root"
    archive = EvidenceArchive(root, now=lambda: NOW)

    assert archive.query(days=90) == []
    assert archive.get("missing") is None
    assert archive.count() == 0
    assert not root.exists()
