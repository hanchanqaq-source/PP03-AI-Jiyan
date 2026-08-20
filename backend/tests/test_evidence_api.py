from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app as app_module
from evidence_verification.models import (
    EvidenceEvent,
    EvidenceItem,
    EvidenceSnapshot,
    FieldVerificationStatus,
    KeyField,
    SourceRole,
    StatusTransition,
    VerificationStatus,
)
from evidence_verification.service import EvidenceVerificationService
from evidence_verification.storage import EvidenceStorage


client = TestClient(app_module.app)
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def evidence_item(evidence_id: str, *, role: SourceRole, official: bool = False) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        content_source=f"{evidence_id}.example",
        collector_source=f"collector-{evidence_id}",
        canonical_url=f"https://{evidence_id}.example/report",
        published_at=NOW,
        source_role=role,
        origin_cluster=f"publisher:{evidence_id}.example",
        supports_claim=True,
        is_official=official,
        title="星河科技公告建设算力中心",
        excerpt="公开证据摘要",
    )


def event(index: int, status: VerificationStatus) -> EvidenceEvent:
    primary = (evidence_item(f"official-{index}", role=SourceRole.PRIMARY, official=True),) if status in {
        VerificationStatus.VERIFIED, VerificationStatus.CORRECTED, VerificationStatus.DISPROVED,
    } else ()
    independent = (
        evidence_item(f"independent-a-{index}", role=SourceRole.INDEPENDENT),
        evidence_item(f"independent-b-{index}", role=SourceRole.INDEPENDENT),
    ) if status == VerificationStatus.CORROBORATED else ()
    return EvidenceEvent(
        event_id=f"{index:020x}",
        title=f"星河科技事件 {index}",
        summary="公开摘要",
        category="company" if index % 2 else "industry",
        related_tags=(("semiconductor", "半导体"),),
        published_at=NOW,
        core_claim=f"星河科技核心主张 {index}",
        verification_status=status,
        verification_reason=f"reason-{status.value}",
        verified_at=NOW,
        evidence_as_of=NOW,
        key_fields=(
            KeyField("money", "12亿元", "CNY:1200000000", FieldVerificationStatus.VERIFIED, ("field-official",), "官方一致"),
            KeyField("date", "2026年8月18日", "2026-08-18", FieldVerificationStatus.UNVERIFIED),
        ),
        primary_evidence=primary,
        independent_evidence=independent,
        syndicated_copies=(evidence_item(f"copy-{index}", role=SourceRole.SYNDICATED),),
        status_history=(StatusTransition(None, status, NOW, f"reason-{status.value}"),),
        holding_relevance="industry_relation" if index == 1 else "none",
    )


def real_service(tmp_path) -> EvidenceVerificationService:
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    statuses = list(VerificationStatus)
    storage.publish(EvidenceSnapshot("a" * 20, NOW, tuple(event(index + 1, status) for index, status in enumerate(statuses))))
    return EvidenceVerificationService(storage=storage, event_loader=lambda: [], now=lambda: NOW)


def test_summary_and_filtered_list_return_real_snapshot_counts(monkeypatch, tmp_path):
    service = real_service(tmp_path)
    monkeypatch.setattr(app_module.evidence_service, "get_service", lambda: service)

    summary = client.get("/api/evidence/summary")
    listing = client.get(
        "/api/evidence/events?verification_status=verified&tag_id=semiconductor&category=company&days=7&holding_relevance=industry_relation"
    )

    assert summary.status_code == 200
    assert summary.json()["data"]["counts"] == {
        "verified": 1,
        "corroborated": 1,
        "unverified": 1,
        "conflicting": 1,
        "corrected": 1,
        "disproved": 1,
    }
    assert summary.json()["data"]["field_counts"] == {"verified": 6, "corroborated": 0, "unverified": 6, "conflicting": 0}
    assert [row["verification_status"] for row in listing.json()["data"]["events"]] == ["verified"]
    assert listing.json()["data"]["snapshot_id"] == "a" * 20
    row = listing.json()["data"]["events"][0]
    assert row["status_change_count"] == 1
    assert row["latest_transition"] == {
        "from_status": None,
        "to_status": "verified",
        "changed_at": NOW.isoformat(),
        "reason": "reason-verified",
    }


def test_detail_returns_complete_chains_fields_and_history(monkeypatch, tmp_path):
    service = real_service(tmp_path)
    monkeypatch.setattr(app_module.evidence_service, "get_service", lambda: service)
    target = event(2, VerificationStatus.CORROBORATED)

    response = client.get(f"/api/evidence/events/{target.event_id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["core_claim"] == target.core_claim
    assert len(data["key_fields"]) == 2
    assert len(data["independent_evidence"]) == 2
    assert len(data["syndicated_copies"]) == 1
    assert data["status_history"][0]["to_status"] == "corroborated"
    assert client.get(f"/api/evidence/events/{'f' * 20}").status_code == 404


def test_trusted_api_projection_keeps_unverified_amount_only_in_key_fields(monkeypatch, tmp_path):
    pending_amount = KeyField(
        "money",
        "12亿元",
        "CNY:1200000000",
        FieldVerificationStatus.UNVERIFIED,
        (),
        "金额尚无确定性证据",
    )
    target = replace(
        event(1, VerificationStatus.VERIFIED),
        title="交易所公告：星河科技建设存储算力中心 12亿元",
        summary="交易所公告确认星河科技建设存储算力中心，项目金额12亿元尚待核验。",
        core_claim="星河科技建设存储算力中心，涉及金额12亿元。",
        key_fields=(pending_amount,),
    )
    storage = EvidenceStorage(root=tmp_path / "projection", now=lambda: NOW)
    storage.publish(EvidenceSnapshot("b" * 20, NOW, (target,)))
    service = EvidenceVerificationService(storage=storage, event_loader=lambda: [], now=lambda: NOW)
    monkeypatch.setattr(app_module.evidence_service, "get_service", lambda: service)

    listing = client.get("/api/evidence/events?days=7").json()["data"]["events"][0]
    detail = client.get(f"/api/evidence/events/{target.event_id}").json()["data"]

    assert listing["title"] == "交易所公告：星河科技建设存储算力中心"
    assert "12亿元" not in listing["core_claim"]
    assert "12亿元" not in detail["title"]
    assert "12亿元" not in detail["summary"]
    assert "12亿元" not in detail["core_claim"]
    assert detail["key_fields"] == [{
        "field_name": "money",
        "raw_value": "12亿元",
        "normalized_value": "CNY:1200000000",
        "verification_status": "unverified",
        "evidence_ids": [],
        "reason": "金额尚无确定性证据",
    }]


def test_no_snapshot_is_explicitly_unloaded_and_does_not_invent_zero_counts(monkeypatch, tmp_path):
    service = EvidenceVerificationService(
        storage=EvidenceStorage(root=tmp_path / "empty", now=lambda: NOW),
        event_loader=lambda: [],
        now=lambda: NOW,
    )
    monkeypatch.setattr(app_module.evidence_service, "get_service", lambda: service)

    data = client.get("/api/evidence/summary").json()["data"]

    assert data["loaded"] is False
    assert data["counts"] is None
    assert data["field_counts"] is None


def test_async_refresh_kickoff_preserves_previous_evidence_snapshot(monkeypatch, tmp_path):
    service = real_service(tmp_path)
    monkeypatch.setattr(service, "_event_loader", lambda: (_ for _ in ()).throw(RuntimeError("upstream unavailable")))
    monkeypatch.setattr(app_module.evidence_service, "get_service", lambda: service)
    monkeypatch.setattr(
        "news_pipeline.api.get_service",
        lambda: SimpleNamespace(start=lambda: SimpleNamespace(
            run_id="run-evidence", raw_snapshot_id="raw-evidence", phase=SimpleNamespace(value="queued"),
        )),
    )

    started = client.post("/api/evidence/refresh")
    after = client.get("/api/evidence/summary")

    assert started.status_code == 202
    assert started.json()["data"] == {
        "run_id": "run-evidence", "raw_snapshot_id": "raw-evidence", "phase": "queued",
    }
    assert after.json()["data"]["snapshot_id"] == "a" * 20


def test_invalid_evidence_days_returns_422_instead_of_internal_error(monkeypatch, tmp_path):
    service = real_service(tmp_path)
    monkeypatch.setattr(app_module.evidence_service, "get_service", lambda: service)
    no_raise_client = TestClient(app_module.app, raise_server_exceptions=False)

    response = no_raise_client.get("/api/evidence/events?days=2")

    assert response.status_code == 422
    assert response.json()["detail"] == "days must be one of 1, 3, 7, 30"
