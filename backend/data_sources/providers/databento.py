"""Databento paid adapter boundary with pure fixture parsing only."""

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


_REFERENCE = "https://databento.com/"
_ENDPOINT = "https://hist.databento.com/v0/timeseries.get_range"
_ENV_NAME = "DATABENTO_API_KEY"
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,31}$")
_DATASET = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_MAX_ROWS = 1_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


class DatabentoAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "databento", "Databento", "databento", "http_client",
        (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("stock_history",), BillingModel.PAID_API, "api_key", (_ENV_NAME,), False,
        "Databento paid account, dataset and market-data terms apply.",
        "默认关闭；本轮只登记已覆盖 Mock 的历史行情能力；只有凭据、数据集许可、费用和测试授权全部可信时才允许请求。",
        "以实际付费套餐、数据集与上游返回为准", "以用户自有付费账户和数据集授权为准",
        "付费 API；成本未知时禁止请求且不自动购买", _REFERENCE, 65, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None, fetched_at=_now) -> None:
        self._http, self._credentials = http, credentials
        self._budget_guard, self._fetched_at = budget_guard, fetched_at

    def _credential(self) -> str | None:
        try:
            value = self._credentials.get("databento", _ENV_NAME)
        except Exception:
            return None
        return value if type(value) is str and value.strip() else None

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, dict[str, str]]:
        if type(request) is not ProviderRequest or type(request.capability_id) is not str or type(request.parameters) is not dict or request.capability_id != "stock_history":
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if any(type(key) is not str for key in request.parameters) or not set(request.parameters).issubset({"symbol", "start_date", "end_date", "dataset", "max_age_days"}):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        symbol, dataset = request.parameters.get("symbol"), request.parameters.get("dataset")
        if type(symbol) is not str or not _SYMBOL.fullmatch(symbol) or type(dataset) is not str or not _DATASET.fullmatch(dataset):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        start, end = _request_date(request.parameters.get("start_date")), _request_date(request.parameters.get("end_date"))
        if start > end:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        max_age = request.parameters.get("max_age_days")
        if max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return _ENDPOINT, {"dataset": dataset, "symbols": symbol, "start": start, "end": end, "schema": "ohlcv-1d"}

    @staticmethod
    def _parse(payload: object, request: ProviderRequest, *, now: datetime, cached: bool) -> tuple[ProviderValue, ...]:
        DatabentoAdapter._request(request)
        if type(payload) is not dict or type(payload.get("metadata")) is not dict or type(payload.get("data")) is not list:
            raise _schema()
        metadata_row, items = payload["metadata"], payload["data"]
        if type(metadata_row.get("dataset")) is not str or not _DATASET.fullmatch(metadata_row["dataset"]) or type(metadata_row.get("schema")) is not str or len(metadata_row["schema"]) > 64:
            raise _schema()
        if not items:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(items) > _MAX_ROWS:
            raise _schema()
        max_age = request.parameters.get("max_age_days")
        rows: list[ProviderValue] = []
        for item in items:
            if type(item) is not dict:
                raise _schema()
            symbol, raw_date = item.get("symbol"), item.get("ts_event")
            if type(symbol) is not str or not _SYMBOL.fullmatch(symbol) or type(raw_date) is not str or len(raw_date) > 64:
                raise _schema()
            try:
                parsed_at = datetime.fromisoformat(raw_date[:-1] + "+00:00" if raw_date.endswith("Z") else raw_date)
            except ValueError:
                raise _schema() from None
            if parsed_at.tzinfo is None:
                raise _schema()
            as_of = parsed_at.astimezone(timezone.utc).date()
            if as_of > now.astimezone(timezone.utc).date():
                raise _schema()
            close, volume = _number(item.get("close")), _number(item.get("volume"))
            stale = type(max_age) is int and (now.date() - as_of).days > max_age
            status = "cached" if cached else ("stale" if stale else "upstream_reported")
            metadata = {
                "source_reference": _REFERENCE,
                "coverage": metadata_row["schema"],
                "dataset": metadata_row["dataset"],
                "plan_observation": "fixture_only_not_live_entitlement",
            }
            if cached:
                metadata["cache_status"] = "fixture_fallback"
            rows.append(ProviderValue(
                {"symbol": symbol, "close": close, "volume": volume}, "databento", "databento", request.capability_id,
                as_of, now, status, "Databento paid dataset terms apply", 65, None, "unknown", "daily", metadata,
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


__all__ = ["DatabentoAdapter"]
