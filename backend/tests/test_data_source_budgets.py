from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from data_sources.budgets import (
    BudgetGuard,
    BudgetPolicy,
    BudgetValidationError,
)
from data_sources.models import BillingModel
from data_sources.usage_store import UsageStore


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def _adapter(adapter_id: str = "paid-test", billing_model: BillingModel = BillingModel.PAID_API):
    return SimpleNamespace(adapter_id=adapter_id, billing_model=billing_model)


def _policy(**overrides: object) -> BudgetPolicy:
    values: dict[str, object] = {
        "adapter_id": "paid-test",
        "enabled": True,
        "configured": True,
        "free_only": False,
        "daily_budget": Decimal("1.00"),
        "monthly_budget": Decimal("5.00"),
        "per_request_budget": Decimal("1.00"),
    }
    values.update(overrides)
    return BudgetPolicy(**values)


def _guard(tmp_path, policy: BudgetPolicy | None = None) -> BudgetGuard:
    return BudgetGuard(UsageStore(tmp_path / "data"), {"paid-test": policy or _policy()})


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"enabled": False}, "disabled"),
        ({"configured": False}, "unconfigured"),
        ({"free_only": True}, "free_only"),
    ],
)
def test_preflight_gate_blocks_before_reserving(tmp_path, overrides: dict[str, object], reason: str):
    guard = _guard(tmp_path, _policy(**overrides))

    decision = guard.authorize(_adapter(), estimated_cost=Decimal("0.01"), now=NOW)

    assert decision.allowed is False
    assert decision.reason == reason
    assert decision.reservation_id is None
    assert guard.usage_store.records(now=NOW) == ()


def test_missing_policy_is_unconfigured_and_not_a_health_failure(tmp_path):
    guard = BudgetGuard(UsageStore(tmp_path / "data"), {})

    decision = guard.authorize(_adapter("not-configured"), estimated_cost=Decimal("0.01"), now=NOW)

    assert decision.to_dict() == {
        "allowed": False,
        "reason": "unconfigured",
        "reservation_id": None,
        "estimated_cost": "0.01",
        "health_failure": False,
    }
    assert guard.usage_store.records(now=NOW) == ()


@pytest.mark.parametrize("field", ["daily_budget", "monthly_budget", "per_request_budget"])
@pytest.mark.parametrize(
    "invalid",
    [True, False, 1, 1.0, Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01"), Decimal("0.000000001")],
)
def test_policy_rejects_non_decimal_nonfinite_negative_or_excess_precision(field: str, invalid: object):
    with pytest.raises(BudgetValidationError):
        _policy(**{field: invalid})


def test_policy_rejects_non_boolean_gate_state():
    with pytest.raises(BudgetValidationError):
        _policy(enabled=1)
    with pytest.raises(BudgetValidationError):
        _policy(configured="yes")
    with pytest.raises(BudgetValidationError):
        _policy(free_only=0)


@pytest.mark.parametrize("invalid", [True, 1, 0.01, Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01"), Decimal("0.000000001")])
def test_authorize_rejects_invalid_estimated_cost_without_writing(tmp_path, invalid: object):
    guard = _guard(tmp_path)

    with pytest.raises(BudgetValidationError):
        guard.authorize(_adapter(), estimated_cost=invalid, now=NOW)

    assert guard.usage_store.records(now=NOW) == ()


def test_exact_budget_boundary_is_reserved_and_one_cent_over_is_blocked(tmp_path):
    guard = _guard(tmp_path)

    allowed = guard.authorize(
        _adapter(), estimated_cost=Decimal("1.00"), now=NOW, reservation_id="boundary-1"
    )
    blocked = guard.authorize(
        _adapter(), estimated_cost=Decimal("0.01"), now=NOW, reservation_id="boundary-2"
    )

    assert allowed.allowed is True
    assert allowed.reason == "authorized"
    assert allowed.reservation_id == "boundary-1"
    assert blocked.allowed is False
    assert blocked.reason == "daily_budget_exhausted"
    assert len(guard.usage_store.records(now=NOW)) == 1


def test_per_request_budget_blocks_before_daily_reservation(tmp_path):
    guard = _guard(tmp_path, _policy(per_request_budget=Decimal("0.50")))

    decision = guard.authorize(_adapter(), estimated_cost=Decimal("0.51"), now=NOW)

    assert decision.allowed is False
    assert decision.reason == "per_request_budget_exhausted"
    assert guard.usage_store.records(now=NOW) == ()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"daily_budget": Decimal("0")}, "daily_budget_not_configured"),
        ({"monthly_budget": Decimal("0")}, "monthly_budget_not_configured"),
        ({"per_request_budget": Decimal("0")}, "per_request_budget_not_configured"),
    ],
)
def test_zero_budget_fails_closed_as_not_configured(tmp_path, overrides: dict[str, object], reason: str):
    decision = _guard(tmp_path, _policy(**overrides)).authorize(
        _adapter(), estimated_cost=Decimal("0.01"), now=NOW
    )

    assert decision.allowed is False
    assert decision.reason == reason


def test_daily_and_monthly_exhaustion_are_distinct(tmp_path):
    daily = _guard(tmp_path / "daily", _policy(daily_budget=Decimal("0.02")))
    assert daily.authorize(_adapter(), estimated_cost=Decimal("0.02"), now=NOW).allowed
    assert daily.authorize(_adapter(), estimated_cost=Decimal("0.01"), now=NOW).reason == "daily_budget_exhausted"

    monthly = _guard(
        tmp_path / "monthly",
        _policy(daily_budget=Decimal("10.00"), monthly_budget=Decimal("0.02")),
    )
    assert monthly.authorize(_adapter(), estimated_cost=Decimal("0.02"), now=NOW).allowed
    assert monthly.authorize(_adapter(), estimated_cost=Decimal("0.01"), now=NOW).reason == "monthly_budget_exhausted"


def test_utc_midnight_resets_daily_but_not_monthly_budget(tmp_path):
    guard = _guard(tmp_path, _policy(daily_budget=Decimal("0.01"), monthly_budget=Decimal("0.02")))
    before_midnight = datetime(2026, 8, 19, 23, 59, tzinfo=timezone.utc)
    after_midnight_in_non_utc_zone = datetime(2026, 8, 20, 8, 1, tzinfo=timezone(timedelta(hours=8)))

    assert guard.authorize(_adapter(), estimated_cost=Decimal("0.01"), now=before_midnight).allowed
    second = guard.authorize(
        _adapter(), estimated_cost=Decimal("0.01"), now=after_midnight_in_non_utc_zone
    )

    assert second.allowed is True
    assert guard.authorize(
        _adapter(),
        estimated_cost=Decimal("0.01"),
        now=after_midnight_in_non_utc_zone + timedelta(minutes=1),
    ).reason == "daily_budget_exhausted"


def test_utc_month_rollover_resets_daily_and_monthly_budget(tmp_path):
    guard = _guard(tmp_path, _policy(daily_budget=Decimal("0.01"), monthly_budget=Decimal("0.01")))
    august = datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)
    september = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    assert guard.authorize(_adapter(), estimated_cost=Decimal("0.01"), now=august).allowed
    assert guard.authorize(_adapter(), estimated_cost=Decimal("0.01"), now=september).allowed


def test_zero_cost_free_key_request_is_allowed_in_free_only_mode_without_paid_budgets(tmp_path):
    policy = _policy(
        free_only=True,
        daily_budget=Decimal("0"),
        monthly_budget=Decimal("0"),
        per_request_budget=Decimal("0"),
    )
    guard = _guard(tmp_path, policy)

    decision = guard.authorize(
        _adapter(billing_model=BillingModel.FREE_KEY),
        estimated_cost=Decimal("0"),
        now=NOW,
        reservation_id="free-request",
    )

    assert decision.allowed is True
    assert decision.reservation_id == "free-request"


def test_record_reconciles_reserved_cost_and_is_idempotent(tmp_path):
    guard = _guard(tmp_path)
    decision = guard.authorize(
        _adapter(), estimated_cost=Decimal("0.10"), now=NOW, reservation_id="reconcile-once"
    )

    first = guard.record(
        "paid-test",
        reservation_id=decision.reservation_id,
        actual_cost=Decimal("0.07"),
        request_count=1,
        status="succeeded",
        units=Decimal("3"),
        now=NOW + timedelta(seconds=1),
    )
    second = guard.record(
        "paid-test",
        reservation_id=decision.reservation_id,
        actual_cost=Decimal("0.07"),
        request_count=1,
        status="succeeded",
        units=Decimal("3"),
        now=NOW + timedelta(seconds=1),
    )

    assert first == second
    summary = guard.usage_store.summary("paid-test", now=NOW + timedelta(seconds=1))
    assert summary.daily_cost == Decimal("0.07")
    assert summary.daily_request_count == 1


def test_guard_rejects_adapter_descriptor_that_does_not_match_policy(tmp_path):
    guard = _guard(tmp_path)
    malformed = SimpleNamespace(adapter_id="paid-test", billing_model="paid_api")

    with pytest.raises(BudgetValidationError):
        guard.authorize(malformed, estimated_cost=Decimal("0.01"), now=NOW)
