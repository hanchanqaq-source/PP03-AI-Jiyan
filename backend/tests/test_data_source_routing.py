from __future__ import annotations

import pytest

from data_sources.catalog import build_catalog
from data_sources.routing import CapabilityRouter


def test_router_preserves_catalog_primary_fallback_and_no_same_origin_cross_check():
    route = CapabilityRouter(build_catalog({"sources": []})).route("stock_snapshot")

    assert route.primary_adapter_ids == ("tencent-quote",)
    assert route.fallback_adapter_ids == ("eastmoney-direct",)
    assert route.cross_check_adapter_ids == ()
    assert "akshare-eastmoney" not in route.cross_check_adapter_ids


def test_router_keeps_baostock_independent_of_eastmoney_and_tencent():
    route = CapabilityRouter(build_catalog({"sources": []})).route("stock_history")

    assert route.primary_adapter_ids == ("baostock",)
    assert route.independent_family_ids == ("baostock",)
    assert not {"eastmoney", "tencent"} & set(route.independent_family_ids)


def test_router_prioritizes_sec_official_capabilities_and_keeps_gdelt_candidate_only():
    router = CapabilityRouter(build_catalog({"sources": []}))

    assert router.route("sec_company_submissions").primary_adapter_ids == ("sec-edgar",)
    discovery = router.route("news_discovery")
    assert discovery.primary_adapter_ids == ()
    assert discovery.fallback_adapter_ids == ()
    assert discovery.cross_check_adapter_ids == ()
    assert discovery.candidate_adapter_ids == ("gdelt",)
    assert discovery.evidence_adapter_ids == ()


def test_router_allows_yahoo_only_for_personal_research_and_never_as_evidence():
    router = CapabilityRouter(build_catalog({"sources": []}))

    assert router.route("overseas_stock_history").fallback_adapter_ids == ()
    research = router.route("overseas_stock_history", personal_research=True)
    assert research.fallback_adapter_ids == ("yahoo-finance",)
    assert research.evidence_adapter_ids == ()


def test_router_rejects_unknown_capability():
    with pytest.raises(KeyError, match="unknown capability"):
        CapabilityRouter(build_catalog({"sources": []})).route("not-a-capability")
