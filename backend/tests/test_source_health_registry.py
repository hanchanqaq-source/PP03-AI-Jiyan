from __future__ import annotations

from dataclasses import fields

import source_health.registry as registry_module
from source_health.models import ProbeObservation, SourceDescriptor
from source_health.registry import (
    build_news_descriptors,
    build_provider_descriptors,
    build_registry,
    news_source_id,
)
from source_health.samples import select_public_samples


class FakeProvider:
    name = "eastmoney-direct"
    priority = 10
    capabilities = {"search", "nav_history", "stock_snapshot"}


class FakeIndustryProvider:
    name = "cninfo-industry"
    priority = 5
    capabilities = {"stock_industry_classification"}


def fake_providers():
    return [FakeProvider, FakeIndustryProvider]


def fake_news_config():
    return {
        "sources": [
            {
                "hint": "ai",
                "name": "Public feed",
                "url": "https://example.com/feed?api_key=do-not-store",
                "api_key": "do-not-store",
                "cookie": "do-not-store",
            }
        ]
    }


def test_models_expose_the_required_contract_fields():
    descriptor_fields = {item.name for item in fields(SourceDescriptor)}
    observation_fields = {item.name for item in fields(ProbeObservation)}

    assert descriptor_fields == {
        "source_id", "source_name", "group", "capability", "source_reference",
        "priority", "critical", "requires_api_key", "probe_kind", "probe_args",
        "freshness_max_age_seconds",
    }
    assert {
        "probe_status", "error_type", "rating", "rating_confidence",
        "repair_value", "consecutive_failures", "last_success_at",
    } <= observation_fields


def test_registry_expands_provider_by_capability_and_reads_provider_priority():
    rows = build_provider_descriptors(fake_providers())
    by_id = {row.source_id: row for row in rows}

    assert "fund:eastmoney-direct:search" in by_id
    assert "fund:eastmoney-direct:nav_history" in by_id
    assert "quote:eastmoney-direct:stock_snapshot" in by_id
    assert "industry:cninfo-industry:stock_industry_classification" in by_id
    assert by_id["fund:eastmoney-direct:search"].priority == FakeProvider.priority
    assert [row.source_id for row in rows] == [
        "fund:eastmoney-direct:nav_history",
        "fund:eastmoney-direct:search",
        "quote:eastmoney-direct:stock_snapshot",
        "industry:cninfo-industry:stock_industry_classification",
    ]


def test_default_registry_preserves_actual_runtime_provider_order():
    from fund_data.service import FundDataService

    runtime_providers = FundDataService(cache=object()).providers
    expected = [
        (provider.name, capability)
        for provider in runtime_providers
        for capability in sorted(provider.capabilities)
    ]
    actual = [(row.source_name, row.capability) for row in build_provider_descriptors()]

    assert actual == expected


def test_registry_assigns_semantic_freshness_windows_without_staling_profile_or_search():
    class ProviderWithAllCapabilities:
        name = "semantic-provider"
        priority = 10
        capabilities = {
            "search", "profile", "nav_history", "holdings",
            "industry_allocation", "stock_snapshot", "stock_industry_classification",
        }

    by_capability = {
        row.capability: row for row in build_provider_descriptors([ProviderWithAllCapabilities])
    }

    assert by_capability["search"].freshness_max_age_seconds is None
    assert by_capability["profile"].freshness_max_age_seconds is None
    for capability in (
        "nav_history", "holdings", "industry_allocation",
        "stock_snapshot", "stock_industry_classification",
    ):
        assert by_capability[capability].freshness_max_age_seconds == registry_module.CAPABILITY_FRESHNESS_MAX_AGE_SECONDS[capability]
        assert by_capability[capability].freshness_max_age_seconds > 0


def test_news_source_identity_includes_track_name_and_url():
    baseline = news_source_id("ai", "Same", "https://example.com/feed")

    assert baseline != news_source_id("semi", "Same", "https://example.com/feed")
    assert baseline != news_source_id("ai", "Other", "https://example.com/feed")
    assert baseline != news_source_id("ai", "Same", "https://example.com/other")
    assert baseline.startswith("news:")


def test_news_configuration_identity_is_unambiguous_when_fields_contain_delimiter():
    rows = build_news_descriptors({
        "sources": [
            {
                "hint": "ai|daily",
                "name": "feed",
                "url": "https://example.com/rss",
                "language": "zh-CN",
                "region": "CN",
            },
            {
                "hint": "ai",
                "name": "daily|feed",
                "url": "https://example.com/rss",
                "language": "zh-CN",
                "region": "CN",
            },
        ]
    })

    assert rows[0].probe_args["configuration_identity"] != rows[1].probe_args["configuration_identity"]


def test_news_identity_and_reference_remove_non_public_query_parameters():
    public_url = "https://example.com/feed?mid=21"
    configured_url = (
        public_url
        + "&client_secret=client-value&password=password-value"
        + "&refresh_token=refresh-value&session=session-value&jwt=jwt-value"
    )
    row = build_news_descriptors({
        "sources": [{"hint": "ai", "name": "Public feed", "url": configured_url}]
    })[0]
    serialized = str(row.to_dict()).lower()

    assert row.source_reference == public_url
    assert row.source_id == news_source_id("ai", "Public feed", public_url)
    for forbidden in (
        "client_secret", "client-value", "password", "password-value",
        "refresh_token", "refresh-value", "session", "session-value",
        "jwt", "jwt-value",
    ):
        assert forbidden not in serialized


def test_registry_never_contains_credentials():
    rows = build_registry(fake_providers(), fake_news_config())
    payload = [row.to_dict() for row in rows]
    serialized = str(payload).lower()

    assert all("api_key" not in row["probe_args"] for row in payload)
    assert all("cookie" not in row["probe_args"] for row in payload)
    assert "cookie" not in serialized
    assert "do-not-store" not in serialized


def test_sample_selection_sorts_codes_and_requires_successful_profile():
    catalog = [
        {"code": "000003", "name": "三号", "fund_type": "混合型-偏股"},
        {"code": "000001", "name": "一号", "fund_type": "混合型-偏股"},
        {"code": "000002", "name": "二号", "fund_type": "混合型-偏股"},
    ]
    calls: list[str] = []

    def profile(code: str):
        calls.append(code)
        if code == "000001":
            raise RuntimeError("public profile unavailable")
        return {"code": code, "name": "已验证"}

    rows = select_public_samples(
        catalog,
        profile,
        verified_at="2026-08-18T00:00:00+00:00",
        source_name="公开目录测试源",
    )
    active = next(row for row in rows if row["category"] == "主动混合型")

    assert calls[:2] == ["000001", "000002"]
    assert active == {
        "category": "主动混合型",
        "code": "000002",
        "name": "二号",
        "fund_type": "混合型-偏股",
        "verified_at": "2026-08-18T00:00:00+00:00",
        "source_name": "公开目录测试源",
        "sample_status": "available",
    }
    assert next(row for row in rows if row["category"] == "新成立基金")["sample_status"] == "unavailable"


def test_stock_sample_does_not_treat_qdii_stock_as_domestic_stock_type():
    catalog = [
        {"code": "000001", "name": "境外股票", "fund_type": "QDII-普通股票"},
        {"code": "000002", "name": "境内股票", "fund_type": "股票型-普通股票"},
    ]

    rows = select_public_samples(
        catalog,
        lambda code: {"code": code},
        verified_at="2026-08-18T00:00:00+00:00",
        source_name="公开目录测试源",
    )

    stock = next(row for row in rows if row["category"] == "股票型")
    assert stock["code"] == "000002"


def test_sample_selection_rejects_profile_for_a_different_code():
    catalog = [
        {"code": "000001", "name": "一号", "fund_type": "货币型-普通货币"},
        {"code": "000002", "name": "二号", "fund_type": "货币型-普通货币"},
    ]

    def profile(code: str):
        return {"code": "999999" if code == "000001" else code}

    rows = select_public_samples(
        catalog,
        profile,
        verified_at="2026-08-18T00:00:00+00:00",
        source_name="公开目录测试源",
    )

    money = next(row for row in rows if row["category"] == "货币型")
    assert money["code"] == "000002"
