from __future__ import annotations

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


client = TestClient(app_module.app)
NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)


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
