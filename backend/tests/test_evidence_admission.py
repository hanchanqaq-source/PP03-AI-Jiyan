from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from evidence_verification.models import (
    EvidenceEvent,
    EvidenceSnapshot,
    FieldVerificationStatus,
    KeyField,
    VerificationStatus,
)
from evidence_verification.service import EvidenceVerificationService
from evidence_verification.storage import EvidenceStorage


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def market_event(event_id: str):
    return SimpleNamespace(
        event_id=event_id,
        title="星河科技拟投资12亿元建设算力中心",
        summary="项目金额12亿元，建设周期三年。",
        impact_tendency="positive",
        impact_basis=["投资12亿元"],
    )


def evidence_event(event_id: str, status: VerificationStatus) -> EvidenceEvent:
    return EvidenceEvent(
        event_id=event_id,
        title="星河科技拟投资12亿元建设算力中心",
        summary="项目金额12亿元，建设周期三年。",
        category="company",
        related_tags=(),
        published_at=NOW,
        core_claim="星河科技拟投资建设算力中心",
        verification_status=status,
        verification_reason=f"reason-{status.value}",
        verified_at=NOW,
        evidence_as_of=NOW,
        key_fields=(
            KeyField("money", "12亿元", "CNY:1200000000", FieldVerificationStatus.UNVERIFIED),
            KeyField("build_cycle", "建设周期三年", "3:year", FieldVerificationStatus.VERIFIED, ("official",), "官方一致"),
        ),
    )


def test_admission_keeps_only_trusted_current_versions_and_removes_unverified_fields(tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    trusted_id, pending_id, corrected_id = "a" * 20, "b" * 20, "c" * 20
    storage.publish(EvidenceSnapshot("d" * 20, NOW, (
        evidence_event(trusted_id, VerificationStatus.VERIFIED),
        evidence_event(pending_id, VerificationStatus.UNVERIFIED),
        evidence_event(corrected_id, VerificationStatus.CORRECTED),
    )))
    service = EvidenceVerificationService(storage=storage, event_loader=lambda: [], now=lambda: NOW)

    snapshot_id, admitted = service.admit([
        market_event(trusted_id), market_event(pending_id), market_event(corrected_id),
    ])

    assert snapshot_id == "d" * 20
    assert [row.event_id for row in admitted] == [trusted_id]
    assert admitted[0].verification_status == "verified"
    assert "12亿元" not in admitted[0].title
    assert "12亿元" not in admitted[0].summary
    assert admitted[0].impact_basis == []
    assert admitted[0].verified_key_fields == [{
        "field_name": "build_cycle",
        "raw_value": "建设周期三年",
        "normalized_value": "3:year",
        "verification_status": "verified",
        "evidence_ids": ["official"],
        "reason": "官方一致",
    }]


def test_missing_snapshot_admits_nothing_instead_of_falling_back_to_raw_news(tmp_path):
    service = EvidenceVerificationService(
        storage=EvidenceStorage(root=tmp_path / "empty", now=lambda: NOW),
        event_loader=lambda: [],
        now=lambda: NOW,
    )

    snapshot_id, admitted = service.admit([market_event("a" * 20)])

    assert snapshot_id == "unavailable"
    assert admitted == []
