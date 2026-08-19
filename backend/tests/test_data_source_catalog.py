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
