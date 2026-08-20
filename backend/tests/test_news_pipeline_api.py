from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app as app_module
from news_pipeline.models import PipelineCounts, PipelinePhase, PipelineRun
from news_pipeline.service import NewsPipelineActiveError


client = TestClient(app_module.app)
NOW = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
WRITE_HEADERS = {
    "X-PP03-Write-Intent": "1",
    "Origin": "http://127.0.0.1:5899",
    "Host": "127.0.0.1:8900",
}


def run(phase: PipelinePhase = PipelinePhase.QUEUED) -> PipelineRun:
    return PipelineRun(
        run_id="run-api",
        raw_snapshot_id="raw-api",
        evidence_snapshot_id=None,
        trusted_snapshot_id=None,
        phase=phase,
        counts=PipelineCounts(),
        created_at=NOW,
        updated_at=NOW,
        redacted_error=None,
        displayed_trusted_snapshot_id="trusted-old",
    )


class FakePipeline:
    def __init__(self) -> None:
        self.starts = 0
        self.selected = []

    def start(self):
        self.starts += 1
        return run()

    def get_status(self, run_id=None):
        self.selected.append(run_id)
        return {
            "run_id": run_id or "run-api",
            "raw_snapshot_id": "raw-api",
            "phase": "verifying",
            "displayed_trusted_snapshot_id": "trusted-old",
            "counts": PipelineCounts().__dict__ if hasattr(PipelineCounts(), "__dict__") else {
                field: getattr(PipelineCounts(), field) for field in PipelineCounts.__dataclass_fields__
            },
        }


def test_all_refresh_compatibility_routes_start_the_same_async_pipeline(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr("news_pipeline.api.get_service", lambda: fake)

    responses = [
        client.post("/api/market-news/refresh?mode=global_tech&days=7", headers=WRITE_HEADERS),
        client.post("/api/evidence/refresh", headers=WRITE_HEADERS),
        client.post("/api/radar/refresh", headers=WRITE_HEADERS),
    ]

    assert [response.status_code for response in responses] == [202, 202, 202]
    assert [response.json()["data"] for response in responses] == [{
        "run_id": "run-api", "raw_snapshot_id": "raw-api", "phase": "queued",
    }] * 3
    assert fake.starts == 3


def test_active_refresh_conflict_is_shared_by_all_compatibility_routes(monkeypatch):
    class Active(FakePipeline):
        def start(self):
            raise NewsPipelineActiveError("news pipeline refresh is active")

    monkeypatch.setattr("news_pipeline.api.get_service", lambda: Active())

    response = client.post("/api/evidence/refresh", headers=WRITE_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"] == "资讯刷新正在运行"


def test_pipeline_status_returns_selected_run_and_displayed_trusted(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr("news_pipeline.api.get_service", lambda: fake)

    response = client.get("/api/news/pipeline-status?run_id=run-selected")

    assert response.status_code == 200
    assert response.json()["data"]["run_id"] == "run-selected"
    assert response.json()["data"]["displayed_trusted_snapshot_id"] == "trusted-old"
    assert fake.selected == ["run-selected"]


def test_pipeline_api_normalizes_runtime_failures_without_leaking_details(monkeypatch):
    class BrokenStart(FakePipeline):
        def start(self):
            raise OSError("C:\\Users\\private\\token.txt?api_key=secret")

    monkeypatch.setattr("news_pipeline.api.get_service", lambda: BrokenStart())
    response = client.post("/api/market-news/refresh", headers=WRITE_HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "资讯刷新暂时不可用"}
    assert "secret" not in response.text

    class BrokenStatus(FakePipeline):
        def get_status(self, run_id=None):
            raise RuntimeError("Authorization: Bearer private")

    monkeypatch.setattr("news_pipeline.api.get_service", lambda: BrokenStatus())
    response = client.get("/api/news/pipeline-status")
    assert response.status_code == 503
    assert response.json() == {"detail": "资讯刷新状态暂时不可用"}
    assert "private" not in response.text

    class CorruptSelectedStatus(FakePipeline):
        def get_status(self, run_id=None):
            raise OSError("storage_corrupt")

    monkeypatch.setattr("news_pipeline.api.get_service", lambda: CorruptSelectedStatus())
    response = client.get("/api/news/pipeline-status?run_id=run-corrupt")
    assert response.status_code == 503
    assert response.json() == {"detail": "资讯刷新状态暂时不可用"}


def test_pipeline_refresh_routes_reject_browser_simple_post_without_write_intent(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr("news_pipeline.api.get_service", lambda: fake)

    responses = [
        client.post(
            path,
            headers={"Origin": "http://127.0.0.1:5899", "Host": "127.0.0.1:8900"},
        )
        for path in (
            "/api/market-news/refresh",
            "/api/evidence/refresh",
            "/api/radar/refresh",
        )
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert fake.starts == 0


def test_pipeline_refresh_routes_reject_foreign_origin_even_with_write_intent(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr("news_pipeline.api.get_service", lambda: fake)

    responses = [
        client.post(
            path,
            headers={
                "X-PP03-Write-Intent": "1",
                "Origin": "https://foreign.example",
                "Host": "127.0.0.1:8900",
            },
        )
        for path in (
            "/api/market-news/refresh",
            "/api/evidence/refresh",
            "/api/radar/refresh",
        )
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert fake.starts == 0


def test_pipeline_refresh_allows_originless_loopback_nonbrowser_client_with_write_intent(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr("news_pipeline.api.get_service", lambda: fake)

    response = client.post(
        "/api/market-news/refresh",
        headers={
            "X-PP03-Write-Intent": "1",
            "Host": "127.0.0.1:8900",
        },
    )

    assert response.status_code == 202
    assert response.json()["data"] == {
        "run_id": "run-api", "raw_snapshot_id": "raw-api", "phase": "queued",
    }
    assert fake.starts == 1


def test_pipeline_refresh_preflight_requires_local_origin_and_write_intent(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr("news_pipeline.api.get_service", lambda: fake)
    headers = {
        "Origin": "http://127.0.0.1:5899",
        "Host": "127.0.0.1:8900",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type, x-pp03-write-intent",
    }

    for path in (
        "/api/market-news/refresh",
        "/api/evidence/refresh",
        "/api/radar/refresh",
    ):
        approved = client.options(path, headers=headers)
        missing_intent = client.options(
            path,
            headers={**headers, "Access-Control-Request-Headers": "content-type"},
        )
        foreign = client.options(
            path,
            headers={**headers, "Origin": "https://foreign.example"},
        )
        assert approved.status_code == 200
        assert approved.headers.get("access-control-allow-origin") == "http://127.0.0.1:5899"
        assert missing_intent.status_code == 403
        assert foreign.status_code == 403

    assert fake.starts == 0


def test_single_source_retry_requires_exact_local_write_authorization_before_handler(monkeypatch):
    calls: list[str] = []

    def retry_source(source_id: str):
        calls.append(source_id)
        return {
            "ok": False,
            "source_status": {
                "source_id": source_id,
                "status": "failed",
            },
        }

    monkeypatch.setattr(app_module.newsradar, "retry_source", retry_source)
    path = "/api/market-news/sources/0123456789abcdef/retry?mode=global_tech"

    approved = client.post(path, headers=WRITE_HEADERS)
    originless = client.post(
        path,
        headers={"X-PP03-Write-Intent": "1", "Host": "[::1]:8900"},
    )

    assert approved.status_code == 200
    assert originless.status_code == 200
    assert calls == ["0123456789abcdef", "0123456789abcdef"]


def test_single_source_retry_rejects_untrusted_posts_before_handler(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        app_module.newsradar,
        "retry_source",
        lambda source_id: calls.append(source_id) or {"ok": False, "source_status": None},
    )
    path = "/api/market-news/sources/0123456789abcdef/retry"
    rejected_headers = [
        {"Origin": "http://127.0.0.1:5899", "Host": "127.0.0.1:8900"},
        {
            "X-PP03-Write-Intent": "1",
            "Origin": "https://foreign.example",
            "Host": "127.0.0.1:8900",
        },
        {
            "X-PP03-Write-Intent": "1",
            "Origin": "http://127.0.0.1:5899",
            "Host": "public.example:8900",
        },
        {"Host": "127.0.0.1:8900"},
    ]

    responses = [client.post(path, headers=headers) for headers in rejected_headers]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]
    assert calls == []


def test_single_source_retry_rejects_ambiguous_duplicate_post_headers_before_handler(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        app_module.newsradar,
        "retry_source",
        lambda source_id: calls.append(source_id) or {"ok": False, "source_status": None},
    )
    path = "/api/market-news/sources/0123456789abcdef/retry"
    base = [
        ("Host", "127.0.0.1:8900"),
        ("X-PP03-Write-Intent", "1"),
    ]
    duplicate_header_sets = [
        [
            *base,
            ("Origin", "http://127.0.0.1:5899"),
            ("Origin", "https://foreign.example"),
        ],
        [
            *base,
            ("Origin", "http://127.0.0.1:5899"),
            ("Origin", "http://localhost:5899"),
        ],
        [
            ("Host", "127.0.0.1:8900"),
            ("X-PP03-Write-Intent", "1"),
            ("X-PP03-Write-Intent", "1"),
        ],
        [
            ("Host", "127.0.0.1:8900"),
            ("Host", "localhost:8900"),
            ("X-PP03-Write-Intent", "1"),
        ],
    ]

    responses = [client.post(path, headers=headers) for headers in duplicate_header_sets]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]
    assert calls == []


def test_single_source_retry_preflight_requires_exact_local_write_authorization(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        app_module.newsradar,
        "retry_source",
        lambda source_id: calls.append(source_id) or {"ok": False, "source_status": None},
    )
    path = "/api/market-news/sources/0123456789abcdef/retry"
    headers = {
        "Origin": "http://127.0.0.1:5899",
        "Host": "127.0.0.1:8900",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type, x-pp03-write-intent",
    }

    approved = client.options(path, headers=headers)
    missing_intent = client.options(
        path,
        headers={**headers, "Access-Control-Request-Headers": "content-type"},
    )
    foreign = client.options(path, headers={**headers, "Origin": "https://foreign.example"})
    malicious_host = client.options(path, headers={**headers, "Host": "public.example:8900"})

    assert approved.status_code == 200
    assert approved.headers.get("access-control-allow-origin") == "http://127.0.0.1:5899"
    assert [missing_intent.status_code, foreign.status_code, malicious_host.status_code] == [403, 403, 403]
    assert calls == []


def test_single_source_retry_preflight_rejects_missing_duplicate_or_non_post_method(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        app_module.newsradar,
        "retry_source",
        lambda source_id: calls.append(source_id) or {"ok": False, "source_status": None},
    )
    path = "/api/market-news/sources/0123456789abcdef/retry"
    base = [
        ("Origin", "http://127.0.0.1:5899"),
        ("Host", "127.0.0.1:8900"),
        ("Access-Control-Request-Headers", "content-type, x-pp03-write-intent"),
    ]
    rejected_header_sets = [
        base,
        [
            *base,
            ("Access-Control-Request-Method", "POST"),
            ("Access-Control-Request-Method", "POST"),
        ],
        [*base, ("Access-Control-Request-Method", "GET")],
        [*base, ("Access-Control-Request-Method", "PUT")],
    ]

    responses = [client.options(path, headers=headers) for headers in rejected_header_sets]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]
    assert calls == []


def test_single_source_retry_preflight_rejects_ambiguous_or_incomplete_headers_before_cors(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        app_module.newsradar,
        "retry_source",
        lambda source_id: calls.append(source_id) or {"ok": False, "source_status": None},
    )
    path = "/api/market-news/sources/0123456789abcdef/retry"
    base = [
        ("Origin", "http://127.0.0.1:5899"),
        ("Host", "127.0.0.1:8900"),
        ("Access-Control-Request-Method", "POST"),
        ("Access-Control-Request-Headers", "content-type, x-pp03-write-intent"),
    ]
    rejected_header_sets = [
        [
            *base,
            ("Origin", "https://foreign.example"),
        ],
        [
            ("Origin", "http://127.0.0.1:5899"),
            ("Host", "127.0.0.1:8900"),
            ("Host", "public.example:8900"),
            ("Access-Control-Request-Method", "POST"),
            ("Access-Control-Request-Headers", "content-type, x-pp03-write-intent"),
        ],
        [
            ("Origin", "http://127.0.0.1:5899"),
            ("Host", "public.example:8900"),
            ("Access-Control-Request-Method", "POST"),
        ],
        [
            ("Origin", "http://127.0.0.1:5899"),
            ("Host", "127.0.0.1:8900"),
            ("Access-Control-Request-Method", "POST"),
            ("Access-Control-Request-Headers", "content-type"),
        ],
        [
            ("Origin", "http://127.0.0.1:5899"),
            ("Host", "127.0.0.1:8900"),
            ("Access-Control-Request-Method", "POST"),
            ("Access-Control-Request-Headers", "content-type"),
            ("Access-Control-Request-Headers", "x-pp03-write-intent"),
        ],
    ]

    responses = [client.options(path, headers=headers) for headers in rejected_header_sets]

    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403]
    assert calls == []


def test_single_source_retry_write_path_match_is_bounded_to_the_canonical_route(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        app_module.newsradar,
        "retry_source",
        lambda source_id: calls.append(source_id) or {"ok": False, "source_status": None},
    )

    responses = [
        client.post(path, headers={"Origin": "https://foreign.example", "Host": "public.example"})
        for path in (
            "/api/market-news/sources/0123456789abcdef/retrying",
            "/api/market-news/sources/0123456789abcdeg/retry",
            "/api/market-news/sources/0123456789abcdef/retry/extra",
            "/api/market-news/sources/0123456789abcdef/retry.evil",
        )
    ]

    assert all(response.status_code in {404, 422} for response in responses)
    assert calls == []


def test_pipeline_read_routes_remain_readable_without_write_intent(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr("news_pipeline.api.get_service", lambda: fake)

    status_response = client.get(
        "/api/news/pipeline-status",
        headers={"Origin": "http://127.0.0.1:5899", "Host": "127.0.0.1:8900"},
    )
    health_response = client.get("/api/health", headers={"Origin": "https://foreign.example"})

    assert status_response.status_code == 200
    assert health_response.status_code == 200
    assert fake.starts == 0


def test_app_lifespan_closes_and_clears_pipeline_before_source_health(monkeypatch):
    calls = []

    class Pipeline:
        def recover_startup(self):
            calls.append("pipeline_recover")

        def close(self):
            calls.append("pipeline_close")

    class Health:
        def shutdown(self):
            calls.append("health_shutdown")

    pipeline = Pipeline()
    monkeypatch.setenv("VR_SOURCE_HEALTH_STARTUP", "0")
    monkeypatch.setattr(app_module, "_run_startup_cache_cleanup", lambda: calls.append("cache"))
    monkeypatch.setattr(app_module.source_health, "get_service", lambda: (calls.append("health_get"), Health())[1])
    monkeypatch.setattr(
        app_module.news_pipeline_service,
        "get_service",
        lambda: (calls.append("pipeline_get"), pipeline)[1],
    )
    monkeypatch.setattr(
        app_module.news_pipeline_service,
        "reset_service",
        lambda: calls.append("pipeline_reset"),
    )

    with TestClient(app_module.app) as lifespan_client:
        assert lifespan_client.get("/api/health").status_code == 200

    assert calls == [
        "cache",
        "health_get",
        "pipeline_get",
        "pipeline_recover",
        "pipeline_close",
        "pipeline_reset",
        "health_shutdown",
    ]
