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


_REFERENCE = "https://api.stlouisfed.org/fred/"
_ENDPOINT = f"{_REFERENCE}series/observations"
_ENV_NAME = "FRED_API_KEY"
_MAX_ROWS = 1_000
_MAX_TEXT = 4_096
_MAX_NUMBER_TEXT = 128
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_FREQUENCIES = {
    "annual": "annual",
    "quarterly": "quarterly",
    "monthly": "monthly",
    "weekly": "weekly",
    "daily": "daily",
}
_CACHEABLE = {"timeout", "tls", "dns", "connection", "server_error", "rate_limited"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: object, *, allow_blank: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not allow_blank and not value):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _iso_date(value: object) -> date:
    raw = _text(value)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as error:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from error
    return parsed


def _number(value: object) -> Decimal | None:
    if type(value) not in {str, int, float, Decimal}:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    if type(value) is str and value == ".":
        return None
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


class FredAdapter(BaseProvider):
    """FRED observations adapter with credential-first, zero-cost preflight."""

    descriptor = AdapterDescriptor(
        "fred", "FRED", "fred", "http_client", (SourceRole.MACRO_DATA, SourceRole.CROSS_CHECK),
        ("macro_series",), BillingModel.FREE_KEY, "api_key", (_ENV_NAME,), False,
        "FRED API key required; public data terms apply.",
        "Free-key macro series adapter; no paid request or automatic upgrade.",
        "以 FRED 发布和修订为准", "以账户实际配额为准", "免费密钥；本适配器不预留或消费付费预算",
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
        self._http = http
        self._credentials = credentials
        self._budget_guard = budget_guard
        self._cache_getter = cache_getter
        self._fetched_at = fetched_at

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
    def _request(request: ProviderRequest) -> tuple[dict[str, object], int | None]:
        if type(request) is not ProviderRequest or type(request.capability_id) is not str or type(request.parameters) is not dict:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if request.capability_id != "macro_series":
            raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)
        series_id = request.parameters.get("series_id")
        if type(series_id) is not str or not _IDENTIFIER.fullmatch(series_id):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        params: dict[str, object] = {"series_id": series_id, "file_type": "json"}
        for field in ("observation_start", "observation_end"):
            value = request.parameters.get(field)
            if value is not None:
                if type(value) is not str:
                    raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
                try:
                    date.fromisoformat(value)
                except ValueError as error:
                    raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE) from error
                params[field] = value
        if "observation_start" in params and "observation_end" in params and params["observation_start"] > params["observation_end"]:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return params, _max_age(request.parameters)

    @staticmethod
    def _payload_error(payload: Mapping[str, object]) -> None:
        if "error_code" not in payload:
            return
        code = payload.get("error_code")
        if type(code) is not int:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if code == 429:
            raise ProviderRateLimited(retry_after_seconds=0.0, reference=_REFERENCE)
        if code in {400, 401, 403}:
            raise ProviderUnavailable("authentication", reference=_REFERENCE)
        raise ProviderUnavailable("provider_error", reference=_REFERENCE)

    def _parse(
        self,
        payload: object,
        request: ProviderRequest,
        *,
        now: datetime,
        max_age_days: int | None,
        cached: bool,
    ) -> tuple[ProviderValue, ...]:
        if type(payload) is not dict:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._payload_error(payload)
        observations = payload.get("observations")
        if type(observations) is not list:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not observations:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(observations) > _MAX_ROWS:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        raw_unit = _text(payload.get("units"), allow_blank=True)
        unit = raw_unit or "unknown"
        raw_frequency = _text(payload.get("frequency")).strip().lower()
        frequency = _FREQUENCIES.get(raw_frequency, raw_frequency)
        series_id = str(request.parameters["series_id"])
        rows: list[ProviderValue] = []
        for observation in observations:
            if type(observation) is not dict:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            as_of = _iso_date(observation.get("date"))
            if as_of > now.date():
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            realtime_start = _iso_date(observation.get("realtime_start")).isoformat()
            realtime_end = _iso_date(observation.get("realtime_end")).isoformat()
            if realtime_start > realtime_end:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            value = _number(observation.get("value"))
            stale = max_age_days is not None and (now.date() - as_of).days > max_age_days
            if value is None:
                status = "missing"
            elif cached:
                status = "cached"
            elif stale:
                status = "stale"
            else:
                status = "upstream_reported"
            metadata = {
                "series_id": series_id,
                "realtime_start": realtime_start,
                "realtime_end": realtime_end,
                "source_reference": _REFERENCE,
            }
            if cached:
                metadata["cache_status"] = "fallback"
            rows.append(ProviderValue(value, "fred", "fred", request.capability_id, as_of, now, status, "FRED public API terms apply", 20, None, unit, frequency, metadata))
        return tuple(rows)

    def _execute(self, request: ProviderRequest, credential: str, now: datetime) -> tuple[ProviderValue, ...]:
        params, max_age_days = self._request(request)
        transport_params = dict(params)
        transport_params["api_key"] = credential
        try:
            payload = self._http.get_json(_ENDPOINT, headers={"Accept": "application/json"}, params=transport_params)
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
        request = ProviderRequest(capability_id, {"series_id": "GDP"} if parameters is None else parameters)
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


__all__ = ["FredAdapter"]
