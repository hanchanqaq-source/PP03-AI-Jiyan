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


def test_recovery_imports_each_valid_snapshot_event_when_a_sibling_is_invalid(tmp_path):
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

    assert report.cache_recovered == 1
    assert report.public_refetched == 0
    assert report.unrecoverable == 1
    assert report.reasons == {"missing_required_fields": 1}
    assert [row["event_id"] for row in archive.query(90)] == [good.event_id]


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


def test_recovery_archive_rejection_is_counted_per_event_without_losing_siblings(tmp_path):
    from evidence_verification.recovery import HistoryRecovery

    good_id = "a" * 20
    bad_id = "b" * 20

    class SelectiveArchive:
        def __init__(self):
            self.rows: dict[str, dict] = {}

        def upsert(self, selected):
            assert len(selected.events) == 1
            event = selected.events[0]
            if event.event_id == bad_id:
                raise OSError("private archive failure")
            self.rows[event.event_id] = {
                "event_id": event.event_id,
                "recovery_metadata": dict(selected.recovery_metadata),
            }

        def get(self, event_id):
            return self.rows.get(event_id)

    root = tmp_path / "evidence"
    selected = EvidenceSnapshot(
        snapshot_id="s" * 20,
        raw_snapshot_id="r" * 20,
        generated_at=NOW - timedelta(days=1),
        events=(evidence_event(good_id), evidence_event(bad_id)),
    )
    current = write_json(root / "current.json", snapshot_document(selected))
    archive = SelectiveArchive()

    report = HistoryRecovery(
        root,
        evidence_current=current,
        archive=archive,
        now=lambda: NOW,
    ).import_records()

    assert report.cache_recovered == 1
    assert report.unrecoverable == 1
    assert report.reasons == {"archive_rejected": 1}
    assert set(archive.rows) == {good_id}
    assert archive.rows[good_id]["recovery_metadata"] == {
        "source": "evidence_current",
        "recovered_at": NOW.isoformat(),
        "recovery_status": "cache_recovered",
        "source_snapshot_id": "s" * 20,
    }


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
