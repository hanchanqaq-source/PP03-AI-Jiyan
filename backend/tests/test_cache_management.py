from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
NOW = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)


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
