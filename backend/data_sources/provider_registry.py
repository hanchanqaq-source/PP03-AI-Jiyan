from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib
import re
import threading
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .catalog import build_catalog
from .credentials import EnvironmentCredentialStore
from .http import SafeHttpClient
from .models import BillingModel


_ENTITLEMENT_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PLAN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_ENTITLEMENT_CAPABILITIES = {
    "alpha-vantage": frozenset({"stock_history"}),
    "finnhub": frozenset({"stock_snapshot", "news_discovery"}),
    "twelve-data": frozenset({"stock_history"}),
    "nasdaq-data-link": frozenset({"macro_series"}),
    "news-api": frozenset({"news_discovery"}),
}
_PROVENANCE = frozenset({"server_validated_config", "account_validation_response", "deterministic_test_fixture"})
_TEST_RESOLVER_TOKEN = object()
_SERVER_RESOLVER_TOKEN = object()


def _entitlement_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or value < 0
        or value > Decimal("1000000")
        or len(value.as_tuple().digits) > 28
        or not -8 <= value.as_tuple().exponent <= 12
    ):
        raise ValueError(f"{field} is invalid")
    return Decimal(value)


def _aware_utc(value: object, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    if normalized.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must normalize to UTC")
    return normalized


@dataclass(frozen=True, slots=True)
class _FreemiumEntitlementSnapshot:
    adapter_id: str
    capability_id: str
    billing_model: BillingModel
    plan_name: str
    available: bool
    quota_remaining: int | None
    estimated_cost: Decimal | None
    actual_cost: Decimal | None
    observed_at: datetime
    expires_at: datetime
    provenance: str
    premium_access: bool
    _issuer: object


class FreemiumEntitlementResolver:
    """Server-owned authority for fresh, capability-scoped account facts.

    Adapters never accept entitlement snapshots from callers.  They request a
    fresh snapshot from this resolver and validate its opaque issuer before use.
    The test constructor is explicit and cannot be mistaken for Live evidence.
    """

    def __init__(self, records: tuple[dict[str, object], ...], *, _token: object) -> None:
        if _token not in {_TEST_RESOLVER_TOKEN, _SERVER_RESOLVER_TOKEN}:
            raise TypeError("use a validated resolver factory")
        if type(records) is not tuple:
            raise ValueError("entitlement records must be an immutable tuple")
        self._issuer = object()
        normalized: dict[tuple[str, str], tuple[object, ...]] = {}
        required = {
            "adapter_id", "capability_id", "billing_model", "plan_name", "available",
            "quota_remaining", "estimated_cost", "actual_cost", "observed_at", "expires_at", "provenance",
        }
        for record in records:
            if type(record) is not dict or frozenset(record) not in {frozenset(required), frozenset((*required, "premium_access"))}:
                raise ValueError("entitlement record schema is invalid")
            adapter_id, capability_id = record["adapter_id"], record["capability_id"]
            if type(adapter_id) is not str or adapter_id not in _ENTITLEMENT_CAPABILITIES:
                raise ValueError("entitlement adapter is invalid")
            if type(capability_id) is not str or capability_id not in _ENTITLEMENT_CAPABILITIES[adapter_id]:
                raise ValueError("entitlement capability is invalid")
            billing = record["billing_model"]
            if billing != BillingModel.FREEMIUM.value or type(billing) is not str:
                raise ValueError("entitlement billing model is invalid")
            plan_name = record["plan_name"]
            available = record["available"]
            quota = record["quota_remaining"]
            provenance = record["provenance"]
            premium_access = record.get("premium_access", False)
            if type(plan_name) is not str or not _PLAN_NAME.fullmatch(plan_name):
                raise ValueError("entitlement plan is invalid")
            if type(available) is not bool or type(premium_access) is not bool:
                raise ValueError("entitlement flags are invalid")
            if quota is not None and (type(quota) is not int or not 0 <= quota <= 1_000_000_000):
                raise ValueError("entitlement quota is invalid")
            if type(provenance) is not str or provenance not in _PROVENANCE:
                raise ValueError("entitlement provenance is invalid")
            if _token is _SERVER_RESOLVER_TOKEN and provenance == "deterministic_test_fixture":
                raise ValueError("test fixture provenance cannot authorize server composition")
            if _token is _TEST_RESOLVER_TOKEN and provenance != "deterministic_test_fixture":
                raise ValueError("test resolver requires deterministic fixture provenance")
            estimated = _entitlement_decimal(record["estimated_cost"], "estimated_cost")
            actual = _entitlement_decimal(record["actual_cost"], "actual_cost")
            observed = _aware_utc(record["observed_at"], "observed_at")
            expires = _aware_utc(record["expires_at"], "expires_at")
            if expires <= observed or expires - observed > timedelta(days=31):
                raise ValueError("entitlement freshness window is invalid")
            if estimated is not None and estimated > 0 and actual is None:
                raise ValueError("cost-bearing entitlement requires trusted actual cost")
            key = (adapter_id, capability_id)
            if key in normalized:
                raise ValueError("duplicate entitlement record")
            normalized[key] = (
                adapter_id, capability_id, BillingModel.FREEMIUM, plan_name, available, quota,
                estimated, actual, observed, expires, provenance, premium_access,
            )
        self._records = MappingProxyType(normalized)

    @classmethod
    def from_server_records(cls, records: tuple[dict[str, object], ...]) -> "FreemiumEntitlementResolver":
        return cls(records, _token=_SERVER_RESOLVER_TOKEN)

    @classmethod
    def from_test_records(cls, records: tuple[dict[str, object], ...]) -> "FreemiumEntitlementResolver":
        return cls(records, _token=_TEST_RESOLVER_TOKEN)

    def resolve(
        self, adapter_id: str, capability_id: str, billing_model: BillingModel, *, now: datetime
    ) -> tuple[str | None, _FreemiumEntitlementSnapshot | None]:
        if type(adapter_id) is not str or type(capability_id) is not str or type(billing_model) is not BillingModel:
            return "entitlement_invalid", None
        record = self._records.get((adapter_id, capability_id))
        if record is None or billing_model is not BillingModel.FREEMIUM:
            return "entitlement_unavailable", None
        try:
            checked_now = _aware_utc(now, "now")
        except ValueError:
            return "entitlement_invalid", None
        observed, expires = record[8], record[9]
        if checked_now < observed or checked_now >= expires:
            return "entitlement_stale", None
        if record[4] is not True:
            return "plan_unavailable", None
        if record[5] == 0:
            return "quota_exhausted", None
        if record[6] is None:
            return "cost_unknown", None
        snapshot = _FreemiumEntitlementSnapshot(*record, self._issuer)
        return None, snapshot

    def validate_snapshot(
        self, snapshot: object, adapter_id: str, capability_id: str, billing_model: BillingModel, *, now: datetime
    ) -> bool:
        if type(snapshot) is not _FreemiumEntitlementSnapshot or snapshot._issuer is not self._issuer:
            return False
        reason, fresh = self.resolve(adapter_id, capability_id, billing_model, now=now)
        return reason is None and fresh is not None and snapshot == fresh


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
}


class ProviderRegistry:
    """Resolve free-provider implementations only when an adapter is requested.

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
        entitlement_resolver: FreemiumEntitlementResolver | None = None,
    ) -> None:
        self._catalog = catalog or build_catalog({"sources": []})
        catalog_adapter_ids = {adapter.adapter_id for adapter in self._catalog.adapters}
        unknown = set(_FACTORIES) - catalog_adapter_ids
        if unknown:
            raise ValueError(f"Provider registry IDs missing from Catalog: {', '.join(sorted(unknown))}")
        self._http_factory = http_factory
        self._credential_factory = credential_factory
        self._budget_guard_factory = budget_guard_factory
        if entitlement_resolver is not None and type(entitlement_resolver) is not FreemiumEntitlementResolver:
            raise TypeError("entitlement_resolver must be registry validated")
        self._entitlement_resolver = entitlement_resolver
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
                    kwargs["credentials"] = self._credential_factory(scope)
                    if self._budget_guard_factory is not None:
                        kwargs["budget_guard"] = self._budget_guard_factory(normalized)
                    if normalized in _ENTITLEMENT_CAPABILITIES and self._entitlement_resolver is not None:
                        kwargs["entitlement_resolver"] = self._entitlement_resolver
                adapter = adapter_type(**kwargs)
                descriptor = getattr(adapter, "descriptor", None)
                if getattr(descriptor, "adapter_id", None) != normalized:
                    raise ValueError(f"Provider implementation does not match Catalog adapter: {normalized}")
                self._instances[normalized] = adapter
            return self._instances[normalized]
