from data_sources.catalog import build_catalog
from data_sources.models import SourceRole
from source_health.registry import load_news_config


def test_catalog_registers_108_news_adapters_without_holdings():
    catalog = build_catalog(news_config=load_news_config())

    assert len([row for row in catalog.adapters if "feed" in row.capability_ids]) == 108
    assert catalog.registration_fingerprint(holding_ids=[]) == catalog.registration_fingerprint(
        holding_ids=["017811"]
    )


def test_eastmoney_access_paths_share_one_family():
    catalog = build_catalog(news_config={"sources": []})
    family = catalog.family("eastmoney")

    assert {row.adapter_id for row in catalog.adapters_for_family(family.source_family_id)} == {
        "eastmoney-direct",
        "akshare-eastmoney",
        "efinance-eastmoney",
    }
    assert family.independent_evidence_eligible is False


def test_catalog_registration_has_unique_identity_and_is_holding_independent():
    catalog = build_catalog(news_config=load_news_config())
    family_ids = [family.source_family_id for family in catalog.families]
    adapter_ids = [adapter.adapter_id for adapter in catalog.adapters]
    eastmoney_families = [family for family in catalog.families if family.source_family_id == "eastmoney"]

    assert len(family_ids) == len(set(family_ids))
    assert len(adapter_ids) == len(set(adapter_ids))
    assert len([adapter for adapter in catalog.adapters if "feed" in adapter.capability_ids]) == 108
    assert len(eastmoney_families) == 1
    assert len(catalog.adapters_for_family("eastmoney")) == 3
    assert catalog.registration_fingerprint(holding_ids=[]) == catalog.registration_fingerprint(
        holding_ids=["017811"]
    )


def test_catalog_registers_baostock_as_independent_but_not_cninfo_or_nav_replacement():
    catalog = build_catalog(news_config={"sources": []})
    baostock = catalog.family("baostock")
    adapter = catalog.adapter("baostock")

    assert baostock.independent_evidence_eligible is True
    assert "stock_industry_classification" not in adapter.capability_ids
    assert "nav_history" not in adapter.capability_ids
    assert adapter.billing_model.value == "free_no_key"
    assert adapter.auth_type == "none"
    assert adapter.catalog_status.value == "configured"


def test_catalog_keeps_yahoo_finance_disabled_and_non_official_for_personal_research():
    catalog = build_catalog(news_config={"sources": []})
    yahoo = catalog.family("yahoo_finance")
    adapter = catalog.adapter("yahoo-finance")

    assert yahoo.independent_evidence_eligible is False
    assert adapter.default_enabled is False
    assert adapter.catalog_status.value == "disabled"
    assert adapter.source_roles == (SourceRole.FALLBACK_DATA,)
    assert SourceRole.OFFICIAL_EVIDENCE not in adapter.source_roles
    assert adapter.capability_ids == (
        "overseas_stock_history",
        "overseas_etf_history",
        "overseas_index_history",
        "overseas_profile_reference",
    )


def test_catalog_registers_official_evidence_adapters_without_claiming_a_fixture_connection():
    """Catches official adapters being omitted from Catalog or pre-labelled as connected."""
    catalog = build_catalog(news_config={"sources": []})

    assert catalog.family("sec_edgar").source_roles == (SourceRole.OFFICIAL_EVIDENCE, SourceRole.PRIMARY_DATA)
    assert catalog.adapter("sec-edgar").catalog_status.value == "configured"
    assert catalog.capability("sec_company_facts").primary_families == ("sec_edgar",)
    assert catalog.adapter("sse-official-evidence").source_roles == (SourceRole.OFFICIAL_EVIDENCE,)
    assert catalog.adapter("hkexnews-official-evidence").catalog_status.value == "configured"
    assert catalog.adapter("fund-company-official-evidence").catalog_status.value == "unconfigured"
    assert catalog.adapter("index-company-official-evidence").catalog_status.value == "unconfigured"
    assert all(family.catalog_status.value != "connected" for family in catalog.families)
