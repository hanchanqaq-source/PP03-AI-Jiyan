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


_REFERENCE = "https://www.alphavantage.co/"
_ENDPOINT = f"{_REFERENCE}query"
_ENV_NAME = "ALPHA_VANTAGE_API_KEY"
_MAX_ROWS = 1_000
_MAX_TEXT = 4_096
_MAX_NUMBER_TEXT = 128
_IDENTIFIER = re.compile(r"^[A-Za-z0-9.^_-]{1,64}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: object, *, allow_blank: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not allow_blank and not value):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _number(value: object) -> Decimal:
    if type(value) not in {str, int, float, Decimal}:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    if type(value) is int and value.bit_length() > 333:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    raw = str(value)
    if len(raw) > _MAX_NUMBER_TEXT:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 100 or abs(parsed.as_tuple().exponent) > 100:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return parsed


class AlphaVantageAdapter(BaseProvider):
    """Low-frequency Alpha Vantage fallback with explicit plan authorization."""

    descriptor = AdapterDescriptor(
        "alpha-vantage", "Alpha Vantage", "alpha_vantage", "http_client",
        (SourceRole.FALLBACK_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK), ("stock_history",),
        BillingModel.FREEMIUM, "api_key", (_ENV_NAME,), False,
        "Alpha Vantage account terms apply; capability and quota depend on the actual plan.",
        "Low-frequency fallback only; never promoted to primary market data.",
        "以 Alpha Vantage 实际返回为准", "以账户实际套餐和配额为准", "套餐成本未知；无可信能力级权益时禁止请求",
        _REFERENCE, 120, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None,
                 entitlement_resolver: Any | None = None,
                 cache_getter: Callable[[ProviderRequest], object | None] | None = None,
                 fetched_at: Callable[[], datetime] = _now) -> None:
        self._http, self._credentials, self._budget_guard = http, credentials, budget_guard
        self._entitlement_resolver = entitlement_resolver
        self._cache_getter, self._fetched_at = cache_getter, fetched_at

    def _credential(self) -> str | None:
        try:
            value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception:
            return None
        return value if type(value) is str and value.strip() else None

    def _trusted_entitlement(self, capability_id: str, now: datetime) -> tuple[str | None, object | None]:
        from data_sources.provider_registry import FreemiumEntitlementResolver
        if type(self._entitlement_resolver) is not FreemiumEntitlementResolver:
            return "entitlement_unavailable", None
        return self._entitlement_resolver.resolve(
            self.descriptor.adapter_id, capability_id, self.descriptor.billing_model, now=now
        )

    @staticmethod
    def _request(request: ProviderRequest) -> dict[str, object]:
        if type(request) is not ProviderRequest or request.capability_id != "stock_history" or type(request.parameters) is not dict:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if set(request.parameters) - {"symbol", "function", "interval", "outputsize", "max_age_days"}:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        symbol, function, interval = request.parameters.get("symbol"), request.parameters.get("function"), request.parameters.get("interval")
        if type(symbol) is not str or not _IDENTIFIER.fullmatch(symbol) or function != "TIME_SERIES_DAILY" or interval != "daily":
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        outputsize = request.parameters.get("outputsize", "compact")
        if outputsize not in {"compact", "full"}:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        max_age = request.parameters.get("max_age_days")
        if max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return {"symbol": symbol, "function": function, "outputsize": outputsize}

    @staticmethod
    def _payload_error(payload: dict[str, object]) -> None:
        if "Note" in payload or "Information" in payload:
            _text(payload.get("Note", payload.get("Information")))
            raise ProviderRateLimited(retry_after_seconds=0.0, reference=_REFERENCE)
        if "Error Message" in payload:
            _text(payload["Error Message"])
            raise ProviderUnavailable("authentication", reference=_REFERENCE)

    def _parse(self, payload: object, request: ProviderRequest, *, now: datetime,
               entitlement: object, cached: bool) -> tuple[ProviderValue, ...]:
        if self._entitlement_resolver is None or not self._entitlement_resolver.validate_snapshot(
            entitlement, self.descriptor.adapter_id, request.capability_id, self.descriptor.billing_model, now=now
        ):
            raise ProviderUnavailable("entitlement_invalid", reference=_REFERENCE)
        if type(payload) is not dict:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._payload_error(payload)
        metadata, series = payload.get("Meta Data"), payload.get("Time Series (Daily)")
        if type(metadata) is not dict or type(series) is not dict:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not series:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(series) > _MAX_ROWS:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        symbol = _text(metadata.get("2. Symbol"))
        if symbol != request.parameters.get("symbol"):
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        _text(metadata.get("1. Information"))
        refreshed = _text(metadata.get("3. Last Refreshed"))
        timezone_name = _text(metadata.get("5. Time Zone"))
        try:
            if datetime.fromisoformat(refreshed).date() > now.date():
                raise ValueError
        except ValueError:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
        rows: list[ProviderValue] = []
        max_age = request.parameters.get("max_age_days")
        for raw_date, values in series.items():
            if type(raw_date) is not str or type(values) is not dict:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            try:
                as_of = date.fromisoformat(raw_date)
            except ValueError:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            if as_of > now.date() or set(values) != {"4. close", "5. volume"}:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            public = {"close": _number(values["4. close"]), "volume": _number(values["5. volume"])}
            source_metadata = {
                "function": "TIME_SERIES_DAILY", "interval": "daily", "symbol": symbol, "timezone": timezone_name,
                "plan_name": entitlement.plan_name, "quota_remaining": "unknown" if entitlement.quota_remaining is None else str(entitlement.quota_remaining),
                "source_reference": _REFERENCE,
            }
            if cached:
                source_metadata["cache_status"] = "fallback"
            stale = type(max_age) is int and (now.date() - as_of).days > max_age
            rows.append(ProviderValue(public, "alpha_vantage", "alpha-vantage", request.capability_id, as_of, now,
                                      "cached" if cached else ("stale" if stale else "upstream_reported"), "Alpha Vantage account terms apply", 120,
                                      None, "unknown", "daily", source_metadata))
        return tuple(rows)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        credential = self._credential()
        if credential is None:
            raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        del credential, request
        raise ProviderUnavailable("unsupported_credential_transport", reference=_REFERENCE)

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        credential = self._credential()
        if credential is None:
            return {"status": "unconfigured", "connected": False, "health_failure": False}
        del credential, parameters
        return {"status": "unsupported_credential_transport", "connected": False, "health_failure": False, "capability_id": capability_id}


__all__ = ["AlphaVantageAdapter"]
