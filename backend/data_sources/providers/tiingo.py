"""Tiingo paid adapter boundary with pure fixture parsing only."""

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


_REFERENCE = "https://www.tiingo.com/"
_ENDPOINT = "https://api.tiingo.com/tiingo/daily/prices"
_ENV_NAME = "TIINGO_API_KEY"
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
class _TiingoRequestContext:
    adapter_id: str
    capability_id: str
    symbol: str
    start_date: date
    end_date: date
    max_age_days: int | None
    resample_frequency: str
    seal: str


def _validated_context(value: object) -> _TiingoRequestContext:
    if type(value) is not _TiingoRequestContext:
        raise ProviderUnavailable("invalid_request_context", reference=_REFERENCE)
    if (
        type(value.adapter_id) is not str or value.adapter_id != "tiingo"
        or type(value.capability_id) is not str or value.capability_id != "stock_history"
        or type(value.symbol) is not str or not _SYMBOL.fullmatch(value.symbol)
        or type(value.start_date) is not date or type(value.end_date) is not date or value.start_date > value.end_date
        or (value.max_age_days is not None and (type(value.max_age_days) is not int or not 0 <= value.max_age_days <= 36_500))
        or type(value.resample_frequency) is not str or value.resample_frequency != "daily"
        or type(value.seal) is not str
        or value.seal != _context_seal(value.adapter_id, value.capability_id, value.symbol, value.start_date.isoformat(), value.end_date.isoformat(), value.max_age_days, value.resample_frequency)
    ):
        raise ProviderUnavailable("invalid_request_context", reference=_REFERENCE)
    return value


class TiingoAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "tiingo", "Tiingo", "tiingo", "http_client",
        (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("stock_history",), BillingModel.PAID_API, "api_key", (_ENV_NAME,), False,
        "Tiingo paid account and data terms apply.",
        "默认关闭；只有凭据、套餐、费用和测试授权全部可信时才允许请求。",
        "以实际付费套餐与上游返回为准", "以用户自有付费账户实际套餐为准",
        "付费 API；成本未知时禁止请求且不自动购买", _REFERENCE, 75, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None, fetched_at=_now) -> None:
        self._http, self._credentials = http, credentials
        self._budget_guard, self._fetched_at = budget_guard, fetched_at

    def _credential(self) -> str | None:
        try:
            value = self._credentials.get("tiingo", _ENV_NAME)
        except Exception:
            return None
        return value if type(value) is str and value.strip() else None

    @staticmethod
    def _context(request: ProviderRequest) -> _TiingoRequestContext:
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
        frequency = "daily"
        return _TiingoRequestContext(
            "tiingo", request.capability_id, symbol, start, end, max_age, frequency,
            _context_seal("tiingo", request.capability_id, symbol, start.isoformat(), end.isoformat(), max_age, frequency),
        )

    @staticmethod
    def _request(context: _TiingoRequestContext) -> tuple[str, dict[str, str]]:
        context = _validated_context(context)
        return _ENDPOINT, {
            "ticker": context.symbol,
            "startDate": context.start_date.isoformat(),
            "endDate": context.end_date.isoformat(),
            "resampleFreq": context.resample_frequency,
        }

    @staticmethod
    def _parse(payload: object, context: _TiingoRequestContext, *, now: datetime, cached: bool) -> tuple[ProviderValue, ...]:
        context = _validated_context(context)
        now = _utc_now(now)
        if type(payload) is not list:
            raise _schema()
        if not payload:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(payload) > _MAX_ROWS:
            raise _schema()
        rows: list[tuple[datetime, ProviderValue]] = []
        seen_timestamps: set[datetime] = set()
        for item in payload:
            if type(item) is not dict:
                raise _schema()
            symbol, raw_date = item.get("ticker"), item.get("date")
            if type(symbol) is not str or not _SYMBOL.fullmatch(symbol) or symbol != context.symbol or type(raw_date) is not str or len(raw_date) > 64:
                raise _schema()
            try:
                parsed_at = datetime.fromisoformat(raw_date[:-1] + "+00:00" if raw_date.endswith("Z") else raw_date)
            except ValueError:
                raise _schema() from None
            if parsed_at.tzinfo is None:
                raise _schema()
            canonical_timestamp = parsed_at.astimezone(timezone.utc)
            as_of = canonical_timestamp.date()
            if as_of > now.astimezone(timezone.utc).date() or not context.start_date <= as_of <= context.end_date or canonical_timestamp in seen_timestamps:
                raise _schema()
            seen_timestamps.add(canonical_timestamp)
            close, volume = _number(item.get("close")), _number(item.get("volume"))
            stale = context.max_age_days is not None and (now.date() - as_of).days > context.max_age_days
            status = "cached" if cached else ("stale" if stale else "upstream_reported")
            metadata = {
                "source_reference": _REFERENCE,
                "coverage": "daily_prices",
                "plan_observation": "fixture_only_not_live_entitlement",
                "requested_symbol": context.symbol,
                "requested_start_date": context.start_date.isoformat(),
                "requested_end_date": context.end_date.isoformat(),
                "resample_frequency": context.resample_frequency,
            }
            if cached:
                metadata["cache_status"] = "fixture_fallback"
            rows.append((canonical_timestamp, ProviderValue(
                {"symbol": symbol, "close": close, "volume": volume}, "tiingo", "tiingo", "stock_history",
                as_of, now, status, "Tiingo paid account terms apply", 75, None, "unknown", "daily", metadata,
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


__all__ = ["TiingoAdapter"]
