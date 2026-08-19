from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .models import CatalogStatus, SourceRole


@dataclass(frozen=True, slots=True)
class CapabilityRoute:
    capability_id: str
    primary_adapter_ids: tuple[str, ...]
    fallback_adapter_ids: tuple[str, ...]
    cross_check_adapter_ids: tuple[str, ...]
    candidate_adapter_ids: tuple[str, ...]
    evidence_adapter_ids: tuple[str, ...]
    independent_family_ids: tuple[str, ...]


class CapabilityRouter:
    """Build deterministic Catalog routes without changing evidence eligibility."""

    def __init__(self, catalog: Any) -> None:
        self._catalog = catalog

    def route(self, capability_id: str, *, personal_research: bool = False) -> CapabilityRoute:
        try:
            capability = self._catalog.capability(capability_id)
        except KeyError as error:
            raise KeyError(f"unknown capability: {capability_id}") from error

        primary = self._adapters_for(capability.primary_families, capability_id, personal_research)
        fallback = self._adapters_for(capability.fallback_families, capability_id, personal_research)
        cross_check = self._adapters_for(capability.cross_check_families, capability_id, personal_research)
        candidates = tuple(
            adapter.adapter_id
            for adapter in fallback
            if SourceRole.CANDIDATE in adapter.source_roles or SourceRole.COLLECTOR in adapter.source_roles
        )
        fallback = tuple(adapter for adapter in fallback if adapter.adapter_id not in candidates)
        primary_ids = tuple(adapter.adapter_id for adapter in primary)
        fallback_ids = tuple(adapter.adapter_id for adapter in fallback)
        cross_check_ids = tuple(adapter.adapter_id for adapter in cross_check)
        evidence_ids = tuple(
            adapter.adapter_id
            for adapter in (*primary, *fallback, *cross_check)
            if self._catalog.family(adapter.source_family_id).independent_evidence_eligible
        )
        independent_families = tuple(dict.fromkeys(
            adapter.source_family_id
            for adapter in (*primary, *fallback, *cross_check)
            if self._catalog.family(adapter.source_family_id).independent_evidence_eligible
        ))
        return CapabilityRoute(
            capability_id,
            primary_ids,
            fallback_ids,
            cross_check_ids,
            candidates,
            evidence_ids,
            independent_families,
        )

    def _adapters_for(
        self,
        family_ids: Iterable[str],
        capability_id: str,
        personal_research: bool,
    ) -> tuple[Any, ...]:
        requested_families = set(family_ids)
        rows = []
        for adapter in self._catalog.adapters:
            if adapter.source_family_id not in requested_families or capability_id not in adapter.capability_ids:
                continue
            if not self._eligible(adapter, personal_research):
                continue
            rows.append(adapter)
        return tuple(sorted(rows, key=lambda adapter: (adapter.current_provider_priority, adapter.adapter_id)))

    @staticmethod
    def _eligible(adapter: Any, personal_research: bool) -> bool:
        status = getattr(adapter.catalog_status, "value", adapter.catalog_status)
        if status in {CatalogStatus.UNCONFIGURED.value, CatalogStatus.CATALOG_ONLY.value, CatalogStatus.LICENSE_REQUIRED.value}:
            return False
        if adapter.adapter_id == "yahoo-finance":
            return personal_research
        return bool(adapter.default_enabled) and status != CatalogStatus.DISABLED.value
