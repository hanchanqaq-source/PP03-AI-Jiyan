from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from typing import Any, Callable, Mapping

import source_health

from .catalog import DataSourceCatalog, build_catalog
from .references import public_source_reference


_EXCLUDED_HEALTH_STATUSES = {"unconfigured", "license_required", "catalog_only", "disabled"}


def _document(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_document(item) for item in value]
    if isinstance(value, list):
        return [_document(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _document(item) for key, item in value.items()}
    return value


def _health_status(row: Mapping[str, Any] | None) -> str:
    if row is None:
        return "unexamined"
    return {
        "success": "healthy",
        "partial": "degraded",
        "failure": "failed",
    }.get(str(row.get("probe_status") or ""), "unexamined")


def _family_health(rows: list[dict[str, Any]], catalog_statuses: list[str]) -> str:
    observed = [
        row for row, catalog_status in zip(rows, catalog_statuses)
        if catalog_status not in _EXCLUDED_HEALTH_STATUSES and row["probe_status"] is not None
    ]
    if not observed:
        return "unexamined"
    statuses = {row["probe_status"] for row in observed}
    if "failure" in statuses and statuses & {"success", "partial"}:
        return "partial_degraded"
    if "failure" in statuses:
        return "failed"
    if "partial" in statuses:
        return "degraded"
    return "healthy"


class DataSourceService:
    """Catalog response layer; source health remains an observation-only overlay."""

    def __init__(
        self,
        *,
        catalog_builder: Callable[[], DataSourceCatalog] = build_catalog,
        health_service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._catalog_builder = catalog_builder
        self._health_service_factory = health_service_factory or (lambda: source_health.get_service())
        self._adapter_enabled: dict[str, bool] = {}

    def _catalog(self) -> DataSourceCatalog:
        return self._catalog_builder()

    def _observations(self, catalog: DataSourceCatalog) -> dict[tuple[str, str, str], dict[str, Any]]:
        known = {
            (adapter.source_family_id, adapter.adapter_id, capability_id)
            for adapter in catalog.adapters
            for capability_id in adapter.capability_ids
        }
        rows: dict[tuple[str, str, str], dict[str, Any]] = {}
        for source in self._health_service_factory().list_sources():
            key = (
                str(source.get("source_family_id") or ""),
                str(source.get("adapter_id") or ""),
                str(source.get("capability_id") or source.get("capability") or ""),
            )
            if key in known:
                rows[key] = dict(source)
        return rows

    @staticmethod
    def _overlay(observation: Mapping[str, Any] | None) -> dict[str, object]:
        if observation is None:
            return {
                "probe_status": None,
                "health_status": "unexamined",
                "observed_final_reference": None,
                "last_success_at": None,
                "latency_ms": None,
                "error_type": None,
                "error_message_redacted": "",
            }
        reference = observation.get("observed_final_reference") or observation.get("final_reference")
        return {
            "probe_status": observation.get("probe_status"),
            "health_status": _health_status(observation),
            "observed_final_reference": public_source_reference(str(reference)) if reference else None,
            "last_success_at": observation.get("last_success_at"),
            "latency_ms": observation.get("latency_ms"),
            "error_type": observation.get("error_type"),
            # SourceHealth already redacts failures. Do not re-export its text,
            # because the Catalog API must remain safe even for a malformed overlay.
            "error_message_redacted": "source health probe failed" if observation.get("error_message_redacted") else "",
        }

    def _capability_document(
        self,
        catalog: DataSourceCatalog,
        capability_id: str,
        observations: Mapping[tuple[str, str, str], dict[str, Any]],
        *,
        adapter: Any | None = None,
    ) -> dict[str, object]:
        capability = _document(asdict(catalog.capability(capability_id)))
        if adapter is not None:
            observation = observations.get((adapter.source_family_id, adapter.adapter_id, capability_id))
            return {
                **capability,
                **self._overlay(observation),
            }
        rows = [
            observations.get((item.source_family_id, item.adapter_id, capability_id))
            for item in catalog.adapters
            if capability_id in item.capability_ids
        ]
        statuses = [
            str(getattr(item.catalog_status, "value", item.catalog_status))
            for item in catalog.adapters
            if capability_id in item.capability_ids
        ]
        compact_rows = [
            {"probe_status": row.get("probe_status")} if row is not None else {"probe_status": None}
            for row in rows
        ]
        return {**capability, "health_status": _family_health(compact_rows, statuses)}

    def _adapter_document(
        self,
        catalog: DataSourceCatalog,
        adapter: Any,
        observations: Mapping[tuple[str, str, str], dict[str, Any]],
    ) -> dict[str, object]:
        document = _document(asdict(adapter))
        enabled = self._adapter_enabled.get(adapter.adapter_id, adapter.default_enabled)
        capabilities = [
            self._capability_document(catalog, capability_id, observations, adapter=adapter)
            for capability_id in adapter.capability_ids
        ]
        rows = [
            {"probe_status": observations.get((adapter.source_family_id, adapter.adapter_id, capability_id), {}).get("probe_status")}
            for capability_id in adapter.capability_ids
        ]
        catalog_status = str(getattr(adapter.catalog_status, "value", adapter.catalog_status))
        return {
            **document,
            "enabled": enabled,
            "health_status": _family_health(rows, [catalog_status] * len(rows)),
            "capabilities": capabilities,
        }

    def _family_document(
        self,
        catalog: DataSourceCatalog,
        family_id: str,
        observations: Mapping[tuple[str, str, str], dict[str, Any]],
    ) -> dict[str, object]:
        family = catalog.family(family_id)
        adapters = [
            self._adapter_document(catalog, adapter, observations)
            for adapter in catalog.adapters_for_family(family_id)
        ]
        rows = [
            {"probe_status": observations.get((adapter.source_family_id, adapter.adapter_id, capability_id), {}).get("probe_status")}
            for adapter in catalog.adapters_for_family(family_id)
            for capability_id in adapter.capability_ids
        ]
        statuses = [
            str(getattr(adapter.catalog_status, "value", adapter.catalog_status))
            for adapter in catalog.adapters_for_family(family_id)
            for _capability_id in adapter.capability_ids
        ]
        return {
            **_document(asdict(family)),
            "health_status": _family_health(rows, statuses),
            "adapters": adapters,
        }

    def catalog_document(self) -> dict[str, object]:
        catalog = self._catalog()
        observations = self._observations(catalog)
        families = [self._family_document(catalog, family.source_family_id, observations) for family in catalog.families]
        news_adapters = [adapter for adapter in catalog.adapters if "feed" in adapter.capability_ids]
        return {
            "registration": {
                "families": len(catalog.families),
                "adapters": len(catalog.adapters),
                "capabilities": len(catalog.capabilities),
                "news_sources": len(news_adapters),
                "fingerprint": catalog.registration_fingerprint(),
            },
            "observed": {
                "sources": len(observations),
                "families": len({key[0] for key in observations}),
                "adapters": len({key[1] for key in observations}),
                "capabilities": len({key[2] for key in observations}),
            },
            "portfolio_relation": {"status": "unavailable_no_holdings"},
            "families": families,
            "capabilities": [
                self._capability_document(catalog, capability.capability_id, observations)
                for capability in catalog.capabilities
            ],
        }

    def families_document(self) -> list[dict[str, object]]:
        catalog = self._catalog()
        observations = self._observations(catalog)
        return [self._family_document(catalog, family.source_family_id, observations) for family in catalog.families]

    def family_document(self, family_id: str) -> dict[str, object]:
        catalog = self._catalog()
        observations = self._observations(catalog)
        return self._family_document(catalog, family_id, observations)

    def capabilities_document(self) -> list[dict[str, object]]:
        catalog = self._catalog()
        observations = self._observations(catalog)
        return [
            self._capability_document(catalog, capability.capability_id, observations)
            for capability in catalog.capabilities
        ]

    def _disabled_adapter_ids(self, catalog: DataSourceCatalog) -> tuple[str, ...]:
        return tuple(
            adapter.adapter_id
            for adapter in catalog.adapters
            if not self._adapter_enabled.get(adapter.adapter_id, adapter.default_enabled)
        )

    def refresh(self) -> dict[str, object]:
        catalog = self._catalog()
        run = self._health_service_factory().start_run(
            "full",
            excluded_adapter_ids=self._disabled_adapter_ids(catalog),
        )
        return {"run_id": str(run["run_id"])}

    def adapter_action(self, adapter_id: str, action: str) -> dict[str, object]:
        catalog = self._catalog()
        adapter = catalog.adapter(adapter_id)
        catalog_status = str(getattr(adapter.catalog_status, "value", adapter.catalog_status))
        local_toggle_allowed = (
            catalog_status == "configured"
            and str(getattr(adapter.billing_model, "value", adapter.billing_model)) == "free_no_key"
            and adapter.auth_type == "none"
        )
        if action in {"enable", "disable"}:
            if not local_toggle_allowed:
                return {
                    "adapter_id": adapter_id,
                    "action": action,
                    "status": "configuration_barrier",
                    "catalog_status": catalog_status,
                    "connected": False,
                }
            self._adapter_enabled[adapter_id] = action == "enable"
            return {
                "adapter_id": adapter_id,
                "action": action,
                "status": "local_toggle_updated",
                "enabled": self._adapter_enabled[adapter_id],
                "connected": False,
            }
        if not local_toggle_allowed or not self._adapter_enabled.get(adapter_id, adapter.default_enabled):
            return {
                "adapter_id": adapter_id,
                "action": "validate",
                "status": "configuration_barrier",
                "catalog_status": catalog_status,
                "connected": False,
            }
        return {
            "adapter_id": adapter_id,
            "action": "validate",
            "status": "full_health_run_started",
            "connected": False,
            **self.refresh(),
        }
