from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import re

from data_sources.budgets import BudgetDecision
from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://api.eia.gov/v2/"
_ENV_NAME = "EIA_API_KEY"
_MAX_ROWS = 1_000
_MAX_TEXT = 4_096
_MAX_NUMBER_TEXT = 128
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_ROUTE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+){0,8}$")
_CACHEABLE = {"timeout", "tls", "dns", "connection", "server_error", "rate_limited"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: object, *, allow_blank: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not allow_blank and not value):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _period(value: object) -> tuple[date, str]:
    raw = _text(value)
    try:
        if re.fullmatch(r"\d{4}", raw):
            return date(int(raw), 1, 1), "annual"
        if re.fullmatch(r"\d{4}-\d{2}", raw):
            return date(int(raw[:4]), int(raw[5:]), 1), "monthly"
        if re.fullmatch(r"\d{4}-Q[1-4]", raw):
            return date(int(raw[:4]), (int(raw[-1]) - 1) * 3 + 1, 1), "quarterly"
        parsed = date.fromisoformat(raw)
        return parsed, "daily"
    except ValueError as error:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from error


def _number(value: object) -> Decimal | None:
    if value is None:
        return None
    if type(value) not in {str, int, float, Decimal}:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    if type(value) is int and value.bit_length() > 333:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    raw = str(value)
    if len(raw) > _MAX_NUMBER_TEXT:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError) as error:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from error
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 100 or abs(parsed.as_tuple().exponent) > 100:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return parsed


def _max_age(parameters: Mapping[str, object]) -> int | None:
    value = parameters.get("max_age_days")
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 36_500:
        raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
    return value


class EiaAdapter(BaseProvider):
    """US EIA v2 adapter preserving series, period, unit, and forecast truth."""

    descriptor = AdapterDescriptor(
        "eia", "U.S. EIA", "eia", "http_client", (SourceRole.MACRO_DATA, SourceRole.CROSS_CHECK),
        ("macro_series",), BillingModel.FREE_KEY, "api_key", (_ENV_NAME,), False,
        "EIA API key required; public data terms apply.",
        "Free-key energy series adapter; no paid request or automatic upgrade.",
        "以 EIA 发布和修订为准", "以账户实际配额为准", "免费密钥；本适配器不预留或消费付费预算",
        _REFERENCE, 20, CatalogStatus.UNCONFIGURED,
    )

    def __init__(
        self,
        *,
        http: Any,
        credentials: Any,
        budget_guard: Any | None = None,
        cache_getter: Callable[[ProviderRequest], object | None] | None = None,
        fetched_at: Callable[[], datetime] = _now,
    ) -> None:
        self._http, self._credentials = http, credentials
        self._budget_guard, self._cache_getter, self._fetched_at = budget_guard, cache_getter, fetched_at

    def _credential(self) -> str | None:
        try:
            value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception:
            return None
        return value if type(value) is str and value.strip() else None

    def _authorize(self, now: datetime) -> str | None:
        if self._budget_guard is None:
            return None
        decision = self._budget_guard.authorize(self.descriptor, estimated_cost=Decimal("0"), now=now)
        if (
            type(decision) is not BudgetDecision
            or type(decision.allowed) is not bool
            or type(decision.reason) is not str
            or type(decision.estimated_cost) is not Decimal
            or (decision.reservation_id is not None and type(decision.reservation_id) is not str)
            or decision.estimated_cost != Decimal("0")
        ):
            raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
        return None if decision.allowed else str(decision.reason)

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, dict[str, object], int | None]:
        if type(request) is not ProviderRequest or type(request.capability_id) is not str or type(request.parameters) is not dict:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if request.capability_id != "macro_series":
            raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)
        route, series_id = request.parameters.get("route"), request.parameters.get("series_id")
        if type(route) is not str or not _ROUTE.fullmatch(route) or ".." in route:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if type(series_id) is not str or not _IDENTIFIER.fullmatch(series_id):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        params: dict[str, object] = {"frequency": "monthly", "data[0]": "value", "facets[series][]": series_id}
        for source, target in (("start", "start"), ("end", "end")):
            value = request.parameters.get(source)
            if value is not None:
                if type(value) is not str or len(value) > 32:
                    raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
                params[target] = value
        if "start" in params and "end" in params and params["start"] > params["end"]:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return route, params, _max_age(request.parameters)

    @staticmethod
    def _payload_error(payload: Mapping[str, object]) -> None:
        if "error" not in payload:
            return
        error = payload.get("error")
        if type(error) is not str or len(error) > _MAX_TEXT:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        upper = error.upper()
        if "KEY" in upper or "AUTH" in upper:
            raise ProviderUnavailable("authentication", reference=_REFERENCE)
        if "RATE" in upper or "QUOTA" in upper:
            raise ProviderRateLimited(retry_after_seconds=0.0, reference=_REFERENCE)
        raise ProviderUnavailable("provider_error", reference=_REFERENCE)

    def _parse(self, payload: object, request: ProviderRequest, *, now: datetime, max_age_days: int | None, cached: bool) -> tuple[ProviderValue, ...]:
        if type(payload) is not dict:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._payload_error(payload)
        response = payload.get("response")
        if type(response) is not dict:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        raw_frequency = _text(response.get("frequency")).strip().lower()
        items = response.get("data")
        if type(items) is not list:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not items:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(items) > _MAX_ROWS:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        expected_series = str(request.parameters["series_id"])
        rows: list[ProviderValue] = []
        for item in items:
            if type(item) is not dict or item.get("series") != expected_series:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            series_name = _text(item.get("series-name"))
            as_of, period_frequency = _period(item.get("period"))
            if as_of > now.date():
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            if raw_frequency != period_frequency:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            observation_type = item.get("type")
            if observation_type not in {"actual", "forecast"}:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            raw_unit = _text(item.get("units"), allow_blank=True)
            value = _number(item.get("value"))
            stale = max_age_days is not None and (now.date() - as_of).days > max_age_days
            status = "cached" if cached else ("missing" if value is None else ("stale" if stale else str(observation_type)))
            metadata = {
                "series_id": expected_series,
                "series_name": series_name,
                "observation_type": str(observation_type),
                "source_reference": _REFERENCE,
            }
            if cached:
                metadata["cache_status"] = "fallback"
            rows.append(ProviderValue(value, "eia", "eia", request.capability_id, as_of, now, status, "EIA public API terms apply", 20, None, raw_unit or "unknown", raw_frequency, metadata))
        return tuple(rows)

    def _execute(self, request: ProviderRequest, credential: str, now: datetime) -> tuple[ProviderValue, ...]:
        route, params, max_age_days = self._request(request)
        transport_params = dict(params)
        transport_params["api_key"] = credential
        try:
            payload = self._http.get_json(f"{_REFERENCE}{route}/data/", headers={"Accept": "application/json"}, params=transport_params)
        except ProviderUnavailable as error:
            if self._cache_getter is None or error.code not in _CACHEABLE:
                raise
            cached = self._cache_getter(request)
            if cached is None:
                raise
            return self._parse(cached, request, now=now, max_age_days=max_age_days, cached=True)
        return self._parse(payload, request, now=now, max_age_days=max_age_days, cached=False)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        credential = self._credential()
        if credential is None:
            raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        now = self._fetched_at()
        blocked = self._authorize(now)
        if blocked is not None:
            raise ProviderUnavailable(blocked, reference=_REFERENCE)
        return self._execute(request, credential, now)

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        credential = self._credential()
        if credential is None:
            return {"status": "unconfigured", "connected": False, "health_failure": False}
        now = self._fetched_at()
        blocked = self._authorize(now)
        if blocked is not None:
            return {"status": blocked, "connected": False, "health_failure": False}
        request = ProviderRequest(capability_id, {"route": "electricity/retail-sales", "series_id": "RES-ALL-M"} if parameters is None else parameters)
        try:
            rows = self._execute(request, credential, now)
        except ProviderRateLimited as error:
            return {"status": "rate_limited", "connected": False, "health_failure": False, "retry_after_seconds": error.retry_after_seconds}
        except ProviderSchemaChanged:
            return {"status": "schema_changed", "connected": False, "health_failure": True}
        except ProviderUnavailable as error:
            status = "authentication_failed" if error.code == "authentication" else error.code
            return {"status": status, "connected": False, "health_failure": error.code not in {"authentication", "empty_result", "unconfigured", "disabled"}}
        return {"status": "available", "connected": True, "health_failure": False, "returned_count": len(rows)}


__all__ = ["EiaAdapter"]
