from __future__ import annotations

import pytest

from data_sources.catalog import build_catalog
from data_sources.enterprise_catalog import build_enterprise_catalog
from data_sources.models import BillingModel, CatalogStatus
from data_sources.provider_registry import ProviderRegistry


ENTERPRISE = {
    "bloomberg": "Bloomberg Data License/B-PIPE",
    "lseg": "LSEG Data Platform/Workspace",
    "factset": "FactSet",
    "wind": "Wind",
    "choice": "Choice",
    "ifind": "iFinD",
    "morningstar-direct": "Morningstar Direct",
    "sp-capital-iq": "S&P Capital IQ",
    "csmar": "CSMAR",
}


@pytest.mark.parametrize("family_id", tuple(ENTERPRISE))
def test_enterprise_source_requires_license_and_is_not_healthy(family_id: str):
    catalog = build_enterprise_catalog()
    row = catalog.family(family_id)
    adapter = catalog.adapters_for_family(family_id)[0]

    assert row.source_family_name == ENTERPRISE[family_id]
    assert row.catalog_status is CatalogStatus.LICENSE_REQUIRED
    assert row.health_status is None
    assert adapter.billing_model is BillingModel.ENTERPRISE_LICENSE
    assert adapter.catalog_status is CatalogStatus.LICENSE_REQUIRED
    assert adapter.default_enabled is False
    assert adapter.provider_type == "enterprise_catalog_shell"
    assert adapter.auth_type == "enterprise_license"
    assert adapter.credential_env_names == ()
    assert adapter.capability_ids
    assert adapter.configured_reference.startswith("https://")


def test_main_catalog_contains_exact_paid_and_enterprise_families_without_connected_claims():
    catalog = build_catalog({"sources": []})
    paid = {"fmp", "massive", "tiingo", "eodhd", "databento"}

    assert set(ENTERPRISE).issubset({row.source_family_id for row in catalog.families})
    assert paid.issubset({row.source_family_id for row in catalog.families})
    assert paid.issubset({row.adapter_id for row in catalog.adapters})
    for family_id in (*ENTERPRISE, *paid):
        assert catalog.family(family_id).health_status is None
        assert catalog.family(family_id).catalog_status is not CatalogStatus.CONNECTED
    for family_id in ENTERPRISE:
        assert catalog.adapter(family_id).catalog_status is CatalogStatus.LICENSE_REQUIRED


def test_enterprise_catalog_is_static_and_registry_has_no_enterprise_runtime_or_network_attempt():
    calls: list[object] = []

    def http_factory():
        calls.append("http")
        raise AssertionError("enterprise Catalog lookup must not construct HTTP")

    registry = ProviderRegistry(build_catalog({"sources": []}), http_factory=http_factory)
    for family_id in ENTERPRISE:
        with pytest.raises(KeyError, match="unknown provider adapter"):
            registry.adapter(family_id)
    assert calls == []


def test_paid_registry_entries_are_lazy_default_disabled_and_unconfigured_without_key():
    calls: list[object] = []

    class FakeHttp:
        def get_json(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("paid adapter must remain zero-network")

    registry = ProviderRegistry(build_catalog({"sources": []}), http_factory=FakeHttp)
    expected = {"fmp", "massive", "tiingo", "eodhd", "databento"}
    assert expected.issubset(set(registry.available_adapter_ids()))
    for adapter_id in expected:
        assert registry.adapter(adapter_id).probe(build_catalog({"sources": []}).adapter(adapter_id).capability_ids[0])["status"] == "unconfigured"
    assert calls == []


def test_enterprise_shells_never_claim_credentials_sdk_license_or_health_evidence():
    catalog = build_enterprise_catalog()
    for adapter in catalog.adapters:
        serialized = repr(adapter).lower()
        assert adapter.default_enabled is False
        assert adapter.catalog_status is CatalogStatus.LICENSE_REQUIRED
        assert adapter.credential_env_names == ()
        assert "connected" not in serialized
        assert "installed" not in serialized
        assert "validated" not in serialized
        assert "api_key" not in serialized
