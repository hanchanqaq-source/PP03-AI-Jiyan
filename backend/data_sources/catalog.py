from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from source_health.registry import load_news_config

from .references import public_source_reference

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

    def capability(self, capability_id: str) -> CapabilityDescriptor:
        return self._capabilities_by_id[capability_id]

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
        CapabilityDescriptor("stock_history", "股票历史行情", "quote", 3 * _DAY_SECONDS, True, "CNY", "daily", ("baostock",), (), ()),
        CapabilityDescriptor("stock_history_adjusted", "股票复权因子", "quote", 400 * _DAY_SECONDS, True, "factor", "event_driven", ("baostock",), (), ()),
        CapabilityDescriptor("stock_financials", "股票财务指标", "financial", 200 * _DAY_SECONDS, True, "ratio", "quarterly", ("baostock",), (), ()),
        CapabilityDescriptor("stock_industry_reference", "BaoStock 行业参考", "industry", 400 * _DAY_SECONDS, True, "classification", "event_driven", ("baostock",), (), ()),
        CapabilityDescriptor("index_calendar", "交易日历", "market", 7 * _DAY_SECONDS, True, "boolean", "daily", ("baostock",), (), ()),
        CapabilityDescriptor("stock_valuation", "股票估值指标", "quote", 3 * _DAY_SECONDS, True, "ratio", "daily", ("baostock",), (), ()),
        CapabilityDescriptor("overseas_stock_history", "海外股票历史行情参考", "quote", 3 * _DAY_SECONDS, False, "upstream_currency", "daily", (), ("yahoo_finance",), ()),
        CapabilityDescriptor("overseas_etf_history", "海外 ETF 历史行情参考", "quote", 3 * _DAY_SECONDS, False, "upstream_currency", "daily", (), ("yahoo_finance",), ()),
        CapabilityDescriptor("overseas_index_history", "海外指数历史行情参考", "quote", 3 * _DAY_SECONDS, False, "upstream_currency", "daily", (), ("yahoo_finance",), ()),
        CapabilityDescriptor("overseas_profile_reference", "海外证券档案参考", "reference", _DAY_SECONDS, False, "upstream_currency", "event_driven", (), ("yahoo_finance",), ()),
        CapabilityDescriptor("sec_company_submissions", "SEC 公司申报目录", "official_disclosure", _DAY_SECONDS, True, "metadata", "event_driven", ("sec_edgar",), (), ()),
        CapabilityDescriptor("sec_filing_index_metadata", "SEC 申报索引元数据", "official_disclosure", _DAY_SECONDS, True, "metadata", "event_driven", ("sec_edgar",), (), ()),
        CapabilityDescriptor("sec_10k_metadata", "SEC 10-K 索引元数据", "official_disclosure", _DAY_SECONDS, True, "metadata", "annual", ("sec_edgar",), (), ()),
        CapabilityDescriptor("sec_10q_metadata", "SEC 10-Q 索引元数据", "official_disclosure", _DAY_SECONDS, True, "metadata", "quarterly", ("sec_edgar",), (), ()),
        CapabilityDescriptor("sec_8k_metadata", "SEC 8-K 索引元数据", "official_disclosure", _DAY_SECONDS, True, "metadata", "event_driven", ("sec_edgar",), (), ()),
        CapabilityDescriptor("sec_13f_metadata", "SEC 13F 索引元数据", "official_disclosure", _DAY_SECONDS, True, "metadata", "quarterly", ("sec_edgar",), (), ()),
        CapabilityDescriptor("sec_company_facts", "SEC Company Facts 元数据", "official_disclosure", _DAY_SECONDS, True, "metadata", "event_driven", ("sec_edgar",), (), ()),
        CapabilityDescriptor("official_evidence_link", "官方披露链接核验", "official_disclosure", None, True, "metadata", "event_driven", ("sse", "szse", "cninfo", "hkexnews", "csrc"), ("fund_company_official", "index_company_official"), ()),
        CapabilityDescriptor("macro_indicator", "宏观指标", "macro", 31 * _DAY_SECONDS, True, "provider_native", "provider_native", ("world_bank",), (), ()),
        CapabilityDescriptor("macro_series", "宏观时间序列", "macro", 31 * _DAY_SECONDS, True, "provider_native", "provider_native", ("oecd",), ("imf", "fred", "eia"), ()),
        CapabilityDescriptor("fund_holdings", "基金持仓明细", "fund", 200 * _DAY_SECONDS, True, "provider_native", "quarterly", (), ("tushare",), ()),
        CapabilityDescriptor("news_discovery", "资讯候选发现", "news", None, False, "candidate", "event_driven", (), ("gdelt",), ()),
    )


def _families() -> tuple[SourceFamily, ...]:
    return (
        SourceFamily("eastmoney", "东方财富数据家族", "CN", "CN", (SourceRole.PRIMARY_DATA, SourceRole.FALLBACK_DATA, SourceRole.MARKET_DATA), False, "public_upstream_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("tencent", "腾讯行情", "CN", "CN", (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA), True, "public_upstream_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("cninfo", "巨潮资讯", "CN", "CN", (SourceRole.OFFICIAL_EVIDENCE, SourceRole.PRIMARY_DATA), True, "public_upstream_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("danjuan", "蛋卷基金", "CN", "CN", (SourceRole.FALLBACK_DATA,), True, "public_upstream_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("baostock", "BaoStock", "CN", "CN", (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK), True, "public_upstream_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("yahoo_finance", "Yahoo Finance／yfinance", "global", "overseas", (SourceRole.FALLBACK_DATA,), False, "non_official_reference_terms_apply", CatalogStatus.DISABLED),
        SourceFamily("sec_edgar", "SEC EDGAR", "US", "US", (SourceRole.OFFICIAL_EVIDENCE, SourceRole.PRIMARY_DATA), True, "sec_public_data_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("sse", "上海证券交易所披露", "CN", "CN", (SourceRole.OFFICIAL_EVIDENCE,), True, "exchange_public_disclosure_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("szse", "深圳证券交易所披露", "CN", "CN", (SourceRole.OFFICIAL_EVIDENCE,), True, "exchange_public_disclosure_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("hkexnews", "香港交易所披露易", "HK", "HK", (SourceRole.OFFICIAL_EVIDENCE,), True, "exchange_public_disclosure_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("csrc", "中国证监会", "CN", "CN", (SourceRole.OFFICIAL_EVIDENCE,), True, "government_public_disclosure_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("fund_company_official", "基金公司官方公告", "CN", "CN", (SourceRole.OFFICIAL_EVIDENCE,), True, "official_company_host_review_required", CatalogStatus.UNCONFIGURED),
        SourceFamily("index_company_official", "指数公司官方公告", "CN", "CN", (SourceRole.OFFICIAL_EVIDENCE,), True, "official_company_host_review_required", CatalogStatus.UNCONFIGURED),
        SourceFamily("world_bank", "World Bank Indicators", "global", "global", (SourceRole.MACRO_DATA,), True, "world_bank_public_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("oecd", "OECD SDMX", "global", "global", (SourceRole.MACRO_DATA,), True, "oecd_public_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("imf", "IMF public SDMX", "global", "global", (SourceRole.MACRO_DATA,), True, "imf_public_sdmx_live_status_unverified", CatalogStatus.CATALOG_ONLY),
        SourceFamily("gdelt", "GDELT DOC 2.0", "global", "news", (SourceRole.COLLECTOR, SourceRole.CANDIDATE), False, "gdelt_public_terms_apply", CatalogStatus.CONFIGURED),
        SourceFamily("fred", "FRED", "US", "US", (SourceRole.MACRO_DATA, SourceRole.CROSS_CHECK), True, "fred_public_api_terms_apply", CatalogStatus.UNCONFIGURED),
        SourceFamily("eia", "U.S. EIA", "US", "US", (SourceRole.MACRO_DATA, SourceRole.CROSS_CHECK), True, "eia_public_api_terms_apply", CatalogStatus.UNCONFIGURED),
        SourceFamily("tushare", "Tushare Pro", "CN", "CN", (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK), True, "tushare_account_terms_apply", CatalogStatus.UNCONFIGURED),
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
    billing_model: BillingModel = BillingModel.FREE_NO_KEY,
    auth_type: str = "none",
    credential_env_names: tuple[str, ...] = (),
    data_delay: str = "以来源实际披露和响应为准",
    quota_policy: str = "未声明；按公开入口合理限速",
    cost_policy: str = "免费无需密钥；不自动购买或升级",
) -> AdapterDescriptor:
    return AdapterDescriptor(
        adapter_id, adapter_name, source_family_id, provider_type, source_roles, capability_ids,
        billing_model, auth_type, credential_env_names, default_enabled, license_note, usage_note,
        data_delay, quota_policy, cost_policy,
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
        _adapter("baostock", "BaoStock", "baostock", "baostock", (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK), ("stock_history", "stock_history_adjusted", "stock_financials", "stock_industry_reference", "index_calendar", "stock_valuation"), "https://www.baostock.com/", 30, usage_note="独立于东方财富和腾讯；仅限公开数据，不能替代巨潮资讯行业证据或基金正式净值。"),
        _adapter("yahoo-finance", "Yahoo Finance／yfinance", "yahoo_finance", "yfinance", (SourceRole.FALLBACK_DATA,), ("overseas_stock_history", "overseas_etf_history", "overseas_index_history", "overseas_profile_reference"), "https://ranaroussi.github.io/yfinance/", 200, default_enabled=False, catalog_status=CatalogStatus.DISABLED, license_note="非官方参考路径；使用须遵守 yfinance 与 Yahoo Finance 条款。", usage_note="默认关闭；只限 personal_research，不得提升为官方证据或独立证据。"),
        _adapter("sec-edgar", "SEC EDGAR", "sec_edgar", "http_client", (SourceRole.OFFICIAL_EVIDENCE, SourceRole.PRIMARY_DATA), ("sec_company_submissions", "sec_filing_index_metadata", "sec_10k_metadata", "sec_10q_metadata", "sec_8k_metadata", "sec_13f_metadata", "sec_company_facts"), "https://data.sec.gov/", 10, license_note="SEC 公开 EDGAR 数据；使用须遵守 SEC 条款和公平访问策略。", usage_note="只请求明确 CIK、表单和索引元数据；不抓取 filing body；不使用凭据。"),
        _adapter("sse-official-evidence", "上交所官方披露链接", "sse", "official_evidence_link", (SourceRole.OFFICIAL_EVIDENCE,), ("official_evidence_link",), "https://www.sse.com.cn/", 10, license_note="上交所公开披露入口；使用须遵守发布者条款。", usage_note="仅核验允许的公开链接；拒绝跨发布者重定向和受保护页面。"),
        _adapter("szse-official-evidence", "深交所官方披露链接", "szse", "official_evidence_link", (SourceRole.OFFICIAL_EVIDENCE,), ("official_evidence_link",), "https://www.szse.cn/", 10, license_note="深交所公开披露入口；使用须遵守发布者条款。", usage_note="仅核验允许的公开链接；拒绝跨发布者重定向和受保护页面。"),
        _adapter("cninfo-official-evidence", "巨潮资讯官方披露链接", "cninfo", "official_evidence_link", (SourceRole.OFFICIAL_EVIDENCE,), ("official_evidence_link",), "https://www.cninfo.com.cn/", 10, license_note="巨潮资讯公开披露入口；使用须遵守发布者条款。", usage_note="仅核验允许的公开链接；拒绝跨发布者重定向和受保护页面。"),
        _adapter("hkexnews-official-evidence", "港交所披露易官方链接", "hkexnews", "official_evidence_link", (SourceRole.OFFICIAL_EVIDENCE,), ("official_evidence_link",), "https://www.hkexnews.hk/", 10, license_note="港交所披露易公开入口；使用须遵守发布者条款。", usage_note="仅核验允许的公开链接；拒绝跨发布者重定向和受保护页面。"),
        _adapter("csrc-official-evidence", "中国证监会官方链接", "csrc", "official_evidence_link", (SourceRole.OFFICIAL_EVIDENCE,), ("official_evidence_link",), "https://www.csrc.gov.cn/", 10, license_note="中国证监会公开披露入口；使用须遵守发布者条款。", usage_note="仅核验允许的公开链接；拒绝跨发布者重定向和受保护页面。"),
        _adapter("fund-company-official-evidence", "基金公司官方公告链接", "fund_company_official", "official_evidence_link", (SourceRole.OFFICIAL_EVIDENCE,), ("official_evidence_link",), "", 30, default_enabled=False, catalog_status=CatalogStatus.UNCONFIGURED, license_note="必须先逐项登记官方基金公司主机并核验公开条款。", usage_note="未登记官方主机时不得请求；不使用登录、Cookie 或 CAPTCHA 绕过。"),
        _adapter("index-company-official-evidence", "指数公司官方公告链接", "index_company_official", "official_evidence_link", (SourceRole.OFFICIAL_EVIDENCE,), ("official_evidence_link",), "", 30, default_enabled=False, catalog_status=CatalogStatus.UNCONFIGURED, license_note="必须先逐项登记官方指数公司主机并核验公开条款。", usage_note="未登记官方主机时不得请求；不使用登录、Cookie 或 CAPTCHA 绕过。"),
        _adapter("world-bank", "World Bank Indicators", "world_bank", "http_client", (SourceRole.MACRO_DATA,), ("macro_indicator",), "https://api.worldbank.org/", 30, license_note="World Bank Indicators API 公开数据；使用须遵守上游条款。", usage_note="仅请求明确国家和指标代码；不使用凭据。"),
        _adapter("oecd", "OECD SDMX", "oecd", "http_client", (SourceRole.MACRO_DATA,), ("macro_series",), "https://sdmx.oecd.org/public/", 30, license_note="OECD SDMX 公开数据；使用须遵守上游条款。", usage_note="仅请求明确 dataset 与 series key；不使用凭据。"),
        _adapter("imf", "IMF public SDMX", "imf", "http_client", (SourceRole.MACRO_DATA,), ("macro_series",), "https://sdmxcentral.imf.org/ws/public/sdmxapi/rest/", 40, default_enabled=False, catalog_status=CatalogStatus.CATALOG_ONLY, license_note="IMF public SDMX contract is registered; current live accessibility is not asserted.", usage_note="Catalog-only pending a bounded official live validation; no credentials or fallback scraping."),
        _adapter("gdelt", "GDELT DOC 2.0", "gdelt", "http_client", (SourceRole.COLLECTOR, SourceRole.CANDIDATE), ("news_discovery",), "https://api.gdeltproject.org/api/v2/doc/doc", 100, license_note="GDELT public discovery API；使用须遵守上游条款。", usage_note="仅返回候选及原发布者标识；不得作为可信或独立证据。"),
        _adapter("fred", "FRED", "fred", "http_client", (SourceRole.MACRO_DATA, SourceRole.CROSS_CHECK), ("macro_series",), "https://api.stlouisfed.org/fred/", 20, default_enabled=False, catalog_status=CatalogStatus.UNCONFIGURED, license_note="FRED API key required; public data terms apply.", usage_note="Free-key macro series adapter; no paid request or automatic upgrade.", billing_model=BillingModel.FREE_KEY, auth_type="api_key", credential_env_names=("FRED_API_KEY",), data_delay="以 FRED 发布和修订为准", quota_policy="以账户实际配额为准", cost_policy="免费密钥；本适配器不预留或消费付费预算"),
        _adapter("eia", "U.S. EIA", "eia", "http_client", (SourceRole.MACRO_DATA, SourceRole.CROSS_CHECK), ("macro_series",), "https://api.eia.gov/v2/", 20, default_enabled=False, catalog_status=CatalogStatus.UNCONFIGURED, license_note="EIA API key required; public data terms apply.", usage_note="Free-key energy series adapter; no paid request or automatic upgrade.", billing_model=BillingModel.FREE_KEY, auth_type="api_key", credential_env_names=("EIA_API_KEY",), data_delay="以 EIA 发布和修订为准", quota_policy="以账户实际配额为准", cost_policy="免费密钥；本适配器不预留或消费付费预算"),
        _adapter("tushare", "Tushare Pro", "tushare", "safe_post_client_required", (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK), ("fund_holdings", "stock_history", "stock_financials", "index_calendar"), "https://api.tushare.pro/", 50, default_enabled=False, catalog_status=CatalogStatus.UNCONFIGURED, license_note="Tushare Pro token and capability-specific account permission required.", usage_note="Configured capability access depends on actual points/plan; denials are not provider-health failures.", billing_model=BillingModel.FREEMIUM, auth_type="api_token", credential_env_names=("TUSHARE_TOKEN",), data_delay="以 Tushare Pro 发布和修订为准", quota_policy="以账户实际积分和套餐权限为准", cost_policy="套餐/积分成本未知；无可信能力级权益时禁止请求"),
    )


def _news_identity(source: Mapping[str, Any]) -> str:
    fields = tuple(str(source.get(key) or "").strip() for key in ("hint", "name", "url", "language", "region"))
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _news_records(news_config: Mapping[str, Any]) -> tuple[tuple[SourceFamily, ...], tuple[AdapterDescriptor, ...]]:
    families: list[SourceFamily] = []
    adapters: list[AdapterDescriptor] = []
    registered_identities: set[str] = set()
    for source in news_config.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        hint, name, url = (str(source.get(key) or "").strip() for key in ("hint", "name", "url"))
        if not hint or not name or not url:
            continue
        identity = _news_identity(source)
        if identity in registered_identities:
            continue
        registered_identities.add(identity)
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
