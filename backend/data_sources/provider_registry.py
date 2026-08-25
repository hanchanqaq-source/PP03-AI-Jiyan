from __future__ import annotations

from dataclasses import dataclass
import importlib
import threading
from typing import Any, Callable, Mapping

from .catalog import build_catalog
from .credentials import EnvironmentCredentialStore
from .http import SafeHttpClient


@dataclass(frozen=True, slots=True)
class _ProviderFactory:
    module_name: str
    class_name: str
    requires_http: bool = False
    requires_credentials: bool = False


_FACTORIES: Mapping[str, _ProviderFactory] = {
    "baostock": _ProviderFactory("data_sources.providers.baostock", "BaoStockAdapter"),
    "gdelt": _ProviderFactory("data_sources.providers.gdelt", "GdeltAdapter", True),
    "imf": _ProviderFactory("data_sources.providers.imf", "ImfAdapter", True),
    "oecd": _ProviderFactory("data_sources.providers.oecd", "OecdAdapter", True),
    "sec-edgar": _ProviderFactory("data_sources.providers.sec_edgar", "SecEdgarAdapter", True),
    "world-bank": _ProviderFactory("data_sources.providers.world_bank", "WorldBankAdapter", True),
    "yahoo-finance": _ProviderFactory("data_sources.providers.yahoo_finance", "YahooFinanceAdapter"),
    "fred": _ProviderFactory("data_sources.providers.fred", "FredAdapter", True, True),
    "eia": _ProviderFactory("data_sources.providers.eia", "EiaAdapter", True, True),
    "tushare": _ProviderFactory("data_sources.providers.tushare", "TushareAdapter", True, True),
    "alpha-vantage": _ProviderFactory("data_sources.providers.alpha_vantage", "AlphaVantageAdapter", True, True),
    "finnhub": _ProviderFactory("data_sources.providers.finnhub", "FinnhubAdapter", True, True),
    "twelve-data": _ProviderFactory("data_sources.providers.twelve_data", "TwelveDataAdapter", True, True),
    "nasdaq-data-link": _ProviderFactory("data_sources.providers.nasdaq_data_link", "NasdaqDataLinkAdapter", True, True),
    "news-api": _ProviderFactory("data_sources.providers.news_api", "NewsApiAdapter", True, True),
    "fmp": _ProviderFactory("data_sources.providers.fmp", "FmpAdapter", True, True),
    "massive": _ProviderFactory("data_sources.providers.massive", "MassiveAdapter", True, True),
    "tiingo": _ProviderFactory("data_sources.providers.tiingo", "TiingoAdapter", True, True),
    "eodhd": _ProviderFactory("data_sources.providers.eodhd", "EodhdAdapter", True, True),
    "databento": _ProviderFactory("data_sources.providers.databento", "DatabentoAdapter", True, True),
    "trendforce-public-price": _ProviderFactory(
        "data_sources.providers.industry_price_public",
        "TrendForcePublicPriceAdapter",
        True,
    ),
}


class ProviderRegistry:
    """Resolve provider implementations only when an adapter is requested.

    Importing this registry (or building the Catalog) does not import optional
    BaoStock/yfinance dependencies and does not create a transport client.
    """

    def __init__(
        self,
        catalog: Any | None = None,
        *,
        http_factory: Callable[[], Any] = SafeHttpClient,
        credential_factory: Callable[[Mapping[str, tuple[str, ...]]], Any] = EnvironmentCredentialStore,
        budget_guard_factory: Callable[[str], Any | None] | None = None,
        credential_store: Any | None = None,
        budget_guard: Any | None = None,
    ) -> None:
        self._catalog = catalog or build_catalog({"sources": []})
        catalog_adapter_ids = {adapter.adapter_id for adapter in self._catalog.adapters}
        unknown = set(_FACTORIES) - catalog_adapter_ids
        if unknown:
            raise ValueError(f"Provider registry IDs missing from Catalog: {', '.join(sorted(unknown))}")
        self._http_factory = http_factory
        self._credential_factory = credential_factory
        self._budget_guard_factory = budget_guard_factory
        self._credential_store = credential_store
        self._budget_guard = budget_guard
        self._instances: dict[str, Any] = {}
        self._instance_lock = threading.RLock()

    def available_adapter_ids(self) -> tuple[str, ...]:
        return tuple(sorted(_FACTORIES))

    def adapter(self, adapter_id: str) -> Any:
        normalized = str(adapter_id).strip()
        factory = _FACTORIES.get(normalized)
        if factory is None:
            raise KeyError(f"unknown provider adapter: {normalized}")
        with self._instance_lock:
            if normalized not in self._instances:
                module = importlib.import_module(factory.module_name)
                adapter_type = getattr(module, factory.class_name)
                kwargs = {"http": self._http_factory()} if factory.requires_http else {}
                if factory.requires_credentials:
                    catalog_descriptor = self._catalog.adapter(normalized)
                    scope = {normalized: tuple(catalog_descriptor.credential_env_names)}
                    kwargs["credentials"] = (
                        self._credential_store
                        if self._credential_store is not None
                        else self._credential_factory(scope)
                    )
                    if self._budget_guard is not None:
                        kwargs["budget_guard"] = self._budget_guard
                    elif self._budget_guard_factory is not None:
                        kwargs["budget_guard"] = self._budget_guard_factory(normalized)
                adapter = adapter_type(**kwargs)
                descriptor = getattr(adapter, "descriptor", None)
                if getattr(descriptor, "adapter_id", None) != normalized:
                    raise ValueError(f"Provider implementation does not match Catalog adapter: {normalized}")
                self._instances[normalized] = adapter
            return self._instances[normalized]
