from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import threading
import time

import pytest

from source_health.models import SourceDescriptor
from source_health.runner import SourceHealthRunner


UTC = timezone.utc
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def descriptor(
    source_id: str,
    *,
    source_name: str,
    group: str = "fund",
    capability: str = "profile",
    critical: bool = False,
) -> SourceDescriptor:
    return SourceDescriptor(
        source_id=source_id,
        source_name=source_name,
        group=group,
        capability=capability,
        source_reference="https://public.example.test/source",
        priority=10,
        critical=critical,
        requires_api_key=False,
        probe_kind="news_feed" if group == "news" else "provider",
        probe_args={"code": "000001"} if group != "news" else {"hint": "ai"},
    )


def successful_result(source_name: str = "公开测试源") -> dict:
    return {
        "status": "success",
        "source_name": source_name,
        "source_reference": "https://public.example.test/source",
        "error_type": "none",
        "error_message_redacted": "",
        "http_status": None,
        "latency_ms": 1,
        "returned_items": 1,
        "data_as_of_date": "2026-08-18",
        "field_completeness_pct": 100.0,
        "used_cache": False,
        "cache_status": "not_used",
        "redirected": False,
        "final_reference": None,
    }


class Provider:
    def __init__(self, name: str):
        self.name = name


def test_quick_selects_only_critical_rows_while_full_selects_every_row():
    rows = [
        descriptor("fund:p1:profile", source_name="p1", critical=True),
        descriptor("fund:p2:profile", source_name="p2"),
        descriptor("news:n1", source_name="n1", group="news", capability="feed"),
    ]
    runner = SourceHealthRunner(
        rows,
        providers=[Provider("p1"), Provider("p2")],
        news_sources={"news:n1": {"name": "n1", "hint": "ai", "url": "https://public.example.test/rss"}},
        provider_probe=lambda *_args, **_kwargs: successful_result(),
        news_probe=lambda *_args, **_kwargs: successful_result(),
        now=lambda: NOW,
    )

    assert [row.source_id for row in runner.select("quick")] == ["fund:p1:profile"]
    assert [row.source_id for row in runner.select("full")] == [
        "fund:p1:profile", "fund:p2:profile", "news:n1",
    ]
    runner.shutdown()


def test_runner_caps_pools_and_serializes_capabilities_for_the_same_provider():
    rows = [
        descriptor(f"fund:p1:cap-{index}", source_name="p1", capability=f"cap-{index}")
        for index in range(3)
    ] + [
        descriptor(f"fund:p{index}:profile", source_name=f"p{index}")
        for index in range(2, 8)
    ] + [
        descriptor(f"news:n{index}", source_name=f"n{index}", group="news", capability="feed")
        for index in range(25)
    ]
    providers = [Provider(f"p{index}") for index in range(1, 8)]
    lock = threading.Lock()
    active = defaultdict(int)
    peaks = defaultdict(int)

    def observe(kind: str, name: str):
        with lock:
            active[kind] += 1
            peaks[kind] = max(peaks[kind], active[kind])
            active[name] += 1
            peaks[name] = max(peaks[name], active[name])
        time.sleep(0.02)
        with lock:
            active[kind] -= 1
            active[name] -= 1
        return successful_result(name)

    runner = SourceHealthRunner(
        rows,
        providers=providers,
        news_sources={row.source_id: {"name": row.source_name} for row in rows if row.group == "news"},
        provider_probe=lambda provider, _capability, **_kwargs: observe("fund", provider.name),
        news_probe=lambda source, **_kwargs: observe("news", source["name"]),
        now=lambda: NOW,
    )

    observations = runner.run("full")

    assert len(observations) == len(rows)
    assert 2 <= peaks["fund"] <= 4
    assert 2 <= peaks["news"] <= 20
    assert peaks["p1"] == 1
    runner.shutdown()


def test_progress_reports_every_result_and_source_failure_does_not_fail_run():
    rows = [
        descriptor("fund:ok:profile", source_name="ok"),
        descriptor("fund:bad:profile", source_name="bad"),
    ]

    def probe(provider, _capability, **_kwargs):
        if provider.name == "bad":
            raise RuntimeError("api_key=private-token-value")
        return successful_result(provider.name)

    updates = []
    runner = SourceHealthRunner(
        rows,
        providers=[Provider("ok"), Provider("bad")],
        news_sources={},
        provider_probe=probe,
        now=lambda: NOW,
    )

    observations = runner.run("full", on_result=lambda item: updates.append(item.to_dict()))

    assert len(updates) == 2
    assert {row.probe_status for row in observations} == {"success", "failure"}
    assert "private-token-value" not in next(
        row for row in observations if row.source_name == "bad"
    ).error_message_redacted
    runner.shutdown()


@pytest.mark.parametrize("reference_field", ["final_reference", "final_url"])
def test_runner_maps_news_probe_reference_to_health_final_reference(reference_field):
    row = descriptor(
        "news:moved",
        source_name="moved",
        group="news",
        capability="feed",
    )
    raw = successful_result("moved")
    raw.update({
        "status": "partial",
        "error_type": "redirect",
        "redirected": True,
    })
    raw.pop("final_reference")
    raw[reference_field] = "https://public.example.test/final.xml"
    runner = SourceHealthRunner(
        [row],
        providers=[],
        news_sources={row.source_id: {"name": "moved"}},
        news_probe=lambda *_args, **_kwargs: raw,
        now=lambda: NOW,
    )

    [observation] = runner.run("full")

    assert observation.final_reference == "https://public.example.test/final.xml"
    runner.shutdown()


def test_shutdown_closes_both_probe_pools():
    runner = SourceHealthRunner([], providers=[], news_sources={})

    runner.shutdown()

    with pytest.raises(RuntimeError, match="shutdown"):
        runner.run("full")
