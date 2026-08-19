from data_sources.catalog import build_catalog
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
