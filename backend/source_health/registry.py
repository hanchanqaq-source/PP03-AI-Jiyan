from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from fund_data.service import default_fund_providers

from .models import SourceDescriptor, SourceGroup

CAPABILITY_GROUPS: dict[str, SourceGroup] = {
    "search": "fund",
    "profile": "fund",
    "nav_history": "fund",
    "holdings": "fund",
    "stock_snapshot": "quote",
    "industry_allocation": "industry",
    "stock_industry_classification": "industry",
}

_DAY_SECONDS = 24 * 60 * 60
CAPABILITY_FRESHNESS_MAX_AGE_SECONDS: dict[str, int] = {
    # Daily public market facts allow weekends and short market holidays.
    "nav_history": 7 * _DAY_SECONDS,
    "stock_snapshot": 3 * _DAY_SECONDS,
    # Fund disclosures are normally quarterly; two quarters plus a small buffer is stale.
    "holdings": 200 * _DAY_SECONDS,
    "industry_allocation": 200 * _DAY_SECONDS,
    # Public industry classifications change less frequently but still have dated evidence.
    "stock_industry_classification": 400 * _DAY_SECONDS,
}

def public_source_reference(url: str) -> str:
    """Compatibility alias for the catalog-owned public-reference sanitizer."""
    from data_sources.references import public_source_reference as sanitize_reference

    return sanitize_reference(url)

def news_source_id(hint: str, name: str, url: str) -> str:
    raw = f"{hint.strip()}|{name.strip()}|{public_source_reference(url)}"
    return "news:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _news_configuration_identity(source: dict[str, Any]) -> str:
    exact_fields = tuple(
        str(source.get(key) or "").strip()
        for key in ("hint", "name", "url", "language", "region")
    )
    canonical = json.dumps(exact_fields, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_provider_descriptors(
    providers: Iterable[Any] | None = None,
) -> list[SourceDescriptor]:
    """Expand current providers through their Catalog adapter identity."""
    from data_sources.catalog import build_catalog
    from data_sources.health_bridge import catalog_probe_descriptors

    provider_rows = list(providers) if providers is not None else default_fund_providers()
    return [
        row for row in catalog_probe_descriptors(build_catalog({"sources": []}), provider_rows, {"sources": []})
        if row.probe_kind == "provider"
    ]


def build_news_descriptors(news_config: dict[str, Any]) -> list[SourceDescriptor]:
    from data_sources.catalog import build_catalog
    from data_sources.health_bridge import catalog_probe_descriptors

    return [
        row for row in catalog_probe_descriptors(build_catalog(news_config), [], news_config)
        if row.probe_kind == "news_feed"
    ]


def load_news_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path is not None else Path(__file__).parents[1] / "news_sources.json"
    with config_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    return loaded if isinstance(loaded, dict) else {"sources": []}


def build_registry(
    providers: Iterable[Any] | None = None,
    news_config: dict[str, Any] | None = None,
) -> list[SourceDescriptor]:
    from data_sources.catalog import build_catalog
    from data_sources.health_bridge import catalog_probe_descriptors

    configured_news = news_config if news_config is not None else load_news_config()
    provider_rows = list(providers) if providers is not None else default_fund_providers()
    return catalog_probe_descriptors(build_catalog(configured_news), provider_rows, configured_news)
