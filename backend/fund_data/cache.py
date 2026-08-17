from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from cache_io_lock import CACHE_IO_LOCK


@dataclass(frozen=True)
class CacheHit:
    payload: Any
    fetched_at: str
    expires_at: str
    is_stale: bool


class FundCache:
    """Small per-capability JSON cache with explicit stale reads."""

    def __init__(self, root: str | os.PathLike[str], now: Callable[[], datetime] | None = None):
        self.root = Path(root)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, key: str, allow_stale: bool = False) -> CacheHit | None:
        path = self.path_for(key)
        with CACHE_IO_LOCK:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("key") != key or "payload" not in raw:
                    return None
                expires_at = datetime.fromisoformat(raw["expires_at"])
                now = self._now()
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                stale = now >= expires_at
                if stale and not allow_stale:
                    return None
                return CacheHit(
                    payload=raw["payload"],
                    fetched_at=raw["fetched_at"],
                    expires_at=raw["expires_at"],
                    is_stale=stale,
                )
            except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
                return None

    def set(self, key: str, payload: Any, ttl_seconds: int) -> CacheHit:
        now = self._now()
        expires = now + timedelta(seconds=ttl_seconds)
        document = {
            "key": key,
            "payload": payload,
            "fetched_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }
        with CACHE_IO_LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.path_for(key)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, path)
        return CacheHit(payload=payload, fetched_at=document["fetched_at"], expires_at=document["expires_at"], is_stale=False)

    def invalidate(self, key: str) -> None:
        path = self.path_for(key)
        with CACHE_IO_LOCK:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
