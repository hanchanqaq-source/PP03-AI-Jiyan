from __future__ import annotations

from data_sources.catalog import build_catalog
from data_sources.health_bridge import catalog_probe_descriptors, merge_health
from fund_data.providers.eastmoney_direct import EastmoneyDirectProvider
from source_health.models import ProbeObservation
from source_health.registry import build_provider_descriptors, build_registry


class EastmoneyProvider:
    adapter_id = "eastmoney-direct"
    priority = 10
    capabilities = {"stock_snapshot"}

    def __init__(self, name: str) -> None:
        self.name = name


def _failed_observation() -> ProbeObservation:
    return ProbeObservation(
        source_id="eastmoney-direct:stock_snapshot",
        source_name="东方财富-天天基金",
        group="quote",
        capability="stock_snapshot",
        started_at="2026-08-19T00:00:00+00:00",
        finished_at="2026-08-19T00:00:01+00:00",
        latency_ms=1,
        probe_status="failure",
        error_type="timeout",
        error_message_redacted="timeout",
        http_status=None,
        returned_items=0,
        data_as_of_date=None,
        freshness_seconds=None,
        field_completeness_pct=0.0,
        used_cache=False,
        cache_status="not_used",
        fallback_available=True,
        redirected=False,
        final_reference=None,
        source_family_id="eastmoney",
        adapter_id="eastmoney-direct",
        capability_id="stock_snapshot",
        configured_reference="https://fund.eastmoney.com/",
        observed_final_reference=None,
    )


def test_bridge_preserves_configured_reference_on_failed_probe():
    catalog = build_catalog({"sources": []})

    row = merge_health(catalog, [_failed_observation()]).adapter(
        "eastmoney-direct"
    ).capability("stock_snapshot")

    assert row.configured_reference == "https://fund.eastmoney.com/"
    assert row.observed_final_reference is None
    assert row.final_reference is None


def test_runtime_source_name_does_not_change_family_identity():
    first = build_provider_descriptors([EastmoneyProvider("东方财富基金档案")])[0]
    second = build_provider_descriptors([EastmoneyProvider("东方财富-天天基金")])[0]

    assert first.source_family_id == second.source_family_id == "eastmoney"
    assert first.adapter_id == second.adapter_id == "eastmoney-direct"
    assert first.source_id == second.source_id == "eastmoney-direct:stock_snapshot"


def test_actual_provider_uses_stable_catalog_adapter_when_display_name_changes():
    class RenamedEastmoneyProvider(EastmoneyDirectProvider):
        name = "东方财富运行时展示名"

    row = next(
        item
        for item in build_provider_descriptors([RenamedEastmoneyProvider(session=object())])
        if item.capability_id == "stock_snapshot"
    )

    assert row.source_family_id == "eastmoney"
    assert row.adapter_id == "eastmoney-direct"
    assert row.source_id == "eastmoney-direct:stock_snapshot"


def test_build_registry_deduplicates_exact_duplicate_news_configurations():
    source = {
        "hint": "ai", "name": "Public feed", "url": "https://public.example.test/rss",
        "language": "zh-CN", "region": "CN",
    }

    rows = build_registry([], {"sources": [source, dict(source)]})

    assert len(rows) == 1
    assert rows[0].source_id.startswith("news-feed:")


def test_catalog_descriptors_keep_configured_reference_as_source_reference_alias():
    catalog = build_catalog({"sources": []})

    [row] = catalog_probe_descriptors(
        catalog,
        [EastmoneyProvider("东方财富-天天基金")],
        {"sources": []},
    )

    assert row.configured_reference == "https://fund.eastmoney.com/"
    assert row.source_reference == row.configured_reference


def test_unexamined_and_mixed_family_health_do_not_treat_missing_observations_as_failures():
    catalog = build_catalog({"sources": []})

    assert merge_health(catalog, []).family("eastmoney").health_status == "unexamined"

    successful = _failed_observation()
    successful.probe_status = "success"
    successful.source_id = "eastmoney-direct:search"
    successful.capability = "search"
    successful.capability_id = "search"
    assert merge_health(catalog, [_failed_observation(), successful]).family(
        "eastmoney"
    ).health_status == "partial_degraded"
