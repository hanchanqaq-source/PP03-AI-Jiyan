from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from data_sources.models import BillingModel, CatalogStatus


PUBLIC_PRICE_URL = "https://www.trendforce.com/price/dram/dram_spot"
PUBLIC_PRICE_SOURCE_IDENTITY = "trendforce_public_price"
PUBLIC_PRICE_RESPONSE_CAP_BYTES = 500_000
PUBLIC_PRICE_TARGET_FIELDS = ("product", "session_average", "date")
PUBLIC_PRICE_FIELD_SHAPE = "html_table:product,session_average,date"
PUBLIC_PRICE_FAILURE_MODES = (
    "structure_changed",
    "missing_data_date",
    "missing_unit",
    "login_required",
    "cookie_required",
    "member_download",
    "response_too_large",
    "redirect_disallowed",
    "license_unverified",
)
VERIFIED_PUBLIC_SNAPSHOT_LICENSE = "verified_public_current_snapshot"
UNVERIFIED_LICENSE = "license_unverified"


@dataclass(frozen=True, slots=True)
class SourceQualificationResult:
    source_identity: str
    request_url: str
    final_url: str | None
    http_status: int | None
    response_cap_bytes: int
    response_bytes: int | None
    target_fields: tuple[str, ...]
    observed_response_fields: tuple[str, ...]
    field_shape: str
    data_date_field: str
    data_date: date | None
    unit: str | None
    frequency: str
    license_conclusion: str
    failure_modes: tuple[str, ...]
    failure_reason: str | None
    login_required: bool
    cookie_required: bool
    member_download: bool


UNVERIFIED_PUBLIC_PRICE_QUALIFICATION = SourceQualificationResult(
    source_identity=PUBLIC_PRICE_SOURCE_IDENTITY,
    request_url=PUBLIC_PRICE_URL,
    final_url=None,
    http_status=None,
    response_cap_bytes=PUBLIC_PRICE_RESPONSE_CAP_BYTES,
    response_bytes=None,
    target_fields=PUBLIC_PRICE_TARGET_FIELDS,
    observed_response_fields=(),
    field_shape="",
    data_date_field="date",
    data_date=None,
    unit=None,
    frequency="current_snapshot",
    license_conclusion=UNVERIFIED_LICENSE,
    failure_modes=PUBLIC_PRICE_FAILURE_MODES,
    failure_reason=UNVERIFIED_LICENSE,
    login_required=False,
    cookie_required=False,
    member_download=False,
)


def qualification_metadata_complete(result: SourceQualificationResult) -> bool:
    """Return True only for the one fully observed, explicitly licensed snapshot contract."""
    return (
        result.source_identity == PUBLIC_PRICE_SOURCE_IDENTITY
        and result.request_url == PUBLIC_PRICE_URL
        and result.final_url == PUBLIC_PRICE_URL
        and type(result.http_status) is int
        and result.http_status == 200
        and type(result.response_cap_bytes) is int
        and result.response_cap_bytes > 0
        and type(result.response_bytes) is int
        and 0 < result.response_bytes <= result.response_cap_bytes
        and result.target_fields == PUBLIC_PRICE_TARGET_FIELDS
        and result.observed_response_fields == PUBLIC_PRICE_TARGET_FIELDS
        and result.field_shape == PUBLIC_PRICE_FIELD_SHAPE
        and result.data_date_field == "date"
        and isinstance(result.data_date, date)
        and not isinstance(result.data_date, datetime)
        and result.unit == "USD"
        and result.frequency == "current_snapshot"
        and result.license_conclusion == VERIFIED_PUBLIC_SNAPSHOT_LICENSE
        and set(PUBLIC_PRICE_FAILURE_MODES).issubset(result.failure_modes)
        and result.failure_reason is None
        and result.login_required is False
        and result.cookie_required is False
        and result.member_download is False
    )


def qualification_allows_enabled_adapter(
    result: SourceQualificationResult,
    descriptor: Any,
) -> bool:
    """Fail-closed scheduling allowlist for the public industry-price candidate."""
    return (
        qualification_metadata_complete(result)
        and getattr(descriptor, "billing_model", None) is BillingModel.FREE_NO_KEY
        and getattr(descriptor, "auth_type", None) == "none"
        and getattr(descriptor, "credential_env_names", None) == ()
        and getattr(descriptor, "default_enabled", None) is True
        and getattr(descriptor, "catalog_status", None) is CatalogStatus.CONFIGURED
        and getattr(descriptor, "configured_reference", None) == PUBLIC_PRICE_URL
    )
