"""Provider cost preflight that reserves budget before transport."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re
from typing import Mapping

from .models import AdapterDescriptor, BillingModel, CatalogStatus, SourceRole
from .usage_store import (
    UsageBudgetExceeded,
    UsageRecord,
    UsageStore,
    UsageValidationError,
)


class BudgetValidationError(ValueError):
    """Raised for an invalid policy or authorization request."""


_MAX_DECIMAL_ABSOLUTE = Decimal("1000000000000")
_MAX_DECIMAL_POSITIVE_EXPONENT = 12
_MAX_TEXT_LENGTH = 8_192
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _decimal(value: object, field: str) -> Decimal:
    if type(value) is not Decimal:
        raise BudgetValidationError(f"{field} must be a Decimal")
    if not value.is_finite():
        raise BudgetValidationError(f"{field} must be a finite non-negative Decimal")
    sign, digits, exponent = value.as_tuple()
    if (
        type(exponent) is not int
        or exponent < -8
        or exponent > _MAX_DECIMAL_POSITIVE_EXPONENT
        or len(digits) > 28
    ):
        raise BudgetValidationError(f"{field} precision is invalid")
    if value < 0 or value > _MAX_DECIMAL_ABSOLUTE:
        raise BudgetValidationError(f"{field} must be a finite non-negative Decimal")
    return Decimal((sign, digits, exponent))


def _identifier(value: object) -> str:
    if type(value) is not str or not _SAFE_IDENTIFIER.fullmatch(value):
        raise BudgetValidationError("adapter_id is invalid")
    return value.encode("utf-8").decode("utf-8")


def _text(value: object, field: str) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT_LENGTH:
        raise BudgetValidationError(f"trusted descriptor {field} is invalid")
    return value.encode("utf-8").decode("utf-8")


def _text_tuple(value: object, field: str) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > 128:
        raise BudgetValidationError(f"trusted descriptor {field} is invalid")
    return tuple(_text(item, field) for item in value)


@dataclass(frozen=True, slots=True)
class _TrustedAdapterIdentity:
    adapter_id: str
    billing_model: BillingModel
    canonical: tuple[object, ...]


def _descriptor_identity(descriptor: object) -> _TrustedAdapterIdentity:
    if type(descriptor) is not AdapterDescriptor:
        raise BudgetValidationError("adapter is not a trusted descriptor")
    adapter_id = _identifier(descriptor.adapter_id)
    if type(descriptor.source_roles) is not tuple or len(descriptor.source_roles) > 32:
        raise BudgetValidationError("trusted descriptor source_roles is invalid")
    if any(type(role) is not SourceRole for role in descriptor.source_roles):
        raise BudgetValidationError("trusted descriptor source_roles is invalid")
    if type(descriptor.billing_model) is not BillingModel:
        raise BudgetValidationError("trusted descriptor billing_model is invalid")
    if type(descriptor.catalog_status) is not CatalogStatus:
        raise BudgetValidationError("trusted descriptor catalog_status is invalid")
    if type(descriptor.default_enabled) is not bool:
        raise BudgetValidationError("trusted descriptor default_enabled is invalid")
    if (
        type(descriptor.current_provider_priority) is not int
        or abs(descriptor.current_provider_priority) > 1_000_000
    ):
        raise BudgetValidationError("trusted descriptor priority is invalid")
    billing_model = BillingModel(descriptor.billing_model.value)
    canonical: tuple[object, ...] = (
        adapter_id,
        _text(descriptor.adapter_name, "adapter_name"),
        _text(descriptor.source_family_id, "source_family_id"),
        _text(descriptor.provider_type, "provider_type"),
        tuple(role.value for role in descriptor.source_roles),
        _text_tuple(descriptor.capability_ids, "capability_ids"),
        billing_model.value,
        _text(descriptor.auth_type, "auth_type"),
        _text_tuple(descriptor.credential_env_names, "credential_env_names"),
        descriptor.default_enabled,
        _text(descriptor.license_note, "license_note"),
        _text(descriptor.usage_note, "usage_note"),
        _text(descriptor.data_delay, "data_delay"),
        _text(descriptor.quota_policy, "quota_policy"),
        _text(descriptor.cost_policy, "cost_policy"),
        _text(descriptor.configured_reference, "configured_reference"),
        descriptor.current_provider_priority,
        descriptor.catalog_status.value,
    )
    return _TrustedAdapterIdentity(adapter_id, billing_model, canonical)


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    adapter_id: str
    enabled: bool
    configured: bool
    free_only: bool
    daily_budget: Decimal
    monthly_budget: Decimal
    per_request_budget: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _identifier(self.adapter_id))
        for field in ("enabled", "configured", "free_only"):
            if type(getattr(self, field)) is not bool:
                raise BudgetValidationError(f"{field} must be boolean")
        for field in ("daily_budget", "monthly_budget", "per_request_budget"):
            object.__setattr__(self, field, _decimal(getattr(self, field), field))


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    reason: str
    reservation_id: str | None
    estimated_cost: Decimal
    health_failure: bool = False

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool or type(self.health_failure) is not bool:
            raise BudgetValidationError("decision flags must be boolean")
        if type(self.reason) is not str or not _SAFE_REASON.fullmatch(self.reason):
            raise BudgetValidationError("decision reason is invalid")
        object.__setattr__(self, "reason", self.reason.encode("utf-8").decode("utf-8"))
        if self.reservation_id is not None:
            object.__setattr__(self, "reservation_id", _identifier(self.reservation_id))
        object.__setattr__(self, "estimated_cost", _decimal(self.estimated_cost, "estimated_cost"))

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "reservation_id": self.reservation_id,
            "estimated_cost": format(_decimal(self.estimated_cost, "estimated_cost"), "f"),
            "health_failure": self.health_failure,
        }


class BudgetGuard:
    """Apply configuration gates and reserve exact cost before any request."""

    def __init__(
        self,
        usage_store: UsageStore,
        policies: Mapping[str, BudgetPolicy],
        *,
        trusted_adapters: Mapping[str, AdapterDescriptor],
    ) -> None:
        if type(usage_store) is not UsageStore:
            raise BudgetValidationError("usage_store is invalid")
        if type(policies) is not dict:
            raise BudgetValidationError("policies must be a mapping")
        normalized: dict[str, BudgetPolicy] = {}
        for adapter_id, policy in policies.items():
            normalized_id = _identifier(adapter_id)
            if type(policy) is not BudgetPolicy or normalized_id != policy.adapter_id:
                raise BudgetValidationError("policy mapping is invalid")
            normalized[normalized_id] = BudgetPolicy(
                adapter_id=policy.adapter_id,
                enabled=policy.enabled,
                configured=policy.configured,
                free_only=policy.free_only,
                daily_budget=policy.daily_budget,
                monthly_budget=policy.monthly_budget,
                per_request_budget=policy.per_request_budget,
            )
        if type(trusted_adapters) is not dict:
            raise BudgetValidationError("trusted adapters must be a mapping")
        trusted: dict[str, _TrustedAdapterIdentity] = {}
        for adapter_id, descriptor in trusted_adapters.items():
            normalized_id = _identifier(adapter_id)
            identity = _descriptor_identity(descriptor)
            if normalized_id != identity.adapter_id:
                raise BudgetValidationError("trusted adapter mapping is invalid")
            trusted[normalized_id] = identity
        if set(normalized) - set(trusted):
            raise BudgetValidationError("policy is missing a trusted adapter")
        self.usage_store = usage_store
        self._policies = normalized
        self._trusted_adapters = trusted

    @staticmethod
    def _blocked(reason: str, estimate: Decimal) -> BudgetDecision:
        return BudgetDecision(False, reason, None, estimate, False)

    def authorize(
        self,
        adapter: AdapterDescriptor,
        *,
        estimated_cost: Decimal,
        now: datetime,
        reservation_id: str | None = None,
    ) -> BudgetDecision:
        estimate = _decimal(estimated_cost, "estimated_cost")
        candidate = _descriptor_identity(adapter)
        adapter_id = candidate.adapter_id
        trusted = self._trusted_adapters.get(adapter_id)
        if trusted is None:
            raise BudgetValidationError("adapter is not in the trusted catalog")
        if candidate.canonical != trusted.canonical:
            raise BudgetValidationError("adapter does not match the trusted catalog")
        billing_model = trusted.billing_model
        policy = self._policies.get(adapter_id)
        if policy is None:
            return self._blocked("unconfigured", estimate)
        if not policy.enabled:
            return self._blocked("disabled", estimate)
        if not policy.configured:
            return self._blocked("unconfigured", estimate)

        always_paid = billing_model in {BillingModel.PAID_API, BillingModel.ENTERPRISE_LICENSE}
        cost_bearing = always_paid or estimate > 0
        if policy.free_only and cost_bearing:
            return self._blocked("free_only", estimate)
        if cost_bearing:
            if policy.daily_budget == 0:
                return self._blocked("daily_budget_not_configured", estimate)
            if policy.monthly_budget == 0:
                return self._blocked("monthly_budget_not_configured", estimate)
            if policy.per_request_budget == 0:
                return self._blocked("per_request_budget_not_configured", estimate)
            if estimate > policy.per_request_budget:
                return self._blocked("per_request_budget_exhausted", estimate)
        try:
            reservation = self.usage_store.reserve(
                adapter_id,
                estimated_cost=estimate,
                daily_budget=policy.daily_budget,
                monthly_budget=policy.monthly_budget,
                now=now,
                reservation_id=reservation_id,
            )
        except UsageBudgetExceeded as exc:
            return self._blocked(exc.reason, estimate)
        except UsageValidationError as exc:
            raise BudgetValidationError(str(exc)) from None
        return BudgetDecision(True, "authorized", reservation.reservation_id, estimate, False)

    def record(
        self,
        adapter_id: str,
        *,
        reservation_id: str | None,
        actual_cost: Decimal,
        request_count: int,
        status: str,
        units: Decimal,
        now: datetime,
    ) -> UsageRecord:
        normalized_id = _identifier(adapter_id)
        if normalized_id not in self._policies:
            raise BudgetValidationError("adapter is unconfigured")
        if reservation_id is None:
            raise BudgetValidationError("reservation_id is required")
        try:
            return self.usage_store.reconcile(
                normalized_id,
                reservation_id=reservation_id,
                actual_cost=actual_cost,
                request_count=request_count,
                status=status,
                units=units,
                now=now,
            )
        except UsageValidationError as exc:
            raise BudgetValidationError(str(exc)) from None


__all__ = ["BudgetDecision", "BudgetGuard", "BudgetPolicy", "BudgetValidationError"]
