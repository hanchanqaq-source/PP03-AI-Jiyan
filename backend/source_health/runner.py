from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any, Callable, Iterable, Literal, Mapping
from urllib.parse import urlsplit

from .models import ProbeObservation, SourceDescriptor
from .probe_errors import classify_probe_error
from .probes import probe_news_source, probe_provider_capability
from .repair_advisor import apply_repair_advice
from .registry import public_source_reference
from .scoring import score_observation


RunScope = Literal["quick", "full"]
ResultCallback = Callable[[ProbeObservation], None]


@dataclass(frozen=True)
class _ProbeOutcome:
    descriptor: SourceDescriptor
    observation: ProbeObservation
    raw: Mapping[str, Any]
    history_rows: list[dict[str, Any]]


def _history_observations(document: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    if document.get("source_id"):
        yield dict(document)
    for key in ("observation", "observations", "sources"):
        nested = document.get(key)
        rows = [nested] if isinstance(nested, Mapping) else nested if isinstance(nested, list) else []
        for row in rows:
            if isinstance(row, Mapping):
                yield from _history_observations(row)


def _history_timestamp(row: Mapping[str, Any]) -> datetime | None:
    for key in ("finished_at", "observed_at", "started_at"):
        value = row.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _same_public_source(left: str | None, right: str | None) -> bool:
    def host(value: str | None) -> str:
        parts = urlsplit(str(value or ""))
        if parts.scheme not in {"http", "https"}:
            return ""
        name = (parts.hostname or "").lower()
        return name[4:] if name.startswith("www.") else name

    return bool(host(left)) and host(left) == host(right)


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
        reliable_cache_source_ids: Iterable[str] | Callable[[], Iterable[str]] = (),
    ) -> None:
        self._descriptors = list(descriptors)
        identities = [
            (row.source_id, str(row.probe_args.get("configuration_identity") or ""))
            for row in self._descriptors
            if row.probe_args.get("configuration_identity")
        ]
        counts = Counter(identity for _source_id, identity in identities)
        self._duplicate_source_ids = {
            source_id for source_id, identity in identities if counts[identity] > 1
        }
        self._providers = {
            str(getattr(provider, "adapter_id", getattr(provider, "name", type(provider).__name__))): provider
            for provider in providers
        }
        self._news_sources = {key: dict(value) for key, value in news_sources.items()}
        self._provider_probe = provider_probe
        self._news_probe = news_probe
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._news_timeout = float(news_timeout)
        self._fund_pool = ThreadPoolExecutor(max_workers=fund_workers, thread_name_prefix="source-health-fund")
        self._news_pool = ThreadPoolExecutor(max_workers=news_workers, thread_name_prefix="source-health-news")
        self._provider_locks = {adapter_id: threading.Lock() for adapter_id in self._providers}
        self._shutdown = False
        self._sample_path = Path(sample_path) if sample_path else Path(__file__).with_name("source-health-samples.json")
        self._sample_code: str | None = None
        if callable(reliable_cache_source_ids):
            self._load_reliable_cache_source_ids = reliable_cache_source_ids
        else:
            configured_cache_source_ids = tuple(str(source_id) for source_id in reliable_cache_source_ids)
            self._load_reliable_cache_source_ids = lambda: configured_cache_source_ids

    def select(
        self,
        scope: RunScope,
        *,
        excluded_adapter_ids: Iterable[str] = (),
    ) -> list[SourceDescriptor]:
        excluded = {str(adapter_id) for adapter_id in excluded_adapter_ids if str(adapter_id)}
        if scope == "quick":
            selected = [descriptor for descriptor in self._descriptors if descriptor.critical]
        elif scope == "full":
            selected = list(self._descriptors)
        else:
            raise ValueError(f"unsupported source-health scope: {scope}")
        return [descriptor for descriptor in selected if descriptor.adapter_id not in excluded]

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
            source_family_id=descriptor.source_family_id,
            adapter_id=descriptor.adapter_id,
            capability_id=descriptor.capability_id,
            configured_reference=descriptor.configured_reference,
            observed_final_reference=None,
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
        raw_final_reference = raw.get("final_reference") or raw.get("final_url")
        observed_final_reference = None
        if (status == "success" or (status == "partial" and raw.get("redirected"))) and raw_final_reference:
            try:
                observed_final_reference = public_source_reference(str(raw_final_reference))
            except ValueError:
                observed_final_reference = None
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
            final_reference=observed_final_reference,
            source_family_id=descriptor.source_family_id,
            adapter_id=descriptor.adapter_id,
            capability_id=descriptor.capability_id,
            configured_reference=descriptor.configured_reference,
            observed_final_reference=observed_final_reference,
        )
        return observation

    def _finalize_observation(
        self,
        descriptor: SourceDescriptor,
        observation: ProbeObservation,
        raw: Mapping[str, Any],
        history_rows: list[dict[str, Any]],
    ) -> ProbeObservation:
        current = observation.to_dict()
        records = [*history_rows, current]
        records.sort(key=lambda row: _history_timestamp(row) or datetime.min.replace(tzinfo=timezone.utc))
        trailing_failures: list[dict[str, Any]] = []
        for row in reversed(records):
            if row.get("probe_status") != "failure":
                break
            trailing_failures.append(row)
        observation.consecutive_failures = len(trailing_failures)
        successful = [row for row in records if row.get("probe_status") == "success" and _history_timestamp(row)]
        if successful:
            latest_success = max(successful, key=lambda row: _history_timestamp(row) or datetime.min.replace(tzinfo=timezone.utc))
            observation.last_success_at = str(latest_success.get("finished_at") or latest_success.get("observed_at") or latest_success.get("started_at"))
        observation_dates = [stamp.date() for row in records if (stamp := _history_timestamp(row)) is not None]
        score_observation(
            observation,
            freshness_max_age_seconds=descriptor.freshness_max_age_seconds,
            freshness_applicable=descriptor.freshness_max_age_seconds is not None,
            fallback_applicable=True,
            observation_dates=observation_dates,
            sample_count=len(records),
        )
        return observation

    def _apply_advice(
        self,
        outcome: _ProbeOutcome,
        *,
        reliable_fallback_available: bool,
        reliable_cache_source_ids: set[str],
    ) -> ProbeObservation:
        descriptor = outcome.descriptor
        observation = outcome.observation
        raw = outcome.raw
        history_rows = outcome.history_rows
        records = [*history_rows, observation.to_dict()]
        records.sort(key=lambda row: _history_timestamp(row) or datetime.min.replace(tzinfo=timezone.utc))
        trailing_failures: list[dict[str, Any]] = []
        for row in reversed(records):
            if row.get("probe_status") != "failure":
                break
            trailing_failures.append(row)
        failure_days = len({stamp.date() for row in trailing_failures if (stamp := _history_timestamp(row)) is not None})
        prior_same_failure = any(
            row.get("error_type") == observation.error_type and row.get("probe_status") == "failure"
            for row in history_rows
        )
        redirect_status = raw.get("redirect_status")
        if redirect_status is None and observation.http_status in {301, 308}:
            redirect_status = observation.http_status
        return apply_repair_advice(
            observation,
            permanent_redirect_same_public_source=(
                redirect_status in {301, 308}
                and observation.redirected
                and _same_public_source(descriptor.configured_reference, observation.observed_final_reference)
            ),
            missing_standard_request_headers=bool(raw.get("missing_standard_request_headers")),
            confirmed_compatibility_issue=bool(raw.get("confirmed_compatibility_issue")),
            source_id_conflict=bool(raw.get("source_id_conflict")),
            duplicate_configuration=descriptor.source_id in self._duplicate_source_ids,
            cache_status_mislabeled=bool(raw.get("cache_status_mislabeled")),
            high_value_source=descriptor.critical,
            reproducible_failure=prior_same_failure,
            public_entry_changed=bool(raw.get("public_entry_changed")),
            reliable_fallback_available=reliable_fallback_available,
            retry_after_present=bool(raw.get("retry_after_present")),
            reliable_cache_available=descriptor.source_id in reliable_cache_source_ids,
            domain_long_unresolvable=bool(raw.get("domain_long_unresolvable")),
            public_feed_removed=bool(raw.get("public_feed_removed")),
            failure_days=failure_days,
            requires_permission_bypass=bool(raw.get("requires_permission_bypass")),
            requires_tls_bypass=bool(raw.get("requires_tls_bypass")),
            duplicate_without_independent_track=bool(raw.get("duplicate_without_independent_track")),
            content_label_mismatch_long_term=bool(raw.get("content_label_mismatch_long_term")),
            compliance_issue=bool(raw.get("compliance_issue")),
        )

    def _run_one(self, descriptor: SourceDescriptor, history_rows: list[dict[str, Any]]) -> _ProbeOutcome:
        started_at = self._now()
        try:
            if descriptor.group == "news":
                source = self._news_sources[descriptor.source_id]
                raw = self._news_probe(source, timeout=self._news_timeout)
            else:
                adapter_id = descriptor.adapter_id or descriptor.source_name
                provider = self._providers[adapter_id]
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
                    with self._provider_locks[adapter_id]:
                        raw = call()
            observation = self._observation(descriptor, raw, started_at)
            self._finalize_observation(descriptor, observation, raw, history_rows)
            return _ProbeOutcome(descriptor, observation, raw, history_rows)
        except Exception as error:
            observation = self._failure(descriptor, started_at, error)
            self._finalize_observation(descriptor, observation, {}, history_rows)
            return _ProbeOutcome(descriptor, observation, {}, history_rows)

    def run(
        self,
        scope: RunScope,
        *,
        excluded_adapter_ids: Iterable[str] = (),
        on_probe_complete: ResultCallback | None = None,
        on_final_result: ResultCallback | None = None,
        history_documents: Iterable[Mapping[str, Any]] = (),
    ) -> list[ProbeObservation]:
        if self._shutdown:
            raise RuntimeError("source-health runner is shutdown")
        reliable_cache_source_ids = {
            str(source_id) for source_id in self._load_reliable_cache_source_ids()
        }
        history_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for document in history_documents:
            if not isinstance(document, Mapping):
                continue
            for row in _history_observations(document):
                source_id = str(row.get("source_id") or "")
                if source_id:
                    history_by_source[source_id].append(row)
        futures: list[Future[_ProbeOutcome]] = []
        for descriptor in self.select(scope, excluded_adapter_ids=excluded_adapter_ids):
            pool = self._news_pool if descriptor.group == "news" else self._fund_pool
            futures.append(pool.submit(self._run_one, descriptor, history_by_source[descriptor.source_id]))
        outcomes: list[_ProbeOutcome] = []
        for future in as_completed(futures):
            outcome = future.result()
            outcomes.append(outcome)
            if on_probe_complete is not None:
                on_probe_complete(outcome.observation)
        reliable_by_capability: dict[tuple[str, str], set[str]] = defaultdict(set)
        for outcome in outcomes:
            observation = outcome.observation
            if observation.rating in {"healthy", "usable"}:
                reliable_by_capability[(observation.group, observation.capability)].add(observation.source_id)
        observations: list[ProbeObservation] = []
        for outcome in outcomes:
            observation = outcome.observation
            reliable_fallback_available = any(
                source_id != observation.source_id
                for source_id in reliable_by_capability[(observation.group, observation.capability)]
            )
            observation = self._apply_advice(
                outcome,
                reliable_fallback_available=reliable_fallback_available,
                reliable_cache_source_ids=reliable_cache_source_ids,
            )
            observations.append(observation)
            if on_final_result is not None:
                on_final_result(observation)
        return observations

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._fund_pool.shutdown(wait=False, cancel_futures=True)
        self._news_pool.shutdown(wait=False, cancel_futures=True)
