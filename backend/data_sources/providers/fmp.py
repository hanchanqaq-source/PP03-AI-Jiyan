"""Financial Modeling Prep paid adapter boundary.

Only pure request construction and fixture parsing are implemented here.  The
approved Work has no verified secret-safe authentication or authoritative
plan/cost source, so configured production instances fail closed before budget
authorization or transport.
"""

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


_REFERENCE = "https://site.financialmodelingprep.com/"
_ENV_NAME = "FMP_API_KEY"
_ENDPOINTS = {
    "stock_snapshot": "https://financialmodelingprep.com/stable/quote",
}
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,31}$")
_MAX_ROWS = 1_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value.astimezone(timezone.utc)


def _text(value: object, *, length: int = 256) -> str:
    if type(value) is not str or not value or len(value) > length:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _decimal(value: object) -> Decimal:
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


def _date(value: object, now: datetime) -> date:
    raw = _text(value, length=32)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
    if parsed > now.date():
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return parsed


def _parameter_date(value: object) -> date:
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE) from None


def _context_seal(*parts: object) -> str:
    encoded = "\x1f".join("" if part is None else str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _FmpRequestContext:
    adapter_id: str
    capability_id: str
    symbol: str
    start_date: date
    end_date: date
    max_age_days: int | None
    mode: str
    seal: str


def _validated_context(value: object) -> _FmpRequestContext:
    if type(value) is not _FmpRequestContext:
        raise ProviderUnavailable("invalid_request_context", reference=_REFERENCE)
    if (
        type(value.adapter_id) is not str
        or value.adapter_id != "fmp"
        or type(value.capability_id) is not str
        or value.capability_id != "stock_snapshot"
        or type(value.symbol) is not str
        or not _SYMBOL.fullmatch(value.symbol)
        or type(value.start_date) is not date
        or type(value.end_date) is not date
        or value.start_date > value.end_date
        or (value.max_age_days is not None and (type(value.max_age_days) is not int or not 0 <= value.max_age_days <= 36_500))
        or type(value.mode) is not str
        or value.mode != "snapshot_fixture"
        or type(value.seal) is not str
        or value.seal != _context_seal(value.adapter_id, value.capability_id, value.symbol, value.start_date.isoformat(), value.end_date.isoformat(), value.max_age_days, value.mode)
    ):
        raise ProviderUnavailable("invalid_request_context", reference=_REFERENCE)
    return value


class FmpAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "fmp", "Financial Modeling Prep", "fmp", "http_client",
        (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("stock_snapshot",), BillingModel.PAID_API, "api_key", (_ENV_NAME,), False,
        "Financial Modeling Prep paid account and dataset terms apply.",
        "默认关闭；本轮只登记已覆盖 Mock 的行情快照能力；只有凭据、套餐、费用和测试授权全部可信时才允许请求。",
        "以实际付费套餐与上游返回为准", "以用户自有付费账户实际套餐为准",
        "付费 API；成本未知时禁止请求且不自动购买", _REFERENCE, 70, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None, fetched_at=_now) -> None:
        self._http = http
        self._credentials = credentials
        self._budget_guard = budget_guard
        self._fetched_at = fetched_at

    def _credential(self) -> str | None:
        try:
            value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception:
            return None
        return value if type(value) is str and value.strip() else None

    @staticmethod
    def _context(request: ProviderRequest) -> _FmpRequestContext:
        if type(request) is not ProviderRequest or type(request.capability_id) is not str or type(request.parameters) is not dict:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        endpoint = _ENDPOINTS.get(request.capability_id)
        if endpoint is None or any(type(key) is not str for key in request.parameters) or not set(request.parameters).issubset({"symbol", "start_date", "end_date", "max_age_days"}):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        symbol = request.parameters.get("symbol")
        if type(symbol) is not str or not _SYMBOL.fullmatch(symbol):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        start_date = _parameter_date(request.parameters.get("start_date"))
        end_date = _parameter_date(request.parameters.get("end_date"))
        if start_date > end_date:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        max_age = request.parameters.get("max_age_days")
        if max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        mode = "snapshot_fixture"
        return _FmpRequestContext(
            "fmp", request.capability_id, symbol, start_date, end_date, max_age, mode,
            _context_seal("fmp", request.capability_id, symbol, start_date.isoformat(), end_date.isoformat(), max_age, mode),
        )

    @staticmethod
    def _request(context: _FmpRequestContext) -> tuple[str, dict[str, str]]:
        context = _validated_context(context)
        return _ENDPOINTS[context.capability_id], {"symbol": context.symbol}

    @staticmethod
    def _parse(payload: object, context: _FmpRequestContext, *, now: datetime, cached: bool) -> tuple[ProviderValue, ...]:
        context = _validated_context(context)
        now = _utc_now(now)
        if type(payload) is not list:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not payload:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(payload) > _MAX_ROWS:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        rows: list[tuple[date, ProviderValue]] = []
        seen_dates: set[date] = set()
        for item in payload:
            if type(item) is not dict:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            symbol = _text(item.get("symbol"), length=32)
            if not _SYMBOL.fullmatch(symbol) or symbol != context.symbol:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            as_of = _date(item.get("date"), now)
            if not context.start_date <= as_of <= context.end_date or as_of in seen_dates:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            seen_dates.add(as_of)
            currency_value = item.get("currency")
            currency = "unknown" if currency_value is None else _text(currency_value, length=16)
            price = _decimal(item.get("price"))
            volume = _decimal(item.get("volume"))
            stale = context.max_age_days is not None and (now.date() - as_of).days > context.max_age_days
            status = "cached" if cached else ("stale" if stale else "upstream_reported")
            metadata = {
                "source_reference": _REFERENCE,
                "coverage": "quote_snapshot",
                "currency": currency,
                "plan_observation": "fixture_only_not_live_entitlement",
                "requested_symbol": context.symbol,
                "requested_start_date": context.start_date.isoformat(),
                "requested_end_date": context.end_date.isoformat(),
                "request_mode": context.mode,
            }
            if cached:
                metadata["cache_status"] = "fixture_fallback"
            rows.append((as_of, ProviderValue(
                {"symbol": symbol, "price": price, "volume": volume}, "fmp", "fmp",
                context.capability_id, as_of, now, status, "FMP paid account terms apply", 70,
                None, currency, "snapshot", metadata,
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


__all__ = ["FmpAdapter"]
