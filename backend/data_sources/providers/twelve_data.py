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


_REFERENCE = "https://api.twelvedata.com/"
_ENDPOINT = f"{_REFERENCE}time_series"
_ENV_NAME = "TWELVE_DATA_API_KEY"
_MAX_ROWS, _MAX_TEXT = 1_000, 4_096
_SYMBOL = re.compile(r"^[A-Za-z0-9.^_:/-]{1,64}$")
_INTERVALS = {"1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "1day", "1week", "1month"}
_PLAN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_CACHEABLE = {"timeout", "tls", "dns", "connection", "server_error", "rate_limited"}
_SENSITIVE = ("api_key", "apikey", "bearer", "credential", "password", "secret", "token")


def _now() -> datetime: return datetime.now(timezone.utc)


def _text(value: object, *, allow_blank: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not allow_blank and not value): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _number(value: object) -> Decimal:
    if type(value) not in {str, int, float, Decimal} or (type(value) is int and value.bit_length() > 333): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    raw = str(value)
    if len(raw) > 128: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try: result = Decimal(raw)
    except (InvalidOperation, ValueError): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
    if not result.is_finite() or len(result.as_tuple().digits) > 100 or abs(result.as_tuple().exponent) > 100: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return result


def _cost(value: object) -> Decimal | None:
    if value is None: return None
    if type(value) is not Decimal or not value.is_finite() or value < 0 or value > Decimal("1000000") or len(value.as_tuple().digits) > 28 or not -8 <= value.as_tuple().exponent <= 12: raise ValueError("estimated_cost is invalid")
    return Decimal(value)


@dataclass(frozen=True, slots=True)
class TwelveDataEntitlement:
    capability_id: str
    plan_name: str
    available: bool
    estimated_cost: Decimal | None
    quota_remaining: int | None

    def __post_init__(self) -> None:
        if self.capability_id != "stock_history" or type(self.plan_name) is not str or not _PLAN.fullmatch(self.plan_name): raise ValueError("invalid Twelve Data entitlement")
        if type(self.available) is not bool: raise ValueError("available must be boolean")
        object.__setattr__(self, "estimated_cost", _cost(self.estimated_cost))
        if self.quota_remaining is not None and (type(self.quota_remaining) is not int or not 0 <= self.quota_remaining <= 1_000_000_000): raise ValueError("quota_remaining is invalid")


def _decision(value: object, estimate: Decimal) -> tuple[bool, str]:
    if type(value) is not BudgetDecision: raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    try: row = value.to_dict()
    except Exception: raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE) from None
    if (type(row) is not dict or set(row) != {"allowed", "reason", "reservation_id", "estimated_cost", "health_failure"} or type(row["allowed"]) is not bool
        or type(row["reason"]) is not str or (row["reservation_id"] is not None and type(row["reservation_id"]) is not str) or type(row["estimated_cost"]) is not str
        or type(row["health_failure"]) is not bool or row["estimated_cost"] != format(estimate, "f") or row["health_failure"] is not False
        or row["allowed"] is not (row["reason"] == "authorized") or (not row["allowed"] and row["reservation_id"] is not None)
        or (row["allowed"] and estimate > 0 and row["reservation_id"] is None)):
        raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    if row["reservation_id"] is not None and any(term in row["reservation_id"].lower() for term in _SENSITIVE): raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    return row["allowed"], row["reason"]


class TwelveDataAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "twelve-data", "Twelve Data", "twelve_data", "http_client", (SourceRole.FALLBACK_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK),
        ("stock_history",), BillingModel.FREEMIUM, "api_key", (_ENV_NAME,), False,
        "Twelve Data account terms apply; interval and credits depend on the actual plan.",
        "Fallback market history with explicit plan and credit metadata.", "以 Twelve Data 实际返回为准", "以账户实际套餐、能力和 credits 为准",
        "套餐成本未知；无可信能力级权益时禁止请求", _REFERENCE, 125, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None, entitlements: tuple[TwelveDataEntitlement, ...] = (),
                 cache_getter: Callable[[ProviderRequest], object | None] | None = None, fetched_at: Callable[[], datetime] = _now) -> None:
        if type(entitlements) is not tuple or any(type(item) is not TwelveDataEntitlement for item in entitlements) or len({item.capability_id for item in entitlements}) != len(entitlements): raise ValueError("invalid Twelve Data entitlements")
        self._http, self._credentials, self._budget_guard = http, credentials, budget_guard
        self._entitlements = {
            item.capability_id: TwelveDataEntitlement(
                item.capability_id, item.plan_name, item.available, item.estimated_cost, item.quota_remaining
            ) for item in entitlements
        }
        self._cache_getter, self._fetched_at = cache_getter, fetched_at

    def _credential(self) -> str | None:
        try: value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception: return None
        return value if type(value) is str and value.strip() else None

    def _preflight(self, capability: str, now: datetime) -> tuple[str | None, TwelveDataEntitlement | None]:
        if self._budget_guard is None: return "budget_guard_unavailable", None
        ent = self._entitlements.get(capability)
        if ent is None or ent.estimated_cost is None: return "cost_unknown", None
        if not ent.available: return "plan_unavailable", ent
        if ent.quota_remaining == 0: return "quota_exhausted", ent
        try: raw = self._budget_guard.authorize(self.descriptor, estimated_cost=ent.estimated_cost, now=now)
        except Exception: raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE) from None
        allowed, reason = _decision(raw, ent.estimated_cost)
        return (None, ent) if allowed else (reason, ent)

    @staticmethod
    def _request(request: ProviderRequest) -> dict[str, object]:
        if type(request) is not ProviderRequest or request.capability_id != "stock_history" or type(request.parameters) is not dict or set(request.parameters) - {"symbol", "interval", "outputsize", "max_age_days"}: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        symbol, interval, outputsize = request.parameters.get("symbol"), request.parameters.get("interval"), request.parameters.get("outputsize", 30)
        max_age = request.parameters.get("max_age_days")
        if type(symbol) is not str or not _SYMBOL.fullmatch(symbol) or type(interval) is not str or interval not in _INTERVALS or type(outputsize) is not int or not 1 <= outputsize <= 1_000 or (max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500)): raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return {"symbol": symbol, "interval": interval, "outputsize": outputsize, "format": "JSON"}

    @staticmethod
    def _error(payload: dict[str, object]) -> None:
        if payload.get("status") != "error": return
        code, message = payload.get("code"), _text(payload.get("message"))
        if type(code) is not int: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if code == 429:
            retry = payload.get("retry_after", 0)
            if type(retry) not in {int, float} or type(retry) is bool or not 0 <= retry <= 60: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            raise ProviderRateLimited(retry_after_seconds=float(retry), reference=_REFERENCE)
        if code == 401: raise ProviderUnavailable("authentication", reference=_REFERENCE)
        if code == 403 or "plan" in message.lower(): raise ProviderUnavailable("plan_unavailable", reference=_REFERENCE)
        raise ProviderUnavailable("provider_error", reference=_REFERENCE)

    def _parse(self, payload: object, request: ProviderRequest, *, now: datetime, ent: TwelveDataEntitlement, cached: bool) -> tuple[ProviderValue, ...]:
        if type(payload) is not dict: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._error(payload)
        if payload.get("status") != "ok": raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        meta, values, credits = payload.get("meta"), payload.get("values"), payload.get("credits")
        if type(meta) is not dict or type(values) is not list or type(credits) is not int or not 0 <= credits <= 1_000_000_000: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not values: raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(values) > _MAX_ROWS: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        symbol, interval = _text(meta.get("symbol")), _text(meta.get("interval"))
        timezone_name, currency = _text(meta.get("exchange_timezone")), _text(meta.get("currency"), allow_blank=True)
        if symbol != request.parameters.get("symbol") or interval != request.parameters.get("interval"): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        rows = []
        max_age = request.parameters.get("max_age_days")
        for item in values:
            if type(item) is not dict or set(item) != {"datetime", "open", "high", "low", "close", "volume"}: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            raw_date = _text(item["datetime"])
            try: as_of = datetime.fromisoformat(raw_date).date()
            except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            if as_of > now.date(): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            public = {field: _number(item[field]) for field in ("open", "high", "low", "close", "volume")}
            metadata = {"symbol": symbol, "interval": interval, "timezone": timezone_name, "credits_used": str(credits), "plan_name": ent.plan_name, "quota_remaining": "unknown" if ent.quota_remaining is None else str(ent.quota_remaining), "source_reference": _REFERENCE}
            if cached: metadata["cache_status"] = "fallback"
            stale = type(max_age) is int and (now.date() - as_of).days > max_age
            rows.append(ProviderValue(public, "twelve_data", "twelve-data", request.capability_id, as_of, now, "cached" if cached else ("stale" if stale else "upstream_reported"), "Twelve Data account terms apply", 125, None, currency or "unknown", interval, metadata))
        return tuple(rows)

    def _execute(self, request: ProviderRequest, credential: str, now: datetime, ent: TwelveDataEntitlement) -> tuple[ProviderValue, ...]:
        params = self._request(request); params["apikey"] = credential
        try: payload = self._http.get_json(_ENDPOINT, headers={"Accept": "application/json"}, params=params)
        except ProviderUnavailable as error:
            if self._cache_getter is None or error.code not in _CACHEABLE: raise
            payload = self._cache_getter(request)
            if payload is None: raise
            cached = True
        else: cached = False
        return self._parse(payload, request, now=now, ent=ent, cached=cached)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        credential = self._credential()
        if credential is None: raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        now = self._fetched_at(); blocked, ent = self._preflight(request.capability_id, now)
        if blocked is not None or ent is None: raise ProviderUnavailable(blocked or "budget_status_invalid", reference=_REFERENCE)
        return self._execute(request, credential, now, ent)

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        credential = self._credential()
        if credential is None: return {"status": "unconfigured", "connected": False, "health_failure": False}
        now = self._fetched_at(); blocked, ent = self._preflight(capability_id, now)
        if blocked is not None or ent is None: return {"status": blocked or "budget_status_invalid", "connected": False, "health_failure": False, "capability_id": capability_id}
        request = ProviderRequest(capability_id, {"symbol": "AAPL", "interval": "1day", "outputsize": 1} if parameters is None else parameters)
        try: rows = self._execute(request, credential, now, ent)
        except ProviderRateLimited as error: return {"status": "rate_limited", "connected": False, "health_failure": False, "capability_id": capability_id, "retry_after_seconds": error.retry_after_seconds}
        except ProviderSchemaChanged: return {"status": "schema_changed", "connected": False, "health_failure": True, "capability_id": capability_id}
        except ProviderUnavailable as error:
            status = "authentication_failed" if error.code == "authentication" else error.code
            return {"status": status, "connected": False, "health_failure": error.code not in {"authentication", "plan_unavailable", "quota_exhausted", "empty_result"}, "capability_id": capability_id}
        return {"status": "available", "connected": True, "health_failure": False, "capability_id": capability_id, "returned_count": len(rows)}


__all__ = ["TwelveDataAdapter", "TwelveDataEntitlement"]
