from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fund_data.providers import (
    AkshareDanjuanProvider,
    AkshareEastmoneyProvider,
    CninfoIndustryProvider,
    EastmoneyDirectProvider,
    TencentQuoteProvider,
)

from .models import SourceDescriptor, SourceGroup

PROVIDER_CLASSES = (
    CninfoIndustryProvider,
    EastmoneyDirectProvider,
    TencentQuoteProvider,
    AkshareEastmoneyProvider,
    AkshareDanjuanProvider,
)

CAPABILITY_GROUPS: dict[str, SourceGroup] = {
    "search": "fund",
    "profile": "fund",
    "nav_history": "fund",
    "holdings": "fund",
    "stock_snapshot": "quote",
    "industry_allocation": "industry",
    "stock_industry_classification": "industry",
}

PROVIDER_REFERENCES = {
    "cninfo-industry": "https://webapi.cninfo.com.cn/",
    "eastmoney-direct": "https://fund.eastmoney.com/",
    "tencent-quote": "https://qt.gtimg.cn/",
    "akshare-eastmoney": "https://fund.eastmoney.com/",
    "akshare-danjuan": "https://danjuanfunds.com/",
}

_SENSITIVE_QUERY_NAMES = {
    "api-key", "api_key", "apikey", "authorization", "auth", "cookie",
    "key", "secret", "signature", "token", "access_token",
}


def news_source_id(hint: str, name: str, url: str) -> str:
    raw = f"{hint.strip()}|{name.strip()}|{url.strip()}"
    return "news:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _public_reference(url: str) -> str:
    """Keep public routing parameters but never retain credential parameters."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.strip().lower() not in _SENSITIVE_QUERY_NAMES
    ]
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), ""))


def build_provider_descriptors(
    providers: Iterable[Any] | None = None,
) -> list[SourceDescriptor]:
    """Expand actual Provider classes or instances into source-by-capability rows."""
    provider_rows = list(providers if providers is not None else PROVIDER_CLASSES)
    minimum_priority: dict[str, int] = {}
    for provider in provider_rows:
        priority = int(getattr(provider, "priority", 100))
        for capability in getattr(provider, "capabilities", set()):
            minimum_priority[capability] = min(priority, minimum_priority.get(capability, priority))

    descriptors: list[SourceDescriptor] = []
    for provider in provider_rows:
        provider_name = str(getattr(provider, "name", provider.__class__.__name__))
        priority = int(getattr(provider, "priority", 100))
        for capability in sorted(getattr(provider, "capabilities", set())):
            group = CAPABILITY_GROUPS.get(capability, "fund")
            descriptors.append(SourceDescriptor(
                source_id=f"{group}:{provider_name}:{capability}",
                source_name=provider_name,
                group=group,
                capability=capability,
                source_reference=PROVIDER_REFERENCES.get(provider_name, ""),
                priority=priority,
                critical=priority == minimum_priority[capability],
                requires_api_key=False,
                probe_kind="provider",
            ))
    return sorted(descriptors, key=lambda row: row.source_id)


def build_news_descriptors(news_config: dict[str, Any]) -> list[SourceDescriptor]:
    descriptors = []
    for source in news_config.get("sources") or []:
        hint = str(source.get("hint") or "").strip()
        name = str(source.get("name") or "").strip()
        url = str(source.get("url") or "").strip()
        if not hint or not name or not url:
            continue
        descriptors.append(SourceDescriptor(
            source_id=news_source_id(hint, name, url),
            source_name=name,
            group="news",
            capability="feed",
            source_reference=_public_reference(url),
            priority=0,
            critical=False,
            requires_api_key=False,
            probe_kind="news_feed",
            probe_args={"hint": hint},
        ))
    return sorted(descriptors, key=lambda row: row.source_id)


def load_news_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path is not None else Path(__file__).parents[1] / "news_sources.json"
    with config_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    return loaded if isinstance(loaded, dict) else {"sources": []}


def build_registry(
    providers: Iterable[Any] | None = None,
    news_config: dict[str, Any] | None = None,
) -> list[SourceDescriptor]:
    rows = build_provider_descriptors(providers)
    rows.extend(build_news_descriptors(news_config if news_config is not None else load_news_config()))
    return sorted(rows, key=lambda row: row.source_id)
