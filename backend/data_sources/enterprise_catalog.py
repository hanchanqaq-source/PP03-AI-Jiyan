"""Static enterprise-provider descriptions without SDK, credential, or Live access."""

from __future__ import annotations

from dataclasses import replace

from .models import AdapterDescriptor, BillingModel, CatalogStatus, SourceFamily, SourceRole


_ENTERPRISE_ROWS: tuple[
    tuple[str, str, str, str, tuple[SourceRole, ...], tuple[str, ...]], ...
] = (
    (
        "bloomberg",
        "Bloomberg Data License/B-PIPE",
        "global",
        "https://www.bloomberg.com/professional/product/data-license/",
        (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA, SourceRole.MACRO_DATA),
        ("stock_snapshot", "stock_history", "stock_financials", "macro_series", "news_discovery"),
    ),
    (
        "lseg",
        "LSEG Data Platform/Workspace",
        "global",
        "https://www.lseg.com/en/data-analytics/products/lseg-data-platform",
        (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA, SourceRole.MACRO_DATA),
        ("stock_snapshot", "stock_history", "stock_financials", "macro_series", "news_discovery"),
    ),
    (
        "factset",
        "FactSet",
        "global",
        "https://www.factset.com/",
        (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK),
        ("stock_snapshot", "stock_history", "stock_financials", "fund_holdings"),
    ),
    (
        "wind",
        "Wind",
        "CN",
        "https://www.wind.com.cn/",
        (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA, SourceRole.MACRO_DATA),
        ("stock_snapshot", "stock_history", "stock_financials", "fund_holdings", "macro_series"),
    ),
    (
        "choice",
        "Choice",
        "CN",
        "https://choice.eastmoney.com/",
        (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("stock_snapshot", "stock_history", "stock_financials", "fund_holdings"),
    ),
    (
        "ifind",
        "iFinD",
        "CN",
        "https://www.10jqka.com.cn/",
        (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("stock_snapshot", "stock_history", "stock_financials", "fund_holdings"),
    ),
    (
        "morningstar-direct",
        "Morningstar Direct",
        "global",
        "https://www.morningstar.com/products/direct",
        (SourceRole.PRIMARY_DATA, SourceRole.CROSS_CHECK),
        ("profile", "nav_history", "holdings", "fund_holdings"),
    ),
    (
        "sp-capital-iq",
        "S&P Capital IQ",
        "global",
        "https://www.spglobal.com/marketintelligence/en/solutions/sp-capital-iq-pro",
        (SourceRole.PRIMARY_DATA, SourceRole.MARKET_DATA, SourceRole.CROSS_CHECK),
        ("stock_snapshot", "stock_history", "stock_financials", "macro_series"),
    ),
    (
        "csmar",
        "CSMAR",
        "CN",
        "https://data.csmar.com/",
        (SourceRole.PRIMARY_DATA, SourceRole.CROSS_CHECK),
        ("stock_history", "stock_financials", "fund_holdings", "macro_series"),
    ),
)


def enterprise_families() -> tuple[SourceFamily, ...]:
    return tuple(
        SourceFamily(
            source_family_id=family_id,
            source_family_name=name,
            region=region,
            market=region if region == "CN" else "global",
            source_roles=roles,
            independent_evidence_eligible=True,
            commercial_use_status="enterprise_contract_and_license_required",
            catalog_status=CatalogStatus.LICENSE_REQUIRED,
            health_status=None,
        )
        for family_id, name, region, _reference, roles, _capabilities in _ENTERPRISE_ROWS
    )


def enterprise_adapters() -> tuple[AdapterDescriptor, ...]:
    return tuple(
        AdapterDescriptor(
            adapter_id=family_id,
            adapter_name=name,
            source_family_id=family_id,
            provider_type="enterprise_catalog_shell",
            source_roles=roles,
            capability_ids=capabilities,
            billing_model=BillingModel.ENTERPRISE_LICENSE,
            auth_type="enterprise_license",
            credential_env_names=(),
            default_enabled=False,
            license_note="需要用户自有企业合同、许可证、获批 SDK 与单独测试授权；本 Work 未接通。",
            usage_note="静态目录边界；不安装 SDK、不读取凭据、不发请求、不推断许可证状态。",
            data_delay="取决于实际企业合同和授权产品；当前未知",
            quota_policy="取决于实际企业合同；当前未配置",
            cost_policy="企业合同费用未知；不自动购买、续费或消费",
            configured_reference=reference,
            current_provider_priority=10,
            catalog_status=CatalogStatus.LICENSE_REQUIRED,
        )
        for family_id, name, _region, reference, roles, capabilities in _ENTERPRISE_ROWS
    )


def build_enterprise_catalog():
    # Local import avoids making the main Catalog depend on a second model.
    from .catalog import DataSourceCatalog, _capabilities

    used = {capability for adapter in enterprise_adapters() for capability in adapter.capability_ids}
    capabilities = tuple(
        replace(row, primary_families=(), fallback_families=(), cross_check_families=())
        for row in _capabilities()
        if row.capability_id in used
    )

    return DataSourceCatalog(
        families=enterprise_families(),
        adapters=enterprise_adapters(),
        capabilities=capabilities,
    )


__all__ = ["build_enterprise_catalog", "enterprise_adapters", "enterprise_families"]
