"""Polygon/Massive paid adapter boundary with zero-network production state."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any, Mapping

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://massive.com/"
_ENDPOINT = "https://api.massive.com/v2/aggs/ticker/range/1/day"
_ENV_NAME = "MASSIVE_API_KEY"
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,31}$")
_MAX_ROWS = 1_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: object, length: int = 256) -> str:
    if type(value) is not str or not value or len(value) > length:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _number(value: object) -> Decimal:
    if type(value) is bool or type(value) not in {str, int, float, Decimal}:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    if type(value) is int and (value.bit_length() > 333 or len(str(abs(value))) > 100):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    if type(value) is float and not math.isfinite(value):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    if type(value) is str and len(value) > 128:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try:
        result = value if type(value) is Decimal else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
    exponent = result.as_tuple().exponent
    if not result.is_finite() or type(exponent) is not int or abs(exponent) > 100 or len(result.as_tuple().digits) > 100:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return result


def _iso_date(value: object) -> date:
    raw = _text(value, 32)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE) from None


class MassiveAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "massive", "Polygon/Massive", "massive", "http_client",
        (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("stock_history",), BillingModel.PAID_API, "api_key", (_ENV_NAME,), False,
        "Polygon/Massive paid account and market-data terms apply.",
        "默认关闭；本轮只登记已覆盖 Mock 的历史行情能力；只有凭据、套餐、费用和测试授权全部可信时才允许请求。",
        "以实际付费套餐与上游返回为准", "以用户自有付费账户实际套餐为准",
        "付费 API；成本未知时禁止请求且不自动购买", _REFERENCE, 70, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None, fetched_at=_now) -> None:
        self._http, self._credentials = http, credentials
        self._budget_guard, self._fetched_at = budget_guard, fetched_at

    def _credential(self) -> str | None:
        try:
            value = self._credentials.get("massive", _ENV_NAME)
        except Exception:
            return None
        return value if type(value) is str and value.strip() else None

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, dict[str, str]]:
        if type(request) is not ProviderRequest or type(request.capability_id) is not str or type(request.parameters) is not dict or request.capability_id != "stock_history":
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if any(type(key) is not str for key in request.parameters) or not set(request.parameters).issubset({"symbol", "start_date", "end_date", "max_age_days"}):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        symbol = request.parameters.get("symbol")
        if type(symbol) is not str or not _SYMBOL.fullmatch(symbol):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        start, end = request.parameters.get("start_date"), request.parameters.get("end_date")
        if type(start) is not str or type(end) is not str or _iso_date(start) > _iso_date(end):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        max_age = request.parameters.get("max_age_days")
        if max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return _ENDPOINT, {"symbol": symbol, "from": start, "to": end, "adjusted": "true"}

    @staticmethod
    def _parse(payload: object, request: ProviderRequest, *, now: datetime, cached: bool) -> tuple[ProviderValue, ...]:
        MassiveAdapter._request(request)
        if type(payload) is not dict or type(payload.get("status")) is not str or payload.get("status") != "OK" or type(payload.get("results")) is not list:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        items = payload["results"]
        if not items:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(items) > _MAX_ROWS:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        max_age = request.parameters.get("max_age_days")
        rows: list[ProviderValue] = []
        for item in items:
            if type(item) is not dict:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            symbol = _text(item.get("T"), 32)
            if not _SYMBOL.fullmatch(symbol):
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            timestamp = item.get("t")
            if type(timestamp) is not int or not 0 <= timestamp <= 4_102_444_800_000:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            as_of = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).date()
            if as_of > now.date():
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            close, volume = _number(item.get("c")), _number(item.get("v"))
            stale = type(max_age) is int and (now.date() - as_of).days > max_age
            status = "cached" if cached else ("stale" if stale else "upstream_reported")
            metadata = {"source_reference": _REFERENCE, "coverage": "aggregate_bars", "plan_observation": "fixture_only_not_live_entitlement"}
            if cached:
                metadata["cache_status"] = "fixture_fallback"
            rows.append(ProviderValue(
                {"symbol": symbol, "close": close, "volume": volume}, "massive", "massive",
                request.capability_id, as_of, now, status, "Polygon/Massive paid account terms apply",
                70, None, "unknown", "daily", metadata,
            ))
        return tuple(rows)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        del request
        if self._credential() is None:
            raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        raise ProviderUnavailable("unsupported_credential_transport", reference=_REFERENCE)

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        del capability_id, parameters
        status = "unconfigured" if self._credential() is None else "unsupported_credential_transport"
        return {"status": status, "connected": False, "health_failure": False}


__all__ = ["MassiveAdapter"]
