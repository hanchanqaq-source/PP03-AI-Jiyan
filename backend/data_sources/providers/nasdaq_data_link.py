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


_REFERENCE = "https://data.nasdaq.com/"
_BASE = f"{_REFERENCE}api/v3/datasets"
_ENV_NAME = "NASDAQ_DATA_LINK_API_KEY"
_MAX_ROWS, _MAX_TEXT = 1_000, 4_096
_RESOURCE = re.compile(r"^[A-Z0-9_]{1,64}$")
_PLAN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_KNOWN_FREE = frozenset({("FRED", "DFF"), ("FRED", "GDP")})
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
    try: parsed = Decimal(raw)
    except (InvalidOperation, ValueError): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 100 or abs(parsed.as_tuple().exponent) > 100: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return parsed


def _cost(value: object) -> Decimal | None:
    if value is None: return None
    if type(value) is not Decimal or not value.is_finite() or value < 0 or value > Decimal("1000000") or len(value.as_tuple().digits) > 28 or not -8 <= value.as_tuple().exponent <= 12: raise ValueError("estimated_cost is invalid")
    return Decimal(value)


@dataclass(frozen=True, slots=True)
class NasdaqDataLinkEntitlement:
    capability_id: str
    plan_name: str
    available: bool
    estimated_cost: Decimal | None
    quota_remaining: int | None
    premium_access: bool

    def __post_init__(self) -> None:
        if self.capability_id != "macro_series" or type(self.plan_name) is not str or not _PLAN.fullmatch(self.plan_name): raise ValueError("invalid Nasdaq Data Link entitlement")
        if type(self.available) is not bool or type(self.premium_access) is not bool: raise ValueError("entitlement flags must be boolean")
        object.__setattr__(self, "estimated_cost", _cost(self.estimated_cost))
        if self.quota_remaining is not None and (type(self.quota_remaining) is not int or not 0 <= self.quota_remaining <= 1_000_000_000): raise ValueError("quota_remaining is invalid")


def _decision(value: object, estimate: Decimal) -> tuple[bool, str]:
    if type(value) is not BudgetDecision: raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    try: row = value.to_dict()
    except Exception: raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE) from None
    if (type(row) is not dict or set(row) != {"allowed", "reason", "reservation_id", "estimated_cost", "health_failure"} or type(row["allowed"]) is not bool or type(row["reason"]) is not str
        or (row["reservation_id"] is not None and type(row["reservation_id"]) is not str) or type(row["estimated_cost"]) is not str or type(row["health_failure"]) is not bool
        or row["estimated_cost"] != format(estimate, "f") or row["health_failure"] is not False or row["allowed"] is not (row["reason"] == "authorized")
        or (not row["allowed"] and row["reservation_id"] is not None) or (row["allowed"] and estimate > 0 and row["reservation_id"] is None)): raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    if row["reservation_id"] is not None and any(term in row["reservation_id"].lower() for term in _SENSITIVE): raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    return row["allowed"], row["reason"]


class NasdaqDataLinkAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "nasdaq-data-link", "Nasdaq Data Link", "nasdaq_data_link", "http_client", (SourceRole.MACRO_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("macro_series",), BillingModel.FREEMIUM, "api_key", (_ENV_NAME,), False,
        "Nasdaq Data Link account and dataset license terms apply.",
        "Known public datasets are distinguished from Premium datasets; Premium requires explicit account entitlement.",
        "以 dataset 实际刷新时间为准", "以账户实际套餐、dataset 权限和配额为准", "套餐和 Premium 成本未知；无可信能力级权益时禁止请求",
        _REFERENCE, 140, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None, entitlements: tuple[NasdaqDataLinkEntitlement, ...] = (),
                 cache_getter: Callable[[ProviderRequest], object | None] | None = None, fetched_at: Callable[[], datetime] = _now) -> None:
        if type(entitlements) is not tuple or any(type(item) is not NasdaqDataLinkEntitlement for item in entitlements) or len({item.capability_id for item in entitlements}) != len(entitlements): raise ValueError("invalid Nasdaq Data Link entitlements")
        self._http, self._credentials, self._budget_guard = http, credentials, budget_guard
        self._entitlements = {
            item.capability_id: NasdaqDataLinkEntitlement(
                item.capability_id, item.plan_name, item.available, item.estimated_cost,
                item.quota_remaining, item.premium_access,
            ) for item in entitlements
        }
        self._cache_getter, self._fetched_at = cache_getter, fetched_at

    def _credential(self) -> str | None:
        try: value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception: return None
        return value if type(value) is str and value.strip() else None

    def _preflight(self, capability: str, now: datetime) -> tuple[str | None, NasdaqDataLinkEntitlement | None]:
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
    def _request(request: ProviderRequest) -> tuple[str, str, int]:
        if type(request) is not ProviderRequest or request.capability_id != "macro_series" or type(request.parameters) is not dict or set(request.parameters) - {"database_code", "dataset_code", "limit", "max_age_days"}: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        database, dataset, limit = request.parameters.get("database_code"), request.parameters.get("dataset_code"), request.parameters.get("limit", 100)
        max_age = request.parameters.get("max_age_days")
        if type(database) is not str or not _RESOURCE.fullmatch(database) or type(dataset) is not str or not _RESOURCE.fullmatch(dataset) or type(limit) is not int or not 1 <= limit <= _MAX_ROWS or (max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500)): raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return database, dataset, limit

    @staticmethod
    def _error(payload: dict[str, object]) -> None:
        if "quandl_error" not in payload: return
        error = payload["quandl_error"]
        if type(error) is not dict or set(error) - {"code", "message", "retry_after"}: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        code, message = _text(error.get("code")), _text(error.get("message"))
        if code.startswith("QEL"):
            retry = error.get("retry_after", 0)
            if type(retry) not in {int, float} or type(retry) is bool or not 0 <= retry <= 60: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            raise ProviderRateLimited(retry_after_seconds=float(retry), reference=_REFERENCE)
        if code.startswith("QEA"): raise ProviderUnavailable("authentication", reference=_REFERENCE)
        if code.startswith("QEP") or "subscription" in message.lower(): raise ProviderUnavailable("plan_unavailable", reference=_REFERENCE)
        raise ProviderUnavailable("provider_error", reference=_REFERENCE)

    def _parse(self, payload: object, request: ProviderRequest, *, now: datetime, ent: NasdaqDataLinkEntitlement, cached: bool, entitlement_label: str) -> tuple[ProviderValue, ...]:
        if type(payload) is not dict: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._error(payload)
        dataset = payload.get("dataset")
        if type(dataset) is not dict: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        required = {"id", "dataset_code", "database_code", "name", "description", "refreshed_at", "newest_available_date", "oldest_available_date", "column_names", "frequency", "type", "premium", "data", "database_name"}
        if set(dataset) != required or type(dataset["premium"]) is not bool: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if dataset["premium"] and not ent.premium_access:
            raise ProviderUnavailable("plan_unavailable", reference=_REFERENCE)
        if dataset["premium"]:
            entitlement_label = "premium_entitled"
        database, code, name, database_name = (_text(dataset[field]) for field in ("database_code", "dataset_code", "name", "database_name"))
        newest, frequency, columns, data = _text(dataset["newest_available_date"]), _text(dataset["frequency"]), dataset["column_names"], dataset["data"]
        if database != request.parameters.get("database_code") or code != request.parameters.get("dataset_code") or type(columns) is not list or type(data) is not list or columns != ["Date", "Value"]: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not data: raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(data) > _MAX_ROWS or any(type(column) is not str or not column or len(column) > _MAX_TEXT for column in columns): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        try: newest_date = date.fromisoformat(newest)
        except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
        if newest_date > now.date(): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        rows = []
        max_age = request.parameters.get("max_age_days")
        for item in data:
            if type(item) is not list or len(item) != 2: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            raw_date = _text(item[0])
            try: as_of = date.fromisoformat(raw_date)
            except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            if as_of > now.date(): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            metadata = {"database_code": database, "dataset_code": code, "database_name": database_name, "dataset_name": name, "newest_available_date": newest, "entitlement": entitlement_label, "plan_name": ent.plan_name, "quota_remaining": "unknown" if ent.quota_remaining is None else str(ent.quota_remaining), "source_reference": _REFERENCE}
            if cached: metadata["cache_status"] = "fallback"
            stale = type(max_age) is int and (now.date() - as_of).days > max_age
            rows.append(ProviderValue(_number(item[1]), "nasdaq_data_link", "nasdaq-data-link", request.capability_id, as_of, now, "cached" if cached else ("stale" if stale else "upstream_reported"), "Nasdaq Data Link dataset terms apply", 140, None, "unknown", frequency, metadata))
        return tuple(rows)

    def _execute(self, request: ProviderRequest, credential: str, now: datetime, ent: NasdaqDataLinkEntitlement) -> tuple[ProviderValue, ...]:
        database, dataset, limit = self._request(request)
        known_free = (database, dataset) in _KNOWN_FREE
        if not known_free and not ent.premium_access: raise ProviderUnavailable("plan_unavailable", reference=_REFERENCE)
        label = "known_free_dataset" if known_free else "premium_entitled"
        endpoint = f"{_BASE}/{database}/{dataset}.json"
        try: payload = self._http.get_json(endpoint, headers={"Accept": "application/json"}, params={"api_key": credential, "limit": limit})
        except ProviderUnavailable as error:
            if self._cache_getter is None or error.code not in _CACHEABLE: raise
            payload = self._cache_getter(request)
            if payload is None: raise
            cached = True
        else: cached = False
        return self._parse(payload, request, now=now, ent=ent, cached=cached, entitlement_label=label)

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
        request = ProviderRequest(capability_id, {"database_code": "FRED", "dataset_code": "DFF", "limit": 1} if parameters is None else parameters)
        try: rows = self._execute(request, credential, now, ent)
        except ProviderRateLimited as error: return {"status": "rate_limited", "connected": False, "health_failure": False, "capability_id": capability_id, "retry_after_seconds": error.retry_after_seconds}
        except ProviderSchemaChanged: return {"status": "schema_changed", "connected": False, "health_failure": True, "capability_id": capability_id}
        except ProviderUnavailable as error:
            status = "authentication_failed" if error.code == "authentication" else error.code
            return {"status": status, "connected": False, "health_failure": error.code not in {"authentication", "plan_unavailable", "quota_exhausted", "empty_result"}, "capability_id": capability_id}
        return {"status": "available", "connected": True, "health_failure": False, "capability_id": capability_id, "returned_count": len(rows)}


__all__ = ["NasdaqDataLinkAdapter", "NasdaqDataLinkEntitlement"]
