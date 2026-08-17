from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any, Callable, Iterable, Literal

from .models import ProbeObservation, SourceDescriptor
from .probe_errors import classify_probe_error
from .probes import probe_news_source, probe_provider_capability
from .repair_advisor import apply_repair_advice
from .scoring import score_observation


RunScope = Literal["quick", "full"]
ResultCallback = Callable[[ProbeObservation], None]


class SourceHealthRunner:
    """Run source probes within the documented concurrency boundaries."""

    def __init__(
        self,
        descriptors: Iterable[SourceDescriptor],
        *,
        providers: Iterable[Any],
        news_sources: dict[str, dict[str, Any]],
        provider_probe: Callable[..., dict[str, Any]] = probe_provider_capability,
        news_probe: Callable[..., dict[str, Any]] = probe_news_source,
        now: Callable[[], datetime] | None = None,
        news_timeout: float = 15,
        fund_workers: int = 4,
        news_workers: int = 20,
        sample_path: str | Path | None = None,
    ) -> None:
        self._descriptors = list(descriptors)
        self._providers = {
            str(getattr(provider, "name", type(provider).__name__)): provider
            for provider in providers
        }
        self._news_sources = {key: dict(value) for key, value in news_sources.items()}
        self._provider_probe = provider_probe
        self._news_probe = news_probe
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._news_timeout = float(news_timeout)
        self._fund_pool = ThreadPoolExecutor(max_workers=fund_workers, thread_name_prefix="source-health-fund")
        self._news_pool = ThreadPoolExecutor(max_workers=news_workers, thread_name_prefix="source-health-news")
        self._provider_locks = {name: threading.Lock() for name in self._providers}
        self._shutdown = False
        self._sample_path = Path(sample_path) if sample_path else Path(__file__).with_name("source-health-samples.json")
        self._sample_code: str | None = None

    def select(self, scope: RunScope) -> list[SourceDescriptor]:
        if scope == "quick":
            return [descriptor for descriptor in self._descriptors if descriptor.critical]
        if scope == "full":
            return list(self._descriptors)
        raise ValueError(f"unsupported source-health scope: {scope}")

    def _public_sample_code(self) -> str:
        if self._sample_code is not None:
            return self._sample_code
        code = "000001"
        try:
            document = json.loads(self._sample_path.read_text(encoding="utf-8"))
            for row in document if isinstance(document, list) else []:
                candidate = str(row.get("code") or "") if isinstance(row, dict) else ""
                if len(candidate) == 6 and candidate.isdigit() and row.get("sample_status") == "available":
                    code = candidate
                    break
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        self._sample_code = code
        return code

    def _probe_args(self, descriptor: SourceDescriptor) -> dict[str, Any]:
        if descriptor.probe_args and set(descriptor.probe_args) != {"hint"}:
            return dict(descriptor.probe_args)
        sample_code = self._public_sample_code()
        if descriptor.capability == "search":
            return {"query": sample_code}
        if descriptor.capability in {"stock_snapshot", "stock_industry_classification"}:
            return {"codes": ["600000"]}
        return {"code": sample_code}

    def _fallback_available(self, descriptor: SourceDescriptor) -> bool:
        return sum(
            row.capability == descriptor.capability and row.source_id != descriptor.source_id
            for row in self._descriptors
        ) > 0

    def _failure(self, descriptor: SourceDescriptor, started_at: datetime, error: BaseException) -> ProbeObservation:
        classified = classify_probe_error(error)
        finished_at = self._now()
        return ProbeObservation(
            source_id=descriptor.source_id,
            source_name=descriptor.source_name,
            group=descriptor.group,
            capability=descriptor.capability,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            latency_ms=max(0, round((finished_at - started_at).total_seconds() * 1000)),
            probe_status="failure",
            error_type=classified.error_type,
            error_message_redacted=classified.message or type(error).__name__,
            http_status=classified.http_status,
            returned_items=0,
            data_as_of_date=None,
            freshness_seconds=None,
            field_completeness_pct=0.0,
            used_cache=False,
            cache_status="not_used",
            fallback_available=self._fallback_available(descriptor),
            redirected=False,
            final_reference=None,
        )

    def _observation(
        self,
        descriptor: SourceDescriptor,
        raw: dict[str, Any],
        started_at: datetime,
    ) -> ProbeObservation:
        finished_at = self._now()
        status = str(raw.get("status") or "failure")
        if status not in {"success", "partial", "failure"}:
            status = "failure"
        data_as_of = raw.get("data_as_of_date") or raw.get("latest_published_at")
        freshness_seconds = None
        if data_as_of:
            try:
                observed = datetime.fromisoformat(str(data_as_of).replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                current = finished_at if finished_at.tzinfo else finished_at.replace(tzinfo=timezone.utc)
                freshness_seconds = max(0, round((current - observed).total_seconds()))
            except ValueError:
                freshness_seconds = None
        observation = ProbeObservation(
            source_id=descriptor.source_id,
            source_name=str(raw.get("source_name") or descriptor.source_name),
            group=descriptor.group,
            capability=descriptor.capability,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            latency_ms=max(0, int(raw.get("latency_ms") or 0)),
            probe_status=status,
            error_type=str(raw.get("error_type") or ("none" if status == "success" else "unknown")),
            error_message_redacted=str(raw.get("error_message_redacted") or ""),
            http_status=raw.get("http_status"),
            returned_items=max(0, int(raw.get("returned_items") or 0)),
            data_as_of_date=str(data_as_of) if data_as_of else None,
            freshness_seconds=freshness_seconds,
            field_completeness_pct=(
                float(raw["field_completeness_pct"])
                if raw.get("field_completeness_pct") is not None
                else (100.0 if status == "success" else 0.0)
            ),
            used_cache=bool(raw.get("used_cache", False)),
            cache_status=str(raw.get("cache_status") or "not_used"),
            fallback_available=self._fallback_available(descriptor),
            redirected=bool(raw.get("redirected", False)),
            final_reference=raw.get("final_reference") or raw.get("final_url"),
        )
        score_observation(
            observation,
            freshness_max_age_seconds=descriptor.freshness_max_age_seconds,
            freshness_applicable=descriptor.freshness_max_age_seconds is not None,
            fallback_applicable=True,
        )
        return apply_repair_advice(observation)

    def _run_one(self, descriptor: SourceDescriptor) -> ProbeObservation:
        started_at = self._now()
        try:
            if descriptor.group == "news":
                source = self._news_sources[descriptor.source_id]
                raw = self._news_probe(source, timeout=self._news_timeout)
            else:
                provider = self._providers[descriptor.source_name]
                call = lambda: self._provider_probe(
                    provider,
                    descriptor.capability,
                    probe_args=self._probe_args(descriptor),
                    freshness_max_age_seconds=descriptor.freshness_max_age_seconds,
                    now=self._now(),
                )
                if getattr(provider, "thread_safe", False):
                    raw = call()
                else:
                    with self._provider_locks[descriptor.source_name]:
                        raw = call()
            return self._observation(descriptor, raw, started_at)
        except Exception as error:
            observation = self._failure(descriptor, started_at, error)
            score_observation(
                observation,
                freshness_max_age_seconds=None,
                freshness_applicable=False,
                fallback_applicable=True,
            )
            return apply_repair_advice(observation)

    def run(self, scope: RunScope, *, on_result: ResultCallback | None = None) -> list[ProbeObservation]:
        if self._shutdown:
            raise RuntimeError("source-health runner is shutdown")
        futures: list[Future[ProbeObservation]] = []
        for descriptor in self.select(scope):
            pool = self._news_pool if descriptor.group == "news" else self._fund_pool
            futures.append(pool.submit(self._run_one, descriptor))
        observations: list[ProbeObservation] = []
        for future in as_completed(futures):
            observation = future.result()
            observations.append(observation)
            if on_result is not None:
                on_result(observation)
        return observations

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._fund_pool.shutdown(wait=False, cancel_futures=True)
        self._news_pool.shutdown(wait=False, cancel_futures=True)
