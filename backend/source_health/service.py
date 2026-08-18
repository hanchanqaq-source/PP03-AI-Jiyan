from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import threading
from typing import Any, Callable, Literal

from cache_io_lock import CACHE_IO_LOCK
from fund_data.service import default_fund_providers

from .models import ProbeObservation
from .registry import build_registry, load_news_config, news_source_id
from .runner import RunScope, SourceHealthRunner
from .storage import SourceHealthStorage


class FullRunConflict(RuntimeError):
    pass


def _empty_counts() -> dict[str, int]:
    return {"healthy": 0, "usable": 0, "degraded": 0, "failed": 0}


class SourceHealthService:
    def __init__(
        self,
        *,
        runner: SourceHealthRunner | Any | None = None,
        storage: SourceHealthStorage | None = None,
        now: Callable[[], datetime] | None = None,
        reclaimable_bytes: Callable[[], int] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.storage = storage or SourceHealthStorage()
        self.runner = runner or self._default_runner()
        self._reclaimable_bytes = reclaimable_bytes or self._default_reclaimable_bytes
        self._runs: dict[str, dict[str, Any]] = {}
        self._sources: list[dict[str, Any]] = []
        self._summary = self._empty_summary()
        self._lock = threading.RLock()
        self._coordinator = ThreadPoolExecutor(max_workers=1, thread_name_prefix="source-health-run")
        self._active_full: str | None = None
        self._quick_pending = False
        self._last_quick_run_at: datetime | None = None
        self._shutdown = False
        self._restore_state()

    def _default_runner(self) -> SourceHealthRunner:
        providers = default_fund_providers()
        news_config = load_news_config()
        news_sources = {
            news_source_id(str(row.get("hint") or ""), str(row.get("name") or ""), str(row.get("url") or "")): row
            for row in news_config.get("sources") or []
            if row.get("hint") and row.get("name") and row.get("url")
        }
        timeout = float((news_config.get("fetch") or {}).get("timeout") or 15)
        return SourceHealthRunner(
            build_registry(providers, news_config),
            providers=providers,
            news_sources=news_sources,
            news_timeout=timeout,
            now=self._now,
        )

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            with CACHE_IO_LOCK:
                value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _restore_state(self) -> None:
        snapshot_run_id = ""
        snapshot = self._read_json(self.storage.current_snapshot_path)
        if snapshot:
            summary = snapshot.get("summary")
            sources = snapshot.get("sources")
            completed_run = snapshot.get("run")
            if isinstance(summary, dict) and isinstance(sources, list) and isinstance(completed_run, dict):
                self._summary = summary
                self._sources = [dict(row) for row in sources if isinstance(row, dict)]
                snapshot_run_id = str(completed_run.get("run_id") or "")
                if snapshot_run_id:
                    self._runs[snapshot_run_id] = completed_run
        last_run = self._read_json(self.storage.last_run_path)
        if last_run:
            run_id = str(last_run.get("run_id") or "")
            if run_id and run_id != snapshot_run_id:
                self._runs[run_id] = last_run
        admission = self._read_json(self.storage.quick_admission_path)
        stamp = admission.get("scheduled_at") if admission else None
        if not stamp and last_run:
            stamp = last_run.get("last_quick_run_at")
            if not stamp and last_run.get("scope") == "quick" and last_run.get("status") == "completed":
                stamp = last_run.get("finished_at")
        if stamp:
            try:
                self._last_quick_run_at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                pass

    def _default_reclaimable_bytes(self) -> int:
        try:
            import cache_management

            return max(0, int(cache_management.get_manager().status().get("reclaimable_bytes") or 0))
        except Exception:
            return 0

    def _empty_summary(self) -> dict[str, Any]:
        return {
            "rating_confidence": "initial",
            "last_run_at": None,
            "fund": _empty_counts(),
            "news": _empty_counts(),
            "total_sources": 0,
            "reclaimable_bytes": 0,
        }

    def _new_run(self, scope: RunScope) -> dict[str, Any]:
        return {
            "run_id": secrets.token_hex(10),
            "scope": scope,
            "status": "queued",
            "started_at": self._now().isoformat(),
            "finished_at": None,
            "total": len(self.runner.select(scope)),
            "completed": 0,
            "success": 0,
            "partial": 0,
            "failure": 0,
            "current_source": "",
        }

    def _start_locked(self, scope: RunScope) -> dict[str, Any]:
        if self._shutdown:
            raise RuntimeError("source-health service is shutdown")
        if scope == "full" and self._active_full is not None:
            active = self._runs.get(self._active_full)
            if active and active.get("status") in {"queued", "running"}:
                raise FullRunConflict("数据源体检正在运行")
        run = self._new_run(scope)
        self._runs[run["run_id"]] = run
        if scope == "full":
            self._active_full = run["run_id"]
        else:
            scheduled_at = self._now()
            self.storage.write_quick_admission({
                "run_id": run["run_id"],
                "scheduled_at": scheduled_at.isoformat(),
            })
            self._quick_pending = True
            self._last_quick_run_at = scheduled_at
        self._coordinator.submit(self._execute, run["run_id"])
        return dict(run)

    def start_run(self, scope: RunScope = "full") -> dict[str, Any]:
        with self._lock:
            return self._start_locked(scope)

    def schedule_quick_if_due(self) -> dict[str, Any] | None:
        with self._lock:
            if self._shutdown or self._quick_pending:
                return None
            if self._last_quick_run_at is not None:
                current = self._now()
                last = self._last_quick_run_at
                if current.tzinfo is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=current.tzinfo)
                if current - last < timedelta(hours=24):
                    return None
            return self._start_locked("quick")

    def _record_result(self, run_id: str, observation: ProbeObservation) -> None:
        with self._lock:
            run = self._runs[run_id]
            run["completed"] += 1
            run[observation.probe_status] += 1
            run["current_source"] = observation.source_name

    def _build_summary(self, sources: list[dict[str, Any]], finished_at: str) -> dict[str, Any]:
        fund = _empty_counts()
        news = _empty_counts()
        for source in sources:
            target = news if source.get("group") == "news" else fund
            target[str(source.get("rating") or "failed")] += 1
        confidence_order = {"initial": 0, "growing": 1, "stable": 2}
        confidence = min(
            (str(source.get("rating_confidence") or "initial") for source in sources),
            key=lambda value: confidence_order.get(value, 0),
            default="initial",
        )
        return {
            "rating_confidence": confidence,
            "last_run_at": finished_at,
            "fund": fund,
            "news": news,
            "total_sources": len(sources),
            "reclaimable_bytes": self._reclaimable_bytes(),
        }

    def _execute(self, run_id: str) -> None:
        with self._lock:
            run = self._runs[run_id]
            run["status"] = "running"
            scope: RunScope = run["scope"]
        try:
            history_documents = self.storage.load_history(now=self._now())
            observations = self.runner.run(
                scope,
                on_result=lambda row: self._record_result(run_id, row),
                history_documents=history_documents,
            )
            finished_at = self._now().isoformat()
            refreshed_rows = [row.to_dict() for row in observations]
            with self._lock:
                if scope == "quick":
                    merged = {row["source_id"]: dict(row) for row in self._sources}
                    for row in refreshed_rows:
                        merged[row["source_id"]] = row
                    source_rows = list(merged.values())
                else:
                    source_rows = refreshed_rows
                completed_run = {
                    **self._runs[run_id],
                    "status": "completed",
                    "finished_at": finished_at,
                }
                persisted_run = dict(completed_run)
                if self._last_quick_run_at is not None:
                    persisted_run["last_quick_run_at"] = self._last_quick_run_at.isoformat()
            summary = self._build_summary(source_rows, finished_at)
            for source in refreshed_rows:
                self.storage.append_history(
                    {"run_id": run_id, "scope": scope, **source},
                    observed_at=finished_at,
                )
            self.storage.write_current_summary(summary)
            self.storage.write_current_snapshot({
                "run": persisted_run,
                "summary": summary,
                "sources": source_rows,
            })
            self.storage.write_last_run(persisted_run)
            with self._lock:
                self._sources = source_rows
                self._summary = summary
                self._runs[run_id] = completed_run
        except Exception:
            finished_at = self._now().isoformat()
            with self._lock:
                run = self._runs[run_id]
                run["status"] = "failed"
                run["finished_at"] = finished_at
                persisted_run = dict(run)
                if self._last_quick_run_at is not None:
                    persisted_run["last_quick_run_at"] = self._last_quick_run_at.isoformat()
            try:
                self.storage.write_last_run(persisted_run)
            except Exception:
                pass
        finally:
            with self._lock:
                if scope == "full" and self._active_full == run_id:
                    self._active_full = None
                if scope == "quick":
                    self._quick_pending = False

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return dict(run) if run is not None else None

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._summary, ensure_ascii=False))

    def list_sources(
        self,
        *,
        group: str | None = None,
        rating: str | None = None,
        repair_value: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._sources)
        return [
            dict(row) for row in rows
            if (group is None or row.get("group") == group)
            and (rating is None or row.get("rating") == rating)
            and (repair_value is None or row.get("repair_value") == repair_value)
        ]

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        self._coordinator.shutdown(wait=False, cancel_futures=True)
        self.runner.shutdown()


_service: SourceHealthService | None = None
_service_lock = threading.Lock()


def get_service() -> SourceHealthService:
    global _service
    with _service_lock:
        if _service is None:
            _service = SourceHealthService()
        return _service


def reset_service() -> None:
    global _service
    with _service_lock:
        current, _service = _service, None
    if current is not None:
        current.shutdown()
