from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading

from fastapi.testclient import TestClient
import pytest

import app as app_module
from industry_research import api as industry_api
from industry_research.models import (
    FundResolutionEmptyReason,
    FundSelectionScope,
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    ConflictingObservation,
    ConflictingSourceValue,
    IndustryFundRelationResolution,
    IndustryReportResponse,
    RefreshPhase,
    RefreshRun,
)
from industry_research.relationships import FundRelationProjection
from industry_research.service import IndustryResearchService
from industry_research.storage import IndustryResearchStorage


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
WRITE_HEADERS = {"X-PP03-Write-Intent": "1"}


def _report_response(*, demo: bool = False) -> IndustryReportResponse:
    report = IndustryResearchService(now=lambda: NOW, production=False).assemble_storage_report(
        trusted_snapshot_id="trusted-storage-old",
        raw_snapshot_id="raw-storage-old",
        evidence_snapshot_id="evidence-storage-old",
        generated_at=NOW,
        trusted_observations=(),
        metric_candidates=None,
        news_snapshot=None,
        demo=demo,
    ).report
    refresh = RefreshRun(
        industry_id="storage",
        run_id=None,
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
        candidate_snapshot_id=None,
        phase=RefreshPhase.IDLE,
        error_code=None,
        displayed_trusted_snapshot_id=report.trusted_snapshot_id,
        published_trusted_snapshot_id=None,
        displayed_raw_snapshot_id=report.raw_snapshot_id,
        displayed_evidence_snapshot_id=report.evidence_snapshot_id,
    )
    return IndustryReportResponse(
        requested_industry_id="storage",
        displayed_industry_id="storage",
        displayed_trusted_report=report,
        candidate_evidence=None,
        refresh_run=refresh,
    )


def _failed_refresh() -> RefreshRun:
    return RefreshRun(
        industry_id="storage",
        run_id="refresh-failed",
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
        candidate_snapshot_id=None,
        phase=RefreshPhase.FAILED,
        error_code="no_eligible_provider",
        displayed_trusted_snapshot_id="trusted-storage-old",
        published_trusted_snapshot_id=None,
        displayed_raw_snapshot_id="raw-storage-old",
        displayed_evidence_snapshot_id="evidence-storage-old",
    )


class RecordingService:
    def __init__(
        self,
        *,
        response: IndustryReportResponse | None = None,
        refresh_result: RefreshRun | None = None,
        refresh_eligible: bool = True,
    ) -> None:
        self.response = response or _report_response()
        self.refresh_result = refresh_result or _failed_refresh()
        self.refresh_eligible = refresh_eligible
        self.reads: list[tuple[str, int]] = []
        self.refreshes: list[str] = []
        self.resolutions: list[tuple[str, tuple[str, ...]]] = []
        self.provider_calls = 0
        self.ai_calls = 0
        self.default_database_reads = 0
        self.default_database_writes = 0
        self.cached_codes: list[str] = []
        self.snapshot_codes: list[str] = []
        self.shutdown_calls = 0
        self.active_tasks = 0
        self.termination_records: list[str] = []

    def read_report(self, industry_id: str, window_days: int) -> IndustryReportResponse:
        self.reads.append((industry_id, window_days))
        return self.response

    def has_qualified_refresh(self, industry_id: str) -> bool:
        return self.refresh_eligible and industry_id == "storage"

    def request_refresh(self, industry_id: str) -> RefreshRun:
        self.refreshes.append(industry_id)
        self.active_tasks += 1
        return self.refresh_result

    def resolve_fund_relations(
        self,
        industry_id: str,
        fund_codes: tuple[str, ...],
    ) -> FundRelationProjection:
        self.resolutions.append((industry_id, fund_codes))
        selection = tuple(
            FundSelectionScope(f"selection-{index}", code, True)
            for index, code in enumerate(fund_codes, start=1)
        )
        resolutions = tuple(
            IndustryFundRelationResolution(
                selection_id=item.selection_id,
                fund_code=item.fund_code,
                relation=None,
                empty_reason=FundResolutionEmptyReason.UNKNOWN,
            )
            for item in selection
        )
        state = "resolved" if selection else "no_holdings"
        return FundRelationProjection(state, selection, resolutions, ())

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self.active_tasks:
            self.termination_records.extend("completed" for _ in range(self.active_tasks))
        self.active_tasks = 0


def _client(service: RecordingService) -> TestClient:
    return TestClient(
        app_module.create_app(industry_research_service=service),
        base_url="http://127.0.0.1:8900",
    )


def test_get_is_read_only_and_passes_only_supported_windows() -> None:
    # Break caught: mounting or switching the report window implicitly queues a provider refresh.
    service = RecordingService()
    client = _client(service)

    for window in (7, 30, 90):
        response = client.get(f"/api/industry-research/storage?window_days={window}")
        assert response.status_code == 200
        assert response.json()["displayed_trusted_report"]["trusted_snapshot_id"] == "trusted-storage-old"

    assert service.reads == [("storage", 7), ("storage", 30), ("storage", 90)]
    assert service.refreshes == []
    assert service.provider_calls == 0


@pytest.mark.parametrize("window", [0, 6, 8, 29, 31, 89, 91, 365])
def test_get_rejects_unsupported_windows_without_touching_service(window: int) -> None:
    service = RecordingService()
    response = _client(service).get(
        f"/api/industry-research/storage?window_days={window}"
    )

    assert response.status_code == 422
    assert service.reads == []
    assert service.provider_calls == 0


def test_unknown_tag_is_building_empty_and_never_reads_another_industry() -> None:
    # Break caught: an unknown/custom tag falls back to storage or invokes AI to fill a report.
    service = RecordingService()
    response = _client(service).get(
        "/api/industry-research/custom-advanced-packaging?window_days=30"
    )

    assert response.status_code == 200
    assert response.json() == {
        "requested_industry_id": "custom-advanced-packaging",
        "displayed_industry_id": None,
        "displayed_trusted_report": None,
        "candidate_evidence": None,
        "refresh_run": {
            "industry_id": "custom-advanced-packaging",
            "run_id": None,
            "raw_snapshot_id": None,
            "evidence_snapshot_id": None,
            "candidate_snapshot_id": None,
            "phase": "idle",
            "error_code": None,
            "displayed_trusted_snapshot_id": None,
            "published_trusted_snapshot_id": None,
            "displayed_raw_snapshot_id": None,
            "displayed_evidence_snapshot_id": None,
        },
        "template_status": "building",
    }
    assert service.reads == []
    assert service.ai_calls == 0
    assert service.provider_calls == 0


def test_known_industry_empty_storage_is_200_without_network_or_refresh(tmp_path) -> None:
    storage_root = tmp_path / "industry-research"
    storage_root.mkdir()
    service = industry_api.ProductionIndustryResearchService(
        storage=IndustryResearchStorage(storage_root),
        now=lambda: NOW,
    )

    response = _client(service).get("/api/industry-research/storage?window_days=90")

    assert response.status_code == 200
    assert response.json()["template_status"] == "complete_layout"
    assert response.json()["displayed_trusted_report"] is None
    assert response.json()["refresh_run"]["phase"] == "idle"
    assert service.has_qualified_refresh("storage") is False


def test_conflicting_snapshot_stays_in_candidate_side_channel() -> None:
    base = _report_response()
    candidate = CandidateEvidencePanel(
        industry_id="storage",
        candidate_snapshot_id="candidate-conflict",
        counts=CandidateEvidenceCounts(0, 1, 0, 0),
        unverified=(),
        conflicting=(
            ConflictingObservation(
                industry_id="storage",
                metric_id="manufacturer_capex",
                aggregate_value=None,
                source_values=(
                    ConflictingSourceValue("evidence-a", "family-a", 1.0, "index", "2026-08-24"),
                    ConflictingSourceValue("evidence-b", "family-b", -1.0, "index", "2026-08-24"),
                ),
                raw_snapshot_id="raw-conflict",
                evidence_snapshot_id="evidence-conflict",
            ),
        ),
        unverified_events=(),
        conflicting_events=(),
        raw_snapshot_id="raw-conflict",
        evidence_snapshot_id="evidence-conflict",
    )
    response_model = IndustryReportResponse(
        requested_industry_id="storage",
        displayed_industry_id="storage",
        displayed_trusted_report=base.displayed_trusted_report,
        candidate_evidence=candidate,
        refresh_run=RefreshRun(
            industry_id="storage",
            run_id="refresh-conflict",
            raw_snapshot_id="raw-conflict",
            evidence_snapshot_id="evidence-conflict",
            candidate_snapshot_id="candidate-conflict",
            phase=RefreshPhase.VERIFYING,
            error_code=None,
            displayed_trusted_snapshot_id="trusted-storage-old",
            published_trusted_snapshot_id=None,
            displayed_raw_snapshot_id="raw-storage-old",
            displayed_evidence_snapshot_id="evidence-storage-old",
        ),
    )

    response = _client(RecordingService(response=response_model)).get(
        "/api/industry-research/storage"
    )

    assert response.status_code == 200
    document = response.json()
    assert document["candidate_evidence"]["conflicting"][0]["aggregate_value"] is None
    trusted_metrics = sum(
        (document["displayed_trusted_report"][section] for section in ("cycle", "metrics", "capital")),
        [],
    )
    assert all(metric["verification_status"] != "conflicting" for metric in trusted_metrics)
    assert all(metric["current_value"] not in {1.0, -1.0} for metric in trusted_metrics)


@pytest.mark.parametrize(
    "path",
    [
        "/api/industry-research/%2E%2E",
        "/api/industry-research/Storage",
        "/api/industry-research/storage%00other",
        "/api/industry-research/storage%2Fother",
        "/api/industry-research/-storage",
        "/api/industry-research/storage..other",
    ],
)
def test_invalid_or_traversal_industry_ids_return_400(path: str) -> None:
    service = RecordingService()
    response = _client(service).get(path, follow_redirects=False)

    assert response.status_code == 400
    assert service.reads == []
    assert service.refreshes == []


def test_refresh_requires_explicit_post_and_a_qualified_free_source() -> None:
    service = RecordingService(refresh_eligible=False)
    client = _client(service)

    assert client.get("/api/industry-research/storage").status_code == 200
    rejected = client.post(
        "/api/industry-research/storage/refresh",
        headers=WRITE_HEADERS,
    )

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "source_unconfigured"
    assert service.refreshes == []

    service.refresh_eligible = True
    queued = client.post(
        "/api/industry-research/storage/refresh",
        headers=WRITE_HEADERS,
    )
    assert queued.status_code == 202
    assert queued.json()["phase"] == "failed"
    assert queued.json()["displayed_trusted_snapshot_id"] == "trusted-storage-old"
    assert service.refreshes == ["storage"]


def test_refresh_endpoint_is_protected_by_the_existing_local_write_gate() -> None:
    service = RecordingService()
    response = _client(service).post("/api/industry-research/storage/refresh")

    assert response.status_code == 403
    assert service.refreshes == []


def test_failed_refresh_keeps_the_whole_previous_trusted_snapshot() -> None:
    service = RecordingService(refresh_result=_failed_refresh())
    client = _client(service)

    refresh = client.post(
        "/api/industry-research/storage/refresh",
        headers=WRITE_HEADERS,
    )
    after = client.get("/api/industry-research/storage")

    assert refresh.status_code == 202
    assert refresh.json()["published_trusted_snapshot_id"] is None
    assert refresh.json()["displayed_trusted_snapshot_id"] == "trusted-storage-old"
    assert after.status_code == 200
    assert after.json()["displayed_trusted_report"]["trusted_snapshot_id"] == "trusted-storage-old"


def test_default_factory_rejects_demo_but_explicit_injection_allows_acceptance_demo(
    monkeypatch,
) -> None:
    # Break caught: an environment/default production path can surface demo fixtures.
    demo_service = RecordingService(response=_report_response(demo=True))
    monkeypatch.setattr(
        industry_api,
        "create_production_industry_research_service",
        lambda: demo_service,
    )

    production = TestClient(
        app_module.create_app(),
        base_url="http://127.0.0.1:8900",
    ).get("/api/industry-research/storage")
    acceptance = _client(demo_service).get("/api/industry-research/storage")

    assert production.status_code == 503
    assert production.json()["detail"] == "demo_fixture_rejected"
    assert acceptance.status_code == 200
    assert acceptance.json()["displayed_trusted_report"]["demo"] is True


def test_app_shutdown_drains_service_tasks_and_leaves_no_industry_workers() -> None:
    service = RecordingService()
    application = app_module.create_app(industry_research_service=service)

    with TestClient(application, base_url="http://127.0.0.1:8900") as client:
        response = client.post(
            "/api/industry-research/storage/refresh",
            headers=WRITE_HEADERS,
        )
        assert response.status_code == 202
        assert service.active_tasks == 1

    assert service.shutdown_calls == 1
    assert service.active_tasks == 0
    assert service.termination_records == ["completed"]
    assert not [thread for thread in threading.enumerate() if thread.name.startswith("industry-refresh")]


def test_app_factory_does_not_restart_the_legacy_global_scheduler(monkeypatch) -> None:
    def forbidden_scheduler(*_args, **_kwargs):
        raise AssertionError("create_app must not restart the module-level scheduler")

    monkeypatch.setattr(app_module.pf, "start_scheduler", forbidden_scheduler)

    first = app_module.create_app(industry_research_service=RecordingService())
    second = app_module.create_app(industry_research_service=RecordingService())

    assert first is not second


def test_fund_resolution_is_body_only_no_store_and_private_field_closed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Break caught: explicit fund codes leak into URL/log/cache/snapshots or trigger default DB reads.
    service = RecordingService()
    client = _client(service)
    caplog.set_level(logging.DEBUG)

    response = client.post(
        "/api/industry-research/storage/fund-relations/resolve",
        headers=WRITE_HEADERS,
        json={"fund_codes": ["900001", "900002"]},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.request.url.path == "/api/industry-research/storage/fund-relations/resolve"
    assert "900001" not in str(response.request.url)
    assert service.resolutions == [("storage", ("900001", "900002"))]
    assert service.default_database_reads == 0
    assert service.default_database_writes == 0
    assert service.cached_codes == []
    assert service.snapshot_codes == []
    assert "900001" not in caplog.text
    assert "900002" not in caplog.text
    forbidden = {
        "amount", "cost", "note", "notes", "account", "cookie", "key",
        "api_key", "token", "authorization", "password", "shares",
    }

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value), set())
        return set()

    assert not (keys(response.json()) & forbidden)


def test_fund_resolution_rejects_private_or_malformed_input_before_service() -> None:
    service = RecordingService()
    client = _client(service)

    private = client.post(
        "/api/industry-research/storage/fund-relations/resolve",
        headers=WRITE_HEADERS,
        json={"fund_codes": ["900001"], "account": "private"},
    )
    malformed = client.post(
        "/api/industry-research/storage/fund-relations/resolve",
        headers=WRITE_HEADERS,
        json={"fund_codes": ["storage-fund", "123"]},
    )

    assert private.status_code == 422
    assert malformed.status_code == 422
    assert private.headers["cache-control"] == "no-store"
    assert malformed.headers["cache-control"] == "no-store"
    assert service.resolutions == []


def test_service_cannot_return_private_fund_fields(monkeypatch) -> None:
    service = RecordingService()

    def unsafe(*_args, **_kwargs):
        return {
            "state": "resolved",
            "fund_selection": [],
            "resolutions": [],
            "pending_lookthrough_selection_ids": [],
            "account": "private",
        }

    monkeypatch.setattr(service, "resolve_fund_relations", unsafe)
    response = _client(service).post(
        "/api/industry-research/storage/fund-relations/resolve",
        headers=WRITE_HEADERS,
        json={"fund_codes": ["900001"]},
    )

    assert response.status_code == 502
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text


def test_legacy_industry_route_and_health_remain_registered() -> None:
    service = RecordingService()
    client = _client(service)

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/industry?top=2").status_code == 422
