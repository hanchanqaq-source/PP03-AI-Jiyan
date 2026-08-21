from __future__ import annotations

import json
import multiprocessing
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

UTC = timezone.utc
NOW = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)


def _cleanup_archive_candidate_in_child(data_dir, acceptance_root, logs_root, start, results):
    from cache_management import CacheManager

    if not start.wait(10):
        raise RuntimeError("cleanup start timeout")
    result = CacheManager(
        data_dir=data_dir,
        acceptance_root=acceptance_root,
        logs_root=logs_root,
        now=lambda: NOW,
    ).cleanup_expired(manual=True)
    results.put(result["released_bytes"])


def _replace_archive_bucket_in_child(bucket, replacement, start):
    if not start.wait(10):
        raise RuntimeError("replacement start timeout")
    os.replace(replacement, bucket)


def write_fund_cache(root: Path, name: str, key: str, expires_at: datetime, size: int = 0) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.json"
    path.write_text(json.dumps({
        "key": key,
        "payload": {"padding": "x" * size},
        "fetched_at": (expires_at - timedelta(days=1)).isoformat(),
        "expires_at": expires_at.isoformat(),
    }), encoding="utf-8")
    return path


def set_age(path: Path, days: int) -> None:
    timestamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


def manager(tmp_path: Path, **kwargs) -> CacheManager:
    from cache_management import CacheManager

    return CacheManager(
        data_dir=tmp_path / "data",
        acceptance_root=tmp_path / "repo" / ".tmp" / "acceptance",
        logs_root=tmp_path / "data" / "logs",
        now=lambda: NOW,
        **kwargs,
    )


def test_status_classifies_retention_windows_and_only_marks_owned_expired_items(tmp_path):
    subject = manager(tmp_path)
    fund_root = tmp_path / "data" / "fund-cache" / "v1"
    cases = [
        ("stock", "stock_snapshot:600000", 1, "stock_quotes"),
        ("search", "search:人工智能", 30, "fund_30d"),
        ("profile", "profile:017811", 30, "fund_30d"),
        ("nav", "nav_history:017811", 30, "fund_30d"),
        ("holdings", "holdings:017811", 180, "fund_180d"),
        ("allocation", "industry_allocation:017811", 180, "fund_180d"),
        ("classification", "stock_industry_classification:600000", 180, "fund_180d"),
    ]
    for name, key, retention, _ in cases:
        write_fund_cache(fund_root, name, key, NOW - timedelta(days=retention + 1))
        write_fund_cache(fund_root, f"{name}-recent", key + ":recent", NOW - timedelta(days=retention - 1))

    translation_file = tmp_path / "data" / "cache" / "translations" / "v1.json"
    translation_file.parent.mkdir(parents=True)
    translation_file.write_text(json.dumps({"version": 1, "entries": {
        "old": {"translation_status": "translated", "last_accessed_at": (NOW - timedelta(days=91)).isoformat()},
        "recent": {"translation_status": "translated", "last_accessed_at": (NOW - timedelta(days=89)).isoformat()},
    }}), encoding="utf-8")

    old_temp = tmp_path / "repo" / ".tmp" / "acceptance" / "old" / "artifact.json"
    new_temp = tmp_path / "repo" / ".tmp" / "acceptance" / "new" / "artifact.json"
    old_temp.parent.mkdir(parents=True)
    new_temp.parent.mkdir(parents=True)
    old_temp.write_text("old", encoding="utf-8")
    new_temp.write_text("new", encoding="utf-8")
    set_age(old_temp, 8)
    set_age(new_temp, 6)

    old_log = tmp_path / "data" / "logs" / "old.log"
    new_log = tmp_path / "data" / "logs" / "new.log"
    old_log.parent.mkdir(parents=True)
    old_log.write_text("old", encoding="utf-8")
    new_log.write_text("new", encoding="utf-8")
    set_age(old_log, 15)
    set_age(new_log, 13)

    status = subject.status()

    assert status["categories"]["stock_quotes"]["expired_count"] == 1
    assert status["categories"]["fund_30d"]["expired_count"] == 3
    assert status["categories"]["fund_180d"]["expired_count"] == 3
    assert status["categories"]["translations"]["expired_count"] == 1
    assert status["categories"]["temporary"]["expired_count"] == 1
    assert status["categories"]["logs"]["expired_count"] == 1
    assert status["expired_count"] == 10
    assert status["reclaimable_bytes"] > 0


def test_cleanup_preserves_user_truth_settings_tracked_evidence_valid_cache_and_pinned_entry(tmp_path):
    subject = manager(tmp_path)
    data_dir = tmp_path / "data"
    portfolio = data_dir / "fund-portfolio.json"
    settings = data_dir / "settings.json"
    legacy_portfolio = data_dir / "portfolio.json"
    tracked_screenshot = tmp_path / "repo" / "docs" / "screenshots" / "v0.2-w2" / "acceptance.png"
    source = tmp_path / "repo" / "backend" / "app.py"
    lock = tmp_path / "repo" / "frontend" / "package-lock.json"
    for path, payload in (
        (portfolio, json.dumps({"holdings": [{"code": "017811"}]})),
        (settings, "settings"),
        (legacy_portfolio, "portfolio"),
        (tracked_screenshot, "screenshot"),
        (source, "source"),
        (lock, "lock"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")

    fund_root = data_dir / "fund-cache" / "v1"
    pinned = write_fund_cache(fund_root, "pinned", "holdings:017811", NOW + timedelta(hours=1))
    valid = write_fund_cache(fund_root, "valid", "profile:000001", NOW + timedelta(hours=1))
    expired = write_fund_cache(fund_root, "expired", "profile:000002", NOW - timedelta(days=31))
    originals = {path: path.read_bytes() for path in (portfolio, settings, legacy_portfolio, tracked_screenshot, source, lock, pinned, valid)}

    before = subject.status()
    result = subject.cleanup_expired(manual=True)

    assert before["categories"]["fund_180d"]["pinned_count"] == 1
    assert not expired.exists()
    for path, content in originals.items():
        assert path.read_bytes() == content
    assert result["released_bytes"] > 0


def test_cleanup_uses_fixed_eviction_order_and_reports_protected_over_limit(tmp_path):
    subject = manager(tmp_path, max_bytes=1)
    fund_root = tmp_path / "data" / "fund-cache" / "v1"
    stock = write_fund_cache(fund_root, "stock", "stock_snapshot:600000", NOW - timedelta(days=2), size=20)
    fund = write_fund_cache(fund_root, "fund", "profile:017811", NOW - timedelta(days=31), size=20)

    translation_file = tmp_path / "data" / "cache" / "translations" / "v1.json"
    translation_file.parent.mkdir(parents=True)
    translation_file.write_text(json.dumps({"version": 1, "entries": {
        "old": {
            "translated_title_zh": "中文", "translated_summary_zh": "摘要",
            "translation_status": "translated", "last_accessed_at": (NOW - timedelta(days=91)).isoformat(),
        },
        "valid-large": {
            "translated_title_zh": "中" * 100, "translated_summary_zh": "摘" * 100,
            "translation_status": "translated", "last_accessed_at": NOW.isoformat(),
        },
    }}), encoding="utf-8")

    temporary = tmp_path / "repo" / ".tmp" / "acceptance" / "old" / "file.bin"
    temporary.parent.mkdir(parents=True)
    temporary.write_bytes(b"x" * 20)
    set_age(temporary, 8)

    result = subject.cleanup_expired()

    assert result["deleted_categories"][:4] == ["temporary", "translations", "stock_quotes", "fund_30d"]
    assert not temporary.exists() and not stock.exists() and not fund.exists()
    assert result["status"]["over_limit_bytes"] > 0
    remaining = json.loads(translation_file.read_text(encoding="utf-8"))["entries"]
    assert set(remaining) == {"valid-large"}


def test_auto_cleanup_runs_at_startup_no_more_than_once_per_twenty_four_hours(tmp_path):
    from cache_management import CacheManager

    clock = [NOW]
    subject = CacheManager(
        data_dir=tmp_path / "data",
        acceptance_root=tmp_path / "acceptance",
        logs_root=tmp_path / "data" / "logs",
        now=lambda: clock[0],
    )

    first = subject.maybe_auto_cleanup()
    clock[0] += timedelta(hours=23)
    second = subject.maybe_auto_cleanup()
    clock[0] += timedelta(hours=1, seconds=1)
    third = subject.maybe_auto_cleanup()

    assert first["ran"] is True
    assert second["ran"] is False
    assert third["ran"] is True
    assert third["status"]["last_auto_cleanup_at"] == clock[0].isoformat()


def test_cleanup_revalidates_a_fund_cache_replaced_after_scan(tmp_path, monkeypatch):
    subject = manager(tmp_path)
    fund_root = tmp_path / "data" / "fund-cache" / "v1"
    path = write_fund_cache(fund_root, "profile", "profile:017811", NOW - timedelta(days=31))
    original_scan = subject._scan
    scan_calls = 0

    def scan_then_refresh():
        nonlocal scan_calls
        scan_calls += 1
        status, candidates = original_scan()
        if scan_calls == 1:
            write_fund_cache(fund_root, "profile", "profile:017811", NOW + timedelta(days=1))
        return status, candidates

    monkeypatch.setattr(subject, "_scan", scan_then_refresh)

    result = subject.cleanup_expired(manual=True)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["expires_at"] == (NOW + timedelta(days=1)).isoformat()
    assert result["released_bytes"] == 0


def test_cleanup_revalidates_a_translation_accessed_after_scan(tmp_path, monkeypatch):
    subject = manager(tmp_path)
    translation_file = tmp_path / "data" / "cache" / "translations" / "v1.json"
    translation_file.parent.mkdir(parents=True)
    translation_file.write_text(json.dumps({"version": 1, "entries": {
        "old": {
            "translated_title_zh": "中文", "translated_summary_zh": "摘要",
            "translation_status": "translated", "last_accessed_at": (NOW - timedelta(days=91)).isoformat(),
        },
    }}), encoding="utf-8")
    original_scan = subject._scan
    scan_calls = 0

    def scan_then_access():
        nonlocal scan_calls
        scan_calls += 1
        status, candidates = original_scan()
        if scan_calls == 1:
            document = json.loads(translation_file.read_text(encoding="utf-8"))
            document["entries"]["old"]["last_accessed_at"] = NOW.isoformat()
            translation_file.write_text(json.dumps(document), encoding="utf-8")
        return status, candidates

    monkeypatch.setattr(subject, "_scan", scan_then_access)

    result = subject.cleanup_expired(manual=True)

    remaining = json.loads(translation_file.read_text(encoding="utf-8"))["entries"]
    assert set(remaining) == {"old"}
    assert result["released_bytes"] == 0


def test_source_health_history_counts_toward_limit_and_only_expired_history_is_reclaimed(tmp_path):
    subject = manager(tmp_path, max_bytes=1)
    health_root = tmp_path / "data" / "source-health"
    history_root = health_root / "history"
    history_root.mkdir(parents=True)
    current = health_root / "current-summary.json"
    last_run = health_root / "last-run.json"
    config = health_root / "health-config.json"
    old_history = history_root / "2026-05-18.jsonl"
    boundary_history = history_root / "2026-05-20.jsonl"
    recent_history = history_root / "2026-08-17.jsonl"
    user_holdings = tmp_path / "data" / "fund-portfolio.json"
    for path, content in (
        (current, "current"),
        (last_run, "last-run"),
        (config, "config"),
        (old_history, "old"),
        (boundary_history, "boundary"),
        (recent_history, "recent"),
        (user_holdings, json.dumps({"holdings": []})),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    protected = {path: path.read_bytes() for path in (current, last_run, config, boundary_history, recent_history, user_holdings)}
    source_health_bytes = sum(path.stat().st_size for path in (current, last_run, config, old_history, boundary_history, recent_history))

    status = subject.status()
    result = subject.cleanup_expired(manual=True)

    assert status["categories"]["source_health"]["file_count"] == 6
    assert status["categories"]["source_health"]["expired_count"] == 1
    assert status["categories"]["source_health"]["bytes"] == source_health_bytes
    assert status["total_bytes"] >= source_health_bytes
    assert not old_history.exists()
    for path, content in protected.items():
        assert path.read_bytes() == content
    assert "source_health" in result["deleted_categories"]


def test_evidence_archive_counts_bytes_and_only_reports_whole_days_older_than_ninety_as_candidates(tmp_path):
    subject = manager(tmp_path, max_bytes=1)
    data_dir = tmp_path / "data"
    archive_root = data_dir / "evidence-verification" / "v1" / "archive"
    archive_root.mkdir(parents=True)
    cutoff_date = NOW.date() - timedelta(days=90)
    old_bucket = archive_root / f"{(cutoff_date - timedelta(days=1)).isoformat()}.jsonl"
    boundary_bucket = archive_root / f"{cutoff_date.isoformat()}.jsonl"
    recent_bucket = archive_root / f"{NOW.date().isoformat()}.jsonl"
    state = archive_root / "state.json"
    index = archive_root / "index.json"
    current = data_dir / "evidence-verification" / "v1" / "current.json"
    trusted = data_dir / "evidence-verification" / "v1" / "trusted" / "trusted-current.json"
    config = data_dir / "settings.json"
    user_data = data_dir / "fund-portfolio.json"
    for path, content in (
        (old_bucket, "old-archive"),
        (boundary_bucket, "boundary-archive"),
        (recent_bucket, "recent-archive"),
        (state, "state"),
        (index, "index"),
        (current, "current"),
        (trusted, "trusted"),
        (config, "config"),
        (user_data, json.dumps({"holdings": [{"cost": "private"}]})),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    protected = {
        path: path.read_bytes()
        for path in (boundary_bucket, recent_bucket, state, index, current, trusted, config, user_data)
    }
    archive_bytes = sum(path.stat().st_size for path in (old_bucket, boundary_bucket, recent_bucket, state, index))

    status = subject.status()
    result = subject.cleanup_expired(manual=True)

    assert status["categories"]["evidence_archive"]["bytes"] == archive_bytes
    assert status["categories"]["evidence_archive"]["file_count"] == 5
    assert status["categories"]["evidence_archive"]["expired_count"] == 1
    assert status["categories"]["evidence_archive"]["reclaimable_bytes"] == len("old-archive")
    assert old_bucket.read_text(encoding="utf-8") == "old-archive"
    for path, content in protected.items():
        assert path.read_bytes() == content
    assert "evidence_archive" not in result["deleted_categories"]
    assert result["released_bytes"] == 0


def test_evidence_archive_cleanup_revalidates_bucket_replaced_after_scan(tmp_path, monkeypatch):
    subject = manager(tmp_path)
    archive_root = tmp_path / "data" / "evidence-verification" / "v1" / "archive"
    archive_root.mkdir(parents=True)
    old_date = NOW.date() - timedelta(days=91)
    bucket = archive_root / f"{old_date.isoformat()}.jsonl"
    bucket.write_text("old", encoding="utf-8")
    original_scan = subject._scan
    scan_calls = 0

    def scan_then_replace():
        nonlocal scan_calls
        scan_calls += 1
        status, candidates = original_scan()
        if scan_calls == 1:
            bucket.write_text("replacement-must-survive", encoding="utf-8")
        return status, candidates

    monkeypatch.setattr(subject, "_scan", scan_then_replace)

    result = subject.cleanup_expired(manual=True)

    assert bucket.read_text(encoding="utf-8") == "replacement-must-survive"
    assert result["released_bytes"] == 0


def test_evidence_archive_cleanup_never_traverses_a_reparse_root(tmp_path):
    subject = manager(tmp_path)
    outside = tmp_path / "outside-archive"
    outside.mkdir()
    old_date = NOW.date() - timedelta(days=91)
    outside_bucket = outside / f"{old_date.isoformat()}.jsonl"
    outside_bucket.write_text("foreign-must-survive", encoding="utf-8")
    archive_root = tmp_path / "data" / "evidence-verification" / "v1" / "archive"
    archive_root.parent.mkdir(parents=True)
    try:
        archive_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    status = subject.status()
    result = subject.cleanup_expired(manual=True)

    assert status["categories"]["evidence_archive"]["bytes"] == 0
    assert outside_bucket.read_text(encoding="utf-8") == "foreign-must-survive"
    assert result["released_bytes"] == 0


def test_evidence_archive_cleanup_never_deletes_a_hardlinked_bucket(tmp_path):
    subject = manager(tmp_path)
    archive_root = tmp_path / "data" / "evidence-verification" / "v1" / "archive"
    archive_root.mkdir(parents=True)
    old_date = NOW.date() - timedelta(days=91)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("foreign-hardlink-must-survive", encoding="utf-8")
    bucket = archive_root / f"{old_date.isoformat()}.jsonl"
    os.link(outside, bucket)
    original = outside.read_bytes()

    status = subject.status()
    result = subject.cleanup_expired(manual=True)

    assert status["categories"]["evidence_archive"]["bytes"] == 0
    assert bucket.exists()
    assert outside.read_bytes() == original
    assert result["released_bytes"] == 0


def test_evidence_archive_cleanup_binds_full_identity_not_only_size_and_mtime(tmp_path, monkeypatch):
    subject = manager(tmp_path)
    archive_root = tmp_path / "data" / "evidence-verification" / "v1" / "archive"
    archive_root.mkdir(parents=True)
    old_date = NOW.date() - timedelta(days=91)
    bucket = archive_root / f"{old_date.isoformat()}.jsonl"
    bucket.write_text("original", encoding="utf-8")
    original_stat = bucket.stat()
    original_scan = subject._scan
    scan_calls = 0

    def scan_then_replace_with_same_visible_metadata():
        nonlocal scan_calls
        scan_calls += 1
        status, candidates = original_scan()
        if scan_calls == 1:
            bucket.unlink()
            bucket.write_text("replaced", encoding="utf-8")
            os.utime(bucket, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        return status, candidates

    monkeypatch.setattr(subject, "_scan", scan_then_replace_with_same_visible_metadata)

    result = subject.cleanup_expired(manual=True)

    assert bucket.read_text(encoding="utf-8") == "replaced"
    assert result["released_bytes"] == 0


def test_evidence_archive_cleanup_preserves_bucket_when_unlink_fails(tmp_path, monkeypatch):
    subject = manager(tmp_path)
    archive_root = tmp_path / "data" / "evidence-verification" / "v1" / "archive"
    archive_root.mkdir(parents=True)
    old_date = NOW.date() - timedelta(days=91)
    bucket = archive_root / f"{old_date.isoformat()}.jsonl"
    bucket.write_text("must-survive", encoding="utf-8")
    original_unlink = Path.unlink

    def fail_selected_unlink(path, *args, **kwargs):
        if path == bucket:
            raise PermissionError("simulated cleanup denial")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)

    result = subject.cleanup_expired(manual=True)

    assert bucket.read_text(encoding="utf-8") == "must-survive"
    assert result["released_bytes"] == 0
    assert "evidence_archive" not in result["deleted_categories"]


def test_concurrent_evidence_archive_cleanup_never_bypasses_archive_authority(tmp_path):
    subjects = (manager(tmp_path), manager(tmp_path))
    archive_root = tmp_path / "data" / "evidence-verification" / "v1" / "archive"
    archive_root.mkdir(parents=True)
    old_date = NOW.date() - timedelta(days=91)
    bucket = archive_root / f"{old_date.isoformat()}.jsonl"
    bucket.write_text("one-cleanup-only", encoding="utf-8")
    barrier = threading.Barrier(3)
    results: list[dict] = []
    failures: list[BaseException] = []

    def cleanup(selected):
        try:
            barrier.wait(timeout=5)
            results.append(selected.cleanup_expired(manual=True))
        except BaseException as error:
            failures.append(error)

    workers = [threading.Thread(target=cleanup, args=(selected,)) for selected in subjects]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=10)

    assert not failures
    assert all(not worker.is_alive() for worker in workers)
    assert bucket.read_text(encoding="utf-8") == "one-cleanup-only"
    assert sum(result["released_bytes"] for result in results) == 0


def test_evidence_archive_cleanup_never_calls_path_unlink_for_an_archive_candidate(tmp_path, monkeypatch):
    subject = manager(tmp_path)
    archive_root = tmp_path / "data" / "evidence-verification" / "v1" / "archive"
    archive_root.mkdir(parents=True)
    bucket = archive_root / f"{(NOW.date() - timedelta(days=91)).isoformat()}.jsonl"
    bucket.write_text("archive-authority-owned", encoding="utf-8")
    original_unlink = Path.unlink

    def reject_archive_unlink(path, *args, **kwargs):
        if path == bucket:
            raise AssertionError("cache cleanup bypassed the archive writer lock")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_archive_unlink)

    result = subject.cleanup_expired(manual=True)

    assert bucket.read_text(encoding="utf-8") == "archive-authority-owned"
    assert result["released_bytes"] == 0


def test_multiprocess_cleanup_and_bucket_replacement_preserve_archive_authority(tmp_path):
    data_dir = tmp_path / "data"
    acceptance_root = tmp_path / "repo" / ".tmp" / "acceptance"
    logs_root = data_dir / "logs"
    archive_root = data_dir / "evidence-verification" / "v1" / "archive"
    archive_root.mkdir(parents=True)
    bucket = archive_root / f"{(NOW.date() - timedelta(days=91)).isoformat()}.jsonl"
    replacement = archive_root / "replacement.tmp"
    bucket.write_text("old-authority", encoding="utf-8")
    replacement.write_text("new-authority", encoding="utf-8")

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_cleanup_archive_candidate_in_child,
            args=(str(data_dir), str(acceptance_root), str(logs_root), start, results),
        )
        for _ in range(2)
    ]
    processes.append(context.Process(
        target=_replace_archive_bucket_in_child,
        args=(str(bucket), str(replacement), start),
    ))
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(20)

    try:
        assert [process.exitcode for process in processes] == [0, 0, 0]
        assert sorted(results.get(timeout=5) for _ in range(2)) == [0, 0]
        assert bucket.read_text(encoding="utf-8") == "new-authority"
    finally:
        results.close()
        results.join_thread()
