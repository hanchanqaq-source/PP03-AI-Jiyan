from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evidence_verification.archive import EvidenceArchive
from evidence_verification.models import (
    EvidenceEvent,
    EvidenceItem,
    EvidenceSnapshot,
    SourceRole,
    StatusTransition,
    VerificationStatus,
)
from evidence_verification.storage import event_document, snapshot_document


NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)


def evidence_event(
    event_id: str = "a" * 20,
    *,
    status: VerificationStatus = VerificationStatus.VERIFIED,
    published_at: datetime | None = None,
    link: str | None = None,
) -> EvidenceEvent:
    observed = published_at or (NOW - timedelta(days=1))
    reason = f"reason-{status.value}"
    evidence = EvidenceItem(
        evidence_id=f"evidence-{event_id}",
        content_source="official.example.com",
        collector_source="collector.example",
        canonical_url=link or f"https://official.example.com/{event_id}",
        published_at=observed,
        source_role=SourceRole.PRIMARY,
        origin_cluster="publisher:official.example",
        supports_claim=True,
        is_official=True,
        title="正式公告",
        excerpt="公开证据摘要",
    )
    return EvidenceEvent(
        event_id=event_id,
        title=f"公开事件 {event_id[:4]}",
        summary="公开摘要",
        category="company",
        related_tags=(("semiconductor", "半导体"),),
        published_at=observed,
        core_claim="公开核心事实",
        verification_status=status,
        verification_reason=reason,
        verified_at=observed,
        evidence_as_of=observed,
        primary_evidence=(evidence,),
        status_history=(StatusTransition(None, status, observed, reason),),
    )


def evidence_snapshot(
    selected: EvidenceEvent,
    *,
    snapshot_id: str = "s" * 20,
    raw_snapshot_id: str = "r" * 20,
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=snapshot_id,
        raw_snapshot_id=raw_snapshot_id,
        generated_at=selected.verified_at,
        events=(selected,),
    )


def write_json(path: Path, document: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_recovery_reads_only_explicit_allowlisted_evidence_files(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    radar = write_json(root / "radar.json", {"industries": []})
    current = write_json(root / "current.json", snapshot_document(evidence_snapshot(evidence_event())))
    history = root / "history" / "2026-08-20.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(json.dumps({
        "event_id": "a" * 20,
        "from_status": None,
        "to_status": "verified",
        "changed_at": (NOW - timedelta(days=1)).isoformat(),
        "reason": "reason-verified",
    }) + "\n", encoding="utf-8")
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-old.json",
        snapshot_document(evidence_snapshot(evidence_event("b" * 20), snapshot_id="t" * 20)),
    )
    portfolio = write_json(root / "fund-portfolio.json", {"holdings": [{"cost": "private"}]})
    credentials = write_json(root / "credentials.json", {"api_key": "private"})

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        evidence_current=current,
        evidence_history=(history,),
        legacy_snapshots=(legacy,),
        now=lambda: NOW,
    ).scan()

    assert set(report.opened_paths) == {radar, current, history, legacy}
    assert portfolio not in report.opened_paths
    assert credentials not in report.opened_paths
    assert "private" not in json.dumps(report.to_dict(), ensure_ascii=False)


def test_recovery_counts_cache_refetch_and_unrecoverable_separately(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    cached = evidence_snapshot(evidence_event("a" * 20), snapshot_id="s" * 20)
    current = write_json(root / "current.json", snapshot_document(cached))
    radar = write_json(root / "radar.json", {
        "industries": [{
            "key": "semi",
            "items": [{
                "event_id": "b" * 20,
                "title": "待恢复公开事件",
                "original_url": "https://publisher.example.com/story",
                "published_at": (NOW - timedelta(days=2)).isoformat(),
            }],
        }],
    })
    history = root / "history" / "2026-08-19.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(json.dumps({
        "event_id": "c" * 20,
        "from_status": "verified",
        "to_status": "disproved",
        "changed_at": (NOW - timedelta(days=2)).isoformat(),
        "reason": "缺少完整事件字段",
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        evidence_current=current,
        evidence_history=(history,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    document = report.to_dict()
    assert document.keys() >= {"cache_recovered", "public_refetched", "unrecoverable", "reasons"}
    assert document["cache_recovered"] == 1
    assert document["public_refetched"] == 0
    assert document["unrecoverable"] == 2
    assert sum(document["reasons"].values()) == 2
    assert [row["event_id"] for row in archive.query(days=90)] == ["a" * 20]


def test_recovery_public_refetch_uses_only_existing_public_link_and_stays_separate(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    link = "https://publisher.example.com/original-story"
    radar = write_json(root / "radar.json", {
        "industries": [{
            "items": [{
                "event_id": "b" * 20,
                "title": "公开事件",
                "original_url": link,
                "published_at": (NOW - timedelta(days=2)).isoformat(),
                "verification_status": "verified",
            }],
        }],
    })
    calls: list[str] = []
    refetched = evidence_snapshot(
        evidence_event("b" * 20, published_at=NOW - timedelta(days=2), link=link),
        snapshot_id="u" * 20,
        raw_snapshot_id="v" * 20,
    )

    def refetch(url: str):
        calls.append(url)
        return snapshot_document(refetched)

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        archive=archive,
        public_refetcher=refetch,
        now=lambda: NOW,
    ).import_records()

    assert calls == [link]
    assert report.cache_recovered == 0
    assert report.public_refetched == 1
    assert report.unrecoverable == 0
    assert archive.get("b" * 20)["primary_evidence"][0]["canonical_url"] == link


def test_recovery_never_refetches_private_or_credential_bearing_links(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    radar = write_json(root / "radar.json", {
        "industries": [{
            "items": [
                {
                    "event_id": "a" * 20,
                    "title": "内网地址",
                    "original_url": "http://127.0.0.1/private",
                    "published_at": NOW.isoformat(),
                    "verification_status": "verified",
                },
                {
                    "event_id": "b" * 20,
                    "title": "含敏感查询参数",
                    "original_url": "https://publisher.example.com/story?api_key=private",
                    "published_at": NOW.isoformat(),
                    "verification_status": "verified",
                },
            ],
        }],
    })
    calls: list[str] = []

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        public_refetcher=lambda url: calls.append(url),
        now=lambda: NOW,
    ).import_records()

    assert calls == []
    assert report.unrecoverable == 2
    assert report.reasons == {"unsafe_public_link": 2}
    assert "private" not in json.dumps(report.to_dict(), ensure_ascii=False)


def test_recovery_rejects_non_allowlisted_named_paths_without_opening_them(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    portfolio = write_json(root / "fund-portfolio.json", {"holdings": [{"amount": "private"}]})
    credentials = write_json(root / "credentials.json", {"token": "private"})

    report = HistoryRecovery(
        root,
        radar_cache=portfolio,
        evidence_current=credentials,
        now=lambda: NOW,
    ).scan()

    assert report.opened_paths == ()
    assert report.unrecoverable == 2
    assert report.reasons == {"path_not_allowlisted": 2}


def test_recovery_rejects_a_parent_symlink_without_following_it(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    outside = tmp_path / "outside"
    radar = write_json(outside / "radar.json", {"industries": []})
    linked_parent = root / "linked"
    linked_parent.parent.mkdir(parents=True)
    try:
        linked_parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    report = HistoryRecovery(root, radar_cache=linked_parent / radar.name, now=lambda: NOW).scan()

    assert report.opened_paths == ()
    assert report.reasons == {"unsafe_file": 1}


def test_recovery_rejects_a_hardlinked_allowlisted_file(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    root.mkdir()
    outside = write_json(tmp_path / "outside-radar.json", {"industries": []})
    radar = root / "radar.json"
    os.link(outside, radar)
    original = outside.read_bytes()

    report = HistoryRecovery(root, radar_cache=radar, now=lambda: NOW).scan()

    assert report.opened_paths == ()
    assert report.reasons == {"unsafe_file": 1}
    assert outside.read_bytes() == original


def test_recovery_rejects_unbounded_integer_tokens_before_import(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    root.mkdir()
    radar = root / "radar.json"
    radar.write_text('{"industries":[],"sequence":' + ("9" * 80) + "}", encoding="utf-8")

    report = HistoryRecovery(root, radar_cache=radar, now=lambda: NOW).scan()

    assert report.opened_paths == (radar,)
    assert report.reasons == {"invalid_document": 1}


def test_recovery_normalizes_archive_failure_as_unrecoverable(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    class BrokenArchive:
        def upsert(self, selected):
            raise OSError("C:\\private\\credentials.json")

    root = tmp_path / "evidence"
    current = write_json(
        root / "current.json",
        snapshot_document(evidence_snapshot(evidence_event())),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=BrokenArchive(),
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"archive_rejected": 1}
    assert "private" not in json.dumps(report.to_dict(), ensure_ascii=False)
