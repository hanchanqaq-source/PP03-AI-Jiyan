from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from source_health.registry import load_news_config, public_source_reference

from .models import (
    AdapterDescriptor,
    BillingModel,
    CapabilityDescriptor,
    CatalogStatus,
    SourceFamily,
    SourceRole,
)


_Row = TypeVar("_Row")
_DAY_SECONDS = 24 * 60 * 60


class DataSourceCatalog:
    def __init__(
        self,
        *,
        families: Iterable[SourceFamily],
        adapters: Iterable[AdapterDescriptor],
        capabilities: Iterable[CapabilityDescriptor],
    ) -> None:
        self._families = tuple(sorted(families, key=lambda row: row.source_family_id))
        self._adapters = tuple(sorted(adapters, key=lambda row: row.adapter_id))
        self._capabilities = tuple(sorted(capabilities, key=lambda row: row.capability_id))
        self._families_by_id = self._index_unique(self._families, "source_family_id", "family")
        self._adapters_by_id = self._index_unique(self._adapters, "adapter_id", "adapter")
        self._capabilities_by_id = self._index_unique(self._capabilities, "capability_id", "capability")
        self._validate_references()
        self._registration_payload = self._serialize_registration()

    @staticmethod
    def _index_unique(rows: Iterable[_Row], attribute: str, label: str) -> dict[str, _Row]:
        indexed: dict[str, _Row] = {}
        for row in rows:
            identifier = getattr(row, attribute)
            if identifier in indexed:
                raise ValueError(f"Duplicate {label} ID: {identifier}")
            indexed[identifier] = row
        return indexed

    def _validate_references(self) -> None:
        family_ids = set(self._families_by_id)
        capability_ids = set(self._capabilities_by_id)
        for adapter in self._adapters:
            if adapter.source_family_id not in family_ids:
                raise ValueError(
                    f"Adapter {adapter.adapter_id} references unknown family {adapter.source_family_id}"
                )
            unknown_capabilities = set(adapter.capability_ids) - capability_ids
            if unknown_capabilities:
                raise ValueError(
                    f"Adapter {adapter.adapter_id} references unknown capabilities: "
                    f"{', '.join(sorted(unknown_capabilities))}"
                )
        for capability in self._capabilities:
            referenced_families = (
                set(capability.primary_families)
                | set(capability.fallback_families)
                | set(capability.cross_check_families)
            )
            unknown_families = referenced_families - family_ids
            if unknown_families:
                raise ValueError(
                    f"Capability {capability.capability_id} references unknown families: "
                    f"{', '.join(sorted(unknown_families))}"
                )

    def _serialize_registration(self) -> str:
        family_rows = []
        for family in self._families:
            row = asdict(family)
            row.pop("health_status")
            family_rows.append(row)
        payload = {
            "families": family_rows,
            "adapters": [asdict(row) for row in self._adapters],
            "capabilities": [asdict(row) for row in self._capabilities],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def families(self) -> tuple[SourceFamily, ...]:
        return self._families

    @property
    def adapters(self) -> tuple[AdapterDescriptor, ...]:
        return self._adapters

    @property
    def capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return self._capabilities

    def family(self, family_id: str) -> SourceFamily:
        return self._families_by_id[family_id]

    def adapter(self, adapter_id: str) -> AdapterDescriptor:
        return self._adapters_by_id[adapter_id]

    def adapters_for_family(self, family_id: str) -> tuple[AdapterDescriptor, ...]:
        self.family(family_id)
        return tuple(row for row in self._adapters if row.source_family_id == family_id)

    def registration_fingerprint(self, holding_ids: Sequence[str] = ()) -> str:
        del holding_ids
        return hashlib.sha256(self._registration_payload.encode("utf-8")).hexdigest()


def _capabilities() -> tuple[CapabilityDescriptor, ...]:
    return (
        CapabilityDescriptor("feed", "资讯订阅", "news", None, True, "publisher_native", "publisher_native", (), (), ()),
        CapabilityDescriptor("search", "基金搜索", "fund", _DAY_SECONDS, True, "provider_native", "daily", ("eastmoney",), ("danjuan",), ()),
        CapabilityDescriptor("profile", "基金档案", "fund", _DAY_SECONDS, True, "provider_native", "daily", ("eastmoney",), ("danjuan",), ()),
        CapabilityDescriptor("nav_history", "基金净值历史", "fund", 7 * _DAY_SECONDS, True, "fund_nav", "daily", ("eastmoney",), ("danjuan",), ()),
        CapabilityDescriptor("holdings", "公开持仓", "fund", 200 * _DAY_SECONDS, True, "weight_pct", "quarterly", ("eastmoney",), ("danjuan",), ()),
        CapabilityDescriptor("industry_allocation", "官方行业配置", "industry", 200 * _DAY_SECONDS, True, "weight_pct", "quarterly", ("eastmoney",), (), ()),
        CapabilityDescriptor("stock_snapshot", "股票行情", "quote", 3 * _DAY_SECONDS, True, "market_quote", "intraday", ("tencent",), ("eastmoney",), ()),
        CapabilityDescriptor("stock_industry_classification", "股票行业分类", "industry", 400 * _DAY_SECONDS, True, "classification", "event_driven", ("cninfo",), (), ()),
    )


def _families() -> tuple[SourceFamily, ...]:
    return (
        SourceFamily("eastmoney", "东方财富数据家族", "CN", "CN", (SourceRole.PRIMARY_DATA, SourceRole.FALLBACK_DATA, SourceRole.MARKET_DATA), False, "public_upstream_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("tencent", "腾讯行情", "CN", "CN", (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA), True, "public_upstream_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("cninfo", "巨潮资讯", "CN", "CN", (SourceRole.OFFICIAL_EVIDENCE, SourceRole.PRIMARY_DATA), True, "public_upstream_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("danjuan", "蛋卷基金", "CN", "CN", (SourceRole.FALLBACK_DATA,), True, "public_upstream_terms_apply", CatalogStatus.CONFIGURED),
    )


def _adapter(
    adapter_id: str,
    adapter_name: str,
    source_family_id: str,
    provider_type: str,
    source_roles: tuple[SourceRole, ...],
    capability_ids: tuple[str, ...],
    configured_reference: str,
    priority: int,
    *,
    default_enabled: bool = True,
    catalog_status: CatalogStatus = CatalogStatus.CONFIGURED,
    license_note: str = "公开入口；使用须遵守上游条款。",
    usage_note: str = "仅访问公开可用数据；不使用凭据。",
) -> AdapterDescriptor:
    return AdapterDescriptor(
        adapter_id, adapter_name, source_family_id, provider_type, source_roles, capability_ids,
        BillingModel.FREE_NO_KEY, "none", (), default_enabled, license_note, usage_note,
        "以来源实际披露和响应为准", "未声明；按公开入口合理限速", "免费无需密钥；不自动购买或升级",
        configured_reference, priority, catalog_status,
    )


def _static_adapters() -> tuple[AdapterDescriptor, ...]:
    return (
        _adapter("eastmoney-direct", "东方财富直连", "eastmoney", "http_client", (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA), ("search", "profile", "nav_history", "holdings", "industry_allocation", "stock_snapshot"), "https://fund.eastmoney.com/", 20),
        _adapter("akshare-eastmoney", "AKShare／东方财富", "eastmoney", "akshare", (SourceRole.FALLBACK_DATA,), ("search", "profile", "nav_history", "holdings", "industry_allocation"), "https://fund.eastmoney.com/", 40, usage_note="AKShare 是访问路径；上游东方财富不增加独立性。"),
        _adapter("efinance-eastmoney", "efinance／东方财富", "eastmoney", "efinance", (SourceRole.FALLBACK_DATA,), ("stock_snapshot",), "https://fund.eastmoney.com/", 90, default_enabled=False, catalog_status=CatalogStatus.DISABLED, license_note="efinance 是东方财富的非独立访问路径；启用前须核验其许可证和上游使用条款。", usage_note="默认关闭；不得将此路径计为独立证据来源。"),
        _adapter("tencent-quote", "腾讯行情", "tencent", "http_client", (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA), ("stock_snapshot",), "https://qt.gtimg.cn/", 10),
        _adapter("cninfo-industry", "巨潮资讯行业分类", "cninfo", "http_client", (SourceRole.OFFICIAL_EVIDENCE, SourceRole.PRIMARY_DATA), ("stock_industry_classification",), "https://webapi.cninfo.com.cn/", 10),
        _adapter("akshare-danjuan", "AKShare／蛋卷基金", "danjuan", "akshare", (SourceRole.FALLBACK_DATA,), ("search", "profile", "nav_history", "holdings"), "https://danjuanfunds.com/", 50, usage_note="AKShare 是访问路径；仅访问公开蛋卷基金数据。"),
    )


def _news_identity(source: Mapping[str, Any]) -> str:
    fields = tuple(str(source.get(key) or "").strip() for key in ("hint", "name", "url", "language", "region"))
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _news_records(news_config: Mapping[str, Any]) -> tuple[tuple[SourceFamily, ...], tuple[AdapterDescriptor, ...]]:
    families: list[SourceFamily] = []
    adapters: list[AdapterDescriptor] = []
    for source in news_config.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        hint, name, url = (str(source.get(key) or "").strip() for key in ("hint", "name", "url"))
        if not hint or not name or not url:
            continue
        identity = _news_identity(source)
        family_id, adapter_id = f"news-publisher:{identity[:16]}", f"news-feed:{identity[:16]}"
        region = str(source.get("region") or "global").strip() or "global"
        families.append(SourceFamily(family_id, name, region, "news", (SourceRole.NEWS_PUBLISHER,), True, "publisher_terms_apply", CatalogStatus.CATALOG_ONLY))
        adapters.append(_adapter(adapter_id, f"{name} feed", family_id, str(source.get("type") or "rss_atom").strip() or "rss_atom", (SourceRole.NEWS_PUBLISHER,), ("feed",), public_source_reference(url), 0, catalog_status=CatalogStatus.CATALOG_ONLY, license_note="发布者公开订阅入口；使用须遵守发布者条款。", usage_note="仅登记配置的公开 RSS/Atom 来源；健康观测另行处理。"))
    return tuple(families), tuple(adapters)


def build_catalog(news_config: Mapping[str, Any] | None = None) -> DataSourceCatalog:
    configured_news = news_config if news_config is not None else load_news_config()
    news_families, news_adapters = _news_records(configured_news)
    return DataSourceCatalog(
        families=(*_families(), *news_families),
        adapters=(*_static_adapters(), *news_adapters),
        capabilities=_capabilities(),
    )
