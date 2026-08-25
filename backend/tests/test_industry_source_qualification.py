from __future__ import annotations

from dataclasses import replace
from datetime import date
import importlib.util

import pytest

from data_sources.catalog import build_catalog
from data_sources.models import BillingModel, CatalogStatus, SourceRole


def _verified_qualification():
    from industry_research.source_qualification import SourceQualificationResult

    return SourceQualificationResult(
        source_identity="trendforce_public_price",
        request_url="https://www.trendforce.com/price/dram/dram_spot",
        final_url="https://www.trendforce.com/price/dram/dram_spot",
        http_status=200,
        response_cap_bytes=500_000,
        response_bytes=480,
        target_fields=("product", "session_average", "date"),
        field_shape="html_table:product,session_average,date",
        data_date_field="date",
        data_date=date(2026, 8, 24),
        unit="USD",
        frequency="current_snapshot",
        license_conclusion="verified_public_current_snapshot",
        failure_modes=(
            "structure_changed",
            "missing_data_date",
            "missing_unit",
            "login_required",
            "cookie_required",
            "member_download",
            "response_too_large",
            "redirect_disallowed",
            "license_unverified",
        ),
        failure_reason=None,
        login_required=False,
        cookie_required=False,
        member_download=False,
    )


def _enabled_descriptor():
    descriptor = build_catalog({"sources": []}).adapter("trendforce-public-price")
    return replace(
        descriptor,
        default_enabled=True,
        catalog_status=CatalogStatus.CONFIGURED,
    )


def test_source_qualification_module_is_registered_before_behavior_is_used():
    """Catches the qualification contract being omitted entirely."""
    assert importlib.util.find_spec("industry_research.source_qualification") is not None


def test_source_qualification_result_is_the_public_industry_research_interface():
    """Catches callers needing to depend on an internal module path for the produced contract."""
    from industry_research import SourceQualificationResult
    from industry_research.source_qualification import SourceQualificationResult as ConcreteResult

    assert SourceQualificationResult is ConcreteResult


def test_catalog_registers_one_candidate_capability_without_claiming_availability_or_evidence():
    """Catches a candidate source being omitted, pre-enabled, or promoted to independent evidence."""
    catalog = build_catalog({"sources": []})
    family = catalog.family("trendforce_public_price")
    adapter = catalog.adapter("trendforce-public-price")
    capability = catalog.capability("industry_price_snapshot")

    assert capability.primary_families == ()
    assert capability.fallback_families == ("trendforce_public_price",)
    assert family.source_roles == (SourceRole.CANDIDATE,)
    assert family.independent_evidence_eligible is False
    assert family.catalog_status is CatalogStatus.UNCONFIGURED
    assert adapter.capability_ids == ("industry_price_snapshot",)
    assert adapter.billing_model is BillingModel.FREE_NO_KEY
    assert adapter.auth_type == "none"
    assert adapter.credential_env_names == ()
    assert adapter.default_enabled is False
    assert adapter.catalog_status is CatalogStatus.UNCONFIGURED


@pytest.mark.parametrize(
    ("field", "missing"),
    [
        ("source_identity", ""),
        ("request_url", ""),
        ("final_url", None),
        ("http_status", None),
        ("response_cap_bytes", 0),
        ("response_bytes", None),
        ("target_fields", ()),
        ("field_shape", ""),
        ("data_date_field", ""),
        ("data_date", None),
        ("unit", None),
        ("frequency", ""),
        ("failure_modes", ()),
    ],
)
def test_missing_required_qualification_metadata_never_enables_candidate(field, missing):
    """Catches incomplete source identity, shape, date, unit, cap, or failure policy being treated as qualified."""
    from industry_research.source_qualification import qualification_allows_enabled_adapter

    assert qualification_allows_enabled_adapter(
        replace(_verified_qualification(), **{field: missing}),
        _enabled_descriptor(),
    ) is False


@pytest.mark.parametrize(
    "qualification_change",
    [
        {"license_conclusion": "license_unverified", "failure_reason": "license_unverified"},
        {"login_required": True, "failure_reason": "login_required"},
        {"cookie_required": True, "failure_reason": "cookie_required"},
        {"member_download": True, "failure_reason": "member_download"},
    ],
)
def test_protected_or_unverified_qualification_never_enables_candidate(qualification_change):
    """Catches login, Cookie, member-download, or unknown-license surfaces entering scheduling."""
    from industry_research.source_qualification import qualification_allows_enabled_adapter

    assert qualification_allows_enabled_adapter(
        replace(_verified_qualification(), **qualification_change),
        _enabled_descriptor(),
    ) is False


@pytest.mark.parametrize(
    "descriptor_change",
    [
        {"billing_model": BillingModel.FREE_KEY},
        {"billing_model": BillingModel.FREEMIUM},
        {"billing_model": BillingModel.PAID_API},
        {"billing_model": BillingModel.ENTERPRISE_LICENSE},
        {"credential_env_names": ("SOME_API_KEY",), "auth_type": "api_key"},
        {"default_enabled": False},
        {"catalog_status": CatalogStatus.UNCONFIGURED},
        {"catalog_status": CatalogStatus.LICENSE_REQUIRED},
    ],
)
def test_scheduling_allowlist_requires_free_no_key_empty_credentials_and_enabled_catalog(descriptor_change):
    """Catches a key, freemium, paid, enterprise, credentialled, or unavailable adapter entering scheduling."""
    from industry_research.source_qualification import qualification_allows_enabled_adapter

    assert qualification_allows_enabled_adapter(
        _verified_qualification(),
        replace(_enabled_descriptor(), **descriptor_change),
    ) is False


def test_complete_public_snapshot_qualification_can_pass_the_explicit_allowlist():
    """Catches the fail-closed gate becoming impossible to satisfy after every required check passes."""
    from industry_research.source_qualification import qualification_allows_enabled_adapter

    assert qualification_allows_enabled_adapter(_verified_qualification(), _enabled_descriptor()) is True


def test_company_level_official_disclosure_is_not_registered_as_an_industry_total_source():
    """Catches SEC or official-link company facts being extrapolated into the industry snapshot capability."""
    capability = build_catalog({"sources": []}).capability("industry_price_snapshot")

    assert "sec_edgar" not in capability.primary_families
    assert "sec_edgar" not in capability.fallback_families
    assert "sec_edgar" not in capability.cross_check_families
