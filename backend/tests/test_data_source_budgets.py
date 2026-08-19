from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from data_sources.budgets import (
    BudgetDecision,
    BudgetGuard,
    BudgetPolicy,
    BudgetValidationError,
)
from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, SourceRole
from data_sources.usage_store import UsageStore


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class _SpoofedIdentifier(str):
    def __hash__(self) -> int:
        return hash("paid-test")

    def __eq__(self, other: object) -> bool:
        return other in {"paid-test", "other-id"}

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)


class _CustomMapping(dict):
    pass


class _SpoofedInteger(int):
    pass


def _adapter(adapter_id: str = "paid-test", billing_model: BillingModel = BillingModel.PAID_API):
    return _trusted_adapter(adapter_id, billing_model)


def _trusted_adapter(
    adapter_id: str = "paid-test", billing_model: BillingModel = BillingModel.PAID_API
) -> AdapterDescriptor:
    return AdapterDescriptor(
        adapter_id=adapter_id,
        adapter_name="Test paid provider",
        source_family_id="test-family",
        provider_type="http_client",
        source_roles=(SourceRole.MARKET_DATA,),
        capability_ids=("test-capability",),
        billing_model=billing_model,
        auth_type="api_key",
        credential_env_names=("TEST_API_KEY",),
        default_enabled=False,
        license_note="fixture",
        usage_note="fixture",
        data_delay="fixture",
        quota_policy="fixture",
        cost_policy="fixture",
        configured_reference="https://example.test/",
        current_provider_priority=10,
        catalog_status=CatalogStatus.UNCONFIGURED,
    )


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


def _guard(
    tmp_path,
    policy: BudgetPolicy | None = None,
    adapter: AdapterDescriptor | None = None,
) -> BudgetGuard:
    trusted = adapter or _trusted_adapter()
    return BudgetGuard(
        UsageStore(tmp_path / "data"),
        {"paid-test": policy or _policy()},
        trusted_adapters={trusted.adapter_id: trusted},
    )


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
    trusted = _trusted_adapter("not-configured")
    guard = BudgetGuard(
        UsageStore(tmp_path / "data"), {}, trusted_adapters={"not-configured": trusted}
    )

    decision = guard.authorize(trusted, estimated_cost=Decimal("0.01"), now=NOW)

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
    adapter = _trusted_adapter(billing_model=BillingModel.FREE_KEY)
    guard = _guard(tmp_path, policy, adapter)

    decision = guard.authorize(
        adapter,
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


def test_guard_binds_authorization_to_trusted_descriptor_and_rejects_billing_spoof(tmp_path):
    trusted = _trusted_adapter()
    guard = BudgetGuard(
        UsageStore(tmp_path / "data"),
        {"paid-test": _policy(free_only=True)},
        trusted_adapters={"paid-test": trusted},
    )
    spoof = _trusted_adapter(billing_model=BillingModel.FREE_NO_KEY)

    with pytest.raises(BudgetValidationError, match="trusted"):
        guard.authorize(spoof, estimated_cost=Decimal("0"), now=NOW)

    assert guard.usage_store.records(now=NOW) == ()


def test_guard_rejects_unknown_descriptor_before_policy_or_reservation(tmp_path):
    trusted = _trusted_adapter()
    guard = BudgetGuard(
        UsageStore(tmp_path / "data"),
        {"paid-test": _policy()},
        trusted_adapters={"paid-test": trusted},
    )

    with pytest.raises(BudgetValidationError, match="trusted"):
        guard.authorize(_trusted_adapter("unknown"), estimated_cost=Decimal("0.01"), now=NOW)

    assert guard.usage_store.records(now=NOW) == ()


def test_guard_authorizes_only_matching_trusted_descriptor(tmp_path):
    trusted = _trusted_adapter()
    guard = BudgetGuard(
        UsageStore(tmp_path / "data"),
        {"paid-test": _policy()},
        trusted_adapters={"paid-test": trusted},
    )

    decision = guard.authorize(
        trusted,
        estimated_cost=Decimal("0.01"),
        now=NOW,
        reservation_id="trusted-request",
    )

    assert decision.allowed is True
    assert decision.reservation_id == "trusted-request"


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1E+1000000"),
        Decimal("0E+1000000"),
        Decimal("-0E+1000000"),
        Decimal("1000000000000.01"),
        Decimal("9" * 29),
    ],
)
def test_policy_rejects_pathological_or_above_ceiling_decimal_magnitude(value: Decimal):
    with pytest.raises(BudgetValidationError):
        _policy(daily_budget=value)


@pytest.mark.parametrize(
    "value",
    [1.0, Decimal("1E+1000000"), Decimal("0E+1000000"), Decimal("-0E+1000000"), Decimal("1000000000000.01")],
)
def test_public_budget_decision_rejects_unsafe_decimal_before_serialization(value: object):
    with pytest.raises(BudgetValidationError):
        BudgetDecision(True, "authorized", "decision-id", value)


def test_budget_decision_revalidates_decimal_at_serialization_boundary():
    decision = BudgetDecision(
        True, "authorized", "decision-id", Decimal("0.01")
    )
    object.__setattr__(decision, "estimated_cost", Decimal("0E+13"))

    with pytest.raises(BudgetValidationError):
        decision.to_dict()


@pytest.mark.parametrize(
    "value",
    [Decimal("0E+12"), Decimal("-0E+12"), Decimal("1E+12"), Decimal("1E-8")],
)
def test_budget_decimal_accepts_explicit_exponent_boundaries(value: Decimal):
    policy = _policy(daily_budget=value)

    assert policy.daily_budget.as_tuple() == value.as_tuple()


@pytest.mark.parametrize("value", [Decimal("0E+13"), Decimal("-0E+13"), Decimal("1E+13"), Decimal("1E-9")])
def test_budget_decimal_rejects_values_outside_exponent_boundaries(value: Decimal):
    with pytest.raises(BudgetValidationError):
        _policy(daily_budget=value)


def test_guard_snapshots_policy_values_and_input_mappings_against_alias_mutation(tmp_path):
    descriptor = _trusted_adapter()
    policy = _policy(free_only=True)
    policies = {"paid-test": policy}
    trusted = {"paid-test": descriptor}
    guard = BudgetGuard(
        UsageStore(tmp_path / "data"), policies, trusted_adapters=trusted
    )

    object.__setattr__(policy, "free_only", False)
    object.__setattr__(policy, "daily_budget", Decimal("1000000000000"))
    policies.clear()
    trusted.clear()

    decision = guard.authorize(
        _trusted_adapter(), estimated_cost=Decimal("0.01"), now=NOW
    )

    assert decision.allowed is False
    assert decision.reason == "free_only"
    assert guard.usage_store.records(now=NOW) == ()


def test_guard_snapshots_descriptor_primitives_against_object_setattr_mutation(tmp_path):
    descriptor = _trusted_adapter()
    guard = BudgetGuard(
        UsageStore(tmp_path / "data"),
        {"paid-test": _policy(free_only=True)},
        trusted_adapters={"paid-test": descriptor},
    )

    object.__setattr__(descriptor, "billing_model", BillingModel.FREE_NO_KEY)
    object.__setattr__(descriptor, "adapter_id", "mutated-id")

    with pytest.raises(BudgetValidationError, match="trusted"):
        guard.authorize(descriptor, estimated_cost=Decimal("0"), now=NOW)

    unchanged = guard.authorize(
        _trusted_adapter(), estimated_cost=Decimal("0.01"), now=NOW
    )
    assert unchanged.allowed is False
    assert unchanged.reason == "free_only"


def test_budget_policy_rejects_custom_string_adapter_identity():
    with pytest.raises(BudgetValidationError):
        _policy(adapter_id=_SpoofedIdentifier("paid-test"))


def test_guard_rejects_custom_mapping_and_mapping_key_before_hash_or_equality(tmp_path):
    descriptor = _trusted_adapter()
    policy = _policy()

    with pytest.raises(BudgetValidationError):
        BudgetGuard(
            UsageStore(tmp_path / "custom-map"),
            _CustomMapping({"paid-test": policy}),
            trusted_adapters={"paid-test": descriptor},
        )

    with pytest.raises(BudgetValidationError):
        BudgetGuard(
            UsageStore(tmp_path / "custom-key"),
            {_SpoofedIdentifier("paid-test"): policy},
            trusted_adapters={"paid-test": descriptor},
        )


def test_guard_rejects_descriptor_with_custom_string_identity(tmp_path):
    descriptor = _trusted_adapter()
    object.__setattr__(descriptor, "adapter_id", _SpoofedIdentifier("paid-test"))

    with pytest.raises(BudgetValidationError):
        BudgetGuard(
            UsageStore(tmp_path / "data"),
            {"paid-test": _policy()},
            trusted_adapters={"paid-test": descriptor},
        )


def test_budget_decision_rejects_custom_reason_and_reservation_strings():
    with pytest.raises(BudgetValidationError):
        BudgetDecision(
            False, _SpoofedIdentifier("authorized"), None, Decimal("0")
        )
    with pytest.raises(BudgetValidationError):
        BudgetDecision(
            True, "authorized", _SpoofedIdentifier("paid-test"), Decimal("0")
        )


def test_guard_rejects_descriptor_integer_subclass_before_identity_snapshot(tmp_path):
    descriptor = _trusted_adapter()
    object.__setattr__(descriptor, "current_provider_priority", _SpoofedInteger(10))

    with pytest.raises(BudgetValidationError):
        BudgetGuard(
            UsageStore(tmp_path / "data"),
            {"paid-test": _policy()},
            trusted_adapters={"paid-test": descriptor},
        )


def test_budget_decision_revalidates_exact_string_type_when_serialized():
    decision = BudgetDecision(False, "free_only", None, Decimal("0"))
    object.__setattr__(decision, "reason", _SpoofedIdentifier("free_only"))

    with pytest.raises(BudgetValidationError):
        decision.to_dict()
