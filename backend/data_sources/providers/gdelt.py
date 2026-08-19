from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import re
import time
from typing import Any
from urllib.parse import urlsplit

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.references import public_source_reference

from .base import BaseProvider


_REFERENCE = "https://api.gdeltproject.org/api/v2/doc/doc"
_TIMESPAN = re.compile(r"^[1-9][0-9]{0,2}(?:h|d|week|weeks|month|months)$")
_SEEN_DATE = re.compile(r"^\d{8}T\d{6}Z$")


def _now() -> datetime: return datetime.now(timezone.utc)


def _seen_at(value: object) -> str:
    if not isinstance(value, str) or not _SEEN_DATE.fullmatch(value):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try:
        datetime.strptime(value, "%Y%m%dT%H%M%SZ")
    except ValueError as error:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from error
    return value


class GdeltAdapter(BaseProvider):
    """GDELT DOC 2.0 discovery collector; it cannot admit or verify evidence."""

    descriptor = AdapterDescriptor("gdelt", "GDELT DOC 2.0", "gdelt", "http_client", (SourceRole.COLLECTOR, SourceRole.CANDIDATE), ("news_discovery",), BillingModel.FREE_NO_KEY, "none", (), True, "GDELT public discovery API；使用须遵守上游条款。", "仅返回候选及原发布者标识；不得作为可信或独立证据。", "近实时采集索引；以原发布者为准", "未声明；按公开入口合理限速", "免费无需密钥；不自动购买或升级", _REFERENCE, 100, CatalogStatus.CONFIGURED)

    def __init__(self, *, http: Any, sleeper: Callable[[float], None] = time.sleep, fetched_at: Callable[[], datetime] = _now) -> None: self._http, self._sleeper, self._fetched_at = http, sleeper, fetched_at

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, str, int]:
        if request.capability_id != "news_discovery": raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)
        query, timespan, maximum = request.parameters.get("query"), request.parameters.get("timespan", "1week"), request.parameters.get("max_records", 25)
        if not isinstance(query, str) or not query.strip() or len(query) > 500 or any(character in query for character in "\r\n") or not isinstance(timespan, str) or not _TIMESPAN.fullmatch(timespan) or type(maximum) is not int or not 1 <= maximum <= 250: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return query.strip(), timespan, maximum

    def _get_json(self, params: Mapping[str, object]) -> object:
        try: return self._http.get_json(_REFERENCE, headers={"Accept": "application/json"}, params=params)
        except ProviderRateLimited as error:
            self._sleeper(min(max(float(error.retry_after_seconds), 0.0), 60.0))
            return self._http.get_json(_REFERENCE, headers={"Accept": "application/json"}, params=params)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        query, timespan, maximum = self._request(request)
        payload = self._get_json({"query": query, "mode": "artlist", "format": "json", "maxrecords": maximum, "timespan": timespan})
        articles = payload.get("articles") if isinstance(payload, Mapping) else None
        if not isinstance(articles, list): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not articles: raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        values: list[ProviderValue] = []
        for article in articles:
            if not isinstance(article, Mapping): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            url, title, seen, domain, language, country = (article.get(field) for field in ("url", "title", "seendate", "domain", "language", "sourcecountry"))
            try: parts = urlsplit(url) if isinstance(url, str) else None
            except ValueError as error: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from error
            if not parts or parts.scheme != "https" or parts.username or parts.password or not parts.hostname or not isinstance(domain, str) or parts.hostname.lower() != domain.lower() or any(not isinstance(value, str) or not value for value in (title, language, country)):
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            try:
                publisher_url = public_source_reference(url)
            except ValueError as error:
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from error
            values.append(ProviderValue({"publisher_url": publisher_url, "origin_domain": domain.lower(), "title": title, "language": language, "source_country": country, "seen_at": _seen_at(seen), "collector": "gdelt", "candidate": True, "independent_evidence_eligible": False}, "gdelt", "gdelt", request.capability_id, None, self._fetched_at(), "candidate", "GDELT public discovery index; original publisher must be independently verified", 100, None, "candidate", "event_driven", {"collector": "gdelt", "origin_domain": domain.lower()}))
        return tuple(values)

    def probe(self, capability_id: str) -> Mapping[str, object]: return {"status": "not_probed" if capability_id == "news_discovery" else "unsupported_capability", "connected": False}
