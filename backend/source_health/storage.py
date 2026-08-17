from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping

from cache_io_lock import CACHE_IO_LOCK


HISTORY_RETENTION_DAYS = 90


def _json_document(value: Mapping[str, Any] | object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        document = to_dict()
        if isinstance(document, dict):
            return document
    raise TypeError("source-health documents must be mappings or expose to_dict()")


def _date_value(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


class SourceHealthStorage:
    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        data_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        if root is not None and data_dir is not None:
            raise ValueError("pass root or data_dir, not both")
        if root is not None:
            self.root = Path(root)
        else:
            configured_data = data_dir or os.environ.get("VR_DATA_DIR")
            if configured_data:
                self.root = Path(configured_data) / "source-health"
            else:
                profile = Path(os.environ.get("USERPROFILE") or Path.home())
                self.root = profile / ".vibe-research" / "source-health"
        self.current_summary_path = self.root / "current-summary.json"
        self.last_run_path = self.root / "last-run.json"
        self.history_root = self.root / "history"

    def _atomic_write(self, path: Path, document: Mapping[str, Any] | object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(_json_document(document), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise

    def write_current_summary(self, document: Mapping[str, Any] | object) -> Path:
        with CACHE_IO_LOCK:
            self._atomic_write(self.current_summary_path, document)
        return self.current_summary_path

    def write_last_run(self, document: Mapping[str, Any] | object) -> Path:
        with CACHE_IO_LOCK:
            self._atomic_write(self.last_run_path, document)
        return self.last_run_path

    def history_path(self, observed_date: date | datetime | str) -> Path:
        return self.history_root / f"{_date_value(observed_date).isoformat()}.jsonl"

    def append_history(
        self,
        document: Mapping[str, Any] | object,
        *,
        observed_at: date | datetime | str | None = None,
    ) -> Path:
        timestamp = observed_at or datetime.now(timezone.utc)
        path = self.history_path(timestamp)
        encoded = json.dumps(_json_document(document), ensure_ascii=False, separators=(",", ":")) + "\n"
        with CACHE_IO_LOCK:
            self.history_root.mkdir(parents=True, exist_ok=True)
            self._cleanup_history(datetime.now(timezone.utc))
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
        return path

    def _cleanup_history(self, now: date | datetime) -> list[Path]:
        if not self.history_root.exists():
            return []
        cutoff = _date_value(now) - timedelta(days=HISTORY_RETENTION_DAYS)
        removed: list[Path] = []
        for path in sorted(self.history_root.glob("*.jsonl")):
            if not path.is_file():
                continue
            try:
                observed_date = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if observed_date >= cutoff:
                continue
            try:
                path.unlink()
                removed.append(path)
            except FileNotFoundError:
                continue
        return removed

    def cleanup_history(self, *, now: date | datetime | None = None) -> list[Path]:
        with CACHE_IO_LOCK:
            return self._cleanup_history(now or datetime.now(timezone.utc))

    def load_history(
        self,
        *,
        now: date | datetime | None = None,
        days: int = HISTORY_RETENTION_DAYS,
    ) -> list[dict[str, Any]]:
        if days < 1 or days > HISTORY_RETENTION_DAYS:
            raise ValueError(f"days must be between 1 and {HISTORY_RETENTION_DAYS}")
        end = _date_value(now or datetime.now(timezone.utc))
        start = end - timedelta(days=days - 1)
        documents: list[dict[str, Any]] = []
        with CACHE_IO_LOCK:
            for offset in range(days):
                path = self.history_path(start + timedelta(days=offset))
                try:
                    lines: Iterable[str] = path.read_text(encoding="utf-8").splitlines()
                except (FileNotFoundError, OSError, UnicodeDecodeError):
                    continue
                for line in lines:
                    try:
                        document = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(document, dict):
                        documents.append(document)
        return documents
