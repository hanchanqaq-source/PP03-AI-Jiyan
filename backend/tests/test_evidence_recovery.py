from __future__ import annotations

import json
import os
from dataclasses import replace
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
        replace(
            evidence_event("b" * 20, published_at=NOW - timedelta(days=2), link=link),
            title="公开事件",
        ),
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


def test_recovery_rejects_a_whole_raw_snapshot_when_one_sibling_is_invalid(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    good = evidence_event("a" * 20)
    invalid = evidence_event("b" * 20)
    document = snapshot_document(EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(good, invalid),
    ))
    document["events"][1]["title"] = ""
    current = write_json(root / "current.json", document)

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == 2
    assert report.reasons == {
        "ambiguous_record": 1,
        "missing_required_fields": 1,
    }
    assert archive.query(90) == []


def test_recovery_does_not_refetch_an_event_already_restored_from_cache(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    event_id = "a" * 20
    link = f"https://official.example.com/{event_id}"
    cached_event = evidence_event(event_id, link=link)
    current = write_json(root / "current.json", snapshot_document(evidence_snapshot(cached_event)))
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": event_id,
            "title": cached_event.title,
            "original_url": link,
            "published_at": cached_event.published_at.isoformat(),
            "verification_status": cached_event.verification_status.value,
        }]}],
    })
    calls: list[str] = []

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        evidence_current=current,
        archive=archive,
        public_refetcher=lambda url: calls.append(url),
        now=lambda: NOW,
    ).import_records()

    assert calls == []
    assert report.cache_recovered == 1
    assert report.public_refetched == 0
    assert report.unrecoverable == 0


@pytest.mark.parametrize(
    ("changed_title", "changed_time", "changed_status"),
    [
        ("different title", False, VerificationStatus.DISPROVED),
        (None, True, VerificationStatus.DISPROVED),
        (None, False, VerificationStatus.VERIFIED),
    ],
)
def test_public_refetch_must_preserve_candidate_title_time_and_verification_truth(
    tmp_path,
    changed_title,
    changed_time,
    changed_status,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    event_id = "b" * 20
    link = "https://publisher.example.com/original"
    published = NOW - timedelta(days=2)
    candidate_title = "已有缓存标题"
    candidate_status = VerificationStatus.DISPROVED
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": event_id,
            "title": candidate_title,
            "original_url": link,
            "published_at": published.isoformat(),
            "verification_status": candidate_status.value,
        }]}],
    })
    returned_event = evidence_event(
        event_id,
        status=changed_status,
        published_at=published + (timedelta(seconds=1) if changed_time else timedelta()),
        link=link,
    )
    returned_event = replace(returned_event, title=changed_title or candidate_title)

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        archive=archive,
        public_refetcher=lambda _url: evidence_snapshot(returned_event),
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"public_refetch_failed": 1}
    assert archive.count() == 0


def test_public_refetch_normalizes_every_dependency_exception_to_a_closed_reason(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": "b" * 20,
            "title": "公开事件",
            "original_url": "https://publisher.example.com/original",
            "published_at": (NOW - timedelta(days=2)).isoformat(),
            "verification_status": "verified",
        }]}],
    })

    def broken(_url: str):
        raise KeyError("C:\\private\\credential?api_key=secret")

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        public_refetcher=broken,
        now=lambda: NOW,
    ).import_records()

    assert report.to_dict() == {
        "cache_recovered": 0,
        "public_refetched": 0,
        "unrecoverable": 1,
        "reasons": {"public_refetch_failed": 1},
    }
    assert "private" not in json.dumps(report.to_dict(), ensure_ascii=False)


def test_recovery_never_counts_events_outside_the_exact_ninety_day_window(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    cached = evidence_snapshot(evidence_event("a" * 20, published_at=NOW - timedelta(days=90, seconds=1)))
    current = write_json(root / "current.json", snapshot_document(cached))
    event_id = "b" * 20
    link = "https://publisher.example.com/old"
    old_time = NOW - timedelta(days=91)
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": event_id,
            "title": "过期事件",
            "original_url": link,
            "published_at": old_time.isoformat(),
            "verification_status": "verified",
        }]}],
    })
    returned = replace(evidence_event(event_id, published_at=old_time, link=link), title="过期事件")

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        evidence_current=current,
        archive=archive,
        public_refetcher=lambda _url: evidence_snapshot(returned),
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == 2
    assert report.reasons == {"outside_retention_window": 2}
    assert archive.query(90) == []
    assert archive.count() == 0


def test_duplicate_cache_and_refetch_candidates_have_one_terminal_event_outcome(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    event_id = "a" * 20
    link = f"https://official.example.com/{event_id}"
    old_time = NOW - timedelta(days=91)
    cached_event = evidence_event(event_id, published_at=old_time, link=link)
    current = write_json(root / "current.json", snapshot_document(evidence_snapshot(cached_event)))
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": event_id,
            "title": cached_event.title,
            "original_url": link,
            "published_at": old_time.isoformat(),
            "verification_status": cached_event.verification_status.value,
        }]}],
    })
    calls: list[str] = []

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        evidence_current=current,
        archive=archive,
        public_refetcher=lambda url: calls.append(url),
        now=lambda: NOW,
    ).import_records()

    assert calls == []
    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"outside_retention_window": 1}


def test_recovery_retry_is_idempotent_and_preserves_the_first_recovery_time(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    clock = [NOW]
    archive = EvidenceArchive(root, now=lambda: clock[0])
    selected = evidence_snapshot(evidence_event("a" * 20))
    current = write_json(root / "current.json", snapshot_document(selected))
    subject = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: clock[0],
    )

    first = subject.import_records()
    clock[0] += timedelta(hours=1)
    second = subject.import_records()

    assert first.cache_recovered == 1 and first.unrecoverable == 0
    assert second.cache_recovered == 1 and second.unrecoverable == 0
    assert archive.count() == 1
    recovery = archive.get("a" * 20)["snapshot_history"][0]["recovery"]
    assert recovery["recovered_at"] == NOW.isoformat()


def test_explicit_history_iterable_is_consumed_only_to_the_bounded_file_limit(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    consumed = 0

    def paths():
        nonlocal consumed
        for index in range(1_000):
            consumed += 1
            yield tmp_path / f"{(NOW.date() - timedelta(days=index)).isoformat()}.jsonl"

    subject = HistoryRecovery(tmp_path, evidence_history=paths(), now=lambda: NOW)
    report = subject.scan()

    assert consumed <= 129
    assert report.reasons.get("file_limit") == 1


def test_default_history_discovery_reports_entry_limit_instead_of_false_empty(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    history = tmp_path / "history"
    history.mkdir()
    for index in range(513):
        (history / f"junk-{index:03d}").write_text("x", encoding="utf-8")

    report = HistoryRecovery(tmp_path, now=lambda: NOW).scan()

    assert report.reasons == {"directory_entry_limit": 1}


def test_recovery_enforces_one_total_row_budget_across_history_files(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    history = tmp_path / "history"
    history.mkdir()
    paths = []
    for name in ("2026-08-19.jsonl", "2026-08-20.jsonl"):
        path = history / name
        path.write_text("{}\n" * 6_000, encoding="utf-8")
        paths.append(path)

    report = HistoryRecovery(
        tmp_path,
        evidence_history=paths,
        now=lambda: NOW,
    ).scan()

    assert report.unrecoverable == 10_001
    assert report.reasons == {
        "missing_required_fields": 10_000,
        "record_limit": 1,
    }


def test_invalid_embedded_radar_snapshots_cannot_bypass_the_total_row_budget(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    radar = write_json(root / "radar.json", {
        "industries": [{
            "items": [{"evidence_snapshot": {}} for _ in range(10_001)],
        }],
    })

    report = HistoryRecovery(root, radar_cache=radar, now=lambda: NOW).scan()

    assert report.unrecoverable == 10_001
    assert report.reasons == {
        "missing_required_fields": 10_000,
        "record_limit": 1,
    }


def test_recovery_archive_rejection_is_atomic_for_one_raw_sibling_group(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    good_id = "a" * 20
    bad_id = "b" * 20

    class SelectiveArchive:
        def __init__(self, root):
            self._archive = EvidenceArchive(root, now=lambda: NOW)
            self.rows: dict[str, dict] = {}

        def upsert(self, selected):
            assert len(selected.events) == 1
            event = selected.events[0]
            if event.event_id == bad_id:
                raise OSError("private archive failure")
            self._archive.upsert(selected)
            self.rows[event.event_id] = self._archive.get(event.event_id)

        def get(self, event_id):
            return self._archive.get(event_id)

    root = tmp_path / "evidence"
    selected = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(evidence_event(good_id), evidence_event(bad_id)),
    )
    current = write_json(root / "current.json", snapshot_document(selected))
    archive = SelectiveArchive(root)

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.unrecoverable == 2
    assert report.reasons == {"archive_rejected": 2}
    assert archive.rows == {}


def test_recovery_rejects_conflicting_cache_records_for_one_event_id_as_ambiguous(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    event_id = "a" * 20
    first = evidence_snapshot(evidence_event(event_id), snapshot_id="s" * 20)
    second_event = replace(evidence_event(event_id), title="冲突标题")
    second = evidence_snapshot(second_event, snapshot_id="t" * 20)
    current = write_json(root / "current.json", snapshot_document(first))
    legacy = write_json(root / "legacy-snapshots" / "evidence-old.json", snapshot_document(second))

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"ambiguous_record": 1}
    assert archive.count() == 0


def test_explicit_path_iterator_failure_is_reported_without_leaking_the_exception(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    def paths():
        yield tmp_path / "2026-08-20.jsonl"
        raise OSError("C:\\private\\credentials.json")

    subject = HistoryRecovery(tmp_path, evidence_history=paths(), now=lambda: NOW)
    report = subject.scan()

    assert report.reasons == {"path_iterator_failed": 1}
    assert "private" not in json.dumps(report.to_dict(), ensure_ascii=False)


def test_default_history_discovery_reports_scan_failure_instead_of_false_empty(tmp_path, monkeypatch):
    import evidence_verification.recovery as recovery_module
    from evidence_verification.recovery import HistoryRecovery

    history = tmp_path / "history"
    history.mkdir()
    original_scandir = recovery_module.os.scandir

    def broken_scandir(path):
        if Path(path) == history:
            raise OSError("C:\\private\\history")
        return original_scandir(path)

    monkeypatch.setattr(recovery_module.os, "scandir", broken_scandir)
    report = HistoryRecovery(tmp_path, now=lambda: NOW).scan()

    assert report.reasons == {"directory_scan_failed": 1}
    assert "private" not in json.dumps(report.to_dict(), ensure_ascii=False)


def test_recovery_enforces_one_total_node_budget_across_documents(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    oversized_nodes = {"events": [], "padding": ["x"] * 60_000}
    current = write_json(root / "current.json", oversized_nodes)
    legacy = write_json(root / "legacy-snapshots" / "evidence-old.json", oversized_nodes)

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        now=lambda: NOW,
    ).scan()

    assert report.reasons == {
        "missing_required_fields": 1,
        "node_limit": 1,
    }


def test_recovery_enforces_one_total_byte_budget_across_files(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    history = root / "history"
    history.mkdir(parents=True)
    payload = json.dumps({"padding": "x" * (3 * 1024 * 1024)}, separators=(",", ":")) + "\n"
    paths = []
    for day in range(6):
        path = history / f"{(NOW.date() - timedelta(days=day)).isoformat()}.jsonl"
        path.write_text(payload, encoding="utf-8")
        paths.append(path)

    report = HistoryRecovery(root, evidence_history=paths, now=lambda: NOW).scan()

    assert report.reasons == {
        "missing_required_fields": 5,
        "total_byte_limit": 1,
    }
    assert len(report.opened_paths) == 5


def test_default_history_discovery_reports_an_unsafe_directory(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    outside = tmp_path / "outside-history"
    outside.mkdir()
    root = tmp_path / "evidence"
    root.mkdir()
    try:
        (root / "history").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    report = HistoryRecovery(root, now=lambda: NOW).scan()

    assert report.reasons == {"unsafe_directory": 1}


def test_default_history_discovery_reports_directory_disappearance_after_chain_validation(
    tmp_path,
    monkeypatch,
):
    import evidence_verification.recovery as recovery_module
    from evidence_verification.recovery import HistoryRecovery

    history = tmp_path / "history"
    history.mkdir()
    original_scandir = recovery_module.os.scandir

    def disappearing_scandir(path):
        if Path(path) == history:
            raise FileNotFoundError("C:\\private\\history")
        return original_scandir(path)

    monkeypatch.setattr(recovery_module.os, "scandir", disappearing_scandir)

    report = HistoryRecovery(tmp_path, now=lambda: NOW).scan()

    assert report.reasons == {"directory_scan_failed": 1}
    assert report.unrecoverable == 1
    assert "private" not in json.dumps(report.to_dict(), ensure_ascii=False)


def test_discovered_history_entry_disappearance_is_not_reported_as_empty(
    tmp_path,
    monkeypatch,
):
    from evidence_verification.recovery import HistoryRecovery

    history = tmp_path / "history"
    candidate = history / "2026-08-20.jsonl"
    candidate.parent.mkdir()
    candidate.write_text("{}\n", encoding="utf-8")
    original_safe_read = HistoryRecovery._safe_read

    def disappearing_read(path, remaining):
        if Path(path) == candidate:
            candidate.unlink()
        return original_safe_read(path, remaining)

    monkeypatch.setattr(HistoryRecovery, "_safe_read", staticmethod(disappearing_read))

    report = HistoryRecovery(tmp_path, now=lambda: NOW).scan()

    assert report.opened_paths == ()
    assert report.reasons == {"history_entry_disappeared": 1}
    assert report.unrecoverable == 1


def test_recovery_rejects_the_raw_group_when_siblings_have_noncanonical_event_ids(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    invalid_ids = (
        "   ",
        "a" * 19,
        "A" * 20,
        ("a" * 19) + "\n",
        "g" * 20,
    )
    originals = tuple(evidence_event(f"{index:020x}") for index in range(len(invalid_ids) + 1))
    document = snapshot_document(EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=originals,
    ))
    for row, invalid_id in zip(document["events"][:len(invalid_ids)], invalid_ids, strict=True):
        row["event_id"] = invalid_id
    current = write_json(root / "current.json", document)

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == len(originals)
    assert report.reasons == {
        "ambiguous_record": 1,
        "missing_required_fields": len(invalid_ids),
    }
    assert archive.query(90) == []


def test_recovery_preserves_causal_verified_to_disproved_lineages(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    event_id = "a" * 20
    verified_at = NOW - timedelta(days=2, hours=1)
    disproved_at = NOW - timedelta(days=2)
    verified = evidence_event(event_id, published_at=verified_at)
    disproved = replace(
        verified,
        verification_status=VerificationStatus.DISPROVED,
        verification_reason="official-disproof",
        verified_at=disproved_at,
        evidence_as_of=disproved_at,
        status_history=(
            verified.status_history[0],
            StatusTransition(
                VerificationStatus.VERIFIED,
                VerificationStatus.DISPROVED,
                disproved_at,
                "official-disproof",
            ),
        ),
    )
    current = write_json(
        root / "current.json",
        snapshot_document(evidence_snapshot(verified, snapshot_id="s" * 20, raw_snapshot_id="r" * 20)),
    )
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-disproved.json",
        snapshot_document(evidence_snapshot(disproved, snapshot_id="t" * 20, raw_snapshot_id="q" * 20)),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.to_dict() == {
        "cache_recovered": 1,
        "public_refetched": 0,
        "unrecoverable": 0,
        "reasons": {},
    }
    archived = archive.get(event_id)
    assert archived["verification_status"] == "disproved"
    assert [row["to_status"] for row in archived["status_history"]] == ["verified", "disproved"]
    assert len(archived["snapshot_history"]) == 2


def test_recovery_preserves_same_truth_from_every_distinct_snapshot_lineage(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    selected = evidence_event("a" * 20)
    current_snapshot = evidence_snapshot(
        selected,
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
    )
    legacy_snapshot = EvidenceSnapshot(
        snapshot_id="t" * 20,
        raw_snapshot_id="q" * 20,
        generated_at=selected.verified_at + timedelta(minutes=1),
        events=(selected,),
    )
    current = write_json(root / "current.json", snapshot_document(current_snapshot))
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-copy.json",
        snapshot_document(legacy_snapshot),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 1
    assert report.unrecoverable == 0
    archived = archive.get(selected.event_id)
    assert {
        (row["evidence_snapshot_id"], row["raw_snapshot_id"])
        for row in archived["snapshot_history"]
    } == {
        (current_snapshot.snapshot_id, current_snapshot.raw_snapshot_id),
        (legacy_snapshot.snapshot_id, legacy_snapshot.raw_snapshot_id),
    }
    assert [row["recovery"]["source"] for row in archived["snapshot_history"]] == [
        "evidence_current",
        "legacy_snapshot",
    ]


def test_duplicate_snapshot_sources_merge_provenance_and_remain_retry_idempotent(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    selected = evidence_snapshot(evidence_event("a" * 20))
    current = write_json(root / "current.json", snapshot_document(selected))

    first = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{"evidence_snapshot": snapshot_document(selected)}]}],
    })
    second = HistoryRecovery(
        root,
        radar_cache=radar,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()
    current.unlink()
    third = HistoryRecovery(
        root,
        radar_cache=radar,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert first.cache_recovered == second.cache_recovered == third.cache_recovered == 1
    assert first.unrecoverable == second.unrecoverable == third.unrecoverable == 0
    archived = archive.get(selected.events[0].event_id)
    assert len(archived["snapshot_history"]) == 1
    assert archived["snapshot_history"][0]["recovery"]["source"] == "evidence_current+radar_cache"


def test_recovery_rejects_archive_ack_without_the_expected_lineage(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    class EventOnlyArchive:
        def __init__(self):
            self.event_id = None

        def upsert(self, selected):
            self.event_id = selected.events[0].event_id

        def get(self, event_id):
            return {"event_id": event_id} if event_id == self.event_id else None

    root = tmp_path / "evidence"
    current = write_json(
        root / "current.json",
        snapshot_document(evidence_snapshot(evidence_event())),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=EventOnlyArchive(),
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"archive_rejected": 1}


def test_recovery_uses_one_clock_sample_at_the_exact_retention_boundary(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    calls = 0

    def advancing_clock():
        nonlocal calls
        calls += 1
        return NOW if calls == 1 else NOW + timedelta(microseconds=calls - 1)

    root = tmp_path / "evidence"
    EvidenceArchive(root, now=lambda: NOW).upsert(evidence_snapshot(
        evidence_event("b" * 20),
        snapshot_id="t" * 20,
        raw_snapshot_id="q" * 20,
    ))
    archive = EvidenceArchive(root, now=advancing_clock)
    selected = evidence_snapshot(evidence_event(
        "a" * 20,
        published_at=NOW - timedelta(days=90),
    ))
    current = write_json(root / "current.json", snapshot_document(selected))

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=advancing_clock,
    ).import_records()

    assert calls == 1
    assert report.cache_recovered == 1
    assert report.unrecoverable == 0
    stable = EvidenceArchive(root, now=lambda: NOW).get(selected.events[0].event_id)
    assert stable is not None
    assert stable["snapshot_history"][0]["recovery"]["status"] == "cache_recovered"


def test_public_refetch_materializes_forged_evidence_iterables_with_a_hard_bound(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    class ManyEvidence:
        def __init__(self, item):
            self.item = item
            self.consumed = 0

        def __iter__(self):
            for _ in range(60_001):
                self.consumed += 1
                yield self.item

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    event_id = "b" * 20
    link = "https://publisher.example.com/bounded"
    published = NOW - timedelta(days=2)
    base = replace(
        evidence_event(event_id, published_at=published, link=link),
        title="公开事件",
    )
    many = ManyEvidence(base.primary_evidence[0])
    forged = replace(base, primary_evidence=many)
    returned = evidence_snapshot(forged)
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": event_id,
            "title": "公开事件",
            "original_url": link,
            "published_at": published.isoformat(),
            "verification_status": "verified",
        }]}],
    })

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        archive=archive,
        public_refetcher=lambda _url: returned,
        now=lambda: NOW,
    ).import_records()

    assert many.consumed <= 513
    assert report.public_refetched == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"public_refetch_failed": 1}
    assert archive.count() == 0


def test_recovery_preserves_causal_corroborated_to_conflicting_lineages(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    event_id = "c" * 20
    corroborated_at = NOW - timedelta(days=3, hours=1)
    conflicting_at = NOW - timedelta(days=3)
    corroborated = evidence_event(
        event_id,
        status=VerificationStatus.CORROBORATED,
        published_at=corroborated_at,
    )
    conflicting = replace(
        corroborated,
        verification_status=VerificationStatus.CONFLICTING,
        verification_reason="independent-conflict",
        verified_at=conflicting_at,
        evidence_as_of=conflicting_at,
        status_history=(
            corroborated.status_history[0],
            StatusTransition(
                VerificationStatus.CORROBORATED,
                VerificationStatus.CONFLICTING,
                conflicting_at,
                "independent-conflict",
            ),
        ),
    )
    current = write_json(
        root / "current.json",
        snapshot_document(evidence_snapshot(
            corroborated,
            snapshot_id="s" * 20,
            raw_snapshot_id="r" * 20,
        )),
    )
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-conflict.json",
        snapshot_document(evidence_snapshot(
            conflicting,
            snapshot_id="t" * 20,
            raw_snapshot_id="q" * 20,
        )),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 1
    assert report.unrecoverable == 0
    archived = archive.get(event_id)
    assert archived["verification_status"] == "conflicting"
    assert [row["to_status"] for row in archived["status_history"]] == [
        "corroborated",
        "conflicting",
    ]
    assert len(archived["snapshot_history"]) == 2


def test_recovery_rejects_same_lineage_content_conflict_as_one_terminal_event(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    selected = evidence_event("a" * 20)
    first = evidence_snapshot(selected, snapshot_id="s" * 20, raw_snapshot_id="r" * 20)
    second = evidence_snapshot(
        replace(selected, title="冲突标题"),
        snapshot_id=first.snapshot_id,
        raw_snapshot_id=first.raw_snapshot_id,
    )
    current = write_json(root / "current.json", snapshot_document(first))
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-conflict.json",
        snapshot_document(second),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"ambiguous_record": 1}
    assert archive.count() == 0


def test_public_refetch_bounds_forged_snapshot_events_iterable(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    class ManyEvents:
        def __init__(self, selected):
            self.selected = selected
            self.consumed = 0

        def __iter__(self):
            for _ in range(60_001):
                self.consumed += 1
                yield self.selected

    root = tmp_path / "evidence"
    event_id = "b" * 20
    link = "https://publisher.example.com/events-bound"
    published = NOW - timedelta(days=2)
    selected = replace(
        evidence_event(event_id, published_at=published, link=link),
        title="公开事件",
    )
    many = ManyEvents(selected)
    returned = replace(evidence_snapshot(selected), events=many)
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": event_id,
            "title": selected.title,
            "original_url": link,
            "published_at": published.isoformat(),
            "verification_status": selected.verification_status.value,
        }]}],
    })

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        public_refetcher=lambda _url: returned,
        now=lambda: NOW,
    ).import_records()

    assert many.consumed <= 2
    assert report.to_dict() == {
        "cache_recovered": 0,
        "public_refetched": 0,
        "unrecoverable": 1,
        "reasons": {"public_refetch_failed": 1},
    }


def test_public_refetch_closes_an_events_generator_exception_without_leakage(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    event_id = "b" * 20
    link = "https://publisher.example.com/events-error"
    published = NOW - timedelta(days=2)
    selected = replace(
        evidence_event(event_id, published_at=published, link=link),
        title="公开事件",
    )
    document = snapshot_document(evidence_snapshot(selected))
    event_row = document["events"][0]

    def events():
        yield event_row
        raise OSError("C:\\private\\credential?api_key=secret")

    document["events"] = events()
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": event_id,
            "title": selected.title,
            "original_url": link,
            "published_at": published.isoformat(),
            "verification_status": selected.verification_status.value,
        }]}],
    })

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        public_refetcher=lambda _url: document,
        now=lambda: NOW,
    ).import_records()

    assert report.to_dict() == {
        "cache_recovered": 0,
        "public_refetched": 0,
        "unrecoverable": 1,
        "reasons": {"public_refetch_failed": 1},
    }
    assert "private" not in json.dumps(report.to_dict(), ensure_ascii=False)


def _verified_disproved_verified_cycle(event_id: str = "a" * 20):
    first_at = NOW - timedelta(days=3, hours=2)
    second_at = NOW - timedelta(days=3, hours=1)
    third_at = NOW - timedelta(days=3)
    first_transition = StatusTransition(
        None, VerificationStatus.VERIFIED, first_at, "official-support-1",
    )
    second_transition = StatusTransition(
        VerificationStatus.VERIFIED,
        VerificationStatus.DISPROVED,
        second_at,
        "official-disproof",
    )
    third_transition = StatusTransition(
        VerificationStatus.DISPROVED,
        VerificationStatus.VERIFIED,
        third_at,
        "official-support-2",
    )
    evidence = EvidenceItem(
        evidence_id=f"evidence-{event_id}",
        content_source="official.example.com",
        collector_source="collector.example",
        canonical_url=f"https://official.example.com/{event_id}",
        published_at=first_at,
        source_role=SourceRole.PRIMARY,
        origin_cluster="publisher:official.example",
        supports_claim=True,
        is_official=True,
        title="正式公告",
        excerpt="公开证据摘要",
    )
    first = replace(
        evidence_event(event_id, published_at=first_at),
        verification_reason="official-support-1",
        verified_at=first_at,
        evidence_as_of=first_at,
        primary_evidence=(evidence,),
        status_history=(first_transition,),
    )
    second = replace(
        first,
        verification_status=VerificationStatus.DISPROVED,
        verification_reason="official-disproof",
        verified_at=second_at,
        evidence_as_of=second_at,
        status_history=(first_transition, second_transition),
    )
    third = replace(
        second,
        verification_status=VerificationStatus.VERIFIED,
        verification_reason="official-support-2",
        verified_at=third_at,
        evidence_as_of=third_at,
        status_history=(first_transition, second_transition, third_transition),
    )
    return (
        EvidenceSnapshot("1" * 20, first_at, (first,), "2" * 20),
        EvidenceSnapshot("3" * 20, second_at, (second,), "4" * 20),
        EvidenceSnapshot("5" * 20, third_at, (third,), "6" * 20),
    )


def test_recovery_acknowledges_an_older_lineage_without_downgrading_newer_current_truth(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    verified, disproved, _restored = _verified_disproved_verified_cycle()
    archive.upsert(disproved)
    current = write_json(root / "current.json", snapshot_document(verified))

    first = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()
    second = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert first.cache_recovered == second.cache_recovered == 1
    assert first.unrecoverable == second.unrecoverable == 0
    archived = archive.get("a" * 20)
    assert archived["verification_status"] == "disproved"
    assert {
        row["evidence_snapshot_id"] for row in archived["snapshot_history"]
    } == {verified.snapshot_id, disproved.snapshot_id}


def test_recovery_accepts_a_strict_causal_status_cycle(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    selected = _verified_disproved_verified_cycle()
    current = write_json(root / "current.json", snapshot_document(selected[0]))
    # The intermediate disproved state is preserved inside the final strict
    # history prefix even when no standalone disproved snapshot survived.
    legacy = (
        write_json(
            root / "legacy-snapshots" / "evidence-restored.json",
            snapshot_document(selected[2]),
        ),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=legacy,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.to_dict() == {
        "cache_recovered": 1,
        "public_refetched": 0,
        "unrecoverable": 0,
        "reasons": {},
    }
    archived = archive.get("a" * 20)
    assert archived["verification_status"] == "verified"
    assert [row["to_status"] for row in archived["status_history"]] == [
        "verified", "disproved", "verified",
    ]
    assert len(archived["snapshot_history"]) == 2


def test_recovery_rejects_one_raw_identity_bound_to_different_evidence_snapshots(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    verified, disproved, _restored = _verified_disproved_verified_cycle()
    conflicting = replace(disproved, raw_snapshot_id=verified.raw_snapshot_id)
    current = write_json(root / "current.json", snapshot_document(verified))
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-conflict.json",
        snapshot_document(conflicting),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"ambiguous_record": 1}
    assert archive.count() == 0


def test_recovery_rejects_one_raw_identity_bound_to_different_event_sets(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    first = evidence_event("a" * 20)
    second = evidence_event("b" * 20)
    complete = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(first, second),
    )
    incomplete = replace(complete, events=(first,))
    current = write_json(root / "current.json", snapshot_document(complete))
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-incomplete.json",
        snapshot_document(incomplete),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == 2
    assert report.reasons == {"ambiguous_record": 2}
    assert archive.count() == 0


def test_recovery_rejects_a_raw_identity_conflicting_with_existing_archive_lineage(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    verified, disproved, _restored = _verified_disproved_verified_cycle()
    archive.upsert(verified)
    conflicting = replace(disproved, raw_snapshot_id=verified.raw_snapshot_id)
    current = write_json(root / "current.json", snapshot_document(conflicting))

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"ambiguous_record": 1}
    archived = archive.get("a" * 20)
    assert archived["verification_status"] == "verified"
    assert len(archived["snapshot_history"]) == 1


def test_recovery_rejects_a_raw_event_set_conflicting_with_existing_archive(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    first = evidence_event("a" * 20)
    second = evidence_event("b" * 20)
    complete = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(first, second),
    )
    archive.upsert(complete)
    incomplete = replace(complete, events=(first,))
    current = write_json(root / "current.json", snapshot_document(incomplete))

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"ambiguous_record": 1}
    assert {row["event_id"] for row in archive.query(90)} == {"a" * 20, "b" * 20}


@pytest.mark.parametrize(
    "field,value",
    (
        ("snapshot_id", "../evidence"),
        ("raw_snapshot_id", "raw?api_key=secret"),
        ("snapshot_id", "snapshot\ncontrol"),
        ("raw_snapshot_id", "r" * 129),
        ("source_snapshot_id", "../source"),
    ),
)
def test_recovery_reuses_pipeline_opaque_id_validation_without_echoing_invalid_ids(
    tmp_path,
    field,
    value,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    document = snapshot_document(evidence_snapshot(evidence_event()))
    if field == "source_snapshot_id":
        document["recovery_metadata"] = {field: value}
    else:
        document[field] = value
    current = write_json(root / "current.json", document)

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    serialized = json.dumps(report.to_dict(), ensure_ascii=False)
    assert report.cache_recovered == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"missing_required_fields": 1}
    assert value not in serialized
    assert archive.count() == 0


def test_public_refetch_rejects_an_invalid_source_snapshot_id_before_serialization(
    tmp_path,
    monkeypatch,
):
    from evidence_verification.recovery import HistoryRecovery
    import evidence_verification.recovery as recovery_module

    root = tmp_path / "evidence"
    link = "https://publisher.example.com/source-id"
    published = NOW - timedelta(days=2)
    event_id = "b" * 20
    selected = evidence_event(event_id, published_at=published, link=link)
    returned = replace(
        evidence_snapshot(selected),
        recovery_metadata={"source_snapshot_id": "../source"},
    )
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": event_id,
            "title": selected.title,
            "original_url": link,
            "published_at": published.isoformat(),
            "verification_status": "verified",
        }]}],
    })
    calls = 0
    real_snapshot_document = recovery_module.snapshot_document

    def observe_snapshot_document(snapshot):
        nonlocal calls
        calls += 1
        return real_snapshot_document(snapshot)

    monkeypatch.setattr(recovery_module, "snapshot_document", observe_snapshot_document)

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        public_refetcher=lambda _url: returned,
        now=lambda: NOW,
    ).import_records()

    assert calls == 0
    assert report.public_refetched == 0
    assert report.reasons == {"public_refetch_failed": 1}


def test_recovery_keeps_pipeline_compatible_legacy_opaque_ids(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    selected = evidence_snapshot(
        evidence_event(),
        snapshot_id="legacy.snapshot-1",
        raw_snapshot_id="raw.snapshot-1",
    )
    current = write_json(root / "current.json", snapshot_document(selected))

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 1
    assert report.unrecoverable == 0
    assert archive.get("a" * 20)["evidence_snapshot_id"] == "legacy.snapshot-1"


def test_public_refetch_rejects_oversized_dataclass_text_before_trusted_text_regex(
    tmp_path,
    monkeypatch,
):
    from evidence_verification.recovery import HistoryRecovery
    import evidence_verification.storage as storage_module

    root = tmp_path / "evidence"
    link = "https://publisher.example.com/preflight"
    published = NOW - timedelta(days=2)
    event_id = "b" * 20
    selected = replace(
        evidence_event(event_id, published_at=published, link=link),
        title="x" * 8_193,
    )
    returned = evidence_snapshot(selected)
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": event_id,
            "title": selected.title,
            "original_url": link,
            "published_at": published.isoformat(),
            "verification_status": "verified",
        }]}],
    })
    calls = 0
    real_trusted_text = storage_module.trusted_event_text

    def observe_trusted_text(event, value):
        nonlocal calls
        calls += 1
        return real_trusted_text(event, value)

    monkeypatch.setattr(storage_module, "trusted_event_text", observe_trusted_text)

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        public_refetcher=lambda _url: returned,
        now=lambda: NOW,
    ).import_records()

    assert calls == 0
    assert report.public_refetched == 0
    assert report.reasons == {"public_refetch_failed": 1}


@pytest.mark.parametrize("kind", ("history", "legacy"))
def test_explicit_supplied_recovery_file_disappearance_is_closed(tmp_path, kind):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    if kind == "history":
        path = root / "history" / "2026-08-20.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("{}\n", encoding="utf-8")
        recovery = HistoryRecovery(root, evidence_history=(path,), now=lambda: NOW)
        expected = "history_entry_disappeared"
    else:
        path = root / "legacy-snapshots" / "evidence-old.json"
        write_json(path, snapshot_document(evidence_snapshot(evidence_event())))
        recovery = HistoryRecovery(root, legacy_snapshots=(path,), now=lambda: NOW)
        expected = "legacy_entry_disappeared"
    path.unlink()

    report = recovery.scan()

    assert report.opened_paths == ()
    assert report.reasons == {expected: 1}
    assert report.unrecoverable == 1


@pytest.mark.parametrize("kind", ("radar", "current"))
def test_explicit_primary_recovery_file_disappearance_is_closed(tmp_path, kind):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    if kind == "radar":
        path = write_json(root / "radar.json", {"industries": []})
        recovery = HistoryRecovery(root, radar_cache=path, now=lambda: NOW)
    else:
        path = write_json(
            root / "current.json",
            snapshot_document(evidence_snapshot(evidence_event())),
        )
        recovery = HistoryRecovery(root, evidence_current=path, now=lambda: NOW)
    path.unlink()

    report = recovery.scan()

    assert report.opened_paths == ()
    assert report.reasons == {f"{kind}_entry_disappeared": 1}
    assert report.unrecoverable == 1


def test_recovery_materializes_all_public_refetches_before_any_archive_write(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    first = evidence_event(
        "a" * 20,
        published_at=NOW - timedelta(days=2),
        link="https://publisher.example.com/a",
    )
    second = evidence_event(
        "b" * 20,
        published_at=NOW - timedelta(days=2),
        link="https://publisher.example.com/b",
    )
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [
            {
                "event_id": first.event_id,
                "title": first.title,
                "original_url": first.primary_evidence[0].canonical_url,
                "published_at": first.published_at.isoformat(),
                "verification_status": first.verification_status.value,
            },
            {
                "event_id": second.event_id,
                "title": second.title,
                "original_url": second.primary_evidence[0].canonical_url,
                "published_at": second.published_at.isoformat(),
                "verification_status": second.verification_status.value,
            },
        ]}],
    })
    observed_archive_counts: list[int] = []

    def refetch(url):
        observed_archive_counts.append(archive.count())
        if url.endswith("/a"):
            return evidence_snapshot(
                first,
                snapshot_id="s" * 20,
                raw_snapshot_id="r" * 20,
            )
        raise RuntimeError("provider detail")

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        archive=archive,
        public_refetcher=refetch,
        now=lambda: NOW,
    ).import_records()

    assert observed_archive_counts == [0, 0]
    assert report.public_refetched == 1
    assert report.unrecoverable == 1
    assert report.reasons == {"public_refetch_failed": 1}


def test_recovery_rejects_a_conflicting_refetch_raw_group_but_commits_an_independent_group(
    tmp_path,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    events = {
        key: evidence_event(
            key * 20,
            published_at=NOW - timedelta(days=2),
            link=f"https://publisher.example.com/{key}",
        )
        for key in ("a", "b", "c")
    }
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [
            {
                "event_id": selected.event_id,
                "title": selected.title,
                "original_url": selected.primary_evidence[0].canonical_url,
                "published_at": selected.published_at.isoformat(),
                "verification_status": selected.verification_status.value,
            }
            for selected in events.values()
        ]}],
    })

    def refetch(url):
        key = url.rsplit("/", 1)[-1]
        return evidence_snapshot(
            events[key],
            snapshot_id={"a": "s", "b": "t", "c": "u"}[key] * 20,
            raw_snapshot_id=("r" if key in {"a", "b"} else "v") * 20,
        )

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        archive=archive,
        public_refetcher=refetch,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 1
    assert report.unrecoverable == 2
    assert report.reasons == {"ambiguous_record": 2}
    assert [row["event_id"] for row in archive.query(90)] == ["c" * 20]


def test_recovery_preflights_a_tampered_refetch_raw_id_before_writing_its_sibling(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    events = {
        key: evidence_event(
            key * 20,
            published_at=NOW - timedelta(days=2),
            link=f"https://publisher.example.com/{key}",
        )
        for key in ("a", "b")
    }
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [
            {
                "event_id": selected.event_id,
                "title": selected.title,
                "original_url": selected.primary_evidence[0].canonical_url,
                "published_at": selected.published_at.isoformat(),
                "verification_status": selected.verification_status.value,
            }
            for selected in events.values()
        ]}],
    })

    def refetch(url):
        key = url.rsplit("/", 1)[-1]
        selected = events[key]
        if key == "b":
            selected = replace(selected, title="篡改后的标题")
        return evidence_snapshot(
            selected,
            snapshot_id="s" * 20,
            raw_snapshot_id="r" * 20,
        )

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        archive=archive,
        public_refetcher=refetch,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == 2
    assert report.reasons == {
        "ambiguous_record": 1,
        "public_refetch_failed": 1,
    }
    assert archive.query(90) == []


def test_recovery_rejects_an_existing_raw_group_tamper_before_writing_any_sibling(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    first = evidence_event("a" * 20, published_at=NOW - timedelta(days=2))
    second = evidence_event("b" * 20, published_at=NOW - timedelta(days=2))
    original = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(first, second),
    )
    archive.upsert(original)
    state_before = archive.state_path.read_bytes()
    tampered = replace(second, summary="篡改后的公开摘要")
    current = write_json(
        root / "current.json",
        snapshot_document(replace(original, events=(first, tampered))),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == 2
    assert report.reasons == {"ambiguous_record": 2}
    assert archive.state_path.read_bytes() == state_before
    assert archive.get(second.event_id)["summary"] == second.summary


def test_recovery_completes_a_legal_preexisting_raw_sibling_and_retries_idempotently(
    tmp_path,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    first = evidence_event("a" * 20, published_at=NOW - timedelta(days=2))
    second = evidence_event("b" * 20, published_at=NOW - timedelta(days=2))
    complete = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(first, second),
    )
    archive.upsert(replace(complete, events=(first,)))
    current = write_json(root / "current.json", snapshot_document(complete))

    first_report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()
    retry_report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert (first_report.cache_recovered, first_report.unrecoverable) == (2, 0)
    assert (retry_report.cache_recovered, retry_report.unrecoverable) == (2, 0)
    assert {row["event_id"] for row in archive.query(90)} == {"a" * 20, "b" * 20}


def test_recovery_builds_archive_raw_authority_once_while_an_independent_group_continues(
    tmp_path,
    monkeypatch,
):
    import evidence_verification.archive as archive_module
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    first = evidence_event("a" * 20, published_at=NOW - timedelta(days=2))
    second = evidence_event("b" * 20, published_at=NOW - timedelta(days=2))
    original = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(first, second),
    )
    archive.upsert(original)
    tampered = replace(second, summary="篡改后的公开摘要")
    current = write_json(
        root / "current.json",
        snapshot_document(replace(original, events=(first, tampered))),
    )
    independent = evidence_snapshot(
        evidence_event("c" * 20, published_at=NOW - timedelta(days=2)),
        snapshot_id="u" * 20,
        raw_snapshot_id="v" * 20,
    )
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-independent.json",
        snapshot_document(independent),
    )
    real_raw_map = archive_module._archive_raw_authority_map
    raw_map_calls = 0

    def observe_raw_map(rows):
        nonlocal raw_map_calls
        raw_map_calls += 1
        return real_raw_map(rows)

    monkeypatch.setattr(archive_module, "_archive_raw_authority_map", observe_raw_map)

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert raw_map_calls == 1
    assert report.cache_recovered == 1
    assert report.unrecoverable == 2
    assert report.reasons == {"ambiguous_record": 2}
    assert {row["event_id"] for row in archive.query(90)} == {
        "a" * 20,
        "b" * 20,
        "c" * 20,
    }
    assert archive.get(second.event_id)["summary"] == second.summary


def test_recovery_allows_a_newer_same_status_snapshot_to_update_real_content(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    first_time = NOW - timedelta(days=2)
    second_time = NOW - timedelta(days=1)
    original = evidence_event("a" * 20, published_at=first_time)
    updated_evidence = replace(
        original.primary_evidence[0],
        evidence_id="evidence-update-a",
        canonical_url="https://official.example.com/update-a",
        published_at=second_time,
        title="后续正式公告证据",
        excerpt="后续公开证据摘要",
    )
    updated = replace(
        original,
        title="后续正式公告标题",
        summary="后续正式公告摘要",
        verified_at=second_time,
        evidence_as_of=second_time,
        primary_evidence=(updated_evidence,),
    )
    first = EvidenceSnapshot("s" * 20, first_time, (original,), "r" * 20)
    second = EvidenceSnapshot("t" * 20, second_time, (updated,), "u" * 20)
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-first.json",
        snapshot_document(first),
    )
    current = write_json(root / "current.json", snapshot_document(second))

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy,),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 1
    assert report.unrecoverable == 0
    archived = archive.get(original.event_id)
    assert archived["title"] == updated.title
    assert archived["summary"] == updated.summary
    evidence_by_id = {
        item["evidence_id"]: item for item in archived["primary_evidence"]
    }
    assert set(evidence_by_id) == {
        original.primary_evidence[0].evidence_id,
        updated_evidence.evidence_id,
    }
    assert evidence_by_id[updated_evidence.evidence_id]["canonical_url"] == (
        updated_evidence.canonical_url
    )
    assert len(archived["status_history"]) == 1
    assert len(archived["snapshot_history"]) == 2


def test_recovery_rejects_duplicate_event_identity_inside_one_source_snapshot(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    duplicate = evidence_event("a" * 20)
    current = write_json(
        root / "current.json",
        snapshot_document(EvidenceSnapshot(
            snapshot_id="s" * 20,
            raw_snapshot_id="r" * 20,
            generated_at=NOW - timedelta(days=1),
            events=(duplicate, duplicate),
        )),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"ambiguous_record": 1}
    assert archive.count() == 0


def test_recovery_raw_group_fault_reports_and_persists_all_or_old_then_retries(
    tmp_path,
    monkeypatch,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    selected = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(evidence_event("a" * 20), evidence_event("b" * 20)),
    )
    current = write_json(root / "current.json", snapshot_document(selected))
    real_write = EvidenceArchive._atomic_write
    writes = 0

    def fail_once(self, path, payload, maximum):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated recovery interruption")
        return real_write(self, path, payload, maximum)

    monkeypatch.setattr(EvidenceArchive, "_atomic_write", fail_once)
    first = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert (first.cache_recovered, first.unrecoverable) in {(0, 2), (2, 0)}
    assert {row["event_id"] for row in archive.query(90)} in (
        set(),
        {"a" * 20, "b" * 20},
    )

    monkeypatch.setattr(EvidenceArchive, "_atomic_write", real_write)
    retry = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert retry.cache_recovered == 2
    assert retry.unrecoverable == 0
    assert {row["event_id"] for row in archive.query(90)} == {"a" * 20, "b" * 20}


def test_recovery_rejects_more_public_candidates_than_the_archive_batch_before_callbacks(
    tmp_path,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    candidate_count = 5_001
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [
            {
                "event_id": f"{index:020x}",
                "title": f"event-{index}",
                "original_url": f"https://publisher.example.com/{index}",
                "published_at": (NOW - timedelta(days=1)).isoformat(),
                "verification_status": "verified",
            }
            for index in range(candidate_count)
        ]}],
    })
    calls = 0

    def refetch(_url):
        nonlocal calls
        calls += 1
        raise AssertionError("callback must not be entered")

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        archive=EvidenceArchive(root, now=lambda: NOW),
        public_refetcher=refetch,
        now=lambda: NOW,
    ).import_records()

    assert calls == 0
    assert report.public_refetched == 0
    assert report.reasons == {"public_refetch_failed": candidate_count}
    assert report.unrecoverable == candidate_count


def test_recovery_rejects_aggregate_public_candidate_input_budget_before_callbacks(
    tmp_path,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    candidate_count = 40
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [
            {
                "event_id": f"{index:020x}",
                "title": "x" * 8_192,
                "original_url": f"https://publisher.example.com/{index}",
                "published_at": (NOW - timedelta(days=1)).isoformat(),
                "verification_status": "verified",
            }
            for index in range(candidate_count)
        ]}],
    })
    calls = 0

    def refetch(_url):
        nonlocal calls
        calls += 1
        raise AssertionError("callback must not be entered")

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        archive=EvidenceArchive(root, now=lambda: NOW),
        public_refetcher=refetch,
        now=lambda: NOW,
    ).import_records()

    assert calls == 0
    assert report.public_refetched == 0
    assert report.reasons == {"public_refetch_failed": candidate_count}
    assert report.unrecoverable == candidate_count


def test_recovery_public_refetch_budget_stops_callbacks_and_rejects_the_related_raw_group(
    tmp_path,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    selected = {
        key: evidence_event(
            key * 20,
            link=f"https://publisher.example.com/{key}",
        )
        for key in ("a", "b", "c")
    }
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [
            {
                "event_id": event.event_id,
                "title": event.title,
                "original_url": event.primary_evidence[0].canonical_url,
                "published_at": event.published_at.isoformat(),
                "verification_status": event.verification_status.value,
            }
            for event in selected.values()
        ]}],
    })
    large_evidence = selected["a"].primary_evidence + tuple(
        replace(
            selected["a"].primary_evidence[0],
            evidence_id=f"large-{index}",
            canonical_url=f"https://official.example.com/large-{index}",
            excerpt="x" * 8_000,
        )
        for index in range(31)
    )
    calls: list[str] = []

    def refetch(url):
        key = url.rsplit("/", 1)[-1]
        calls.append(key)
        selected_event = selected[key]
        if key == "a":
            selected_event = replace(selected_event, primary_evidence=large_evidence)
        elif key == "b":
            selected_event = replace(
                selected_event,
                summary="y" * 8_192,
                core_claim="z" * 2_000,
            )
        return evidence_snapshot(
            selected_event,
            snapshot_id=("s" if key in {"a", "b"} else "u") * 20,
            raw_snapshot_id=("r" if key in {"a", "b"} else "v") * 20,
        )

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        archive=archive,
        public_refetcher=refetch,
        now=lambda: NOW,
    ).import_records()

    assert calls == ["a", "b"]
    assert report.public_refetched == 0
    assert report.unrecoverable == 3
    assert report.reasons == {
        "ambiguous_record": 1,
        "public_refetch_failed": 2,
    }
    assert archive.query(90) == []


@pytest.mark.parametrize("invalid_shape", ("empty_title", "missing_title", "duplicate_id"))
def test_recovery_invalid_current_raw_marker_blocks_legacy_sibling_but_not_independent(
    tmp_path,
    invalid_shape,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    first = evidence_event("a" * 20)
    invalid = evidence_event("b" * 20)
    source = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(first, invalid),
    )
    current_document = snapshot_document(source)
    if invalid_shape == "empty_title":
        current_document["events"][1]["title"] = ""
    elif invalid_shape == "missing_title":
        current_document["events"][1].pop("title")
    else:
        current_document["events"][1]["event_id"] = first.event_id
    current = write_json(root / "current.json", current_document)
    legacy_sibling = write_json(
        root / "legacy-snapshots" / "evidence-sibling.json",
        snapshot_document(replace(source, events=(first,))),
    )
    independent = evidence_snapshot(
        evidence_event("c" * 20),
        snapshot_id="u" * 20,
        raw_snapshot_id="v" * 20,
    )
    legacy_independent = write_json(
        root / "legacy-snapshots" / "evidence-independent.json",
        snapshot_document(independent),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy_sibling, legacy_independent),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 1
    assert report.public_refetched == 0
    expected_reasons = {"ambiguous_record": 1}
    if invalid_shape != "duplicate_id":
        expected_reasons["missing_required_fields"] = 1
    assert report.unrecoverable == sum(expected_reasons.values())
    assert report.reasons == expected_reasons
    assert {row["event_id"] for row in archive.query(90)} == {"c" * 20}


def test_recovery_invalid_refetch_keeps_raw_marker_when_declared_events_have_no_ids(
    tmp_path,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    candidate = evidence_event(
        "a" * 20,
        link="https://publisher.example.com/a",
    )
    sibling = evidence_event("b" * 20)
    generated_at = NOW - timedelta(days=1)
    legacy_source = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=generated_at,
        events=(sibling,),
    )
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-sibling.json",
        snapshot_document(legacy_source),
    )
    radar = write_json(root / "radar.json", {
        "industries": [{"items": [{
            "event_id": candidate.event_id,
            "title": candidate.title,
            "original_url": candidate.primary_evidence[0].canonical_url,
            "published_at": candidate.published_at.isoformat(),
            "verification_status": candidate.verification_status.value,
        }]}],
    })

    def refetch(_url):
        return EvidenceSnapshot(
            snapshot_id=legacy_source.snapshot_id,
            raw_snapshot_id=legacy_source.raw_snapshot_id,
            generated_at=legacy_source.generated_at,
            events=(object(), object(), candidate),
        )

    report = HistoryRecovery(
        root,
        radar_cache=radar,
        legacy_snapshots=(legacy,),
        archive=archive,
        public_refetcher=refetch,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 0
    assert report.public_refetched == 0
    assert report.unrecoverable == 2
    assert report.reasons == {
        "ambiguous_record": 1,
        "public_refetch_failed": 1,
    }
    assert archive.query(90) == []


@pytest.mark.parametrize(
    "missing_field",
    ("event_id", "snapshot_id", "generated_at"),
)
def test_recovery_invalid_cache_keeps_verifiable_raw_marker_with_missing_fields(
    tmp_path,
    missing_field,
):
    from evidence_verification.recovery import HistoryRecovery

    root = tmp_path / "evidence"
    archive = EvidenceArchive(root, now=lambda: NOW)
    generated_at = NOW - timedelta(days=1)
    invalid_source = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=generated_at,
        events=(evidence_event("b" * 20),),
    )
    invalid_document = snapshot_document(invalid_source)
    if missing_field == "event_id":
        invalid_document["events"][0].pop("event_id")
    else:
        invalid_document.pop(missing_field)
    current = write_json(root / "current.json", invalid_document)
    legacy_source = replace(
        invalid_source,
        events=(evidence_event("a" * 20),),
    )
    legacy = write_json(
        root / "legacy-snapshots" / "evidence-sibling.json",
        snapshot_document(legacy_source),
    )
    independent = evidence_snapshot(
        evidence_event("c" * 20),
        snapshot_id="u" * 20,
        raw_snapshot_id="v" * 20,
    )
    independent_path = write_json(
        root / "legacy-snapshots" / "evidence-independent.json",
        snapshot_document(independent),
    )

    report = HistoryRecovery(
        root,
        evidence_current=current,
        legacy_snapshots=(legacy, independent_path),
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 1
    assert report.public_refetched == 0
    assert report.unrecoverable == 2
    assert report.reasons == {
        "ambiguous_record": 1,
        "missing_required_fields": 1,
    }
    assert {row["event_id"] for row in archive.query(90)} == {"c" * 20}
