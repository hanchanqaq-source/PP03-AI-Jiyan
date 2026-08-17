"""Conservative lifecycle management for application-owned cache files only."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_MAX_BYTES = 500 * 1024 * 1024
DEFAULT_LOG_MAX_BYTES = 50 * 1024 * 1024
RETENTION_AFTER_EXPIRY = {
    "stock_quotes": timedelta(days=1),
    "fund_30d": timedelta(days=30),
    "fund_180d": timedelta(days=180),
}
CAPABILITY_CATEGORY = {
    "stock_snapshot": "stock_quotes",
    "search": "fund_30d",
    "profile": "fund_30d",
    "nav_history": "fund_30d",
    "holdings": "fund_180d",
    "industry_allocation": "fund_180d",
    "stock_industry_classification": "fund_180d",
}
CATEGORY_ORDER = ("temporary", "translations", "stock_quotes", "fund_30d", "fund_180d", "logs")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_time(value: object) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError):
        return None


def _empty_category() -> dict[str, int]:
    return {"bytes": 0, "file_count": 0, "expired_count": 0, "reclaimable_bytes": 0, "pinned_count": 0}


class CacheManager:
    def __init__(
        self,
        data_dir: str | os.PathLike[str] | None = None,
        acceptance_root: str | os.PathLike[str] | None = None,
        logs_root: str | os.PathLike[str] | None = None,
        now: Callable[[], datetime] | None = None,
        max_bytes: int | None = None,
        log_max_bytes: int | None = None,
    ) -> None:
        default_data = Path(os.environ.get("VR_DATA_DIR") or Path.home() / ".vibe-research")
        repo_root = Path(__file__).resolve().parent.parent
        self.data_dir = Path(data_dir) if data_dir is not None else default_data
        self.fund_root = self.data_dir / "fund-cache" / "v1"
        self.translation_file = self.data_dir / "cache" / "translations" / "v1.json"
        self.state_file = self.data_dir / "cache" / "cleanup-state.json"
        self.acceptance_root = Path(
            acceptance_root or os.environ.get("VR_ACCEPTANCE_DIR") or repo_root / ".tmp" / "acceptance"
        )
        self.logs_root = Path(logs_root or os.environ.get("VR_LOG_DIR") or self.data_dir / "logs")
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.max_bytes = int(max_bytes if max_bytes is not None else os.environ.get("VR_CACHE_MAX_BYTES") or DEFAULT_MAX_BYTES)
        self.log_max_bytes = int(
            log_max_bytes if log_max_bytes is not None else os.environ.get("VR_LOG_MAX_BYTES") or DEFAULT_LOG_MAX_BYTES
        )

    def _held_fund_codes(self) -> set[str]:
        try:
            document = json.loads((self.data_dir / "fund-portfolio.json").read_text(encoding="utf-8"))
            return {
                str(item.get("code") or "")
                for item in document.get("holdings") or []
                if isinstance(item, dict) and item.get("code")
            }
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return set()

    def _read_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _atomic_write(path: Path, document: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _contained(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (OSError, ValueError):
            return False

    def _scan_fund_files(self, categories: dict[str, dict[str, int]], candidates: list[dict]) -> None:
        records: list[dict[str, Any]] = []
        held = self._held_fund_codes()
        if not self.fund_root.exists():
            return
        for path in self.fund_root.glob("*.json"):
            if not path.is_file() or not self._contained(path, self.fund_root):
                continue
            try:
                size = path.stat().st_size
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                size = path.stat().st_size if path.exists() else 0
                category = categories["unclassified_fund"]
                category["bytes"] += size
                category["file_count"] += 1
                continue
            key = str(raw.get("key") or "")
            capability, _, cache_key = key.partition(":")
            category_name = CAPABILITY_CATEGORY.get(capability, "unclassified_fund")
            expires = _parse_time(raw.get("expires_at"))
            fetched = _parse_time(raw.get("fetched_at"))
            records.append({
                "path": path, "size": size, "category": category_name, "capability": capability,
                "cache_key": cache_key, "expires": expires, "fetched": fetched,
            })

        pinned_paths: set[Path] = set()
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        now = _aware(self._now())
        for record in records:
            code = record["cache_key"].split(",", 1)[0].split(":", 1)[0]
            if code in held and record["expires"] is not None and now < record["expires"]:
                groups.setdefault((record["capability"], code), []).append(record)
        for group in groups.values():
            newest = max(group, key=lambda row: row["fetched"] or datetime.min.replace(tzinfo=timezone.utc))
            pinned_paths.add(newest["path"])

        for record in records:
            category = categories[record["category"]]
            category["bytes"] += record["size"]
            category["file_count"] += 1
            if record["path"] in pinned_paths:
                category["pinned_count"] += 1
            retention = RETENTION_AFTER_EXPIRY.get(record["category"])
            expired = bool(
                retention is not None
                and record["expires"] is not None
                and now > record["expires"] + retention
                and record["path"] not in pinned_paths
            )
            if expired:
                category["expired_count"] += 1
                category["reclaimable_bytes"] += record["size"]
                candidates.append({
                    "kind": "file", "category": record["category"], "path": record["path"],
                    "root": self.fund_root, "size": record["size"],
                    "time": (record["expires"] or now).timestamp(),
                })

    def _scan_translations(self, categories: dict[str, dict[str, int]], candidates: list[dict]) -> None:
        path = self.translation_file
        if not path.exists() or not self._contained(path, path.parent):
            return
        category = categories["translations"]
        try:
            size = path.stat().st_size
            document = json.loads(path.read_text(encoding="utf-8"))
            entries = document.get("entries") or {}
            if not isinstance(entries, dict):
                raise ValueError("invalid translation cache")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            category["bytes"] += path.stat().st_size if path.exists() else 0
            category["file_count"] += int(path.exists())
            return
        category["bytes"] += size
        category["file_count"] += 1
        cutoff = _aware(self._now()) - timedelta(days=90)
        for digest, entry in entries.items():
            accessed = _parse_time(entry.get("last_accessed_at") if isinstance(entry, dict) else None)
            if accessed is None or accessed >= cutoff:
                continue
            estimate = len(json.dumps({digest: entry}, ensure_ascii=False).encode("utf-8"))
            category["expired_count"] += 1
            category["reclaimable_bytes"] += estimate
            candidates.append({
                "kind": "translation", "category": "translations", "key": digest,
                "path": path, "root": path.parent, "size": estimate, "time": accessed.timestamp(),
            })

    def _scan_owned_files(
        self,
        root: Path,
        category_name: str,
        max_age: timedelta,
        categories: dict[str, dict[str, int]],
        candidates: list[dict],
    ) -> None:
        if not root.exists():
            return
        now = _aware(self._now())
        rows: list[tuple[Path, os.stat_result]] = []
        for path in root.rglob("*"):
            if not path.is_file() or not self._contained(path, root):
                continue
            try:
                rows.append((path, path.stat()))
            except OSError:
                continue
        category = categories[category_name]
        category["bytes"] += sum(stat.st_size for _, stat in rows)
        category["file_count"] += len(rows)

        log_overflow_paths: set[Path] = set()
        if category_name == "logs":
            remaining = sum(stat.st_size for _, stat in rows)
            for path, stat in sorted(rows, key=lambda row: row[1].st_mtime):
                if remaining <= self.log_max_bytes:
                    break
                log_overflow_paths.add(path)
                remaining -= stat.st_size

        for path, stat in rows:
            modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            expired = now > modified + max_age or path in log_overflow_paths
            if not expired:
                continue
            category["expired_count"] += 1
            category["reclaimable_bytes"] += stat.st_size
            candidates.append({
                "kind": "file", "category": category_name, "path": path, "root": root,
                "size": stat.st_size, "time": stat.st_mtime,
            })

    def _scan(self) -> tuple[dict[str, Any], list[dict]]:
        categories = {name: _empty_category() for name in (*CATEGORY_ORDER, "unclassified_fund")}
        candidates: list[dict] = []
        self._scan_fund_files(categories, candidates)
        self._scan_translations(categories, candidates)
        self._scan_owned_files(self.acceptance_root, "temporary", timedelta(days=7), categories, candidates)
        self._scan_owned_files(self.logs_root, "logs", timedelta(days=14), categories, candidates)
        total_bytes = sum(row["bytes"] for row in categories.values())
        state = self._read_state()
        status = {
            "total_bytes": total_bytes,
            "file_count": sum(row["file_count"] for row in categories.values()),
            "expired_count": sum(row["expired_count"] for row in categories.values()),
            "reclaimable_bytes": sum(row["reclaimable_bytes"] for row in categories.values()),
            "categories": categories,
            "last_auto_cleanup_at": state.get("last_auto_cleanup_at"),
            "limit_bytes": self.max_bytes,
            "over_limit_bytes": max(0, total_bytes - self.max_bytes),
        }
        return status, candidates

    def status(self) -> dict[str, Any]:
        return self._scan()[0]

    def _remove_translation_entries(self, keys: set[str]) -> int:
        try:
            before = self.translation_file.stat().st_size
            document = json.loads(self.translation_file.read_text(encoding="utf-8"))
            entries = document.get("entries") or {}
            for key in keys:
                entries.pop(key, None)
            document["entries"] = entries
            self._atomic_write(self.translation_file, document)
            return max(0, before - self.translation_file.stat().st_size)
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return 0

    def cleanup_expired(self, manual: bool = False) -> dict[str, Any]:
        _, candidates = self._scan()
        priority = {name: index for index, name in enumerate(CATEGORY_ORDER)}
        candidates.sort(key=lambda row: (priority.get(row["category"], 99), row["time"]))
        released = 0
        deleted_categories: list[str] = []
        translation_keys: set[str] = set()
        for candidate in candidates:
            category = candidate["category"]
            if candidate["kind"] == "translation":
                translation_keys.add(candidate["key"])
                continue
            path = candidate["path"]
            root = candidate["root"]
            if not self._contained(path, root):
                continue
            try:
                size = path.stat().st_size
                path.unlink()
                released += size
                if category not in deleted_categories:
                    deleted_categories.append(category)
            except (FileNotFoundError, OSError):
                continue
            if category == "temporary":
                parent = path.parent
                while parent != self.acceptance_root and self._contained(parent, self.acceptance_root):
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent

        if translation_keys:
            translation_released = self._remove_translation_entries(translation_keys)
            if translation_released or translation_keys:
                released += translation_released
                insert_at = min(1, len(deleted_categories))
                deleted_categories.insert(insert_at, "translations")

        # Preserve the documented priority even though translation entries are rewritten in one atomic batch.
        deleted_categories = sorted(dict.fromkeys(deleted_categories), key=lambda name: priority.get(name, 99))
        return {
            "manual": manual,
            "released_bytes": released,
            "deleted_categories": deleted_categories,
            "status": self.status(),
        }

    def maybe_auto_cleanup(self) -> dict[str, Any]:
        now = _aware(self._now())
        last = _parse_time(self._read_state().get("last_auto_cleanup_at"))
        if last is not None and now - last < timedelta(hours=24):
            return {"ran": False, "released_bytes": 0, "status": self.status()}
        result = self.cleanup_expired(manual=False)
        self._atomic_write(self.state_file, {
            "last_auto_cleanup_at": now.isoformat(),
            "last_released_bytes": result["released_bytes"],
        })
        return {"ran": True, "released_bytes": result["released_bytes"], "status": self.status()}


_manager: CacheManager | None = None


def get_manager() -> CacheManager:
    global _manager
    if _manager is None:
        _manager = CacheManager()
    return _manager

