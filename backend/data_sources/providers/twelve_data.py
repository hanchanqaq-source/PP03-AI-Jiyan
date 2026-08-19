from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://api.twelvedata.com/"
_ENV_NAME = "TWELVE_DATA_API_KEY"
_MAX_ROWS, _MAX_TEXT = 1_000, 4_096
_SYMBOL = re.compile(r"^[A-Za-z0-9.^_:/-]{1,64}$")
_INTERVALS = {"1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "1day", "1week", "1month"}


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


class TwelveDataAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "twelve-data", "Twelve Data", "twelve_data", "http_client", (SourceRole.FALLBACK_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK),
        ("stock_history",), BillingModel.FREEMIUM, "api_key", (_ENV_NAME,), False,
        "Twelve Data account terms apply; interval and credits depend on the actual plan.",
        "Fallback market history with explicit plan and credit metadata.", "以 Twelve Data 实际返回为准", "以账户实际套餐、能力和 credits 为准",
        "套餐成本未知；无可信能力级权益时禁止请求", _REFERENCE, 125, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None,
                 cache_getter: Callable[[ProviderRequest], object | None] | None = None, fetched_at: Callable[[], datetime] = _now) -> None:
        self._http, self._credentials, self._budget_guard = http, credentials, budget_guard
        self._cache_getter, self._fetched_at = cache_getter, fetched_at

    def _credential(self) -> str | None:
        try: value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception: return None
        return value if type(value) is str and value.strip() else None

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

    def _parse(self, payload: object, request: ProviderRequest, *, now: datetime, cached: bool) -> tuple[ProviderValue, ...]:
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
            metadata = {"symbol": symbol, "interval": interval, "timezone": timezone_name, "credits_used": str(credits), "source_reference": _REFERENCE}
            if cached: metadata["cache_status"] = "fallback"
            stale = type(max_age) is int and (now.date() - as_of).days > max_age
            rows.append(ProviderValue(public, "twelve_data", "twelve-data", request.capability_id, as_of, now, "cached" if cached else ("stale" if stale else "upstream_reported"), "Twelve Data account terms apply", 125, None, currency or "unknown", interval, metadata))
        return tuple(rows)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        credential = self._credential()
        if credential is None: raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        del credential, request
        raise ProviderUnavailable("unsupported_credential_transport", reference=_REFERENCE)

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        credential = self._credential()
        if credential is None: return {"status": "unconfigured", "connected": False, "health_failure": False}
        del credential, parameters
        return {"status": "unsupported_credential_transport", "connected": False, "health_failure": False, "capability_id": capability_id}


__all__ = ["TwelveDataAdapter"]
