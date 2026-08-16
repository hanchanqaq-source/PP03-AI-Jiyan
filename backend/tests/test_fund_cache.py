from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fund_data.cache import FundCache


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 16, 8, tzinfo=timezone.utc)

    def now(self):
        return self.value


def test_cache_distinguishes_fresh_and_stale_hits(tmp_path):
    clock = Clock()
    cache = FundCache(tmp_path, now=clock.now)
    cache.set("nav:000001", {"unit_nav": 1.25}, ttl_seconds=60)

    fresh = cache.get("nav:000001")
    assert fresh is not None
    assert fresh.payload == {"unit_nav": 1.25}
    assert fresh.is_stale is False

    clock.value += timedelta(seconds=61)
    assert cache.get("nav:000001") is None
    stale = cache.get("nav:000001", allow_stale=True)
    assert stale is not None
    assert stale.is_stale is True
    assert stale.payload == {"unit_nav": 1.25}


def test_cache_write_is_atomic_and_invalidation_is_scoped(tmp_path):
    clock = Clock()
    cache = FundCache(tmp_path, now=clock.now)
    cache.set("profile:000001", {"name": "甲"}, ttl_seconds=60)
    cache.set("profile:000002", {"name": "乙"}, ttl_seconds=60)

    assert list(tmp_path.glob("*.tmp")) == []
    cache.invalidate("profile:000001")
    assert cache.get("profile:000001") is None
    assert cache.get("profile:000002").payload == {"name": "乙"}


def test_corrupt_cache_entry_is_ignored_without_overwriting_original_bytes(tmp_path):
    clock = Clock()
    cache = FundCache(tmp_path, now=clock.now)
    path = cache.path_for("nav:000001")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b"{broken-cache"
    path.write_bytes(original)

    assert cache.get("nav:000001", allow_stale=True) is None
    assert path.read_bytes() == original
