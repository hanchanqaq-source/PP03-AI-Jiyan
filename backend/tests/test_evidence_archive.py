from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import time
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
        verification_reason=history[-1].reason,
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


def _hold_archive_lock(root: str, ready, seconds: float) -> None:
    archive = EvidenceArchive(root, now=lambda: NOW)
    with archive._process_lock():
        ready.set()
        time.sleep(seconds)


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
        {key: value for key, value in lineage.items() if key != "content_digest"}
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
    assert [(field["field_name"], field["verification_status"]) for field in row["key_fields"]] == [
        ("money", "verified"),
        ("money", "conflicting"),
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
        archive.upsert(snapshot(event(
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
        ), snapshot_id="f" * 20))

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
    archive.journal_path.write_bytes(b"x" * 65)
    monkeypatch.setattr(archive_module, "_MAX_JOURNAL_BYTES", 64)

    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)


def test_archive_future_journal_fails_closed(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event()))
    row = archive.get("a" * 20)
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


def test_archive_reads_authoritative_journal_first_with_shared_scan_budget_and_diagnostics(
    tmp_path,
    monkeypatch,
):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event(status=VerificationStatus.VERIFIED)))
    _verified, disproved = _same_time_competing_snapshots()
    journal_row = archive_module._archive_document(
        disproved,
        archive_module.event_document(disproved.events[0]),
        NOW,
    )
    payload = archive._journal_payload([journal_row])
    archive.journal_path.write_bytes(payload)
    bucket_size = (archive.archive_root / "2026-08-20.jsonl").stat().st_size
    monkeypatch.setattr(archive_module, "_MAX_SCAN_BYTES", len(payload) + bucket_size)

    rows = archive.query(days=90)

    assert [row["verification_status"] for row in rows] == ["disproved"]
    assert archive.last_diagnostics["scanned_files"] == 2
    assert archive.last_diagnostics["scanned_rows"] == 2
    assert archive.last_diagnostics["skipped_files"] == 0
    assert archive.last_diagnostics["duplicate_rows"] == 1


def test_archive_authoritative_journal_suppresses_stale_bucket_row_outside_query_window(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(_timed_event("a" * 20, NOW)))
    journal_snapshot = snapshot(
        _timed_event("a" * 20, NOW - timedelta(days=91)),
        snapshot_id="1" * 20,
        raw_snapshot_id="2" * 20,
    )
    journal_row = archive_module._archive_document(
        journal_snapshot,
        archive_module.event_document(journal_snapshot.events[0]),
        NOW,
    )
    archive.journal_path.write_bytes(archive._journal_payload([journal_row]))

    assert archive.query(days=90) == []
    assert archive.last_diagnostics["duplicate_rows"] == 1


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
    bucket = archive.archive_root / "2026-08-20.jsonl"
    second_snapshot = snapshot(
        event("b" * 20),
        snapshot_id="2" * 20,
        raw_snapshot_id="3" * 20,
    )
    journal_row = archive_module._archive_document(
        second_snapshot,
        archive_module.event_document(second_snapshot.events[0]),
        NOW,
    )
    journal = archive._journal_payload([journal_row])
    archive.journal_path.write_bytes(journal)
    required = len(journal) + len(bucket.read_bytes())

    monkeypatch.setattr(archive_module, "_MAX_SCAN_BYTES", required - 1)
    with pytest.raises(OSError, match="storage_corrupt"):
        archive.query(days=90)

    monkeypatch.setattr(archive_module, "_MAX_SCAN_BYTES", required)
    assert {row["event_id"] for row in archive.query(days=90)} == {"a" * 20, "b" * 20}


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
    archive.journal_path.write_bytes(
        b'{"schema_version":1,"rows":' + (b"[" * 20) + (b"]" * 20) + b"}\n"
    )
    parsed = False

    def forbidden_parse(_raw):
        nonlocal parsed
        parsed = True
        raise AssertionError("journal reached json.loads before structural preflight")

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
