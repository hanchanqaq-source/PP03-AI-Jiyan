from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import threading
from typing import Any, Callable, Iterable, Literal, Mapping

from cache_io_lock import CACHE_IO_LOCK
from fund_data.service import default_fund_providers

from .models import ProbeObservation
from .registry import build_registry, load_news_config, news_source_id, public_source_reference
from .runner import RunScope, SourceHealthRunner
from .storage import SourceHealthStorage


class FullRunConflict(RuntimeError):
    pass


def _empty_counts() -> dict[str, int]:
    return {"healthy": 0, "usable": 0, "degraded": 0, "failed": 0}


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _load_reliable_news_cache_source_ids(
    cache_path: Path,
    *,
    now: datetime,
    radar_source_identities: Mapping[str, tuple[str, str]],
) -> set[str]:
    try:
        with CACHE_IO_LOCK:
            document = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    if not isinstance(document, dict):
        return set()
    cache_status = document.get("cache_status")
    if not isinstance(cache_status, str) or cache_status not in {"cache", "partial", "realtime"}:
        return set()
    generated_at = _parse_timestamp(document.get("generated_at"))
    if generated_at is None:
        return set()
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    raw_recent_days = document.get("recent_days")
    if not isinstance(raw_recent_days, int) or isinstance(raw_recent_days, bool):
        return set()
    recent_days = raw_recent_days
    if not 1 <= recent_days <= 90:
        return set()
    cache_age = current.astimezone(timezone.utc) - generated_at.astimezone(timezone.utc)
    if cache_age < timedelta(0) or cache_age > timedelta(days=recent_days):
        return set()
    source_statuses = document.get("source_statuses")
    if not isinstance(source_statuses, list):
        return set()
    reliable: set[str] = set()
    for row in source_statuses:
        if not isinstance(row, dict):
            continue
        radar_source_id = str(row.get("source_id") or "")
        identity = radar_source_identities.get(radar_source_id)
        if identity is None:
            continue
        health_source_id, expected_public_reference = identity
        source_url = str(row.get("source_url") or "")
        try:
            actual_public_reference = public_source_reference(source_url)
        except ValueError:
            continue
        if actual_public_reference != expected_public_reference:
            continue
        raw_item_count = row.get("item_count")
        if not isinstance(raw_item_count, int) or isinstance(raw_item_count, bool):
            continue
        item_count = raw_item_count
        if (
            row.get("used_cached_items") is True
            and item_count > 0
            and _parse_timestamp(row.get("last_success_at")) is not None
        ):
            reliable.add(health_source_id)
    return reliable


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
        import newsradar
        from data_sources.health_bridge import news_adapter_id

        providers = default_fund_providers()
        news_config = load_news_config()
        descriptors = build_registry(providers, news_config)
        news_sources = {
            f"{news_adapter_id(row)}:feed": row
            for row in news_config.get("sources") or []
            if row.get("hint") and row.get("name") and row.get("url")
        }
        descriptors_by_id = {descriptor.source_id: descriptor for descriptor in descriptors}
        radar_source_identities: dict[str, tuple[str, str]] = {}
        for source in news_config.get("sources") or []:
            if not isinstance(source, dict):
                continue
            hint = str(source.get("hint") or "")
            name = str(source.get("name") or "")
            url = str(source.get("url") or "")
            health_source_id = f"{news_adapter_id(source)}:feed"
            descriptor = descriptors_by_id.get(health_source_id)
            if descriptor is not None:
                radar_source_identities[newsradar.source_id(source)] = (
                    health_source_id,
                    descriptor.source_reference,
                )
        timeout = float((news_config.get("fetch") or {}).get("timeout") or 15)
        return SourceHealthRunner(
            descriptors,
            providers=providers,
            news_sources=news_sources,
            news_timeout=timeout,
            now=self._now,
            reliable_cache_source_ids=lambda: _load_reliable_news_cache_source_ids(
                Path(newsradar.CACHE_FILE),
                now=self._now(),
                radar_source_identities=radar_source_identities,
            ),
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

    def _selected_descriptors(
        self,
        scope: RunScope,
        excluded_adapter_ids: tuple[str, ...],
    ) -> list[Any]:
        if not excluded_adapter_ids:
            return self.runner.select(scope)
        return self.runner.select(scope, excluded_adapter_ids=excluded_adapter_ids)

    def _new_run(self, scope: RunScope, excluded_adapter_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        return {
            "run_id": secrets.token_hex(10),
            "scope": scope,
            "status": "queued",
            "started_at": self._now().isoformat(),
            "finished_at": None,
            "total": len(self._selected_descriptors(scope, excluded_adapter_ids)),
            "completed": 0,
            "success": 0,
            "partial": 0,
            "failure": 0,
            "current_source": "",
            "excluded_adapter_ids": list(excluded_adapter_ids),
        }

    def _start_locked(self, scope: RunScope, excluded_adapter_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        if self._shutdown:
            raise RuntimeError("source-health service is shutdown")
        if scope == "full" and self._active_full is not None:
            active = self._runs.get(self._active_full)
            if active and active.get("status") in {"queued", "running"}:
                raise FullRunConflict("数据源体检正在运行")
        run = self._new_run(scope, excluded_adapter_ids)
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

    def start_run(
        self,
        scope: RunScope = "full",
        *,
        excluded_adapter_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        excluded = tuple(sorted({str(adapter_id) for adapter_id in excluded_adapter_ids if str(adapter_id)}))
        with self._lock:
            return self._start_locked(scope, excluded)

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
            excluded_adapter_ids = tuple(str(adapter_id) for adapter_id in run.get("excluded_adapter_ids") or ())
        try:
            history_documents = self.storage.load_history(now=self._now())
            run_kwargs = {
                "on_probe_complete": lambda row: self._record_result(run_id, row),
                "history_documents": history_documents,
            }
            if excluded_adapter_ids:
                run_kwargs["excluded_adapter_ids"] = excluded_adapter_ids
            observations = self.runner.run(scope, **run_kwargs)
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
            summary = json.loads(json.dumps(self._summary, ensure_ascii=False))
            observed = {
                "fund": sum(row.get("group") != "news" for row in self._sources),
                "news": sum(row.get("group") == "news" for row in self._sources),
            }
        registered = {"fund": 0, "news": 0}
        registry_known = True
        try:
            descriptors = self.runner.select("full")
        except Exception:
            descriptors = []
            registry_known = False
        for descriptor in descriptors:
            group = getattr(descriptor, "group", None)
            if group not in {"fund", "quote", "industry", "news"}:
                registry_known = False
                continue
            registered["news" if group == "news" else "fund"] += 1
        summary["group_status"] = {
            group: {
                "registered": registered[group] if registry_known else None,
                "observed": observed[group],
                "loaded": observed[group] > 0 or (registry_known and registered[group] == 0),
            }
            for group in ("fund", "news")
        }
        return summary

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
