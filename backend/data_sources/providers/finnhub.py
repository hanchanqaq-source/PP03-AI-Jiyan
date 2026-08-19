from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from urllib.parse import urlsplit

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.references import public_source_reference

from .base import BaseProvider


_REFERENCE = "https://finnhub.io/"
_QUOTE_ENDPOINT = f"{_REFERENCE}api/v1/quote"
_NEWS_ENDPOINT = f"{_REFERENCE}api/v1/company-news"
_ENV_NAME = "FINNHUB_API_KEY"
_MAX_ROWS = 1_000
_MAX_TEXT = 4_096
_SYMBOL = re.compile(r"^[A-Za-z0-9.^_-]{1,64}$")


def _now() -> datetime: return datetime.now(timezone.utc)


def _text(value: object, *, allow_blank: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not allow_blank and not value):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _number(value: object) -> Decimal:
    if type(value) not in {str, int, float, Decimal} or (type(value) is int and value.bit_length() > 333):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    raw = str(value)
    if len(raw) > 128: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try: parsed = Decimal(raw)
    except (InvalidOperation, ValueError): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 100 or abs(parsed.as_tuple().exponent) > 100:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return parsed


class FinnhubAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "finnhub", "Finnhub", "finnhub", "http_client",
        (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.COLLECTOR, SourceRole.CANDIDATE),
        ("stock_snapshot", "news_discovery"), BillingModel.FREEMIUM, "api_key", (_ENV_NAME,), False,
        "Finnhub account terms apply; plan and quota must be checked per capability.",
        "Market reference and news discovery only; collected news never replaces SEC or official evidence.",
        "以 Finnhub 和原发布者实际时间为准", "以账户实际套餐和配额为准", "套餐成本未知；无可信能力级权益时禁止请求",
        _REFERENCE, 130, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None,
                 entitlement_resolver: Any | None = None, cache_getter: Callable[[ProviderRequest], object | None] | None = None,
                 fetched_at: Callable[[], datetime] = _now) -> None:
        self._http, self._credentials, self._budget_guard = http, credentials, budget_guard
        self._entitlement_resolver = entitlement_resolver
        self._cache_getter, self._fetched_at = cache_getter, fetched_at

    def _credential(self) -> str | None:
        try: value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception: return None
        return value if type(value) is str and value.strip() else None

    def _trusted_entitlement(self, capability: str, now: datetime) -> tuple[str | None, object | None]:
        from data_sources.provider_registry import FreemiumEntitlementResolver
        if type(self._entitlement_resolver) is not FreemiumEntitlementResolver: return "entitlement_unavailable", None
        return self._entitlement_resolver.resolve(self.descriptor.adapter_id, capability, self.descriptor.billing_model, now=now)

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, dict[str, object]]:
        if type(request) is not ProviderRequest or type(request.parameters) is not dict: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if request.capability_id == "stock_snapshot":
            if set(request.parameters) != {"symbol"}: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
            symbol = request.parameters.get("symbol")
            if type(symbol) is not str or not _SYMBOL.fullmatch(symbol): raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
            return _QUOTE_ENDPOINT, {"symbol": symbol}
        if request.capability_id == "news_discovery":
            if set(request.parameters) != {"symbol", "from", "to"}: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
            symbol, start, end = (request.parameters.get(key) for key in ("symbol", "from", "to"))
            if type(symbol) is not str or not _SYMBOL.fullmatch(symbol) or type(start) is not str or type(end) is not str: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
            try:
                start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
            except ValueError: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE) from None
            if start_date > end_date: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
            return _NEWS_ENDPOINT, {"symbol": symbol, "from": start, "to": end}
        raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)

    @staticmethod
    def _payload_error(payload: dict[str, object]) -> None:
        if "error" not in payload: return
        message = _text(payload["error"]).lower()
        if "access" in message or "plan" in message or "subscription" in message: raise ProviderUnavailable("plan_unavailable", reference=_REFERENCE)
        if "key" in message or "auth" in message or "token" in message: raise ProviderUnavailable("authentication", reference=_REFERENCE)
        raise ProviderUnavailable("provider_error", reference=_REFERENCE)

    def _parse_quote(self, payload: object, request: ProviderRequest, *, now: datetime, ent: object, cached: bool) -> tuple[ProviderValue, ...]:
        if self._entitlement_resolver is None or not self._entitlement_resolver.validate_snapshot(ent, self.descriptor.adapter_id, request.capability_id, self.descriptor.billing_model, now=now): raise ProviderUnavailable("entitlement_invalid", reference=_REFERENCE)
        if type(payload) is not dict: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._payload_error(payload)
        if set(payload) != {"c", "d", "dp", "h", "l", "o", "pc", "t"}: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        timestamp = payload["t"]
        if type(timestamp) is not int or timestamp < 0 or timestamp > 4_102_444_800: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        observed = datetime.fromtimestamp(timestamp, timezone.utc)
        if observed > now: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        public = {name: _number(payload[field]) for name, field in (("current", "c"), ("change", "d"), ("change_percent", "dp"), ("high", "h"), ("low", "l"), ("open", "o"), ("previous_close", "pc"))}
        metadata = {"symbol": str(request.parameters["symbol"]), "provider_timestamp": observed.isoformat(), "publisher_role": "market_provider", "plan_name": ent.plan_name, "quota_remaining": "unknown" if ent.quota_remaining is None else str(ent.quota_remaining), "source_reference": _REFERENCE}
        if cached: metadata["cache_status"] = "fallback"
        return (ProviderValue(public, "finnhub", "finnhub", request.capability_id, observed.date(), now, "cached" if cached else "upstream_reported", "Finnhub account terms apply", 130, None, "unknown", "intraday", metadata),)

    def _parse_news(self, payload: object, request: ProviderRequest, *, now: datetime, ent: object, cached: bool) -> tuple[ProviderValue, ...]:
        if self._entitlement_resolver is None or not self._entitlement_resolver.validate_snapshot(ent, self.descriptor.adapter_id, request.capability_id, self.descriptor.billing_model, now=now): raise ProviderUnavailable("entitlement_invalid", reference=_REFERENCE)
        if type(payload) is dict:
            self._payload_error(payload)
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if type(payload) is not list: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not payload: raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(payload) > _MAX_ROWS: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        rows = []
        for article in payload:
            if type(article) is not dict or set(article) != {"category", "datetime", "headline", "id", "image", "related", "source", "summary", "url"}: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            category, title, summary, publisher, raw_url = (_text(article[field], allow_blank=field == "summary") for field in ("category", "headline", "summary", "source", "url"))
            timestamp = article["datetime"]
            if type(timestamp) is not int or timestamp < 0: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            published = datetime.fromtimestamp(timestamp, timezone.utc)
            if published > now: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            try: parts = urlsplit(raw_url)
            except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            if parts.scheme != "https" or parts.username or parts.password or not parts.hostname: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            try: url = public_source_reference(raw_url)
            except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            domain = parts.hostname.lower()
            public = {"title": title, "summary": summary, "publisher_name": publisher, "publisher_url": url, "origin_domain": domain, "published_at": published.isoformat(), "category": category, "collector": "finnhub", "candidate": True, "independent_evidence_eligible": False}
            metadata = {"collector": "finnhub", "collector_relation": "discovery_only", "origin_domain": domain, "origin_identity": domain, "plan_name": ent.plan_name, "quota_remaining": "unknown" if ent.quota_remaining is None else str(ent.quota_remaining), "source_reference": _REFERENCE, "delay_seconds": str(int((now - published).total_seconds()))}
            if cached: metadata["cache_status"] = "fallback"
            rows.append(ProviderValue(public, "finnhub", "finnhub", request.capability_id, published.date(), now, "cached_candidate" if cached else "candidate", "Finnhub discovery index; original publisher must be verified", 130, None, "candidate", "event_driven", metadata))
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


__all__ = ["FinnhubAdapter"]
