from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
import importlib
from typing import Any

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://www.baostock.com/"
_SUPPORTED_CAPABILITIES = {
    "stock_history",
    "stock_history_adjusted",
    "stock_financials",
    "stock_industry_reference",
    "index_calendar",
    "stock_valuation",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_baostock() -> Any:
    return importlib.import_module("baostock")


def _empty_to_none(value: object) -> object:
    return None if value is None or (isinstance(value, str) and not value.strip()) else value


def _as_date(value: object) -> date | None:
    value = _empty_to_none(value)
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from exc


class BaoStockAdapter(BaseProvider):
    """BaoStock adapter with a per-request login/query/logout lifecycle."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        client_loader: Callable[[], Any] = _load_baostock,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._client = client
        self._client_loader = client_loader
        self._clock = clock

    def _client_or_unavailable(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            return self._client_loader()
        except (ImportError, ModuleNotFoundError) as exc:
            raise ProviderUnavailable("optional_dependency_unavailable", reference=_REFERENCE) from exc

    @staticmethod
    def _result_rows(result: Any) -> tuple[dict[str, object], ...]:
        if str(getattr(result, "error_code", "0")) != "0":
            raise ProviderUnavailable("baostock_query_failed", reference=_REFERENCE)
        fields = tuple(getattr(result, "fields", ()) or ())
        if not fields or not callable(getattr(result, "next", None)) or not callable(getattr(result, "get_row_data", None)):
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        rows: list[dict[str, object]] = []
        while result.next():
            values = tuple(result.get_row_data())
            if len(values) != len(fields):
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            rows.append({str(key): _empty_to_none(value) for key, value in zip(fields, values)})
        return tuple(rows)

    @staticmethod
    def _require(parameters: Mapping[str, object], name: str) -> str:
        value = _empty_to_none(parameters.get(name))
        if value is None:
            raise ProviderUnavailable("missing_required_parameter", reference=_REFERENCE)
        return str(value)

    def _query(self, client: Any, request: ProviderRequest) -> tuple[dict[str, object], ...]:
        parameters = request.parameters
        capability = request.capability_id
        if capability == "stock_history":
            return self._result_rows(client.query_history_k_data_plus(
                self._require(parameters, "code"),
                "date,code,open,high,low,close,volume,amount,adjustflag",
                start_date=parameters.get("start_date"), end_date=parameters.get("end_date"),
                frequency="d", adjustflag="3",
            ))
        if capability == "stock_history_adjusted":
            return self._result_rows(client.query_adjust_factor(
                self._require(parameters, "code"), start_date=parameters.get("start_date"), end_date=parameters.get("end_date"),
            ))
        if capability == "stock_financials":
            return self._result_rows(client.query_profit_data(
                self._require(parameters, "code"), int(parameters.get("year", 0)), int(parameters.get("quarter", 0)),
            ))
        if capability == "stock_industry_reference":
            return self._result_rows(client.query_stock_industry(
                code=self._require(parameters, "code"), date=parameters.get("date"),
            ))
        if capability == "index_calendar":
            return self._result_rows(client.query_trade_dates(
                self._require(parameters, "start_date"), self._require(parameters, "end_date"),
            ))
        if capability == "stock_valuation":
            return self._result_rows(client.query_history_k_data_plus(
                self._require(parameters, "code"), "date,code,peTTM,psTTM,pbMRQ",
                start_date=parameters.get("start_date"), end_date=parameters.get("end_date"),
                frequency="d", adjustflag="3",
            ))
        raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)

    @staticmethod
    def _normalise(capability: str, row: Mapping[str, object]) -> dict[str, object]:
        aliases = {
            "foreAdjustFactor": "forward_adjust_factor",
            "backAdjustFactor": "back_adjust_factor",
            "adjustFactor": "adjust_factor",
            "pubDate": "published_date",
            "statDate": "reported_date",
            "roeAvg": "roe_avg",
            "npMargin": "net_profit_margin",
            "calendar_date": "date",
            "is_trading_day": "is_trading_day",
            "peTTM": "pe_ttm",
            "psTTM": "ps_ttm",
            "pbMRQ": "pb_mrq",
        }
        return {aliases.get(key, key): value for key, value in row.items()}

    @staticmethod
    def _metadata(capability: str) -> tuple[str, str, str]:
        return {
            "stock_history": ("CNY", "daily", "date"),
            "stock_history_adjusted": ("factor", "event_driven", "dividOperateDate"),
            "stock_financials": ("ratio", "quarterly", "pubDate"),
            "stock_industry_reference": ("classification", "event_driven", "updateDate"),
            "index_calendar": ("boolean", "daily", "calendar_date"),
            "stock_valuation": ("ratio", "daily", "date"),
        }[capability]

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        if request.capability_id not in _SUPPORTED_CAPABILITIES:
            raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)
        client = self._client_or_unavailable()
        try:
            login = client.login()
            if str(getattr(login, "error_code", "0")) != "0":
                raise ProviderUnavailable("baostock_login_failed", reference=_REFERENCE)
            source_rows = self._query(client, request)
            unit, frequency, as_of_key = self._metadata(request.capability_id)
            return tuple(
                ProviderValue(
                    value=self._normalise(request.capability_id, row),
                    source_family_id="baostock",
                    adapter_id="baostock",
                    capability_id=request.capability_id,
                    as_of_date=_as_date(row.get(as_of_key)),
                    fetched_at=self._clock(),
                    data_status="upstream_reported",
                    license="BaoStock upstream terms apply",
                    priority=30,
                    difference_from_primary=None,
                    unit=unit,
                    frequency=frequency,
                )
                for row in source_rows
            )
        except ProviderUnavailable:
            raise
        except ProviderSchemaChanged:
            raise
        except Exception as exc:
            raise ProviderUnavailable("baostock_query_failed", reference=_REFERENCE) from exc
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def probe(self, capability_id: str) -> Mapping[str, object]:
        if capability_id not in _SUPPORTED_CAPABILITIES:
            return {"status": "unsupported_capability", "connected": False}
        try:
            self._client_or_unavailable()
        except ProviderUnavailable as error:
            return {"status": error.code, "connected": False}
        return {"status": "unexamined", "connected": False}
    descriptor = AdapterDescriptor(
        "baostock", "BaoStock", "baostock", "baostock",
        (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK),
        ("stock_history", "stock_history_adjusted", "stock_financials", "stock_industry_reference", "index_calendar", "stock_valuation"),
        BillingModel.FREE_NO_KEY, "none", (), True,
        "公开入口；使用须遵守 BaoStock 上游条款。",
        "独立于东方财富和腾讯；不能替代巨潮资讯行业证据或基金正式净值。",
        "以来源实际披露和响应为准", "未声明；按公开入口合理限速", "免费无需密钥；不自动购买或升级",
        _REFERENCE, 30, CatalogStatus.CONFIGURED,
    )
