from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os

import pytest

from industry_research.models import (
    ConclusionStatus,
    DataCompleteness,
    DisplayedTrustedReport,
    ReportCounts,
    SourceCoverage,
    TemplateStatus,
    render_conclusion_text,
)
from industry_research.storage import IndustryResearchStorage


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def report(snapshot_id: str, *, industry_id: str = "storage") -> DisplayedTrustedReport:
    conclusion_values = {
        "conclusion_id": f"conclusion-{snapshot_id}",
        "industry_id": industry_id,
        "rule_version": "storage-cycle-v1",
        "status": ConclusionStatus.UNAVAILABLE,
        "cycle_stage": None,
        "outlook_direction": None,
        "confidence_level": None,
        "data_completeness": DataCompleteness(0, 0, None),
        "basis_metric_ids": (),
        "evidence_ids": (),
        "invalidating_conditions": ("新可信证据可用",),
    }
    from industry_research.models import IndustryConclusion

    return DisplayedTrustedReport(
        industry_id=industry_id,
        template_status=TemplateStatus.COMPLETE_LAYOUT,
        trusted_snapshot_id=snapshot_id,
        displayed_trusted_snapshot_id=snapshot_id,
        generated_at=NOW.isoformat(),
        demo=False,
        source_coverage=SourceCoverage("capability", 0, 0, 0, 0, 0, 0),
        counts=ReportCounts(0, 0),
        overview=IndustryConclusion(
            **conclusion_values,
            text=render_conclusion_text(**conclusion_values),
        ),
        cycle=(),
        chain=(),
        metrics=(),
        capital=(),
        companies=(),
        fund_selection=(),
        funds=(),
        news_risk=(),
    )


def publish(storage: IndustryResearchStorage, value: DisplayedTrustedReport):
    return storage.publish(
        value,
        expected_industry_id=value.industry_id,
        expected_raw_snapshot_id="raw-storage-1",
        expected_evidence_snapshot_id="evidence-storage-1",
    )


def test_trusted_snapshot_round_trips_as_one_checksummed_report(tmp_path) -> None:
    # Break caught: sections are stored independently or load without checksum verification.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    expected = report("trusted-storage-1")

    result = publish(storage, expected)
    document = json.loads(storage.trusted_snapshot_path("storage").read_text(encoding="utf-8"))

    assert result.displayed_report == expected
    assert result.published_trusted_snapshot_id == "trusted-storage-1"
    assert document["report"]["trusted_snapshot_id"] == "trusted-storage-1"
    assert len(document["checksum"]) == 64
    assert storage.load_current("storage") == expected


def test_refresh_write_failure_returns_and_preserves_previous_complete_snapshot(tmp_path, monkeypatch) -> None:
    # Break caught: a failed final replace exposes the new partial report or deletes the old one.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    old = report("trusted-storage-old")
    publish(storage, old)
    old_bytes = storage.trusted_snapshot_path("storage").read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("injected final replace failure")

    monkeypatch.setattr("industry_research.storage._replace_durable", fail_replace)
    result = publish(storage, report("trusted-storage-new"))

    assert result.published_trusted_snapshot_id is None
    assert result.previous_trusted_snapshot_id == "trusted-storage-old"
    assert result.displayed_report == old
    assert result.error_code == "storage_error"
    assert storage.trusted_snapshot_path("storage").read_bytes() == old_bytes
    assert not list(storage.trusted_snapshot_path("storage").parent.glob("*.tmp"))


def test_missing_required_section_does_not_replace_previous_snapshot(tmp_path) -> None:
    # Break caught: a refresh missing one report section becomes the displayed report.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    old = report("trusted-storage-old")
    publish(storage, old)
    old_bytes = storage.trusted_snapshot_path("storage").read_bytes()
    incomplete = report("trusted-storage-incomplete").to_dict()
    del incomplete["news_risk"]

    with pytest.raises(ValueError, match="required report sections"):
        storage.publish_document(
            incomplete,
            expected_industry_id="storage",
            expected_raw_snapshot_id="raw-storage-1",
            expected_evidence_snapshot_id="evidence-storage-1",
        )

    assert storage.trusted_snapshot_path("storage").read_bytes() == old_bytes
    assert storage.load_current("storage") == old


def test_checksum_tampering_fails_closed_instead_of_loading_mixed_content(tmp_path) -> None:
    # Break caught: edited report content is accepted under an old checksum.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    publish(storage, report("trusted-storage-1"))
    path = storage.trusted_snapshot_path("storage")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["report"]["generated_at"] = "2099-01-01T00:00:00+00:00"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert storage.load_current("storage") is None


def test_industry_directories_are_isolated_and_cross_industry_publish_is_rejected(tmp_path) -> None:
    # Break caught: one industry's current pointer can be reused for another report.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    publish(storage, report("trusted-storage-1"))

    assert storage.trusted_snapshot_path("storage").parent.name == "storage"
    assert storage.trusted_snapshot_path("robotics").parent.name == "robotics"
    with pytest.raises(ValueError, match="industry_id"):
        storage.publish_document(
            report("trusted-storage-2").to_dict(),
            expected_industry_id="robotics",
            expected_raw_snapshot_id="raw-robotics-1",
            expected_evidence_snapshot_id="evidence-robotics-1",
        )


def test_report_count_or_snapshot_identity_mismatch_is_rejected_before_write(tmp_path) -> None:
    # Break caught: metadata identifies one snapshot while the report displays another.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    mismatched = replace(report("trusted-storage-1"), displayed_trusted_snapshot_id="trusted-storage-0")

    with pytest.raises(ValueError, match="snapshot identity"):
        publish(storage, mismatched)
    assert not storage.trusted_snapshot_path("storage").exists()


def test_publish_requires_and_checks_expected_refresh_lineage_before_write(tmp_path) -> None:
    # Break caught: a complete-looking report publishes under a stale/wrong refresh lineage.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    expected = report("trusted-storage-1")

    with pytest.raises(TypeError):
        storage.publish(expected)
    with pytest.raises(ValueError, match="industry_id"):
        storage.publish(
            expected,
            expected_industry_id="robotics",
            expected_raw_snapshot_id="raw-robotics-1",
            expected_evidence_snapshot_id="evidence-robotics-1",
        )
    for invalid_raw_id in ("", " raw-storage-1 "):
        with pytest.raises(ValueError, match="lineage"):
            storage.publish(
                expected,
                expected_industry_id="storage",
                expected_raw_snapshot_id=invalid_raw_id,
                expected_evidence_snapshot_id="evidence-storage-1",
            )

    assert not storage.trusted_snapshot_path("storage").exists()


def test_default_production_storage_rejects_demo_report_but_isolated_mode_can_publish(tmp_path) -> None:
    # Break caught: isolated fixture data is persisted as ordinary production truth.
    demo = replace(report("trusted-demo-1"), demo=True)
    production = IndustryResearchStorage(root=tmp_path / "production")

    with pytest.raises(ValueError, match="demo"):
        publish(production, demo)

    isolated = IndustryResearchStorage(root=tmp_path / "isolated", production=False)
    assert publish(isolated, demo).displayed_report == demo
    assert IndustryResearchStorage(root=tmp_path / "isolated").load_current("storage") is None


def test_industry_directory_symlink_cannot_redirect_trusted_snapshot_write(tmp_path) -> None:
    # Break caught: an industry directory junction/symlink redirects trusted data outside the root.
    root = tmp_path / "industry"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        os.symlink(outside, root / "storage", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    storage = IndustryResearchStorage(root=root)

    with pytest.raises(OSError, match="storage_error"):
        publish(storage, report("trusted-storage-1"))

    assert not (outside / "trusted_snapshot.json").exists()


def test_deep_malformed_json_load_fails_closed(tmp_path) -> None:
    # Break caught: a deeply nested malformed cache escapes fail-closed parsing with RecursionError.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    path = storage.trusted_snapshot_path("storage")
    path.parent.mkdir(parents=True)
    path.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")

    assert storage.load_current("storage") is None
