"""Provider cost preflight that reserves budget before transport."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping

from .models import BillingModel
from .usage_store import (
    UsageBudgetExceeded,
    UsageRecord,
    UsageStore,
    UsageValidationError,
)


class BudgetValidationError(ValueError):
    """Raised for an invalid policy or authorization request."""


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise BudgetValidationError(f"{field} must be a Decimal")
    if not value.is_finite() or value < 0:
        raise BudgetValidationError(f"{field} must be a finite non-negative Decimal")
    _sign, digits, exponent = value.as_tuple()
    if exponent < -8 or len(digits) > 28:
        raise BudgetValidationError(f"{field} precision is invalid")
    return value


def _identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "._:-") for character in value)
    ):
        raise BudgetValidationError("adapter_id is invalid")
    return value


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
            if not isinstance(getattr(self, field), bool):
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

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "reservation_id": self.reservation_id,
            "estimated_cost": format(self.estimated_cost, "f"),
            "health_failure": self.health_failure,
        }


class BudgetGuard:
    """Apply configuration gates and reserve exact cost before any request."""

    def __init__(self, usage_store: UsageStore, policies: Mapping[str, BudgetPolicy]) -> None:
        if not isinstance(usage_store, UsageStore):
            raise BudgetValidationError("usage_store is invalid")
        if not isinstance(policies, Mapping):
            raise BudgetValidationError("policies must be a mapping")
        normalized: dict[str, BudgetPolicy] = {}
        for adapter_id, policy in policies.items():
            if not isinstance(policy, BudgetPolicy) or adapter_id != policy.adapter_id:
                raise BudgetValidationError("policy mapping is invalid")
            normalized[adapter_id] = policy
        self.usage_store = usage_store
        self._policies = normalized

    @staticmethod
    def _blocked(reason: str, estimate: Decimal) -> BudgetDecision:
        return BudgetDecision(False, reason, None, estimate, False)

    def authorize(
        self,
        adapter: object,
        *,
        estimated_cost: Decimal,
        now: datetime,
        reservation_id: str | None = None,
    ) -> BudgetDecision:
        estimate = _decimal(estimated_cost, "estimated_cost")
        adapter_id = _identifier(getattr(adapter, "adapter_id", None))
        billing_model = getattr(adapter, "billing_model", None)
        if not isinstance(billing_model, BillingModel):
            raise BudgetValidationError("adapter billing_model is invalid")
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
