from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest

import app as app_module
import newsradar
import source_health
import source_health.service as service_module
from source_health.models import ProbeObservation
from source_health.models import SourceDescriptor
from source_health.registry import news_source_id
from source_health.runner import SourceHealthRunner
from source_health.service import FullRunConflict, SourceHealthService
from source_health.storage import SourceHealthStorage


UTC = timezone.utc
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def observation(source_id="fund:p1:profile", *, group="fund", status="success", rating="healthy"):
    return ProbeObservation(
        source_id=source_id,
        source_name=source_id,
        group=group,
        capability="feed" if group == "news" else "profile",
        started_at=NOW.isoformat(),
        finished_at=NOW.isoformat(),
        latency_ms=1,
        probe_status=status,
        error_type="none" if status == "success" else "timeout",
        error_message_redacted="",
        http_status=None,
        returned_items=1 if status == "success" else 0,
        data_as_of_date="2026-08-18",
        freshness_seconds=0,
        field_completeness_pct=100.0 if status == "success" else 0.0,
        used_cache=False,
        cache_status="not_used",
        fallback_available=False,
        redirected=False,
        final_reference=None,
        rating_score=95.0 if rating == "healthy" else 30.0,
        rating=rating,
        rating_confidence="initial",
    )


class ImmediateRunner:
    def __init__(self, rows=None):
        self.rows = rows or [observation()]
        self.calls = []
        self.shutdown_called = False

    def select(self, scope):
        return [object() for _ in self.rows]

    def run(self, scope, *, on_result=None, history_documents=()):
        self.calls.append(scope)
        for row in self.rows:
            if on_result:
                on_result(row)
        return list(self.rows)

    def shutdown(self):
        self.shutdown_called = True


class BlockingRunner(ImmediateRunner):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, scope, *, on_result=None, history_documents=()):
        self.calls.append(scope)
        self.started.set()
        self.release.wait(timeout=2)
        return super().run(scope, on_result=on_result, history_documents=history_documents)


class ScopedRunner(ImmediateRunner):
    def __init__(self, *, full_rows, quick_rows):
        super().__init__(full_rows)
        self.full_rows = full_rows
        self.quick_rows = quick_rows

    def select(self, scope):
        return [object() for _ in (self.quick_rows if scope == "quick" else self.full_rows)]

    def run(self, scope, *, on_result=None, history_documents=()):
        rows = self.quick_rows if scope == "quick" else self.full_rows
        self.calls.append(scope)
        for row in rows:
            if on_result:
                on_result(row)
        return list(rows)


def wait_for(service: SourceHealthService, run_id: str, status: str = "completed"):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        current = service.get_run(run_id)
        if current and current["status"] == status:
            return current
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {status}")


@pytest.mark.parametrize(
    ("cache_case", "expected"),
    [
        ("fresh_match", "observe"),
        ("stale", "worth_fixing"),
        ("became_stale", "worth_fixing"),
        ("mismatch", "worth_fixing"),
        ("malformed", "worth_fixing"),
    ],
)
def test_default_service_uses_only_matching_fresh_radar_cache_as_reliable_evidence(
    tmp_path,
    monkeypatch,
    cache_case,
    expected,
):
    source = {
        "hint": "ai", "name": "Public feed",
        "url": "https://public.example.test/rss", "language": "zh-CN", "region": "CN",
    }
    configured_id = news_source_id(source["hint"], source["name"], source["url"])
    cache_source_id = "news:not-the-configured-source" if cache_case == "mismatch" else configured_id
    generated_at = NOW - timedelta(days=8) if cache_case == "stale" else NOW
    cache_path = tmp_path / "news" / "radar.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps({
        "generated_at": generated_at.isoformat(),
        "recent_days": 7,
        "cache_status": "partial",
        "source_state": "partial_failure",
        "source_statuses": [{
            "source_id": cache_source_id,
            "source_name": source["name"],
            "source_url": source["url"],
            "status": "failed",
            "last_success_at": (NOW - timedelta(hours=1)).isoformat(),
            "used_cached_items": True,
            "item_count": "not-a-count" if cache_case == "malformed" else 1,
        }],
        "industries": [{
            "key": source["hint"],
            "items": [{
                "title": "Cached public item", "source_name": source["name"],
                "source_url": source["url"], "published_at": (NOW - timedelta(hours=2)).isoformat(),
            }],
        }],
        "stats": {"industries": 1, "total_sources": 1, "failed_sources": 1},
    }, ensure_ascii=False), encoding="utf-8")
    original_cache = cache_path.read_bytes()
    monkeypatch.setattr(service_module, "default_fund_providers", lambda: [])
    monkeypatch.setattr(service_module, "load_news_config", lambda: {
        "fetch": {"timeout": 1}, "sources": [source],
    })
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(cache_path))
    monkeypatch.setattr(newsradar, "probe_source_config", lambda *_args, **_kwargs: {
        "status": "failure", "source_name": source["name"], "error_type": "unknown",
        "error_message_redacted": "public source unavailable", "http_status": None,
        "latency_ms": 1, "returned_items": 0, "data_as_of_date": None,
        "field_completeness_pct": 0.0, "used_cache": False,
        "cache_status": "not_used", "redirected": False, "final_url": source["url"],
    })
    clock = [NOW]
    service = SourceHealthService(
        storage=SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW),
        now=lambda: clock[0],
    )
    if cache_case == "became_stale":
        clock[0] = NOW + timedelta(days=8)

    run = service.start_run("full")
    wait_for(service, run["run_id"])

    assert service.list_sources()[0]["repair_value"] == expected
    assert cache_path.read_bytes() == original_cache
    service.shutdown()


def test_quick_cooldown_is_persisted_for_twenty_four_hours(tmp_path):
    storage = SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW)
    storage.write_last_run({
        "run_id": "a" * 20,
        "scope": "quick",
        "status": "completed",
        "finished_at": (NOW - timedelta(hours=23, minutes=59)).isoformat(),
        "last_quick_run_at": (NOW - timedelta(hours=23, minutes=59)).isoformat(),
    })
    service = SourceHealthService(runner=ImmediateRunner(), storage=storage, now=lambda: NOW)

    assert service.schedule_quick_if_due() is None

    storage.write_last_run({
        "run_id": "b" * 20,
        "scope": "quick",
        "status": "completed",
        "finished_at": (NOW - timedelta(hours=24)).isoformat(),
        "last_quick_run_at": (NOW - timedelta(hours=24)).isoformat(),
    })
    due = SourceHealthService(runner=ImmediateRunner(), storage=storage, now=lambda: NOW)
    run = due.schedule_quick_if_due()

    assert run is not None and run["scope"] == "quick"
    assert len(run["run_id"]) == 20
    wait_for(due, run["run_id"])
    service.shutdown()
    due.shutdown()


def test_full_run_is_non_blocking_mutually_exclusive_and_tracks_progress(tmp_path):
    runner = BlockingRunner()
    service = SourceHealthService(
        runner=runner,
        storage=SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW),
        now=lambda: NOW,
    )

    started = time.monotonic()
    run = service.start_run("full")
    elapsed = time.monotonic() - started
    assert elapsed < 0.2
    assert runner.started.wait(timeout=1)
    assert service.get_run(run["run_id"])["status"] in {"queued", "running"}
    with pytest.raises(FullRunConflict, match="数据源体检正在运行"):
        service.start_run("full")

    runner.release.set()
    completed = wait_for(service, run["run_id"])
    assert completed["completed"] == completed["total"] == 1
    assert completed["success"] == 1
    service.shutdown()


def test_completed_run_persists_last_run_summary_and_history(tmp_path):
    rows = [
        observation(),
        observation("news:n1", group="news", status="failure", rating="failed"),
    ]
    storage = SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW)
    service = SourceHealthService(
        runner=ImmediateRunner(rows),
        storage=storage,
        now=lambda: NOW,
        reclaimable_bytes=lambda: 321,
    )

    run = service.start_run("full")
    completed = wait_for(service, run["run_id"])

    assert completed["status"] == "completed"
    assert completed["success"] == 1
    assert completed["failure"] == 1
    saved_run = json.loads(storage.last_run_path.read_text(encoding="utf-8"))
    saved_summary = json.loads(storage.current_summary_path.read_text(encoding="utf-8"))
    history = storage.load_history(now=NOW)
    assert saved_run["status"] == "completed"
    assert saved_summary == {
        "rating_confidence": "initial",
        "last_run_at": NOW.isoformat(),
        "fund": {"healthy": 1, "usable": 0, "degraded": 0, "failed": 0},
        "news": {"healthy": 0, "usable": 0, "degraded": 0, "failed": 1},
        "total_sources": 2,
        "reclaimable_bytes": 321,
    }
    assert len(history) == 2
    assert {row["run_id"] for row in history} == {run["run_id"]}
    assert service.list_sources(group="news", rating="failed")[0]["source_id"] == "news:n1"
    service.shutdown()


def test_quick_refresh_merges_critical_rows_without_dropping_full_snapshot(tmp_path):
    critical_id = "fund:critical:profile"
    news_id = "news:noncritical"
    runner = ScopedRunner(
        full_rows=[
            observation(critical_id),
            observation(news_id, group="news"),
        ],
        quick_rows=[observation(critical_id, status="failure", rating="failed")],
    )
    storage = SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW)
    service = SourceHealthService(runner=runner, storage=storage, now=lambda: NOW)

    full = service.start_run("full")
    wait_for(service, full["run_id"])
    quick = service.schedule_quick_if_due()
    assert quick is not None
    wait_for(service, quick["run_id"])

    sources = {row["source_id"]: row for row in service.list_sources()}
    summary = service.get_summary()
    assert set(sources) == {critical_id, news_id}
    assert sources[critical_id]["rating"] == "failed"
    assert sources[news_id]["rating"] == "healthy"
    assert summary["total_sources"] == 2
    assert summary["fund"]["failed"] == 1
    assert summary["news"]["healthy"] == 1
    service.shutdown()


def test_run_stays_running_until_persistence_succeeds_or_becomes_failed(tmp_path):
    class FailingSnapshotStorage(SourceHealthStorage):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.persistence_reached = threading.Event()
            self.release = threading.Event()

        def write_current_snapshot(self, document):
            self.persistence_reached.set()
            self.release.wait(timeout=2)
            raise OSError("snapshot unavailable")

    storage = FailingSnapshotStorage(root=tmp_path / "source-health", now=lambda: NOW)
    service = SourceHealthService(runner=ImmediateRunner(), storage=storage, now=lambda: NOW)

    run = service.start_run("full")
    assert storage.persistence_reached.wait(timeout=1)
    assert service.get_run(run["run_id"])["status"] == "running"
    assert service.get_summary()["total_sources"] == 0
    assert service.list_sources() == []

    storage.release.set()
    failed = wait_for(service, run["run_id"], status="failed")
    assert failed["status"] == "failed"
    assert service.get_summary()["total_sources"] == 0
    assert service.list_sources() == []
    service.shutdown()


def test_authoritative_snapshot_is_durable_before_completed_last_run_marker(tmp_path):
    class RecordingStorage(SourceHealthStorage):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.durable = []

        def write_current_summary(self, document):
            path = super().write_current_summary(document)
            self.durable.append("summary")
            return path

        def write_current_snapshot(self, document):
            path = super().write_current_snapshot(document)
            self.durable.append("snapshot")
            return path

        def write_last_run(self, document):
            path = super().write_last_run(document)
            self.durable.append(f"last-run:{document['status']}")
            return path

    storage = RecordingStorage(root=tmp_path / "source-health", now=lambda: NOW)
    service = SourceHealthService(runner=ImmediateRunner(), storage=storage, now=lambda: NOW)

    run = service.start_run("full")
    wait_for(service, run["run_id"])

    assert storage.durable == ["summary", "snapshot", "last-run:completed"]
    service.shutdown()


def test_authoritative_snapshot_wins_over_failed_last_run_for_same_run_after_restart(tmp_path):
    class FailCompletedLastRunStorage(SourceHealthStorage):
        def write_last_run(self, document):
            if document.get("status") == "completed":
                raise OSError("completed marker unavailable")
            return super().write_last_run(document)

    storage = FailCompletedLastRunStorage(root=tmp_path / "source-health", now=lambda: NOW)
    first = SourceHealthService(runner=ImmediateRunner(), storage=storage, now=lambda: NOW)

    run = first.start_run("full")
    wait_for(first, run["run_id"], status="failed")
    assert first.get_summary()["total_sources"] == 0
    first.shutdown()

    restarted = SourceHealthService(runner=ImmediateRunner(), storage=storage, now=lambda: NOW)

    assert restarted.get_summary()["total_sources"] == 1
    assert restarted.list_sources()[0]["source_id"] == "fund:p1:profile"
    assert restarted.get_run(run["run_id"])["status"] == "completed"

    distinct_run_id = "d" * 20
    storage.write_last_run({"run_id": distinct_run_id, "scope": "full", "status": "failed"})
    with_distinct_last_run = SourceHealthService(runner=ImmediateRunner(), storage=storage, now=lambda: NOW)
    assert with_distinct_last_run.get_run(run["run_id"])["status"] == "completed"
    assert with_distinct_last_run.get_run(distinct_run_id)["status"] == "failed"
    restarted.shutdown()
    with_distinct_last_run.shutdown()


def test_quick_admission_is_durable_before_background_run_completes(tmp_path):
    runner = BlockingRunner()
    storage = SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW)
    first = SourceHealthService(runner=runner, storage=storage, now=lambda: NOW)

    admitted = first.schedule_quick_if_due()
    assert admitted is not None
    assert runner.started.wait(timeout=1)
    admission = json.loads(storage.quick_admission_path.read_text(encoding="utf-8"))
    assert admission == {"run_id": admitted["run_id"], "scheduled_at": NOW.isoformat()}

    restarted = SourceHealthService(runner=ImmediateRunner(), storage=storage, now=lambda: NOW)
    assert restarted.schedule_quick_if_due() is None

    runner.release.set()
    wait_for(first, admitted["run_id"])
    first.shutdown()
    restarted.shutdown()


def test_completed_source_snapshot_restores_summary_and_sources_after_restart(tmp_path):
    rows = [
        observation("fund:persisted:profile"),
        observation("news:persisted", group="news", status="failure", rating="failed"),
    ]
    storage = SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW)
    first = SourceHealthService(runner=ImmediateRunner(rows), storage=storage, now=lambda: NOW)
    run = first.start_run("full")
    wait_for(first, run["run_id"])
    expected_summary = first.get_summary()
    first.shutdown()

    restarted = SourceHealthService(runner=ImmediateRunner(), storage=storage, now=lambda: NOW)

    assert restarted.get_summary() == expected_summary
    assert {row["source_id"] for row in restarted.list_sources()} == {
        "fund:persisted:profile", "news:persisted",
    }
    assert restarted.list_sources(group="news", rating="failed")[0]["source_id"] == "news:persisted"
    assert "sources" not in restarted.get_summary()
    assert "sources" not in json.loads(storage.current_summary_path.read_text(encoding="utf-8"))
    restarted.shutdown()


def test_service_never_reads_fund_portfolio_user_data(monkeypatch, tmp_path):
    real_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path.name == "fund-portfolio.json":
            raise AssertionError("source health must not read user portfolio data")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    service = SourceHealthService(
        runner=ImmediateRunner(),
        storage=SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW),
        now=lambda: NOW,
    )

    run = service.start_run("full")
    wait_for(service, run["run_id"])
    service.shutdown()


def test_source_health_api_returns_202_409_and_404(monkeypatch, tmp_path):
    runner = BlockingRunner()
    service = SourceHealthService(
        runner=runner,
        storage=SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW),
        now=lambda: NOW,
    )
    monkeypatch.setattr(app_module.source_health, "get_service", lambda: service)
    client = TestClient(app_module.app)

    response = client.post("/api/source-health/runs", json={"scope": "full"})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert len(run_id) == 20
    assert client.post("/api/source-health/runs", json={"scope": "full"}).json() == {
        "detail": "数据源体检正在运行",
    }
    assert client.post("/api/source-health/runs", json={"scope": "full"}).status_code == 409
    assert client.get("/api/source-health/runs/missing").status_code == 404

    runner.release.set()
    wait_for(service, run_id)
    assert client.get(f"/api/source-health/runs/{run_id}").json()["status"] == "completed"
    assert client.get("/api/source-health/summary").status_code == 200
    assert client.get("/api/source-health/sources", params={"group": "fund", "rating": "healthy"}).status_code == 200
    service.shutdown()


def test_lifespan_schedules_quick_without_waiting_and_shutdowns(monkeypatch):
    calls = []

    class Service:
        def schedule_quick_if_due(self):
            calls.append("schedule")
            return {"run_id": "a" * 20}

        def shutdown(self):
            calls.append("shutdown")

    monkeypatch.setenv("VR_SOURCE_HEALTH_STARTUP", "1")
    monkeypatch.setattr(app_module.source_health, "get_service", lambda: Service())
    monkeypatch.setattr(app_module, "_run_startup_cache_cleanup", lambda: calls.append("cache"))

    with TestClient(app_module.app) as client:
        assert client.get("/api/health").status_code == 200

    assert calls == ["cache", "schedule", "shutdown"]


def test_lifespan_can_disable_quick_and_startup_failure_never_blocks(monkeypatch):
    calls = []

    class Service:
        def schedule_quick_if_due(self):
            calls.append("schedule")
            raise RuntimeError("offline")

        def shutdown(self):
            calls.append("shutdown")

    service = Service()
    monkeypatch.setattr(app_module.source_health, "get_service", lambda: service)
    monkeypatch.setattr(app_module, "_run_startup_cache_cleanup", lambda: None)
    monkeypatch.setenv("VR_SOURCE_HEALTH_STARTUP", "0")
    with TestClient(app_module.app) as client:
        assert client.get("/api/health").status_code == 200
    assert calls == ["shutdown"]

    monkeypatch.setenv("VR_SOURCE_HEALTH_STARTUP", "1")
    with TestClient(app_module.app) as client:
        assert client.get("/api/health").status_code == 200
    assert calls == ["shutdown", "schedule", "shutdown"]


def test_lifespan_service_construction_failure_never_blocks_api(monkeypatch):
    monkeypatch.setattr(
        app_module.source_health,
        "get_service",
        lambda: (_ for _ in ()).throw(RuntimeError("broken source-health state")),
    )
    monkeypatch.setattr(app_module, "_run_startup_cache_cleanup", lambda: None)

    with TestClient(app_module.app) as client:
        assert client.get("/api/health").status_code == 200


def test_history_survives_restart_and_exposes_recent_success_and_failure_streak(tmp_path):
    clock = [NOW - timedelta(days=1)]
    storage = SourceHealthStorage(root=tmp_path / "source-health", now=lambda: clock[0])
    source = SourceDescriptor(
        source_id="fund:runtime:profile", source_name="runtime", group="fund", capability="profile",
        source_reference="https://public.example.test/profile", priority=10, critical=True,
        requires_api_key=False, probe_kind="provider", probe_args={"code": "000001"},
    )

    class RuntimeProvider:
        name = "runtime"

    def make_runner(status):
        raw = {
            "status": status,
            "source_name": "runtime",
            "error_type": "none" if status == "success" else "timeout",
            "error_message_redacted": "",
            "latency_ms": 1,
            "returned_items": 1 if status == "success" else 0,
            "field_completeness_pct": 100.0 if status == "success" else 0.0,
        }
        return SourceHealthRunner(
            [source], providers=[RuntimeProvider()], news_sources={},
            provider_probe=lambda *_args, **_kwargs: raw, now=lambda: clock[0],
        )

    first = SourceHealthService(runner=make_runner("success"), storage=storage, now=lambda: clock[0])
    first_run = first.start_run("full")
    wait_for(first, first_run["run_id"])
    first.shutdown()

    clock[0] = NOW
    restarted = SourceHealthService(runner=make_runner("failure"), storage=storage, now=lambda: clock[0])
    second_run = restarted.start_run("full")
    wait_for(restarted, second_run["run_id"])
    row = restarted.list_sources()[0]

    assert row["consecutive_failures"] == 1
    assert row["last_success_at"] == (NOW - timedelta(days=1)).isoformat()
    assert row["rating_confidence"] == "initial"
    restarted.shutdown()


def test_history_container_rows_upgrade_source_and_summary_confidence_at_threshold(tmp_path):
    storage = SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW)
    source_id = "fund:p1:profile"
    observations = []
    for index in range(19):
        observed = NOW - timedelta(days=6 - index % 7)
        observations.append({
            "source_id": source_id,
            "probe_status": "success",
            "finished_at": observed.isoformat(),
        })
    storage.append_history({"run_id": "container", "summary": {}, "observations": observations}, observed_at=NOW)
    source = SourceDescriptor(
        source_id=source_id, source_name="p1", group="fund", capability="profile",
        source_reference="https://public.example.test/profile", priority=10, critical=True,
        requires_api_key=False, probe_kind="provider", probe_args={"code": "000001"},
    )

    class Provider:
        name = "p1"

    runner = SourceHealthRunner(
        [source], providers=[Provider()], news_sources={},
        provider_probe=lambda *_args, **_kwargs: {
            "status": "success", "source_name": "p1", "error_type": "none",
            "latency_ms": 1, "returned_items": 1, "field_completeness_pct": 100.0,
        },
        now=lambda: NOW,
    )
    service = SourceHealthService(runner=runner, storage=storage, now=lambda: NOW)

    run = service.start_run("full")
    wait_for(service, run["run_id"])

    assert service.list_sources()[0]["rating_confidence"] == "stable"
    assert service.get_summary()["rating_confidence"] == "stable"
    service.shutdown()
