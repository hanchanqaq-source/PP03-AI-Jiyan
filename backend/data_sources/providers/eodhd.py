"""EODHD paid adapter boundary with pure fixture parsing only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import math
import re
from typing import Any, Mapping

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://eodhd.com/"
_ENDPOINT = "https://eodhd.com/api/eod"
_ENV_NAME = "EODHD_API_KEY"
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,31}$")
_MAX_ROWS = 1_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value.astimezone(timezone.utc)


def _schema() -> ProviderSchemaChanged:
    return ProviderSchemaChanged("schema_changed", reference=_REFERENCE)


def _number(value: object) -> Decimal:
    if type(value) is bool or type(value) not in {str, int, float, Decimal}:
        raise _schema()
    if type(value) is int and (value.bit_length() > 333 or len(str(abs(value))) > 100):
        raise _schema()
    if type(value) is float and not math.isfinite(value):
        raise _schema()
    if type(value) is str and len(value) > 128:
        raise _schema()
    try:
        result = value if type(value) is Decimal else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _schema() from None
    exponent = result.as_tuple().exponent
    if not result.is_finite() or type(exponent) is not int or abs(exponent) > 100 or len(result.as_tuple().digits) > 100:
        raise _schema()
    return result


def _request_date(value: object) -> str:
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE) from None
    return value


def _context_seal(*parts: object) -> str:
    return hashlib.sha256("\x1f".join("" if part is None else str(part) for part in parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _EodhdRequestContext:
    adapter_id: str
    capability_id: str
    symbol: str
    start_date: date
    end_date: date
    max_age_days: int | None
    period: str
    seal: str


def _validated_context(value: object) -> _EodhdRequestContext:
    if type(value) is not _EodhdRequestContext:
        raise ProviderUnavailable("invalid_request_context", reference=_REFERENCE)
    if (
        type(value.adapter_id) is not str or value.adapter_id != "eodhd"
        or type(value.capability_id) is not str or value.capability_id != "stock_history"
        or type(value.symbol) is not str or not _SYMBOL.fullmatch(value.symbol)
        or type(value.start_date) is not date or type(value.end_date) is not date or value.start_date > value.end_date
        or (value.max_age_days is not None and (type(value.max_age_days) is not int or not 0 <= value.max_age_days <= 36_500))
        or type(value.period) is not str or value.period != "d"
        or type(value.seal) is not str
        or value.seal != _context_seal(value.adapter_id, value.capability_id, value.symbol, value.start_date.isoformat(), value.end_date.isoformat(), value.max_age_days, value.period)
    ):
        raise ProviderUnavailable("invalid_request_context", reference=_REFERENCE)
    return value


class EodhdAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "eodhd", "EODHD", "eodhd", "http_client",
        (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("stock_history",), BillingModel.PAID_API, "api_key", (_ENV_NAME,), False,
        "EODHD paid account and data terms apply.",
        "默认关闭；本轮只登记已覆盖 Mock 的历史行情能力；只有凭据、套餐、费用和测试授权全部可信时才允许请求。",
        "以实际付费套餐与上游返回为准", "以用户自有付费账户实际套餐为准",
        "付费 API；成本未知时禁止请求且不自动购买", _REFERENCE, 75, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None, fetched_at=_now) -> None:
        self._http, self._credentials = http, credentials
        self._budget_guard, self._fetched_at = budget_guard, fetched_at

    def _credential(self) -> str | None:
        try:
            value = self._credentials.get("eodhd", _ENV_NAME)
        except Exception:
            return None
        return value if type(value) is str and value.strip() else None

    @staticmethod
    def _context(request: ProviderRequest) -> _EodhdRequestContext:
        if type(request) is not ProviderRequest or type(request.capability_id) is not str or type(request.parameters) is not dict or request.capability_id != "stock_history":
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if any(type(key) is not str for key in request.parameters) or not set(request.parameters).issubset({"symbol", "start_date", "end_date", "max_age_days"}):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        symbol = request.parameters.get("symbol")
        if type(symbol) is not str or not _SYMBOL.fullmatch(symbol):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        start_text, end_text = _request_date(request.parameters.get("start_date")), _request_date(request.parameters.get("end_date"))
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        if start > end:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        max_age = request.parameters.get("max_age_days")
        if max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        period = "d"
        return _EodhdRequestContext(
            "eodhd", request.capability_id, symbol, start, end, max_age, period,
            _context_seal("eodhd", request.capability_id, symbol, start.isoformat(), end.isoformat(), max_age, period),
        )

    @staticmethod
    def _request(context: _EodhdRequestContext) -> tuple[str, dict[str, str]]:
        context = _validated_context(context)
        return _ENDPOINT, {
            "symbol": context.symbol,
            "from": context.start_date.isoformat(),
            "to": context.end_date.isoformat(),
            "fmt": "json",
            "period": context.period,
        }

    @staticmethod
    def _parse(payload: object, context: _EodhdRequestContext, *, now: datetime, cached: bool) -> tuple[ProviderValue, ...]:
        context = _validated_context(context)
        now = _utc_now(now)
        if type(payload) is not list:
            raise _schema()
        if not payload:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(payload) > _MAX_ROWS:
            raise _schema()
        rows: list[tuple[date, ProviderValue]] = []
        seen_dates: set[date] = set()
        for item in payload:
            if type(item) is not dict:
                raise _schema()
            symbol, raw_date = item.get("code"), item.get("date")
            if type(symbol) is not str or not _SYMBOL.fullmatch(symbol) or symbol != context.symbol or type(raw_date) is not str:
                raise _schema()
            try:
                as_of = date.fromisoformat(raw_date)
            except ValueError:
                raise _schema() from None
            if as_of > now.date() or not context.start_date <= as_of <= context.end_date or as_of in seen_dates:
                raise _schema()
            seen_dates.add(as_of)
            currency_value = item.get("currency")
            if currency_value is None:
                currency = "unknown"
            elif type(currency_value) is str and re.fullmatch(r"[A-Z]{3,8}", currency_value):
                currency = currency_value
            else:
                raise _schema()
            close, volume = _number(item.get("close")), _number(item.get("volume"))
            stale = context.max_age_days is not None and (now.date() - as_of).days > context.max_age_days
            status = "cached" if cached else ("stale" if stale else "upstream_reported")
            metadata = {
                "source_reference": _REFERENCE,
                "coverage": "end_of_day",
                "currency": currency,
                "plan_observation": "fixture_only_not_live_entitlement",
                "requested_symbol": context.symbol,
                "requested_start_date": context.start_date.isoformat(),
                "requested_end_date": context.end_date.isoformat(),
                "period": context.period,
            }
            if cached:
                metadata["cache_status"] = "fixture_fallback"
            rows.append((as_of, ProviderValue(
                {"symbol": symbol, "close": close, "volume": volume}, "eodhd", "eodhd", context.capability_id,
                as_of, now, status, "EODHD paid account terms apply", 75, None, currency, "daily", metadata,
            )))
        return tuple(row for _key, row in sorted(rows, key=lambda item: item[0]))

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        del request
        if self._credential() is None:
            raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        raise ProviderUnavailable("unsupported_credential_transport", reference=_REFERENCE)

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        del capability_id, parameters
        status = "unconfigured" if self._credential() is None else "unsupported_credential_transport"
        return {"status": status, "connected": False, "health_failure": False}


__all__ = ["EodhdAdapter"]
