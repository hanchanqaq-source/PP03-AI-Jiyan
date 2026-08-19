from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from data_sources.budgets import BudgetDecision
from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://www.alphavantage.co/"
_ENDPOINT = f"{_REFERENCE}query"
_ENV_NAME = "ALPHA_VANTAGE_API_KEY"
_MAX_ROWS = 1_000
_MAX_TEXT = 4_096
_MAX_NUMBER_TEXT = 128
_IDENTIFIER = re.compile(r"^[A-Za-z0-9.^_-]{1,64}$")
_PLAN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_CACHEABLE = {"timeout", "tls", "dns", "connection", "server_error", "rate_limited"}
_SENSITIVE_MARKERS = ("api_key", "apikey", "bearer", "credential", "password", "secret", "token")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: object, *, allow_blank: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not allow_blank and not value):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _number(value: object) -> Decimal:
    if type(value) not in {str, int, float, Decimal}:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    if type(value) is int and value.bit_length() > 333:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    raw = str(value)
    if len(raw) > _MAX_NUMBER_TEXT:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 100 or abs(parsed.as_tuple().exponent) > 100:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return parsed


def _bounded_cost(value: object) -> Decimal | None:
    if value is None:
        return None
    if type(value) is not Decimal or not value.is_finite() or value < 0 or value > Decimal("1000000"):
        raise ValueError("estimated_cost must be a bounded Decimal or None")
    if len(value.as_tuple().digits) > 28 or not -8 <= value.as_tuple().exponent <= 12:
        raise ValueError("estimated_cost precision is invalid")
    return Decimal(value)


@dataclass(frozen=True, slots=True)
class AlphaVantageEntitlement:
    capability_id: str
    plan_name: str
    available: bool
    estimated_cost: Decimal | None
    quota_remaining: int | None

    def __post_init__(self) -> None:
        if self.capability_id != "stock_history" or type(self.plan_name) is not str or not _PLAN_NAME.fullmatch(self.plan_name):
            raise ValueError("invalid Alpha Vantage entitlement identity")
        if type(self.available) is not bool:
            raise ValueError("available must be boolean")
        object.__setattr__(self, "estimated_cost", _bounded_cost(self.estimated_cost))
        if self.quota_remaining is not None and (type(self.quota_remaining) is not int or not 0 <= self.quota_remaining <= 1_000_000_000):
            raise ValueError("quota_remaining is invalid")


def _budget_snapshot(decision: object, expected_cost: Decimal) -> tuple[bool, str, str | None]:
    if type(decision) is not BudgetDecision:
        raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    try:
        snapshot = decision.to_dict()
    except Exception:
        raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE) from None
    if (
        type(snapshot) is not dict
        or set(snapshot) != {"allowed", "reason", "reservation_id", "estimated_cost", "health_failure"}
        or type(snapshot["allowed"]) is not bool
        or type(snapshot["reason"]) is not str
        or (snapshot["reservation_id"] is not None and type(snapshot["reservation_id"]) is not str)
        or type(snapshot["estimated_cost"]) is not str
        or type(snapshot["health_failure"]) is not bool
        or snapshot["estimated_cost"] != format(expected_cost, "f")
        or snapshot["health_failure"] is not False
        or snapshot["allowed"] is not (snapshot["reason"] == "authorized")
        or (not snapshot["allowed"] and snapshot["reservation_id"] is not None)
        or (snapshot["allowed"] and expected_cost > 0 and snapshot["reservation_id"] is None)
    ):
        raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    reservation_id = snapshot["reservation_id"]
    if reservation_id is not None and any(marker in reservation_id.lower() for marker in _SENSITIVE_MARKERS):
        raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    return snapshot["allowed"], snapshot["reason"], reservation_id


class AlphaVantageAdapter(BaseProvider):
    """Low-frequency Alpha Vantage fallback with explicit plan authorization."""

    descriptor = AdapterDescriptor(
        "alpha-vantage", "Alpha Vantage", "alpha_vantage", "http_client",
        (SourceRole.FALLBACK_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK), ("stock_history",),
        BillingModel.FREEMIUM, "api_key", (_ENV_NAME,), False,
        "Alpha Vantage account terms apply; capability and quota depend on the actual plan.",
        "Low-frequency fallback only; never promoted to primary market data.",
        "以 Alpha Vantage 实际返回为准", "以账户实际套餐和配额为准", "套餐成本未知；无可信能力级权益时禁止请求",
        _REFERENCE, 120, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None,
                 entitlements: tuple[AlphaVantageEntitlement, ...] = (),
                 cache_getter: Callable[[ProviderRequest], object | None] | None = None,
                 fetched_at: Callable[[], datetime] = _now) -> None:
        if type(entitlements) is not tuple or any(type(item) is not AlphaVantageEntitlement for item in entitlements):
            raise ValueError("entitlements must be an immutable Alpha Vantage entitlement tuple")
        if len({item.capability_id for item in entitlements}) != len(entitlements):
            raise ValueError("duplicate capability entitlement")
        self._http, self._credentials, self._budget_guard = http, credentials, budget_guard
        self._entitlements = {
            item.capability_id: AlphaVantageEntitlement(
                item.capability_id, item.plan_name, item.available, item.estimated_cost, item.quota_remaining
            ) for item in entitlements
        }
        self._cache_getter, self._fetched_at = cache_getter, fetched_at

    def _credential(self) -> str | None:
        try:
            value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception:
            return None
        return value if type(value) is str and value.strip() else None

    def _preflight(self, capability_id: str, now: datetime) -> tuple[str | None, AlphaVantageEntitlement | None, str | None]:
        if self._budget_guard is None:
            return "budget_guard_unavailable", None, None
        entitlement = self._entitlements.get(capability_id)
        if entitlement is None or entitlement.estimated_cost is None:
            return "cost_unknown", None, None
        if not entitlement.available:
            return "plan_unavailable", entitlement, None
        if entitlement.quota_remaining == 0:
            return "quota_exhausted", entitlement, None
        try:
            decision = self._budget_guard.authorize(self.descriptor, estimated_cost=entitlement.estimated_cost, now=now)
        except Exception:
            raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE) from None
        allowed, reason, reservation_id = _budget_snapshot(decision, entitlement.estimated_cost)
        return (None, entitlement, reservation_id) if allowed else (reason, entitlement, None)

    @staticmethod
    def _request(request: ProviderRequest) -> dict[str, object]:
        if type(request) is not ProviderRequest or request.capability_id != "stock_history" or type(request.parameters) is not dict:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if set(request.parameters) - {"symbol", "function", "interval", "outputsize", "max_age_days"}:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        symbol, function, interval = request.parameters.get("symbol"), request.parameters.get("function"), request.parameters.get("interval")
        if type(symbol) is not str or not _IDENTIFIER.fullmatch(symbol) or function != "TIME_SERIES_DAILY" or interval != "daily":
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        outputsize = request.parameters.get("outputsize", "compact")
        if outputsize not in {"compact", "full"}:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        max_age = request.parameters.get("max_age_days")
        if max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return {"symbol": symbol, "function": function, "outputsize": outputsize}

    @staticmethod
    def _payload_error(payload: dict[str, object]) -> None:
        if "Note" in payload or "Information" in payload:
            _text(payload.get("Note", payload.get("Information")))
            raise ProviderRateLimited(retry_after_seconds=0.0, reference=_REFERENCE)
        if "Error Message" in payload:
            _text(payload["Error Message"])
            raise ProviderUnavailable("authentication", reference=_REFERENCE)

    def _parse(self, payload: object, request: ProviderRequest, *, now: datetime,
               entitlement: AlphaVantageEntitlement, cached: bool) -> tuple[ProviderValue, ...]:
        if type(payload) is not dict:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._payload_error(payload)
        metadata, series = payload.get("Meta Data"), payload.get("Time Series (Daily)")
        if type(metadata) is not dict or type(series) is not dict:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not series:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(series) > _MAX_ROWS:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        symbol = _text(metadata.get("2. Symbol"))
        if symbol != request.parameters.get("symbol"):
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        _text(metadata.get("1. Information"))
        refreshed = _text(metadata.get("3. Last Refreshed"))
        timezone_name = _text(metadata.get("5. Time Zone"))
        try:
            if datetime.fromisoformat(refreshed).date() > now.date():
                raise ValueError
        except ValueError:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
        rows: list[ProviderValue] = []
        max_age = request.parameters.get("max_age_days")
        for raw_date, values in series.items():
            if type(raw_date) is not str or type(values) is not dict:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            try:
                as_of = date.fromisoformat(raw_date)
            except ValueError:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            if as_of > now.date() or set(values) != {"4. close", "5. volume"}:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            public = {"close": _number(values["4. close"]), "volume": _number(values["5. volume"])}
            source_metadata = {
                "function": "TIME_SERIES_DAILY", "interval": "daily", "symbol": symbol, "timezone": timezone_name,
                "plan_name": entitlement.plan_name, "quota_remaining": "unknown" if entitlement.quota_remaining is None else str(entitlement.quota_remaining),
                "source_reference": _REFERENCE,
            }
            if cached:
                source_metadata["cache_status"] = "fallback"
            stale = type(max_age) is int and (now.date() - as_of).days > max_age
            rows.append(ProviderValue(public, "alpha_vantage", "alpha-vantage", request.capability_id, as_of, now,
                                      "cached" if cached else ("stale" if stale else "upstream_reported"), "Alpha Vantage account terms apply", 120,
                                      None, "unknown", "daily", source_metadata))
        return tuple(rows)

    def _execute(self, request: ProviderRequest, credential: str, now: datetime,
                 entitlement: AlphaVantageEntitlement) -> tuple[ProviderValue, ...]:
        params = self._request(request)
        transport_params = dict(params)
        transport_params["apikey"] = credential
        try:
            payload = self._http.get_json(_ENDPOINT, headers={"Accept": "application/json"}, params=transport_params)
        except ProviderUnavailable as error:
            if self._cache_getter is None or error.code not in _CACHEABLE:
                raise
            cached = self._cache_getter(request)
            if cached is None:
                raise
            return self._parse(cached, request, now=now, entitlement=entitlement, cached=True)
        return self._parse(payload, request, now=now, entitlement=entitlement, cached=False)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        credential = self._credential()
        if credential is None:
            raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        now = self._fetched_at()
        blocked, entitlement, _reservation = self._preflight(request.capability_id, now)
        if blocked is not None or entitlement is None:
            raise ProviderUnavailable(blocked or "budget_status_invalid", reference=_REFERENCE)
        return self._execute(request, credential, now, entitlement)

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        credential = self._credential()
        if credential is None:
            return {"status": "unconfigured", "connected": False, "health_failure": False}
        now = self._fetched_at()
        blocked, entitlement, _reservation = self._preflight(capability_id, now)
        if blocked is not None or entitlement is None:
            return {"status": blocked or "budget_status_invalid", "connected": False, "health_failure": False, "capability_id": capability_id}
        request = ProviderRequest(capability_id, {"symbol": "IBM", "function": "TIME_SERIES_DAILY", "interval": "daily"} if parameters is None else parameters)
        try:
            rows = self._execute(request, credential, now, entitlement)
        except ProviderRateLimited as error:
            return {"status": "rate_limited", "connected": False, "health_failure": False, "capability_id": capability_id, "retry_after_seconds": error.retry_after_seconds}
        except ProviderSchemaChanged:
            return {"status": "schema_changed", "connected": False, "health_failure": True, "capability_id": capability_id}
        except ProviderUnavailable as error:
            status = "authentication_failed" if error.code == "authentication" else error.code
            return {"status": status, "connected": False, "health_failure": error.code not in {"authentication", "plan_unavailable", "quota_exhausted", "empty_result"}, "capability_id": capability_id}
        return {"status": "available", "connected": True, "health_failure": False, "capability_id": capability_id, "returned_count": len(rows)}


__all__ = ["AlphaVantageAdapter", "AlphaVantageEntitlement"]
