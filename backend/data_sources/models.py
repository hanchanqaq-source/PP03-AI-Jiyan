from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class BillingModel(str, Enum):
    FREE_NO_KEY = "free_no_key"
    FREE_KEY = "free_key"
    FREEMIUM = "freemium"
    PAID_API = "paid_api"
    ENTERPRISE_LICENSE = "enterprise_license"
    INTERNAL_ONLY = "internal_only"


class CatalogStatus(str, Enum):
    CONNECTED = "connected"
    CONFIGURED = "configured"
    UNCONFIGURED = "unconfigured"
    CATALOG_ONLY = "catalog_only"
    LICENSE_REQUIRED = "license_required"
    DISABLED = "disabled"


class SourceRole(str, Enum):
    OFFICIAL_EVIDENCE = "official_evidence"
    PRIMARY_DATA = "primary_data"
    FALLBACK_DATA = "fallback_data"
    CROSS_CHECK = "cross_check"
    MACRO_DATA = "macro_data"
    MARKET_DATA = "market_data"
    NEWS_PUBLISHER = "news_publisher"
    INDUSTRY_MEDIA = "industry_media"
    COLLECTOR = "collector"
    CANDIDATE = "candidate"


@dataclass(frozen=True, slots=True)
class SourceFamily:
    source_family_id: str
    source_family_name: str
    region: str
    market: str
    source_roles: tuple[SourceRole, ...]
    independent_evidence_eligible: bool
    commercial_use_status: str
    catalog_status: CatalogStatus
    health_status: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    capability_id: str
    capability_name: str
    data_category: str
    freshness_max_age_seconds: int | None
    probe_enabled: bool
    unit_policy: str
    frequency_policy: str
    primary_families: tuple[str, ...]
    fallback_families: tuple[str, ...]
    cross_check_families: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    adapter_id: str
    adapter_name: str
    source_family_id: str
    provider_type: str
    source_roles: tuple[SourceRole, ...]
    capability_ids: tuple[str, ...]
    billing_model: BillingModel
    auth_type: str
    credential_env_names: tuple[str, ...]
    default_enabled: bool
    license_note: str
    usage_note: str
    data_delay: str
    quota_policy: str
    cost_policy: str
    configured_reference: str
    current_provider_priority: int
    catalog_status: CatalogStatus


@dataclass(frozen=True, slots=True)
class ProviderValue:
    value: object
    source_family_id: str
    adapter_id: str
    capability_id: str
    as_of_date: date | None
    fetched_at: datetime
    data_status: str
    license: str
    priority: int
    difference_from_primary: Decimal | None
    unit: str
    frequency: str
