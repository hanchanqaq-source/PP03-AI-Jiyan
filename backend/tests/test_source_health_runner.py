from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import threading
import time

import pytest

from source_health.models import SourceDescriptor
from source_health.registry import build_news_descriptors
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
    freshness_max_age_seconds: int | None = None,
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
        freshness_max_age_seconds=freshness_max_age_seconds,
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

    observations = runner.run(
        "full", on_probe_complete=lambda item: updates.append(item.to_dict()),
    )

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
    assert observation.observed_final_reference == observation.final_reference
    runner.shutdown()


def test_runner_retains_configured_reference_when_a_probe_fails():
    row = descriptor("news:failed", source_name="failed", group="news", capability="feed")
    runner = SourceHealthRunner(
        [row], providers=[], news_sources={row.source_id: {"name": "failed"}},
        news_probe=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timeout")),
        now=lambda: NOW,
    )

    [observation] = runner.run("full")

    assert observation.configured_reference == "https://public.example.test/source"
    assert observation.observed_final_reference is None
    assert observation.final_reference is None
    runner.shutdown()


def test_shutdown_closes_both_probe_pools():
    runner = SourceHealthRunner([], providers=[], news_sources={})

    runner.shutdown()

    with pytest.raises(RuntimeError, match="shutdown"):
        runner.run("full")


def test_runner_uses_history_for_confidence_failure_streak_and_last_success():
    row = descriptor("news:history", source_name="history", group="news", capability="feed")
    raw = successful_result("history")
    raw.update({"status": "failure", "error_type": "timeout", "returned_items": 0})
    history = [
        {
            "run_id": "old-container",
            "observations": [
                {
                    "source_id": row.source_id,
                    "probe_status": "success" if index == 0 else "failure",
                    "finished_at": f"2026-08-{11 + index // 3:02d}T12:00:00+00:00",
                    "error_type": "none" if index == 0 else "timeout",
                }
                for index in range(19)
            ],
        }
    ]
    runner = SourceHealthRunner(
        [row], providers=[], news_sources={row.source_id: {"name": "history"}},
        news_probe=lambda *_args, **_kwargs: raw, now=lambda: NOW,
    )

    [result] = runner.run("full", history_documents=history)

    assert result.rating_confidence == "stable"
    assert result.consecutive_failures == 19
    assert result.last_success_at == "2026-08-11T12:00:00+00:00"
    runner.shutdown()


def test_runner_reaches_growing_confidence_at_three_dates_and_five_samples():
    row = descriptor("news:growing", source_name="growing", group="news", capability="feed")
    history = [
        {"source_id": row.source_id, "probe_status": "success", "finished_at": stamp}
        for stamp in (
            "2026-08-16T10:00:00+00:00", "2026-08-16T11:00:00+00:00",
            "2026-08-17T10:00:00+00:00", "2026-08-17T11:00:00+00:00",
        )
    ]
    runner = SourceHealthRunner(
        [row], providers=[], news_sources={row.source_id: {"name": "growing"}},
        news_probe=lambda *_args, **_kwargs: successful_result("growing"), now=lambda: NOW,
    )

    [result] = runner.run("full", history_documents=history)

    assert result.rating_confidence == "growing"
    assert result.consecutive_failures == 0
    assert result.last_success_at == NOW.isoformat()
    runner.shutdown()


@pytest.mark.parametrize(
    ("source_id", "critical", "raw_updates", "history", "expected"),
    [
        (
            "news:redirect", False,
            {"status": "partial", "error_type": "redirect", "http_status": 301, "redirected": True,
             "final_reference": "https://public.example.test/new.xml"},
            [], "immediate_fix",
        ),
        (
            "news:limited", False,
            {"status": "failure", "error_type": "rate_limit", "http_status": 429,
             "retry_after_present": True, "returned_items": 0},
            [], "observe",
        ),
        (
            "news:multi-day", False,
            {"status": "failure", "error_type": "timeout", "returned_items": 0},
            [
                {"source_id": "news:multi-day", "probe_status": "failure", "finished_at": "2026-08-16T12:00:00+00:00"},
                {"source_id": "news:multi-day", "probe_status": "failure", "finished_at": "2026-08-17T12:00:00+00:00"},
            ], "replace_candidate",
        ),
        (
            "news:valuable-parse", True,
            {"status": "failure", "error_type": "parse", "returned_items": 0},
            [{"source_id": "news:valuable-parse", "probe_status": "failure", "error_type": "parse",
              "finished_at": "2026-08-17T12:00:00+00:00"}], "worth_fixing",
        ),
        (
            "news:cached", False,
            {"status": "failure", "error_type": "http", "http_status": 500, "returned_items": 0,
             "used_cache": True, "cache_status": "cache"},
            [], "observe",
        ),
    ],
)
def test_runner_connects_runtime_repair_evidence(source_id, critical, raw_updates, history, expected):
    row = descriptor(source_id, source_name=source_id, group="news", capability="feed", critical=critical)
    companion = descriptor("news:companion", source_name="companion", group="news", capability="feed")
    raw = successful_result(source_id)
    raw.update(raw_updates)
    runner = SourceHealthRunner(
        [row, companion], providers=[],
        news_sources={row.source_id: {"name": source_id}, companion.source_id: {"name": "companion"}},
        news_probe=lambda source, **_kwargs: raw if source["name"] == source_id else successful_result("companion"),
        now=lambda: NOW,
    )

    results = runner.run("full", history_documents=history)

    result = next(item for item in results if item.source_id == source_id)
    assert result.repair_value == expected
    runner.shutdown()


@pytest.mark.parametrize(
    ("fallback_status", "expected"),
    [("failure", "worth_fixing"), ("success", "none")],
)
def test_runner_uses_completed_observations_for_reliable_fallback_and_callback(
    fallback_status,
    expected,
):
    primary = descriptor(
        "news:primary", source_name="primary", group="news", capability="feed",
    )
    fallback = descriptor(
        "news:fallback", source_name="fallback", group="news", capability="feed",
    )

    def probe(source, **_kwargs):
        raw = successful_result(source["name"])
        if source["name"] == "primary" or fallback_status == "failure":
            raw.update({
                "status": "failure", "error_type": "unknown", "returned_items": 0,
                "field_completeness_pct": 0.0,
            })
        return raw

    progress_rows = []
    final_rows = []
    runner = SourceHealthRunner(
        [primary, fallback], providers=[],
        news_sources={
            primary.source_id: {"name": "primary"},
            fallback.source_id: {"name": "fallback"},
        },
        news_probe=probe,
        now=lambda: NOW,
    )

    results = runner.run(
        "full",
        on_probe_complete=lambda row: progress_rows.append(row.to_dict()),
        on_final_result=lambda row: final_rows.append(row.to_dict()),
    )

    primary_result = next(row for row in results if row.source_id == primary.source_id)
    primary_progress = next(row for row in progress_rows if row["source_id"] == primary.source_id)
    primary_final = next(row for row in final_rows if row["source_id"] == primary.source_id)
    assert primary_result.fallback_available is True
    assert primary_result.repair_value == expected
    assert primary_progress["repair_value"] == "none"
    assert primary_final["repair_value"] == expected
    runner.shutdown()


def test_runner_does_not_accept_probe_raw_as_reliable_cache_evidence():
    row = descriptor(
        "news:raw-cache", source_name="raw-cache", group="news", capability="feed",
    )
    raw = successful_result("raw-cache")
    raw.update({
        "status": "failure", "error_type": "unknown", "returned_items": 0,
        "field_completeness_pct": 0.0, "used_cache": True, "cache_status": "cache",
        "reliable_cache_available": True,
    })
    runner = SourceHealthRunner(
        [row], providers=[], news_sources={row.source_id: {"name": "raw-cache"}},
        news_probe=lambda *_args, **_kwargs: raw, now=lambda: NOW,
    )

    [result] = runner.run("full")

    assert result.repair_value == "worth_fixing"
    runner.shutdown()


def test_runner_marks_exact_duplicate_registration_but_permission_rule_keeps_priority():
    config = {"hint": "ai", "name": "duplicate", "url": "https://public.example.test/feed", "language": "zh-CN", "region": "CN"}
    duplicate = build_news_descriptors({"sources": [config, dict(config)]})[0]
    healthy = successful_result("duplicate")
    runner = SourceHealthRunner(
        [duplicate, duplicate], providers=[], news_sources={duplicate.source_id: {"name": "duplicate"}},
        news_probe=lambda *_args, **_kwargs: healthy, now=lambda: NOW,
    )
    duplicate_results = runner.run("full", history_documents=[])
    assert {row.repair_value for row in duplicate_results} == {"immediate_fix"}
    runner.shutdown()

    denied = successful_result("duplicate")
    denied.update({"status": "failure", "error_type": "authentication", "http_status": 403})
    priority_runner = SourceHealthRunner(
        [duplicate, duplicate], providers=[], news_sources={duplicate.source_id: {"name": "duplicate"}},
        news_probe=lambda *_args, **_kwargs: denied, now=lambda: NOW,
    )
    denied_results = priority_runner.run("full", history_documents=[])
    assert {row.repair_value for row in denied_results} == {"replace_candidate"}
    priority_runner.shutdown()

    distinct = build_news_descriptors({"sources": [config, {**config, "region": "US"}]})
    distinct_runner = SourceHealthRunner(
        distinct, providers=[], news_sources={duplicate.source_id: {"name": "duplicate"}},
        news_probe=lambda *_args, **_kwargs: healthy, now=lambda: NOW,
    )
    distinct_results = distinct_runner.run("full", history_documents=[])
    assert {row.repair_value for row in distinct_results} == {"none"}
    distinct_runner.shutdown()
