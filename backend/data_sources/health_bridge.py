from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from source_health.models import ProbeObservation, SourceDescriptor


_EXCLUDED_CATALOG_STATUSES = {
    "unconfigured",
    "license_required",
    "catalog_only",
    "disabled",
}


def _status_name(status: Any) -> str:
    return str(getattr(status, "value", status))


def _news_configuration_identity(source: Mapping[str, Any]) -> str:
    exact_fields = tuple(
        str(source.get(key) or "").strip()
        for key in ("hint", "name", "url", "language", "region")
    )
    canonical = json.dumps(exact_fields, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def news_adapter_id(source: Mapping[str, Any]) -> str:
    return f"news-feed:{_news_configuration_identity(source)[:16]}"


def _provider_adapter_id(provider: Any) -> str:
    return str(getattr(provider, "adapter_id", getattr(provider, "name", type(provider).__name__)))


def _source_group(capability_id: str) -> str:
    if capability_id == "feed":
        return "news"
    if capability_id == "stock_snapshot":
        return "quote"
    if capability_id in {"industry_allocation", "stock_industry_classification"}:
        return "industry"
    return "fund"


@dataclass(frozen=True, slots=True)
class HealthOverlay:
    probe_status: str | None
    health_status: str
    observed_final_reference: str | None
    last_success_at: str | None
    latency_ms: int | None
    error_type: str | None
    error_message_redacted: str


@dataclass(frozen=True, slots=True)
class HealthRow:
    source_family_id: str
    adapter_id: str
    capability_id: str
    configured_reference: str
    source_reference: str
    source_id: str
    source_name: str
    overlay: HealthOverlay

    @property
    def observed_final_reference(self) -> str | None:
        return self.overlay.observed_final_reference

    @property
    def final_reference(self) -> str | None:
        return self.observed_final_reference

    @property
    def health_status(self) -> str:
        return self.overlay.health_status


@dataclass(frozen=True, slots=True)
class FamilyHealth:
    source_family_id: str
    health_status: str


class _AdapterRows:
    def __init__(self, rows: Sequence[HealthRow]) -> None:
        self._rows = tuple(rows)

    def capability(self, capability_id: str) -> HealthRow:
        matches = [row for row in self._rows if row.capability_id == capability_id]
        if len(matches) != 1:
            raise KeyError(f"No unique capability {capability_id!r} for adapter")
        return matches[0]


class MergedHealth:
    def __init__(self, catalog: Any, rows: Iterable[HealthRow]) -> None:
        self.rows = tuple(rows)
        self._catalog = catalog

    def adapter(self, adapter_id: str) -> _AdapterRows:
        return _AdapterRows([row for row in self.rows if row.adapter_id == adapter_id])

    def family(self, source_family_id: str) -> FamilyHealth:
        family_rows = [row for row in self.rows if row.source_family_id == source_family_id]
        statuses = [
            adapter.catalog_status
            for adapter in self._catalog.adapters_for_family(source_family_id)
            for _capability_id in adapter.capability_ids
        ]
        return FamilyHealth(
            source_family_id=source_family_id,
            health_status=family_health([row.overlay for row in family_rows], statuses),
        )


def catalog_probe_descriptors(
    catalog: Any,
    providers: Iterable[Any],
    news_config: Mapping[str, Any],
) -> list[SourceDescriptor]:
    """Build probe rows from Catalog identity, never from display names."""
    provider_rows = list(providers)
    adapters_by_id = {adapter.adapter_id: adapter for adapter in catalog.adapters}
    descriptors: list[SourceDescriptor] = []
    for provider in provider_rows:
        adapter = adapters_by_id.get(_provider_adapter_id(provider))
        if adapter is None:
            continue
        available_capabilities = set(getattr(provider, "capabilities", set()))
        source_name = str(getattr(provider, "name", adapter.adapter_name))
        priority = int(getattr(provider, "priority", adapter.current_provider_priority))
        for capability_id in sorted(set(adapter.capability_ids) & available_capabilities):
            descriptors.append(SourceDescriptor(
                source_id=f"{adapter.adapter_id}:{capability_id}",
                source_name=source_name,
                group=_source_group(capability_id),
                capability=capability_id,
                source_reference=adapter.configured_reference,
                priority=priority,
                critical=False,
                requires_api_key=False,
                probe_kind="provider",
                freshness_max_age_seconds=(
                    None
                    if capability_id in {"search", "profile"}
                    else catalog.capability(capability_id).freshness_max_age_seconds
                ),
                source_family_id=adapter.source_family_id,
                adapter_id=adapter.adapter_id,
                capability_id=capability_id,
                configured_reference=adapter.configured_reference,
            ))

    for source in news_config.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        hint = str(source.get("hint") or "").strip()
        name = str(source.get("name") or "").strip()
        url = str(source.get("url") or "").strip()
        if not hint or not name or not url:
            continue
        adapter_id = news_adapter_id(source)
        adapter = adapters_by_id.get(adapter_id)
        if adapter is None:
            continue
        descriptors.append(SourceDescriptor(
            source_id=f"{adapter.adapter_id}:feed",
            source_name=name,
            group="news",
            capability="feed",
            source_reference=adapter.configured_reference,
            priority=adapter.current_provider_priority,
            critical=False,
            requires_api_key=False,
            probe_kind="news_feed",
            probe_args={
                "hint": hint,
                "configuration_identity": _news_configuration_identity(source),
            },
            freshness_max_age_seconds=catalog.capability("feed").freshness_max_age_seconds,
            source_family_id=adapter.source_family_id,
            adapter_id=adapter.adapter_id,
            capability_id="feed",
            configured_reference=adapter.configured_reference,
        ))

    minimum_priority: dict[str, int] = {}
    for descriptor in descriptors:
        minimum_priority[descriptor.capability_id] = min(
            descriptor.priority,
            minimum_priority.get(descriptor.capability_id, descriptor.priority),
        )
    return [
        replace(row, critical=(row.group != "news" and row.priority == minimum_priority[row.capability_id]))
        for row in descriptors
    ]


def _overlay(observation: ProbeObservation | None) -> HealthOverlay:
    if observation is None:
        return HealthOverlay(None, "unexamined", None, None, None, None, "")
    health_status = {
        "success": "healthy",
        "partial": "degraded",
        "failure": "failed",
    }.get(observation.probe_status, "failed")
    return HealthOverlay(
        probe_status=observation.probe_status,
        health_status=health_status,
        observed_final_reference=observation.observed_final_reference,
        last_success_at=observation.last_success_at,
        latency_ms=observation.latency_ms,
        error_type=observation.error_type,
        error_message_redacted=observation.error_message_redacted,
    )


def merge_health(catalog: Any, observations: Iterable[ProbeObservation]) -> MergedHealth:
    observed = {
        (row.source_family_id, row.adapter_id, row.capability_id): row
        for row in observations
    }
    rows: list[HealthRow] = []
    for adapter in catalog.adapters:
        for capability_id in adapter.capability_ids:
            observation = observed.get((adapter.source_family_id, adapter.adapter_id, capability_id))
            rows.append(HealthRow(
                source_family_id=adapter.source_family_id,
                adapter_id=adapter.adapter_id,
                capability_id=capability_id,
                configured_reference=adapter.configured_reference,
                source_reference=adapter.configured_reference,
                source_id=f"{adapter.adapter_id}:{capability_id}",
                source_name=adapter.adapter_name,
                overlay=_overlay(observation),
            ))
    return MergedHealth(catalog, rows)


def family_health(
    rows: Sequence[HealthOverlay],
    catalog_statuses: Sequence[Any],
) -> str:
    eligible = [
        row
        for row, status in zip(rows, catalog_statuses)
        if _status_name(status) not in _EXCLUDED_CATALOG_STATUSES
    ]
    observed = [row for row in eligible if row.probe_status is not None]
    if not observed:
        return "unexamined"
    statuses = {row.probe_status for row in observed}
    if "failure" in statuses and statuses & {"success", "partial"}:
        return "partial_degraded"
    if "failure" in statuses:
        return "failed"
    if "partial" in statuses:
        return "degraded"
    return "healthy"
