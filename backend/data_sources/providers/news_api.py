from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from typing import Any
from urllib.parse import urlsplit

from data_sources.budgets import BudgetDecision
from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.references import public_source_reference

from .base import BaseProvider


_REFERENCE = "https://newsapi.org/"
_ENDPOINT = f"{_REFERENCE}v2/everything"
_ENV_NAME = "NEWS_API_KEY"
_MAX_ROWS, _MAX_TEXT = 1_000, 4_096
_PLAN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_CACHEABLE = {"timeout", "tls", "dns", "connection", "server_error", "rate_limited"}
_SENSITIVE = ("api_key", "apikey", "bearer", "credential", "password", "secret", "token")


def _now() -> datetime: return datetime.now(timezone.utc)


def _text(value: object, *, allow_blank: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not allow_blank and not value): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _cost(value: object) -> Decimal | None:
    if value is None: return None
    if type(value) is not Decimal or not value.is_finite() or value < 0 or value > Decimal("1000000") or len(value.as_tuple().digits) > 28 or not -8 <= value.as_tuple().exponent <= 12: raise ValueError("estimated_cost is invalid")
    return Decimal(value)


@dataclass(frozen=True, slots=True)
class NewsApiEntitlement:
    capability_id: str
    plan_name: str
    available: bool
    estimated_cost: Decimal | None
    quota_remaining: int | None

    def __post_init__(self) -> None:
        if self.capability_id != "news_discovery" or type(self.plan_name) is not str or not _PLAN.fullmatch(self.plan_name): raise ValueError("invalid NewsAPI entitlement")
        if type(self.available) is not bool: raise ValueError("available must be boolean")
        object.__setattr__(self, "estimated_cost", _cost(self.estimated_cost))
        if self.quota_remaining is not None and (type(self.quota_remaining) is not int or not 0 <= self.quota_remaining <= 1_000_000_000): raise ValueError("quota_remaining is invalid")


def _decision(value: object, estimate: Decimal) -> tuple[bool, str]:
    if type(value) is not BudgetDecision: raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    try: row = value.to_dict()
    except Exception: raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE) from None
    if (type(row) is not dict or set(row) != {"allowed", "reason", "reservation_id", "estimated_cost", "health_failure"} or type(row["allowed"]) is not bool or type(row["reason"]) is not str
        or (row["reservation_id"] is not None and type(row["reservation_id"]) is not str) or type(row["estimated_cost"]) is not str or type(row["health_failure"]) is not bool
        or row["estimated_cost"] != format(estimate, "f") or row["health_failure"] is not False or row["allowed"] is not (row["reason"] == "authorized")
        or (not row["allowed"] and row["reservation_id"] is not None) or (row["allowed"] and estimate > 0 and row["reservation_id"] is None)): raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    if row["reservation_id"] is not None and any(term in row["reservation_id"].lower() for term in _SENSITIVE): raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
    return row["allowed"], row["reason"]


class NewsApiAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "news-api", "NewsAPI", "news_api", "http_client", (SourceRole.COLLECTOR, SourceRole.CANDIDATE), ("news_discovery",),
        BillingModel.FREEMIUM, "api_key", (_ENV_NAME,), False, "NewsAPI account and publisher terms apply.",
        "仅用于候选发现；保留原发布者身份，不是内容来源或独立证据，不进入可信准入计数。",
        "以原发布者时间为准；聚合发现可能延迟", "以账户实际套餐、延迟和配额为准", "套餐成本未知；无可信能力级权益时禁止请求",
        _REFERENCE, 160, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None, entitlements: tuple[NewsApiEntitlement, ...] = (),
                 cache_getter: Callable[[ProviderRequest], object | None] | None = None, fetched_at: Callable[[], datetime] = _now) -> None:
        if type(entitlements) is not tuple or any(type(item) is not NewsApiEntitlement for item in entitlements) or len({item.capability_id for item in entitlements}) != len(entitlements): raise ValueError("invalid NewsAPI entitlements")
        self._http, self._credentials, self._budget_guard = http, credentials, budget_guard
        self._entitlements = {
            item.capability_id: NewsApiEntitlement(
                item.capability_id, item.plan_name, item.available, item.estimated_cost, item.quota_remaining
            ) for item in entitlements
        }
        self._cache_getter, self._fetched_at = cache_getter, fetched_at

    def _credential(self) -> str | None:
        try: value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception: return None
        return value if type(value) is str and value.strip() else None

    def _preflight(self, capability: str, now: datetime) -> tuple[str | None, NewsApiEntitlement | None]:
        if self._budget_guard is None: return "budget_guard_unavailable", None
        ent = self._entitlements.get(capability)
        if ent is None or ent.estimated_cost is None: return "cost_unknown", None
        if not ent.available: return "plan_unavailable", ent
        if ent.quota_remaining == 0: return "quota_exhausted", ent
        try: raw = self._budget_guard.authorize(self.descriptor, estimated_cost=ent.estimated_cost, now=now)
        except Exception: raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE) from None
        allowed, reason = _decision(raw, ent.estimated_cost)
        return (None, ent) if allowed else (reason, ent)

    @staticmethod
    def _request(request: ProviderRequest) -> dict[str, object]:
        allowed = {"query", "from", "to", "page_size", "language", "sort_by"}
        if type(request) is not ProviderRequest or request.capability_id != "news_discovery" or type(request.parameters) is not dict or set(request.parameters) - allowed: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        query, start, end = request.parameters.get("query"), request.parameters.get("from"), request.parameters.get("to")
        page_size, language, sort_by = request.parameters.get("page_size", 25), request.parameters.get("language", "en"), request.parameters.get("sort_by", "publishedAt")
        if type(query) is not str or not query.strip() or len(query) > 500 or any(char in query for char in "\r\n") or type(start) is not str or type(end) is not str: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        try: start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE) from None
        if start_date > end_date or type(page_size) is not int or not 1 <= page_size <= 100 or type(language) is not str or not re.fullmatch(r"[a-z]{2}", language) or sort_by not in {"publishedAt", "relevancy", "popularity"}: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return {"q": query.strip(), "from": start, "to": end, "pageSize": page_size, "language": language, "sortBy": sort_by}

    @staticmethod
    def _error(payload: dict[str, object]) -> None:
        if payload.get("status") != "error": return
        code, _message = _text(payload.get("code")), _text(payload.get("message"))
        if code == "rateLimited":
            retry = payload.get("retry_after", 0)
            if type(retry) not in {int, float} or type(retry) is bool or not 0 <= retry <= 60: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            raise ProviderRateLimited(retry_after_seconds=float(retry), reference=_REFERENCE)
        if code in {"apiKeyInvalid", "apiKeyMissing", "apiKeyDisabled"}: raise ProviderUnavailable("authentication", reference=_REFERENCE)
        if code in {"apiKeyExhausted", "parameterInvalid"}: raise ProviderUnavailable("plan_unavailable", reference=_REFERENCE)
        raise ProviderUnavailable("provider_error", reference=_REFERENCE)

    def _parse(self, payload: object, request: ProviderRequest, *, now: datetime, ent: NewsApiEntitlement, cached: bool) -> tuple[ProviderValue, ...]:
        if type(payload) is not dict: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._error(payload)
        total, articles = payload.get("totalResults"), payload.get("articles")
        if payload.get("status") != "ok" or type(total) is not int or total < 0 or type(articles) is not list: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not articles: raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(articles) > _MAX_ROWS or total < len(articles): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        rows = []
        required = {"source", "author", "title", "description", "url", "urlToImage", "publishedAt", "content"}
        for article in articles:
            if type(article) is not dict or set(article) != required or type(article["source"]) is not dict or set(article["source"]) != {"id", "name"}: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            publisher_id = article["source"]["id"]
            if publisher_id is not None and type(publisher_id) is not str: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            publisher, title, summary, raw_url, raw_published = (_text(value, allow_blank=allow_blank) for value, allow_blank in ((article["source"]["name"], False), (article["title"], False), (article["description"], True), (article["url"], False), (article["publishedAt"], False)))
            try: published = datetime.fromisoformat(raw_published.replace("Z", "+00:00"))
            except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            if published.tzinfo is None: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            published = published.astimezone(timezone.utc)
            if published > now: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            try: parts = urlsplit(raw_url)
            except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            if parts.scheme != "https" or parts.username or parts.password or not parts.hostname: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            try: url = public_source_reference(raw_url)
            except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            domain = parts.hostname.lower()
            public = {"title": title, "summary": summary, "publisher_name": publisher, "publisher_url": url, "origin_domain": domain, "published_at": published.isoformat(), "collector": "news_api", "candidate": True, "independent_evidence_eligible": False}
            metadata = {"collector": "news_api", "collector_relation": "discovery_only", "origin_domain": domain, "origin_identity": domain, "publisher_id": publisher_id or "unknown", "delay_seconds": str(int((now - published).total_seconds())), "plan_name": ent.plan_name, "quota_remaining": "unknown" if ent.quota_remaining is None else str(ent.quota_remaining), "source_reference": _REFERENCE}
            if cached: metadata["cache_status"] = "fallback"
            rows.append(ProviderValue(public, "news_api", "news-api", request.capability_id, published.date(), now, "cached_candidate" if cached else "candidate", "NewsAPI discovery index; original publisher must be verified", 160, None, "candidate", "event_driven", metadata))
        return tuple(rows)

    def _execute(self, request: ProviderRequest, credential: str, now: datetime, ent: NewsApiEntitlement) -> tuple[ProviderValue, ...]:
        params = self._request(request); params["apiKey"] = credential
        try: payload = self._http.get_json(_ENDPOINT, headers={"Accept": "application/json"}, params=params)
        except ProviderUnavailable as error:
            if self._cache_getter is None or error.code not in _CACHEABLE: raise
            payload = self._cache_getter(request)
            if payload is None: raise
            cached = True
        else: cached = False
        return self._parse(payload, request, now=now, ent=ent, cached=cached)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        credential = self._credential()
        if credential is None: raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        now = self._fetched_at(); blocked, ent = self._preflight(request.capability_id, now)
        if blocked is not None or ent is None: raise ProviderUnavailable(blocked or "budget_status_invalid", reference=_REFERENCE)
        return self._execute(request, credential, now, ent)

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        credential = self._credential()
        if credential is None: return {"status": "unconfigured", "connected": False, "health_failure": False}
        now = self._fetched_at(); blocked, ent = self._preflight(capability_id, now)
        if blocked is not None or ent is None: return {"status": blocked or "budget_status_invalid", "connected": False, "health_failure": False, "capability_id": capability_id}
        today = now.date().isoformat(); request = ProviderRequest(capability_id, {"query": "market", "from": today, "to": today, "page_size": 1} if parameters is None else parameters)
        try: rows = self._execute(request, credential, now, ent)
        except ProviderRateLimited as error: return {"status": "rate_limited", "connected": False, "health_failure": False, "capability_id": capability_id, "retry_after_seconds": error.retry_after_seconds}
        except ProviderSchemaChanged: return {"status": "schema_changed", "connected": False, "health_failure": True, "capability_id": capability_id}
        except ProviderUnavailable as error:
            status = "authentication_failed" if error.code == "authentication" else error.code
            return {"status": status, "connected": False, "health_failure": error.code not in {"authentication", "plan_unavailable", "quota_exhausted", "empty_result"}, "capability_id": capability_id}
        return {"status": "available", "connected": True, "health_failure": False, "capability_id": capability_id, "returned_count": len(rows)}


__all__ = ["NewsApiAdapter", "NewsApiEntitlement"]
