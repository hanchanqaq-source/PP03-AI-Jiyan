from __future__ import annotations

import pytest

from data_sources.catalog import build_catalog
from data_sources.models import (
    AdapterDescriptor,
    BillingModel,
    CapabilityDescriptor,
    CatalogStatus,
    SourceFamily,
    SourceRole,
)
from data_sources.routing import CapabilityRouter
from data_sources.catalog import DataSourceCatalog


def _router(catalog):
    return CapabilityRouter(
        catalog,
        configuration={"free_only": True, "adapters": {}},
    )


def test_router_preserves_catalog_primary_fallback_and_no_same_origin_cross_check():
    route = _router(build_catalog({"sources": []})).route("stock_snapshot")

    assert route.primary_adapter_ids == ("tencent-quote",)
    assert route.fallback_adapter_ids == ("eastmoney-direct",)
    assert route.cross_check_adapter_ids == ()
    assert "akshare-eastmoney" not in route.cross_check_adapter_ids


def test_router_keeps_baostock_independent_of_eastmoney_and_tencent():
    route = _router(build_catalog({"sources": []})).route("stock_history")

    assert route.primary_adapter_ids == ("baostock",)
    assert route.independent_family_ids == ("baostock",)
    assert not {"eastmoney", "tencent"} & set(route.independent_family_ids)


def test_router_prioritizes_sec_official_capabilities_and_keeps_gdelt_candidate_only():
    router = _router(build_catalog({"sources": []}))

    assert router.route("sec_company_submissions").primary_adapter_ids == ("sec-edgar",)
    discovery = router.route("news_discovery")
    assert discovery.primary_adapter_ids == ()
    assert discovery.fallback_adapter_ids == ()
    assert discovery.cross_check_adapter_ids == ()
    assert discovery.candidate_adapter_ids == ("gdelt",)
    assert discovery.evidence_adapter_ids == ()


def test_router_keeps_catalog_disabled_yahoo_out_even_for_personal_research():
    router = _router(build_catalog({"sources": []}))

    assert router.route("overseas_stock_history").fallback_adapter_ids == ()
    research = router.route("overseas_stock_history", personal_research=True)
    assert research.fallback_adapter_ids == ()
    assert research.evidence_adapter_ids == ()


def test_router_rejects_unknown_capability():
    with pytest.raises(KeyError, match="unknown capability"):
        _router(build_catalog({"sources": []})).route("not-a-capability")


def test_router_preserves_requested_family_order_and_catalog_order_within_each_family():
    capability_id = "ordered_capability"
    family = lambda family_id: SourceFamily(
        family_id, family_id, "test", "test", (SourceRole.PRIMARY_DATA,), True,
        "test", CatalogStatus.CONFIGURED,
    )
    adapter = lambda adapter_id, family_id: AdapterDescriptor(
        adapter_id, adapter_id, family_id, "test", (SourceRole.PRIMARY_DATA,), (capability_id,),
        BillingModel.FREE_NO_KEY, "none", (), True, "test", "test", "test", "test", "test",
        "https://public.example.test/", 1, CatalogStatus.CONFIGURED,
    )
    catalog = DataSourceCatalog(
        families=(family("first"), family("second")),
        adapters=(
            adapter("first-z", "first"), adapter("second-z", "second"),
            adapter("first-a", "first"), adapter("second-a", "second"),
        ),
        capabilities=(CapabilityDescriptor(
            capability_id, "ordered", "test", None, True, "test", "test", ("second", "first"), (), (),
        ),),
    )

    route = _router(catalog).route(capability_id)

    assert route.primary_adapter_ids == ("second-a", "second-z", "first-a", "first-z")
