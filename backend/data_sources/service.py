from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
import threading
from typing import Any, Callable, Mapping

import source_health

from cache_io_lock import CACHE_IO_LOCK
from .catalog import DataSourceCatalog, build_catalog
from .budgets import BudgetDecision, BudgetGuard, BudgetPolicy, BudgetValidationError
from .config_store import ConfigValidationError, DataSourceConfigStore
from .credentials import (
    CredentialState,
    CredentialStoreUnavailable,
    CredentialWriteNotSupported,
    EnvironmentCredentialStore,
    KeyringCredentialStore,
)
from .models import BillingModel, CatalogStatus
from .provider_registry import ProviderRegistry
from .routing import CapabilityRoute, CapabilityRouter, effective_adapter_enabled
from source_health.probes.data_source_adapter import probe_data_source_adapter
from .references import public_source_reference
from .usage_store import UsageConflictError, UsageStore, UsageStoreError


_EXCLUDED_HEALTH_STATUSES = {"unconfigured", "license_required", "catalog_only", "disabled"}
_DECIMAL_TEXT = re.compile(r"^(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,8})?$")
_USAGE_MODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ADAPTER_CONFIG_FIELDS = {
    "usage_mode", "daily_budget", "monthly_budget", "per_request_budget",
    "daily_request_limit", "monthly_request_limit",
}
_CREDENTIAL_MARKERS = (
    "credential", "secret", "token", "password", "api_key", "apikey", "access_key",
)
_MAX_BUDGET = Decimal("1000000000000")
_CONFIG_MUTATION_FIELDS = {"enabled": False, "last_validated_at": None}


class DataSourceRequestInvalid(ValueError):
    pass


class DataSourceConflict(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DataSourceUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DefaultCredentialStore:
    """Keyring-first credential access with a read-only environment fallback."""

    def __init__(self, scope: Mapping[str, tuple[str, ...]]) -> None:
        self._keyring = KeyringCredentialStore(scope)
        self._environment = EnvironmentCredentialStore(scope)

    def get(self, adapter_id: str, env_name: str) -> str | None:
        return self._keyring.get(adapter_id, env_name) or self._environment.get(adapter_id, env_name)

    def set(self, adapter_id: str, env_name: str, value: str) -> None:
        if self._environment.get(adapter_id, env_name) is not None and not self._keyring.state(adapter_id).configured:
            raise CredentialWriteNotSupported("environment credentials are read-only")
        self._keyring.set(adapter_id, env_name, value)

    def delete(self, adapter_id: str, env_name: str) -> None:
        keyring_state = self._keyring.state(adapter_id)
        if keyring_state.configured:
            self._keyring.delete(adapter_id, env_name)
            return
        if self._environment.get(adapter_id, env_name) is not None:
            raise CredentialWriteNotSupported("environment credentials are read-only")
        self._keyring.delete(adapter_id, env_name)

    def state(self, adapter_id: str) -> CredentialState:
        keyring_state = self._keyring.state(adapter_id)
        if keyring_state.configured:
            return keyring_state
        environment_state = self._environment.state(adapter_id)
        if environment_state.configured:
            return CredentialState(
                True,
                "environment_fallback_active",
                None,
                "environment",
            )
        return keyring_state if keyring_state.status == "credential_store_unavailable" else environment_state


_MAX_AUTHORIZATION_CONTEXTS = 4_096
_VALIDATION_USAGE_STATUSES = frozenset({
    "validation_success",
    "validation_partial",
    "validation_failure",
})


@dataclass(frozen=True, slots=True)
class _AuthorizationContext:
    adapter_id: str
    reservation_id: str
    decision: BudgetDecision
    guard: BudgetGuard
    usage_store: UsageStore
    settlement: "_AuthorizationSettlement | None" = None


@dataclass(frozen=True, slots=True)
class _AuthorizationSettlement:
    adapter_id: str
    reservation_id: str
    actual_cost: Decimal
    request_count: int
    status: str
    units: Decimal
    recorded_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.adapter_id) is not str
            or type(self.reservation_id) is not str
            or type(self.actual_cost) is not Decimal
            or type(self.request_count) is not int
            or type(self.status) is not str
            or type(self.units) is not Decimal
            or type(self.recorded_at) is not datetime
            or self.recorded_at.tzinfo is None
            or self.recorded_at.utcoffset() is None
        ):
            raise BudgetValidationError("authorization settlement is invalid")
        if (
            not self.actual_cost.is_finite()
            or self.actual_cost != 0
            or not self.units.is_finite()
            or self.units < 0
            or self.units != self.units.to_integral_value()
            or self.units > 1_000_000_000
            or self.request_count != 1
            or self.status not in _VALIDATION_USAGE_STATUSES
            or (
                self.status == "validation_failure"
                and self.units != 0
            )
        ):
            raise BudgetValidationError("authorization settlement is invalid")


class _ServerAuthorizationGuard:
    """Build an authorization snapshot only from the owning Service's stores."""

    def __init__(self, service: "DataSourceService") -> None:
        self._service = service
        self._context_lock = threading.RLock()
        self._contexts: dict[str, _AuthorizationContext] = {}
        self._completed_contexts: set[str] = set()

    @property
    def usage_store(self) -> UsageStore:
        self._service._ensure_configuration_dependencies()
        return self._service._usage_store

    @staticmethod
    def _budget(entry: Mapping[str, Any], field: str) -> Decimal:
        value = entry.get(field)
        return Decimal("0") if value is None else Decimal(value)

    def _snapshot(self, adapter_id: str) -> BudgetGuard:
        catalog = self._service._catalog()
        adapter = catalog.adapter(adapter_id)
        config = self._service._load_config()
        entry = config["adapters"].get(adapter_id, {})
        credential = self._service._credential_state(adapter, config)
        credential_required = bool(adapter.credential_env_names)
        free_entitlement = adapter.billing_model in {
            BillingModel.FREE_NO_KEY,
            BillingModel.FREE_KEY,
        }
        policy = BudgetPolicy(
            adapter_id=adapter_id,
            enabled=self._service._effective_enabled(adapter, config),
            configured=(not credential_required) or credential["configured"] is True,
            credential_validated=(
                not credential_required
                or (
                    credential["configured"] is True
                    and bool(entry.get("last_validated_at"))
                )
            ),
            trusted_entitlement=free_entitlement,
            free_only=config["free_only"],
            daily_budget=self._budget(entry, "daily_budget"),
            monthly_budget=self._budget(entry, "monthly_budget"),
            per_request_budget=self._budget(entry, "per_request_budget"),
            daily_request_limit=entry.get("daily_request_limit"),
            monthly_request_limit=entry.get("monthly_request_limit"),
            # No keyed provider currently has a reviewed secret-safe transport.
            transport_supported=not credential_required and adapter.auth_type == "none",
            # Paid live access additionally needs an explicit future runtime gate.
            live_authorized=free_entitlement,
        )
        return BudgetGuard(
            self.usage_store,
            {adapter_id: policy},
            trusted_adapters={adapter_id: adapter},
        )

    def _prune_completed_context(self) -> None:
        if len(self._contexts) < _MAX_AUTHORIZATION_CONTEXTS:
            return
        for reservation_id in tuple(self._contexts):
            if reservation_id in self._completed_contexts:
                self._contexts.pop(reservation_id)
                self._completed_contexts.remove(reservation_id)
                return

    def authorize(self, adapter: Any, **kwargs: Any):
        if "reservation_id" in kwargs:
            raise BudgetValidationError("caller reservation authority is not accepted")
        with self._context_lock:
            self._service._assert_usage_reconciliation_ready()
            self._prune_completed_context()
            if len(self._contexts) >= _MAX_AUTHORIZATION_CONTEXTS:
                raise BudgetValidationError("authorization context capacity is exhausted")
            guard = self._snapshot(adapter.adapter_id)
            decision = guard.authorize(adapter, **kwargs)
            if decision.allowed:
                reservation_id = decision.reservation_id
                if type(reservation_id) is not str or reservation_id in self._contexts:
                    raise BudgetValidationError("authorization context is invalid")
                self._contexts[reservation_id] = _AuthorizationContext(
                    adapter.adapter_id,
                    reservation_id,
                    decision,
                    guard,
                    guard.usage_store,
                )
            return decision

    def record(self, adapter_id: str, **kwargs: Any):
        expected_fields = {
            "reservation_id", "actual_cost", "request_count", "status", "units", "now",
        }
        if type(kwargs) is not dict or set(kwargs) != expected_fields:
            raise BudgetValidationError("authorization settlement is invalid")
        reservation_id = kwargs.get("reservation_id")
        if type(adapter_id) is not str or type(reservation_id) is not str:
            raise BudgetValidationError("authorization context is unavailable")
        settlement = _AuthorizationSettlement(
            adapter_id,
            reservation_id,
            kwargs["actual_cost"],
            kwargs["request_count"],
            kwargs["status"],
            kwargs["units"],
            kwargs["now"],
        )
        with self._context_lock:
            context = self._contexts.get(reservation_id)
            if (
                context is None
                or context.adapter_id != adapter_id
                or context.reservation_id != reservation_id
                or context.decision.allowed is not True
                or context.decision.reason != "authorized"
                or context.decision.reservation_id != reservation_id
                or context.guard.usage_store is not context.usage_store
            ):
                raise BudgetValidationError("authorization context is unavailable")
            if context.settlement is not None and context.settlement != settlement:
                raise BudgetValidationError("authorization settlement is unavailable")
            if context.settlement is None:
                context = replace(context, settlement=settlement)
                self._contexts[reservation_id] = context
            try:
                context.usage_store._stage_validation_reconciliation(
                    settlement.adapter_id,
                    reservation_id=settlement.reservation_id,
                    status=settlement.status,
                    units=settlement.units,
                    recorded_at=settlement.recorded_at,
                )
            except UsageStoreError:
                self._service._mark_usage_reconciliation_pending()
                raise
            try:
                record = context.guard.record(adapter_id, **kwargs)
            except BudgetValidationError:
                self._service._mark_usage_reconciliation_pending()
                raise
            except UsageConflictError:
                self._service._mark_usage_reconciliation_pending()
                raise
            except UsageStoreError:
                # One bounded retry closes both pre-write transient failures and
                # post-write ambiguous failures via UsageStore idempotency. A
                # persistent failure keeps the reservation open and fail-closed.
                try:
                    record = context.guard.record(adapter_id, **kwargs)
                except (BudgetValidationError, UsageConflictError, UsageStoreError):
                    self._service._mark_usage_reconciliation_pending()
                    raise
            self._completed_contexts.add(reservation_id)
            return record

    def cancel_unattempted(
        self, adapter_id: str, *, reservation_id: str, now: datetime,
    ):
        """Close a pre-probe reservation without creating replay authority."""
        if (
            type(adapter_id) is not str
            or type(reservation_id) is not str
            or type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise BudgetValidationError("authorization cancellation is invalid")
        with self._context_lock:
            context = self._contexts.get(reservation_id)
            if (
                context is None
                or context.adapter_id != adapter_id
                or context.reservation_id != reservation_id
                or context.settlement is not None
                or context.decision.allowed is not True
                or context.decision.reservation_id != reservation_id
            ):
                raise BudgetValidationError("authorization context is unavailable")
            kwargs = {
                "reservation_id": reservation_id,
                "actual_cost": Decimal("0"),
                "request_count": 0,
                "status": "validation_not_attempted",
                "units": Decimal("0"),
                "now": now,
            }
            try:
                record = context.guard.record(adapter_id, **kwargs)
            except (BudgetValidationError, UsageConflictError):
                self._service._mark_usage_reconciliation_pending()
                raise
            except UsageStoreError:
                try:
                    record = context.guard.record(adapter_id, **kwargs)
                except (BudgetValidationError, UsageConflictError, UsageStoreError):
                    self._service._mark_usage_reconciliation_pending()
                    raise
            self._completed_contexts.add(reservation_id)
            return record


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
        config_store: Any | None = None,
        credential_store: Any | None = None,
        budget_guard: Any | None = None,
        usage_store: Any | None = None,
        provider_registry: Any | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._catalog_builder = catalog_builder
        self._health_service_factory = health_service_factory or (lambda: source_health.get_service())
        self._config_store = config_store
        self._credential_store = credential_store
        self._budget_guard = budget_guard
        self._usage_store = usage_store
        self._provider_registry = provider_registry
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self._mutation_lock = threading.RLock()
        self._adapter_locks: dict[str, threading.RLock] = {}
        self._usage_recovery_lock = threading.RLock()
        self._usage_recovery_attempted = False
        self._usage_recovery_ready = False

    def _catalog(self) -> DataSourceCatalog:
        return self._catalog_builder()

    def _trusted_reconciliation_adapter_ids(self) -> frozenset[str]:
        return frozenset(
            adapter.adapter_id
            for adapter in self._catalog().adapters
            if (
                adapter.billing_model is BillingModel.FREE_NO_KEY
                and adapter.auth_type == "none"
                and tuple(adapter.credential_env_names) == ()
            )
        )

    def _mark_usage_reconciliation_pending(self) -> None:
        with self._usage_recovery_lock:
            self._usage_recovery_attempted = True
            self._usage_recovery_ready = False

    def _assert_usage_reconciliation_ready(self) -> None:
        # Revalidate the shared ledger at every controlled authorization
        # boundary. A prior ready result is only a point-in-time observation;
        # another process may have reserved after it. This remains one bounded,
        # payload-free pass and does not recurse through authorization.
        self.recover_usage_reconciliation()
        with self._usage_recovery_lock:
            if not self._usage_recovery_ready:
                raise UsageStoreError("usage reconciliation is pending")

    def recover_usage_reconciliation(self) -> dict[str, object]:
        """Run one bounded, payload-free recovery pass over server-owned intents."""
        with self._usage_recovery_lock:
            if self._usage_store is None:
                self._usage_store = UsageStore()
            try:
                result = self._usage_store.recover_reconciliations(
                    trusted_adapter_ids=self._trusted_reconciliation_adapter_ids(),
                    now=self._now(),
                )
            except (UsageStoreError, BudgetValidationError, TypeError, ValueError):
                self._usage_recovery_attempted = True
                self._usage_recovery_ready = False
                return {
                    "status": "blocked",
                    "attempted": 0,
                    "recovered": 0,
                    "retained": 0,
                }
            attempted = result["attempted"]
            recovered = result["recovered"]
            retained = result["retained"]
            blocked = result["blocked"] is True
            self._usage_recovery_attempted = True
            self._usage_recovery_ready = not blocked and retained == 0
            return {
                "status": (
                    "blocked" if blocked else "closed" if retained == 0 else "pending"
                ),
                "attempted": attempted,
                "recovered": recovered,
                "retained": retained,
            }

    def _ensure_configuration_dependencies(self) -> None:
        catalog = self._catalog()
        scope = {row.adapter_id: tuple(row.credential_env_names) for row in catalog.adapters if row.credential_env_names}
        if self._usage_store is None:
            self._usage_store = UsageStore()
        if not self._usage_recovery_attempted:
            self.recover_usage_reconciliation()
        if self._config_store is None:
            self._config_store = DataSourceConfigStore(catalog=catalog)
        if self._credential_store is None:
            self._credential_store = _DefaultCredentialStore(scope)
        if self._budget_guard is None:
            self._budget_guard = _ServerAuthorizationGuard(self)
        if self._provider_registry is None:
            self._provider_registry = ProviderRegistry(
                catalog=catalog,
                credential_store=self._credential_store,
                budget_guard=self._budget_guard,
            )

    @staticmethod
    def _effective_enabled(adapter: Any, config: Mapping[str, Any]) -> bool:
        enabled = effective_adapter_enabled(adapter, config)
        entry = config.get("adapters", {}).get(adapter.adapter_id, {})
        if "enabled" in entry and type(entry["enabled"]) is not bool:
            raise DataSourceUnavailable("configuration_store_unavailable")
        return enabled

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
        config: Mapping[str, Any],
    ) -> dict[str, object]:
        document = _document(asdict(adapter))
        enabled = self._effective_enabled(adapter, config)
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
        config: Mapping[str, Any],
    ) -> dict[str, object]:
        family = catalog.family(family_id)
        adapters = [
            self._adapter_document(catalog, adapter, observations, config)
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
        config = self._load_config()
        observations = self._observations(catalog)
        families = [
            self._family_document(
                catalog, family.source_family_id, observations, config,
            )
            for family in catalog.families
        ]
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
        config = self._load_config()
        observations = self._observations(catalog)
        return [
            self._family_document(
                catalog, family.source_family_id, observations, config,
            )
            for family in catalog.families
        ]

    def family_document(self, family_id: str) -> dict[str, object]:
        catalog = self._catalog()
        config = self._load_config()
        observations = self._observations(catalog)
        return self._family_document(catalog, family_id, observations, config)

    def capabilities_document(self) -> list[dict[str, object]]:
        catalog = self._catalog()
        observations = self._observations(catalog)
        return [
            self._capability_document(catalog, capability.capability_id, observations)
            for capability in catalog.capabilities
        ]

    def capability_route(
        self, capability_id: str, *, personal_research: bool = False,
    ) -> CapabilityRoute:
        """Build a production route from the latest persisted configuration."""
        catalog = self._catalog()
        config = self._load_config()
        return CapabilityRouter(catalog, configuration=config).route(
            capability_id, personal_research=personal_research,
        )

    def _disabled_adapter_ids(
        self, catalog: DataSourceCatalog, config: Mapping[str, Any],
    ) -> tuple[str, ...]:
        return tuple(
            adapter.adapter_id
            for adapter in catalog.adapters
            if not self._effective_enabled(adapter, config)
        )

    def refresh(self) -> dict[str, object]:
        catalog = self._catalog()
        config = self._load_config()
        run = self._health_service_factory().start_run(
            "full",
            excluded_adapter_ids=self._disabled_adapter_ids(catalog, config),
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
            if action == "enable":
                self.enable_adapter(adapter_id)
            else:
                self.disable_adapter(adapter_id)
            return {
                "adapter_id": adapter_id,
                "action": action,
                "status": "local_toggle_updated",
                "enabled": action == "enable",
                "connected": False,
            }
        config = self._load_config()
        if not local_toggle_allowed or not self._effective_enabled(adapter, config):
            return {
                "adapter_id": adapter_id,
                "action": "validate",
                "status": "configuration_barrier",
                "catalog_status": catalog_status,
                "connected": False,
            }
        return self.validate_adapter(adapter_id)

    def _adapter(self, adapter_id: str):
        if type(adapter_id) is not str:
            raise KeyError(adapter_id)
        return self._catalog().adapter(adapter_id)

    def _adapter_lock(self, adapter_id: str) -> threading.RLock:
        with self._mutation_lock:
            lock = self._adapter_locks.get(adapter_id)
            if lock is None:
                lock = threading.RLock()
                self._adapter_locks[adapter_id] = lock
            return lock

    def _now(self) -> datetime:
        value = self._now_factory()
        if type(value) is not datetime or value.tzinfo is None:
            raise DataSourceUnavailable("clock_unavailable")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _load_config(self) -> dict[str, Any]:
        self._ensure_configuration_dependencies()
        try:
            return self._config_store.load()
        except (ConfigValidationError, OSError):
            raise DataSourceUnavailable("configuration_store_unavailable") from None

    def _credential_state(self, adapter: Any, config: Mapping[str, Any]) -> dict[str, object]:
        entry = config.get("adapters", {}).get(adapter.adapter_id, {})
        last_validated_at = entry.get("last_validated_at")
        if adapter.catalog_status is CatalogStatus.LICENSE_REQUIRED:
            return CredentialState(False, "license_required", None, "none").to_dict()
        if not adapter.credential_env_names:
            return CredentialState(True, "not_required", None, "none").to_dict()
        try:
            state = self._credential_store.state(adapter.adapter_id)
        except CredentialStoreUnavailable:
            raise DataSourceUnavailable("credential_store_unavailable") from None
        document = state.to_dict()
        document["last_validated_at"] = last_validated_at
        if document["configured"] and last_validated_at:
            document["status"] = "validated"
        return document

    def config_document(self) -> dict[str, object]:
        catalog = self._catalog()
        config = self._load_config()
        rows = []
        for adapter in catalog.adapters:
            entry = config["adapters"].get(adapter.adapter_id, {})
            rows.append({
                "adapter_id": adapter.adapter_id,
                "billing_model": adapter.billing_model.value,
                "catalog_status": adapter.catalog_status.value,
                "enabled": entry.get("enabled", adapter.default_enabled),
                "usage_mode": entry.get("usage_mode"),
                "daily_budget": entry.get("daily_budget"),
                "monthly_budget": entry.get("monthly_budget"),
                "per_request_budget": entry.get("per_request_budget"),
                "daily_request_limit": entry.get("daily_request_limit"),
                "monthly_request_limit": entry.get("monthly_request_limit"),
                "credential": self._credential_state(adapter, config),
            })
        return {"free_only": config["free_only"], "adapters": rows}

    def _atomic_update_free_only(self, free_only: bool) -> None:
        if type(self._config_store) is DataSourceConfigStore:
            # Keep the Task 1 cross-process lock across read/merge/write so a
            # concurrent adapter update cannot be overwritten.
            with CACHE_IO_LOCK:
                with self._config_store._process_lock():
                    current = self._config_store._load_unlocked()
                    validated = self._config_store._validate({
                        "free_only": free_only, "adapters": current["adapters"],
                    })
                    self._config_store._atomic_write(validated)
            return
        current = self._config_store.load()
        self._config_store.save({"free_only": free_only, "adapters": current["adapters"]})

    def update_free_only(self, free_only: object) -> dict[str, object]:
        if type(free_only) is not bool:
            raise DataSourceRequestInvalid("invalid configuration")
        self._ensure_configuration_dependencies()
        with self._mutation_lock:
            try:
                self._atomic_update_free_only(free_only)
            except (ConfigValidationError, OSError):
                raise DataSourceUnavailable("configuration_store_unavailable") from None
        return self.config_document()

    @staticmethod
    def _normalize_adapter_updates(updates: object) -> dict[str, object]:
        if type(updates) is not dict or not updates or set(updates) - _ADAPTER_CONFIG_FIELDS:
            raise DataSourceRequestInvalid("invalid adapter configuration")
        normalized: dict[str, object] = {}
        for field, value in updates.items():
            if field in {"daily_budget", "monthly_budget", "per_request_budget"}:
                if type(value) is not str or not _DECIMAL_TEXT.fullmatch(value):
                    raise DataSourceRequestInvalid("invalid adapter configuration")
                try:
                    parsed = Decimal(value)
                except InvalidOperation:
                    raise DataSourceRequestInvalid("invalid adapter configuration") from None
                if not parsed.is_finite() or parsed < 0 or parsed > _MAX_BUDGET:
                    raise DataSourceRequestInvalid("invalid adapter configuration")
            elif field in {"daily_request_limit", "monthly_request_limit"}:
                if type(value) is not int or value < 0 or value > 1_000_000_000:
                    raise DataSourceRequestInvalid("invalid adapter configuration")
            elif (
                type(value) is not str
                or not _USAGE_MODE.fullmatch(value)
                or any(marker in value.lower() for marker in _CREDENTIAL_MARKERS)
            ):
                raise DataSourceRequestInvalid("invalid adapter configuration")
            normalized[field] = value
        return normalized

    def update_adapter_config(self, adapter_id: str, updates: object) -> dict[str, object]:
        self._adapter(adapter_id)
        normalized = self._normalize_adapter_updates(updates)
        self._ensure_configuration_dependencies()
        with self._adapter_lock(adapter_id):
            try:
                document = self._config_store.update_adapter(adapter_id, normalized)
            except (ConfigValidationError, OSError):
                raise DataSourceUnavailable("configuration_store_unavailable") from None
        return {"adapter_id": adapter_id, "config": document["adapters"][adapter_id]}

    @staticmethod
    def _conflict_for_catalog(adapter: Any) -> None:
        if adapter.catalog_status is CatalogStatus.LICENSE_REQUIRED:
            raise DataSourceConflict("license_required")
        if adapter.catalog_status is CatalogStatus.CATALOG_ONLY:
            raise DataSourceConflict("catalog_only")
        if adapter.catalog_status is CatalogStatus.DISABLED:
            raise DataSourceConflict("disabled")

    @staticmethod
    def _has_positive_budgets(entry: Mapping[str, Any]) -> bool:
        try:
            return all(Decimal(entry.get(field, "0")) > 0 for field in (
                "daily_budget", "monthly_budget", "per_request_budget",
            ))
        except (InvalidOperation, TypeError, ValueError):
            return False

    def enable_adapter(self, adapter_id: str, *, confirm_paid_usage: object = False) -> dict[str, object]:
        if type(confirm_paid_usage) is not bool:
            raise DataSourceRequestInvalid("invalid confirmation")
        adapter = self._adapter(adapter_id)
        self._conflict_for_catalog(adapter)
        config = self._load_config()
        entry = config["adapters"].get(adapter_id, {})
        credential = self._credential_state(adapter, config)
        is_paid = adapter.billing_model in {BillingModel.PAID_API, BillingModel.ENTERPRISE_LICENSE}
        if adapter.credential_env_names and not credential["configured"]:
            raise DataSourceConflict("unconfigured")
        if is_paid:
            if config["free_only"]:
                raise DataSourceConflict("free_only")
            if not self._has_positive_budgets(entry):
                raise DataSourceConflict("budget_required")
            if not confirm_paid_usage:
                raise DataSourceConflict("explicit_confirmation_required")
        if adapter.credential_env_names:
            # No current keyed/paid descriptor has reviewed, secret-safe validation
            # transport metadata. Fail closed before resolving an implementation.
            raise DataSourceConflict("unsupported_credential_transport")
        if adapter.billing_model is not BillingModel.FREE_NO_KEY or adapter.auth_type != "none":
            raise DataSourceConflict("configuration_barrier")
        with self._adapter_lock(adapter_id):
            try:
                self._config_store.update_adapter(adapter_id, {"enabled": True})
            except (ConfigValidationError, OSError):
                raise DataSourceUnavailable("configuration_store_unavailable") from None
        return {"adapter_id": adapter_id, "action": "enable", "status": "enabled", "enabled": True, "connected": False}

    def disable_adapter(self, adapter_id: str) -> dict[str, object]:
        adapter = self._adapter(adapter_id)
        self._conflict_for_catalog(adapter)
        self._ensure_configuration_dependencies()
        with self._adapter_lock(adapter_id):
            try:
                self._config_store.update_adapter(adapter_id, {"enabled": False})
            except (ConfigValidationError, OSError):
                raise DataSourceUnavailable("configuration_store_unavailable") from None
        return {"adapter_id": adapter_id, "action": "disable", "status": "disabled", "enabled": False, "connected": False}

    def validate_adapter(self, adapter_id: str) -> dict[str, object]:
        adapter = self._adapter(adapter_id)
        self._conflict_for_catalog(adapter)
        config = self._load_config()
        credential = self._credential_state(adapter, config)
        if adapter.credential_env_names and not credential["configured"]:
            return {"adapter_id": adapter_id, "status": "unconfigured", "connected": False, "health_failure": False, "last_validated_at": None}
        if adapter.credential_env_names:
            # An explicit trusted validation_transport_supported flag does not
            # yet exist in Catalog/runtime metadata. Do not resolve a Provider.
            raise DataSourceConflict("unsupported_credential_transport")
        if (
            adapter.billing_model is not BillingModel.FREE_NO_KEY
            or adapter.auth_type != "none"
        ):
            raise DataSourceConflict("disabled")
        self._ensure_configuration_dependencies()
        now = self._now()
        try:
            decision = self._budget_guard.authorize(
                adapter,
                estimated_cost=Decimal("0"),
                now=now,
            )
        except (BudgetValidationError, UsageStoreError):
            raise DataSourceUnavailable("usage_store_unavailable") from None
        if not decision.allowed:
            # Preserve the existing HTTP conflict contract for persisted local
            # disablement while still deriving it from the server-owned guard.
            if decision.reason == "disabled":
                raise DataSourceConflict("disabled")
            return {
                "adapter_id": adapter_id,
                "status": decision.reason,
                "connected": False,
                "health_failure": False,
                "last_validated_at": None,
            }
        if type(decision.reservation_id) is not str:
            raise DataSourceUnavailable("usage_store_unavailable")

        probe_started = False
        usage_status = "validation_failure"
        units = Decimal("0")
        result: Mapping[str, Any] | None = None
        recorded_at: datetime | None = None
        try:
            # Registry resolution and the bounded health probe are both after
            # authorization. Missing implementations consume no request.
            available = set(self._provider_registry.available_adapter_ids())
            if adapter_id not in available:
                raise DataSourceConflict("configuration_barrier")
            capability_id = next(
                (
                    capability_id
                    for capability_id in adapter.capability_ids
                    if self._catalog().capability(capability_id).probe_enabled
                ),
                None,
            )
            if capability_id is None:
                raise DataSourceConflict("configuration_barrier")
            provider = self._provider_registry.adapter(adapter_id)
            probe_started = True
            result = probe_data_source_adapter(provider, capability_id)
            probe_status = result.get("status")
            usage_status = {
                "success": "validation_success",
                "partial": "validation_partial",
            }.get(probe_status, "validation_failure")
            returned_items = result.get("returned_items")
            if (
                usage_status != "validation_failure"
                and type(returned_items) is int
                and 0 <= returned_items <= 1_000_000_000
            ):
                units = Decimal(returned_items)
        finally:
            try:
                recorded_at = self._now()
                if probe_started:
                    self._budget_guard.record(
                        adapter_id,
                        reservation_id=decision.reservation_id,
                        actual_cost=Decimal("0"),
                        request_count=1,
                        status=usage_status,
                        units=units,
                        now=recorded_at,
                    )
                else:
                    self._budget_guard.cancel_unattempted(
                        adapter_id,
                        reservation_id=decision.reservation_id,
                        now=recorded_at,
                    )
            except (BudgetValidationError, UsageConflictError, UsageStoreError):
                raise DataSourceUnavailable("usage_store_unavailable") from None

        if result is None:
            raise DataSourceUnavailable("usage_store_unavailable")
        status = str(result.get("connection_status") or result.get("status") or "failure")
        connected = result.get("status") == "success" and status == "success"
        return {
            "adapter_id": adapter_id,
            "capability_id": capability_id,
            "status": status,
            "connected": connected,
            "health_failure": result.get("status") == "failure",
            "last_validated_at": self._timestamp(recorded_at) if connected else None,
        }

    @staticmethod
    def _updated_config_document(
        document: Mapping[str, Any], adapter_id: str, updates: Mapping[str, object],
    ) -> dict[str, object]:
        adapters = {
            key: dict(value) for key, value in document.get("adapters", {}).items()
        }
        entry = dict(adapters.get(adapter_id, {}))
        entry.update(updates)
        adapters[adapter_id] = entry
        return {"free_only": document.get("free_only", True), "adapters": adapters}

    @staticmethod
    def _restored_config_document(
        current: Mapping[str, Any], adapter_id: str,
        before_entry: Mapping[str, object], written: Mapping[str, object],
    ) -> dict[str, object]:
        adapters = {
            key: dict(value) for key, value in current.get("adapters", {}).items()
        }
        current_entry = dict(adapters.get(adapter_id, {}))
        for field, expected in written.items():
            if (
                field not in current_entry
                or type(current_entry[field]) is not type(expected)
                or current_entry[field] != expected
            ):
                raise DataSourceUnavailable("configuration_recovery_required")
        for field in written:
            if field in before_entry:
                current_entry[field] = before_entry[field]
            else:
                current_entry.pop(field, None)
        if current_entry:
            adapters[adapter_id] = current_entry
        else:
            adapters.pop(adapter_id, None)
        return {"free_only": current.get("free_only", True), "adapters": adapters}

    def _recover_generic_config(
        self, adapter_id: str, before_entry: Mapping[str, object],
        written: Mapping[str, object],
    ) -> None:
        try:
            current = self._config_store.load()
            restored = self._restored_config_document(
                current, adapter_id, before_entry, written,
            )
            if restored != current:
                self._config_store.save(restored)
        except DataSourceUnavailable:
            raise
        except (AttributeError, ConfigValidationError, OSError):
            raise DataSourceUnavailable("configuration_recovery_required") from None

    def _credential_config_transaction(
        self, adapter_id: str, mutate_credential: Callable[[], None],
    ) -> None:
        written = dict(_CONFIG_MUTATION_FIELDS)
        self._ensure_configuration_dependencies()
        with self._adapter_lock(adapter_id):
            if type(self._config_store) is DataSourceConfigStore:
                store = self._config_store
                try:
                    with CACHE_IO_LOCK:
                        with store._process_lock():
                            before = store._load_unlocked()
                            before_entry = dict(before["adapters"].get(adapter_id, {}))
                            updated = store._validate(self._updated_config_document(
                                before, adapter_id, written,
                            ))
                            try:
                                store._atomic_write(updated)
                            except (ConfigValidationError, OSError):
                                try:
                                    current = store._load_unlocked()
                                    if current != before:
                                        restored = store._validate(self._restored_config_document(
                                            current, adapter_id, before_entry, written,
                                        ))
                                        store._atomic_write(restored)
                                except (ConfigValidationError, OSError, DataSourceUnavailable):
                                    raise DataSourceUnavailable(
                                        "configuration_recovery_required"
                                    ) from None
                                raise DataSourceUnavailable(
                                    "configuration_store_unavailable"
                                ) from None
                            try:
                                mutate_credential()
                            except (CredentialWriteNotSupported, CredentialStoreUnavailable):
                                try:
                                    current = store._load_unlocked()
                                    restored = store._validate(self._restored_config_document(
                                        current, adapter_id, before_entry, written,
                                    ))
                                    store._atomic_write(restored)
                                except (ConfigValidationError, OSError, DataSourceUnavailable):
                                    raise DataSourceUnavailable(
                                        "configuration_recovery_required"
                                    ) from None
                                raise
                except DataSourceUnavailable:
                    raise
                except (ConfigValidationError, OSError):
                    raise DataSourceUnavailable(
                        "configuration_store_unavailable"
                    ) from None
                return

            try:
                before = self._config_store.load()
                before_entry = dict(before.get("adapters", {}).get(adapter_id, {}))
            except (ConfigValidationError, OSError):
                raise DataSourceUnavailable("configuration_store_unavailable") from None
            try:
                self._config_store.update_adapter(adapter_id, written)
            except (ConfigValidationError, OSError):
                try:
                    current = self._config_store.load()
                    if current != before:
                        self._recover_generic_config(
                            adapter_id, before_entry, written,
                        )
                except DataSourceUnavailable:
                    raise
                except (ConfigValidationError, OSError):
                    raise DataSourceUnavailable(
                        "configuration_recovery_required"
                    ) from None
                raise DataSourceUnavailable("configuration_store_unavailable") from None
            try:
                mutate_credential()
            except (CredentialWriteNotSupported, CredentialStoreUnavailable):
                self._recover_generic_config(
                    adapter_id, before_entry, written,
                )
                raise

    def put_credential(self, adapter_id: str, value: object) -> dict[str, object]:
        adapter = self._adapter(adapter_id)
        self._conflict_for_catalog(adapter)
        if len(adapter.credential_env_names) != 1:
            raise DataSourceConflict("credential_not_supported")
        if type(value) is not str or not value.strip() or len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise DataSourceRequestInvalid("invalid credential")
        self._ensure_configuration_dependencies()
        env_name = adapter.credential_env_names[0]
        try:
            self._credential_config_transaction(
                adapter_id,
                lambda: self._credential_store.set(adapter_id, env_name, value),
            )
        except CredentialWriteNotSupported:
            raise DataSourceConflict("credential_source_read_only") from None
        except CredentialStoreUnavailable:
            raise DataSourceUnavailable("credential_store_unavailable") from None
        try:
            state = self._credential_store.state(adapter_id).to_dict()
        except CredentialStoreUnavailable:
            raise DataSourceUnavailable("credential_store_unavailable") from None
        state["last_validated_at"] = None
        return state

    def delete_credential(self, adapter_id: str) -> dict[str, object]:
        adapter = self._adapter(adapter_id)
        self._conflict_for_catalog(adapter)
        if len(adapter.credential_env_names) != 1:
            raise DataSourceConflict("credential_not_supported")
        self._ensure_configuration_dependencies()
        env_name = adapter.credential_env_names[0]
        try:
            self._credential_config_transaction(
                adapter_id,
                lambda: self._credential_store.delete(adapter_id, env_name),
            )
        except CredentialWriteNotSupported:
            raise DataSourceConflict("credential_source_read_only") from None
        except CredentialStoreUnavailable:
            raise DataSourceUnavailable("credential_store_unavailable") from None
        try:
            state = self._credential_store.state(adapter_id).to_dict()
        except CredentialStoreUnavailable:
            raise DataSourceUnavailable("credential_store_unavailable") from None
        state["last_validated_at"] = None
        return state

    def _usage_rows(self, adapter_id: str | None) -> tuple[datetime, list[dict[str, object]]]:
        catalog = self._catalog()
        adapters = [self._adapter(adapter_id)] if adapter_id is not None else list(catalog.adapters)
        self._ensure_configuration_dependencies()
        now = self._now()
        try:
            records = self._usage_store.records(now=now)
        except UsageStoreError:
            raise DataSourceUnavailable("usage_store_unavailable") from None
        day, month = now.date().isoformat(), now.strftime("%Y-%m")
        rows: list[dict[str, object]] = []
        for adapter in adapters:
            observed = False
            daily_cost = monthly_cost = Decimal("0")
            daily_units = monthly_units = Decimal("0")
            daily_requests = monthly_requests = 0
            statuses: Counter[str] = Counter()
            open_count = 0
            for record in records:
                if record.adapter_id != adapter.adapter_id or record.authorized_at.strftime("%Y-%m") != month:
                    continue
                observed = True
                cost = record.actual_cost if record.actual_cost is not None else record.estimated_cost
                monthly_cost += cost
                monthly_units += record.units
                monthly_requests += record.request_count
                statuses[record.status] += 1
                if record.actual_cost is None:
                    open_count += 1
                if record.authorized_at.date().isoformat() == day:
                    daily_cost += cost
                    daily_units += record.units
                    daily_requests += record.request_count
            rows.append({
                "adapter_id": adapter.adapter_id, "day": day, "month": month,
                "usage_status": "observed" if observed else "unobserved",
                "daily_cost": format(daily_cost, "f") if observed else None,
                "monthly_cost": format(monthly_cost, "f") if observed else None,
                "daily_request_count": daily_requests if observed else None,
                "monthly_request_count": monthly_requests if observed else None,
                "daily_units": format(daily_units, "f") if observed else None,
                "monthly_units": format(monthly_units, "f") if observed else None,
                "status_counts": dict(sorted(statuses.items())),
                "open_reservations": open_count if observed else None,
            })
        return now, rows

    @staticmethod
    def _usage_status(rows: list[dict[str, object]]) -> str:
        return "observed" if any(
            row["usage_status"] == "observed" for row in rows
        ) else "unobserved"

    def usage_document(self, adapter_id: str | None = None) -> dict[str, object]:
        now, rows = self._usage_rows(adapter_id)
        return {
            "as_of": self._timestamp(now), "timezone": "UTC",
            "usage_status": self._usage_status(rows), "adapters": rows,
        }

    def cost_document(self, adapter_id: str | None = None) -> dict[str, object]:
        config = self._load_config()
        now, usage_rows = self._usage_rows(adapter_id)
        usage_by_id = {row["adapter_id"]: row for row in usage_rows}
        adapters = [self._adapter(adapter_id)] if adapter_id is not None else list(self._catalog().adapters)
        rows = []
        for adapter in adapters:
            entry = config["adapters"].get(adapter.adapter_id, {})
            usage = usage_by_id[adapter.adapter_id]
            credential = self._credential_state(adapter, config)
            enabled = entry.get("enabled", adapter.default_enabled)
            if adapter.catalog_status is CatalogStatus.LICENSE_REQUIRED:
                status = "license_required"
            elif not enabled:
                status = "disabled"
            elif adapter.credential_env_names and not credential["configured"]:
                status = "unconfigured"
            elif config["free_only"] and adapter.billing_model in {BillingModel.PAID_API, BillingModel.ENTERPRISE_LICENSE}:
                status = "free_only"
            else:
                status = "configured"
            daily_budget, monthly_budget = entry.get("daily_budget"), entry.get("monthly_budget")
            observed = usage["usage_status"] == "observed"
            daily_remaining = (
                None if daily_budget is None or not observed
                else format(max(Decimal(daily_budget) - Decimal(usage["daily_cost"]), Decimal("0")), "f")
            )
            monthly_remaining = (
                None if monthly_budget is None or not observed
                else format(max(Decimal(monthly_budget) - Decimal(usage["monthly_cost"]), Decimal("0")), "f")
            )
            rows.append({
                "adapter_id": adapter.adapter_id, "billing_model": adapter.billing_model.value,
                "enabled": enabled, "credential_configured": credential["configured"], "status": status,
                "usage_status": usage["usage_status"],
                "day": usage["day"], "month": usage["month"],
                "daily_budget": daily_budget, "monthly_budget": monthly_budget,
                "per_request_budget": entry.get("per_request_budget"),
                "daily_cost": usage["daily_cost"], "monthly_cost": usage["monthly_cost"],
                "daily_remaining": daily_remaining, "monthly_remaining": monthly_remaining,
                "open_reservations": usage["open_reservations"],
            })
        return {
            "as_of": self._timestamp(now), "timezone": "UTC",
            "free_only": config["free_only"],
            "usage_status": self._usage_status(usage_rows),
            "adapters": rows,
        }
