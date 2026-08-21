from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app as app_module
from evidence_verification.archive import EvidenceArchive
from evidence_verification.models import (
    EvidenceEvent,
    EvidenceItem,
    EvidenceSnapshot,
    SourceRole,
    StatusTransition,
    VerificationStatus,
)
from evidence_verification.storage import snapshot_document


client = TestClient(app_module.app)
NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)


def _cursor_document(**overrides) -> str:
    document = {
        "v": 1,
        "days": 90,
        "verification_status": None,
        "limit": 2,
        "event_time": "2026-08-20T09:00:00+00:00",
        "verified_at": "2026-08-20T09:00:00+00:00",
        "event_id": "legacy event 2",
    }
    document.update(overrides)
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _duplicate_key_cursor() -> str:
    payload = base64.urlsafe_b64decode(_cursor_document() + "==").decode("utf-8")
    duplicated = payload.replace('"v":1,', '"v":1,"v":1,', 1).encode("utf-8")
    return base64.urlsafe_b64encode(duplicated).decode("ascii").rstrip("=")


def _api_row(event_id: str, *, minute: int, status: str = "verified", padding_lineages: int = 0):
    timestamp = (NOW - timedelta(minutes=minute)).isoformat()
    history = [{
        "evidence_snapshot_id": f"snapshot {event_id} {index}",
        "raw_snapshot_id": f"raw {event_id} {index}",
        "generated_at": timestamp,
        "content_digest": f"{index % 16:x}" * 64,
        "raw_input_digest": f"{(index + 1) % 16:x}" * 64,
    } for index in range(max(1, padding_lineages))]
    return {
        "event_id": event_id,
        "published_at": timestamp,
        "verified_at": timestamp,
        "verification_status": status,
        "evidence_snapshot_id": history[-1]["evidence_snapshot_id"],
        "raw_snapshot_id": history[-1]["raw_snapshot_id"],
        "snapshot_history": history,
    }


class _ArchiveRows:
    def __init__(self, rows):
        self.rows = rows
        self.last_diagnostics = {
            "scanned_files": 1,
            "skipped_files": 0,
            "scanned_rows": len(rows),
            "skipped_corrupt_rows": 0,
            "duplicate_rows": 0,
        }

    def query(self, days, status=None):
        del days, status
        return list(self.rows)


def event(
    event_id: str,
    *,
    days_old: float,
    status: VerificationStatus = VerificationStatus.VERIFIED,
    history: tuple[StatusTransition, ...] | None = None,
    excerpt: str = "公开证据摘要",
) -> EvidenceEvent:
    observed = NOW - timedelta(days=days_old)
    if history is None:
        reason = f"reason-{status.value}"
        history = (StatusTransition(None, status, observed, reason),)
    evidence = EvidenceItem(
        evidence_id=f"evidence-{event_id}",
        content_source="official.example.com",
        collector_source="collector.example",
        canonical_url=f"https://official.example.com/{event_id}",
        published_at=observed,
        source_role=SourceRole.PRIMARY,
        origin_cluster="publisher:official.example",
        supports_claim=True,
        is_official=True,
        title="正式公告",
        excerpt=excerpt,
    )
    return EvidenceEvent(
        event_id=event_id,
        title=f"事件 {event_id[:4]}",
        summary="公开摘要",
        category="company",
        related_tags=(("semiconductor", "半导体"),),
        published_at=observed,
        core_claim="公开核心事实",
        verification_status=status,
        verification_reason=history[-1].reason,
        verified_at=observed,
        evidence_as_of=observed,
        primary_evidence=(evidence,),
        status_history=history,
    )


def snapshot(selected: EvidenceEvent, sequence: int) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=f"{sequence:020x}",
        raw_snapshot_id=f"{sequence + 100:020x}",
        generated_at=selected.verified_at,
        events=(selected,),
    )


def archive_with_events(tmp_path, *events: EvidenceEvent) -> EvidenceArchive:
    archive = EvidenceArchive(tmp_path / "evidence-verification" / "v1", now=lambda: NOW)
    for sequence, selected in enumerate(events, start=1):
        archive.upsert(snapshot(selected, sequence))
    return archive


def test_archive_api_supports_all_five_exact_day_windows(tmp_path, monkeypatch):
    selected = (
        event("1" * 20, days_old=0.5),
        event("2" * 20, days_old=2),
        event("3" * 20, days_old=6),
        event("4" * 20, days_old=20),
        event("5" * 20, days_old=60),
    )
    archive = archive_with_events(tmp_path, *selected)
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: archive)

    expected = {
        1: ["1" * 20],
        3: ["1" * 20, "2" * 20],
        7: ["1" * 20, "2" * 20, "3" * 20],
        30: ["1" * 20, "2" * 20, "3" * 20, "4" * 20],
        90: [row.event_id for row in selected],
    }
    for days, event_ids in expected.items():
        response = client.get(f"/api/news/archive?days={days}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert [row["event_id"] for row in data["events"]] == event_ids
        assert data["total"] == len(event_ids)
        assert data["filters"] == {"days": days, "verification_status": None}
        assert set(data["diagnostics"]) == {
            "scanned_files", "skipped_files", "scanned_rows", "skipped_corrupt_rows", "duplicate_rows",
        }


def test_archive_api_filters_status_and_preserves_disproof_history_and_lineage(tmp_path, monkeypatch):
    first_time = NOW - timedelta(days=2, hours=1)
    second_time = NOW - timedelta(days=2)
    original = event(
        "d" * 20,
        days_old=2,
        status=VerificationStatus.VERIFIED,
        history=(StatusTransition(None, VerificationStatus.VERIFIED, first_time, "initial-verified"),),
    )
    disproved = event(
        "d" * 20,
        days_old=2,
        status=VerificationStatus.DISPROVED,
        history=(
            StatusTransition(None, VerificationStatus.VERIFIED, first_time, "initial-verified"),
            StatusTransition(VerificationStatus.VERIFIED, VerificationStatus.DISPROVED, second_time, "official-disproof"),
        ),
    )
    archive = archive_with_events(tmp_path, original)
    archive.upsert(EvidenceSnapshot(
        snapshot_id="f" * 20,
        raw_snapshot_id="e" * 20,
        generated_at=second_time,
        events=(disproved,),
    ))
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: archive)

    response = client.get("/api/news/archive?days=90&verification_status=disproved")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    row = data["events"][0]
    assert row["verification_status"] == "disproved"
    assert [item["to_status"] for item in row["status_history"]] == ["verified", "disproved"]
    assert len(row["snapshot_history"]) == 2
    assert data["provenance"] == [{
        "event_id": "d" * 20,
        "evidence_snapshot_id": row["evidence_snapshot_id"],
        "raw_snapshot_id": row["raw_snapshot_id"],
        "snapshot_history": row["snapshot_history"],
    }]


def test_archive_api_persists_and_exposes_only_closed_recovery_provenance(tmp_path, monkeypatch):
    recovered_event = event("c" * 20, days_old=2)
    snapshot_id = "s" * 20
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(EvidenceSnapshot(
        snapshot_id=snapshot_id,
        raw_snapshot_id="r" * 20,
        generated_at=recovered_event.verified_at,
        events=(recovered_event,),
        recovery_metadata={
            "source": "evidence_current",
            "recovered_at": NOW.isoformat(),
            "recovery_status": "cache_recovered",
            "source_snapshot_id": snapshot_id,
        },
    ))
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: archive)

    response = client.get("/api/news/archive?days=90")

    assert response.status_code == 200
    provenance = response.json()["data"]["provenance"]
    assert provenance[0]["recovery"] == [{
        "source": "evidence_current",
        "status": "cache_recovered",
        "recovered_at": NOW.isoformat(),
        "source_snapshot_id": snapshot_id,
    }]
    assert set(provenance[0]["recovery"][0]) == {
        "source", "status", "recovered_at", "source_snapshot_id",
    }


def test_archive_api_returns_excerpt_only_and_never_a_full_article_body(tmp_path, monkeypatch):
    archive = archive_with_events(tmp_path, event("a" * 20, days_old=1, excerpt="x" * 10_000))
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: archive)

    response = client.get("/api/news/archive?days=90")

    assert response.status_code == 200
    serialized = json.dumps(response.json(), ensure_ascii=False)
    assert "article_body" not in serialized
    assert "x" * 5_000 not in serialized
    assert len(response.json()["data"]["events"][0]["primary_evidence"][0]["excerpt"]) <= 1_200


def test_archive_api_rejects_unknown_windows_and_statuses(tmp_path, monkeypatch):
    archive = archive_with_events(tmp_path, event("a" * 20, days_old=1))
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: archive)

    invalid_days = client.get("/api/news/archive?days=2")
    invalid_status = client.get("/api/news/archive?days=90&verification_status=trusted")

    assert invalid_days.status_code == 422
    assert invalid_status.status_code == 422


def test_archive_api_normalizes_storage_failures_without_leaking_details(monkeypatch):
    class BrokenArchive:
        last_diagnostics = {}

        def query(self, days, status=None):
            raise OSError("C:\\Users\\private\\token.txt?api_key=secret")

    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", BrokenArchive)

    response = client.get("/api/news/archive?days=90")

    assert response.status_code == 503
    assert response.json() == {"detail": "资讯历史暂时不可用"}
    assert "private" not in response.text
    assert "secret" not in response.text


def test_archive_api_empty_read_does_not_create_storage(tmp_path, monkeypatch):
    archive = EvidenceArchive(tmp_path / "missing-evidence-root", now=lambda: NOW)
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: archive)

    response = client.get("/api/news/archive?days=90")

    assert response.status_code == 200
    assert response.json()["data"]["events"] == []
    assert response.json()["data"]["total"] == 0
    assert not archive.root.exists()


def test_archive_api_exposes_every_recovered_lineage_and_merged_cache_source(tmp_path, monkeypatch):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    selected = event("a" * 20, days_old=2)
    current_snapshot = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=selected.verified_at,
        events=(selected,),
    )
    legacy_snapshot = EvidenceSnapshot(
        snapshot_id="t" * 20,
        raw_snapshot_id="q" * 20,
        generated_at=selected.verified_at,
        events=(selected,),
    )
    current = root / "current.json"
    current.parent.mkdir(parents=True)
    current.write_text(json.dumps(snapshot_document(current_snapshot)), encoding="utf-8")
    legacy = root / "legacy-snapshots" / "evidence-copy.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(snapshot_document(legacy_snapshot)), encoding="utf-8")

    first = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()
    radar = root / "radar.json"
    radar.write_text(json.dumps({
        "industries": [{"items": [{"evidence_snapshot": snapshot_document(current_snapshot)}]}],
    }), encoding="utf-8")
    second = HistoryRecovery(
        root,
        radar_cache=radar,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: archive)

    response = client.get("/api/news/archive?days=90")

    assert first.to_dict() == {
        "cache_recovered": 1,
        "public_refetched": 0,
        "unrecoverable": 0,
        "reasons": {},
    }
    assert second.to_dict() == first.to_dict()
    assert response.status_code == 200
    provenance = response.json()["data"]["provenance"][0]
    assert len(provenance["snapshot_history"]) == 2
    assert {row["source"] for row in provenance["recovery"]} == {
        "evidence_current+radar_cache",
        "legacy_snapshot",
    }


def test_archive_api_keeps_the_legacy_unpaged_response_shape_exact(monkeypatch):
    rows = [_api_row("legacy event 1", minute=1)]
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: _ArchiveRows(rows))

    response = client.get("/api/news/archive?days=90")

    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"events", "total", "filters", "diagnostics", "provenance"}
    assert data["events"] == rows
    assert data["total"] == 1


def test_archive_api_paginates_more_than_one_bucket_limit_without_loss_or_duplicates(monkeypatch):
    rows = [_api_row(f"legacy event {index:05d}", minute=index) for index in range(4_097)]
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: _ArchiveRows(rows))

    cursor = None
    received = []
    while True:
        suffix = "" if cursor is None else f"&cursor={cursor}"
        response = client.get(f"/api/news/archive?days=90&limit=100{suffix}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 4_097
        assert data["filters"] == {"days": 90, "verification_status": None}
        assert len(data["provenance"]) == len(data["events"])
        assert data["page"]["returned"] == len(data["events"])
        received.extend(row["event_id"] for row in data["events"])
        cursor = data["page"]["next_cursor"]
        assert data["page"]["has_more"] is (cursor is not None)
        if cursor is None:
            break

    assert received == [row["event_id"] for row in rows]
    assert len(received) == len(set(received)) == 4_097


def test_archive_cursor_uses_python_codepoint_keyset_order_for_opaque_ids(monkeypatch):
    timestamp = NOW.isoformat()
    rows = [
        _api_row("Z opaque", minute=0),
        _api_row("a opaque", minute=0),
        _api_row("_ opaque", minute=0),
        _api_row("- opaque", minute=0),
    ]
    for row in rows:
        row["published_at"] = timestamp
        row["verified_at"] = timestamp
    rows.sort(key=lambda row: (timestamp, timestamp, row["event_id"]), reverse=True)
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: _ArchiveRows(rows))

    first = client.get("/api/news/archive?days=90&limit=2")
    assert first.status_code == 200
    first_data = first.json()["data"]
    second = client.get(
        "/api/news/archive",
        params={"days": 90, "limit": 2, "cursor": first_data["page"]["next_cursor"]},
    )

    assert second.status_code == 200
    combined = first_data["events"] + second.json()["data"]["events"]
    assert [row["event_id"] for row in combined] == [row["event_id"] for row in rows]


def test_archive_cursor_is_bound_to_days_status_and_limit(monkeypatch):
    rows = [_api_row(f"legacy event {index}", minute=index) for index in range(3)]
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: _ArchiveRows(rows))
    first = client.get("/api/news/archive?days=90&verification_status=verified&limit=2")
    assert first.status_code == 200
    cursor = first.json()["data"]["page"]["next_cursor"]

    assert client.get("/api/news/archive", params={"days": 30, "verification_status": "verified", "limit": 2, "cursor": cursor}).status_code == 422
    assert client.get("/api/news/archive", params={"days": 90, "verification_status": "disproved", "limit": 2, "cursor": cursor}).status_code == 422
    assert client.get("/api/news/archive", params={"days": 90, "verification_status": "verified", "limit": 3, "cursor": cursor}).status_code == 422


def test_archive_cursor_rejects_missing_limit_unknown_keys_noncanonical_base64_and_non_utc(monkeypatch):
    # Keep the valid cursor anchor present so a malformed parser cannot pass this
    # matrix merely because pagination later rejects an unknown keyset anchor.
    monkeypatch.setattr(
        "news_pipeline.api.EvidenceArchive",
        lambda: _ArchiveRows([_api_row("legacy event 2", minute=24 * 60)]),
    )
    cases = [
        ("/api/news/archive", {"days": 90, "cursor": _cursor_document()}),
        ("/api/news/archive", {"days": 90, "limit": 2, "cursor": _cursor_document(extra="unknown")}),
        ("/api/news/archive", {"days": 90, "limit": 2, "cursor": _cursor_document() + "="}),
        ("/api/news/archive", {"days": 90, "limit": 2, "cursor": _cursor_document(event_time="2026-08-20T17:00:00+08:00")}),
        ("/api/news/archive", {"days": 90, "limit": 2, "cursor": _duplicate_key_cursor()}),
        ("/api/news/archive", {"days": 90, "limit": 2, "cursor": "not!base64"}),
    ]

    for path, params in cases:
        response = client.get(path, params=params)
        assert response.status_code == 422
        assert "unknown" not in response.text


def test_archive_page_applies_three_mib_budget_before_adding_the_next_row(monkeypatch):
    # A long but row-bounded lineage makes event + repeated provenance material
    # large enough that two rows exceed the public page budget while either row fits.
    rows = [_api_row(f"large event {index}", minute=index, padding_lineages=3_500) for index in range(2)]
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: _ArchiveRows(rows))

    first = client.get("/api/news/archive?days=90&limit=100")

    assert first.status_code == 200
    data = first.json()["data"]
    assert len(first.content) <= 3 * 1_048_576
    assert data["page"]["returned"] == 1
    assert data["page"]["has_more"] is True
    assert data["page"]["next_cursor"]


def test_archive_page_rejects_a_single_projection_that_exceeds_the_budget_without_details(monkeypatch):
    row = _api_row("oversized internal row", minute=0)
    row["snapshot_history"] = [{"private": "x" * (3 * 1_048_576)}]
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: _ArchiveRows([row]))

    response = client.get("/api/news/archive?days=90&limit=1")

    assert response.status_code == 503
    assert response.json() == {"detail": "资讯历史暂时不可用"}
    assert "private" not in response.text


def test_archive_api_fails_closed_when_filtered_storage_returns_another_status(monkeypatch):
    rows = [_api_row("wrong filtered row", minute=1, status="corrected")]
    monkeypatch.setattr("news_pipeline.api.EvidenceArchive", lambda: _ArchiveRows(rows))

    response = client.get("/api/news/archive?days=90&verification_status=disproved&limit=100")

    assert response.status_code == 503
    assert response.json() == {"detail": "资讯历史暂时不可用"}
    assert "wrong filtered row" not in response.text
