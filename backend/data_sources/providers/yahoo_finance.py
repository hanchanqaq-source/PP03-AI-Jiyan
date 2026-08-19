from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timezone
import importlib
from typing import Any

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://ranaroussi.github.io/yfinance/"
_HISTORY_CAPABILITIES = {"overseas_stock_history", "overseas_etf_history", "overseas_index_history"}
_PROFILE_CAPABILITY = "overseas_profile_reference"
_SUPPORTED_CAPABILITIES = _HISTORY_CAPABILITIES | {_PROFILE_CAPABILITY}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_yfinance() -> Any:
    return importlib.import_module("yfinance")


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from exc


def _timestamp(value: object) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return {str(key): item for key, item in result.items()}
    raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)


class YahooFinanceAdapter(BaseProvider):
    """Non-official Yahoo Finance reference adapter, restricted to personal research."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        client_loader: Callable[[], Any] = _load_yfinance,
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
    def _symbol(request: ProviderRequest) -> str:
        symbol = str(request.parameters.get("symbol") or "").strip()
        if not symbol:
            raise ProviderUnavailable("missing_required_parameter", reference=_REFERENCE)
        upper = symbol.upper()
        if upper.endswith((".SS", ".SZ", ".BJ")) or upper.startswith(("SH.", "SZ.", "BJ.")):
            raise ProviderUnavailable("overseas_symbol_required", reference=_REFERENCE)
        return symbol

    @staticmethod
    def _ensure_personal_research(request: ProviderRequest) -> None:
        if request.parameters.get("usage_mode") != "personal_research":
            raise ProviderUnavailable("personal_research_required", reference=_REFERENCE)

    @staticmethod
    def _history_rows(history: object) -> Iterable[tuple[object, dict[str, object]]]:
        iterrows = getattr(history, "iterrows", None)
        if callable(iterrows):
            return tuple((index, _mapping(row)) for index, row in iterrows())
        if isinstance(history, Iterable) and not isinstance(history, (str, bytes, Mapping)):
            return tuple((_mapping(row).get("Date"), _mapping(row)) for row in history)
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)

    def _value(self, *, request: ProviderRequest, row: Mapping[str, object], timestamp: object, currency: object) -> ProviderValue:
        upstream_timestamp = _timestamp(timestamp or row.get("Date") or row.get("Datetime"))
        return ProviderValue(
            value={
                "symbol": self._symbol(request),
                "open": row.get("Open"),
                "high": row.get("High"),
                "low": row.get("Low"),
                "close": row.get("Close"),
                "volume": row.get("Volume"),
                "upstream_timestamp": upstream_timestamp,
                "currency": currency,
                "official_evidence_eligible": False,
            },
            source_family_id="yahoo_finance",
            adapter_id="yahoo-finance",
            capability_id=request.capability_id,
            as_of_date=_as_date(upstream_timestamp),
            fetched_at=self._clock(),
            data_status="non_official_reference",
            license="yfinance and Yahoo Finance terms apply; non-official reference only",
            priority=200,
            difference_from_primary=None,
            unit=str(currency) if currency else "unknown",
            frequency="daily",
        )

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        if request.capability_id not in _SUPPORTED_CAPABILITIES:
            raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)
        self._ensure_personal_research(request)
        symbol = self._symbol(request)
        client = self._client_or_unavailable()
        try:
            ticker = client.Ticker(symbol)
            if request.capability_id == _PROFILE_CAPABILITY:
                get_info = getattr(ticker, "get_info", None)
                info = get_info() if callable(get_info) else getattr(ticker, "info", None)
                info = _mapping(info)
                currency = info.get("currency")
                return (ProviderValue(
                    value={
                        "symbol": symbol,
                        "name": info.get("longName") or info.get("shortName"),
                        "instrument_type": info.get("quoteType"),
                        "exchange": info.get("exchange"),
                        "currency": currency,
                        "official_evidence_eligible": False,
                    },
                    source_family_id="yahoo_finance", adapter_id="yahoo-finance", capability_id=request.capability_id,
                    as_of_date=None, fetched_at=self._clock(), data_status="non_official_reference",
                    license="yfinance and Yahoo Finance terms apply; non-official reference only", priority=200,
                    difference_from_primary=None, unit=str(currency) if currency else "unknown", frequency="event_driven",
                ),)
            history = ticker.history(
                start=request.parameters.get("start_date"), end=request.parameters.get("end_date"), interval="1d", auto_adjust=False,
            )
            rows = tuple(self._history_rows(history))
            if not rows:
                raise ProviderUnavailable("empty_upstream_response", reference=_REFERENCE)
            currency = next((row.get("Currency") for _stamp, row in rows if row.get("Currency")), None)
            return tuple(self._value(request=request, row=row, timestamp=stamp, currency=currency) for stamp, row in rows)
        except (ProviderUnavailable, ProviderSchemaChanged):
            raise
        except Exception as exc:
            raise ProviderUnavailable("yahoo_finance_query_failed", reference=_REFERENCE) from exc

    def probe(self, capability_id: str) -> Mapping[str, object]:
        if capability_id not in _SUPPORTED_CAPABILITIES:
            return {"status": "unsupported_capability", "connected": False}
        try:
            self._client_or_unavailable()
        except ProviderUnavailable as error:
            return {"status": error.code, "connected": False}
        return {"status": "disabled_personal_research_only", "connected": False}
    descriptor = AdapterDescriptor(
        "yahoo-finance", "Yahoo Finance／yfinance", "yahoo_finance", "yfinance",
        (SourceRole.FALLBACK_DATA,),
        ("overseas_stock_history", "overseas_etf_history", "overseas_index_history", "overseas_profile_reference"),
        BillingModel.FREE_NO_KEY, "none", (), False,
        "非官方参考路径；使用须遵守 yfinance 与 Yahoo Finance 条款。",
        "默认关闭；只限 personal_research，不得提升为官方证据或独立证据。",
        "以来源实际披露和响应为准", "未声明；按公开入口合理限速", "免费无需密钥；不自动购买或升级",
        _REFERENCE, 200, CatalogStatus.DISABLED,
    )
