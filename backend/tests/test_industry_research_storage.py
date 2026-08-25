from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os

import pytest

from industry_research.models import (
    AvailabilityStatus,
    ConclusionStatus,
    DataCompleteness,
    DisplayedTrustedReport,
    EmptyReason,
    EvidenceReference,
    FreshnessStatus,
    IndustryMetricObservation,
    ReportCounts,
    SourceCoverage,
    SourceRunStatus,
    TemplateStatus,
    render_conclusion_text,
    VerificationStatus,
)
from industry_research.storage import (
    IndustryResearchStorage,
    _canonical,
    _create_owned_temp,
    _replace_owned_temp,
)
from industry_research.templates import get_industry_template


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def empty_observation(metric_id: str, *, industry_id: str = "storage") -> IndustryMetricObservation:
    return IndustryMetricObservation(
        industry_id=industry_id,
        metric_id=metric_id,
        label=metric_id,
        current_value=None,
        unit=None,
        change=None,
        historical_position=None,
        availability_status=AvailabilityStatus.UNAVAILABLE,
        verification_status=VerificationStatus.NOT_EVALUATED,
        freshness_status=FreshnessStatus.UNKNOWN,
        source_run_status=SourceRunStatus.PARTIAL_FAILURE,
        empty_reason=EmptyReason.NO_RELIABLE_DATA,
        as_of_date=None,
        fetched_at=None,
        methodology="",
        judgment_basis=(),
        invalidating_conditions=(),
        evidence=(),
        independent_source_families=(),
        independent_content_sources=(),
        independent_origin_clusters=(),
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
    )


def report(snapshot_id: str, *, industry_id: str = "storage") -> DisplayedTrustedReport:
    template = get_industry_template(industry_id)
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
        source_coverage=SourceCoverage("capability", 8, 8, 0, 8, 0, 0),
        counts=ReportCounts(0, 0),
        overview=IndustryConclusion(
            **conclusion_values,
            text=render_conclusion_text(**conclusion_values),
        ),
        cycle=tuple(empty_observation(metric_id) for metric_id in template.cycle_metric_ids),
        chain=(),
        metrics=tuple(empty_observation(metric_id) for metric_id in template.core_metric_ids),
        capital=tuple(empty_observation(metric_id) for metric_id in template.capital_metric_ids),
        companies=(),
        fund_selection=(),
        funds=(),
        news_risk=(),
    )


def trusted_observation(metric_id: str, *, industry_id: str = "storage") -> IndustryMetricObservation:
    evidence = EvidenceReference(
        evidence_id=f"evidence-{metric_id}",
        source_family_id="official-family",
        content_source="sec.gov",
        origin_cluster="publisher:sec.gov",
        collector_source="sec.gov",
        final_url=f"https://sec.gov/{metric_id}",
        is_official=True,
        is_official_attested=True,
        supports_claim=True,
        supports_fields=(metric_id,),
        contradicts_claim=False,
        as_of_date="2026-08-25",
        verified_at=NOW.isoformat(),
    )
    return IndustryMetricObservation(
        industry_id=industry_id,
        metric_id=metric_id,
        label=metric_id,
        current_value=1,
        unit="index",
        change=None,
        historical_position=None,
        availability_status=AvailabilityStatus.AVAILABLE,
        verification_status=VerificationStatus.VERIFIED,
        freshness_status=FreshnessStatus.FRESH,
        source_run_status=SourceRunStatus.HEALTHY,
        empty_reason=None,
        as_of_date="2026-08-25",
        fetched_at=NOW.isoformat(),
        methodology="official publication",
        judgment_basis=("official source",),
        invalidating_conditions=("official correction",),
        evidence=(evidence,),
        independent_source_families=(),
        independent_content_sources=(),
        independent_origin_clusters=(),
        raw_snapshot_id="raw-storage-1",
        evidence_snapshot_id="evidence-storage-1",
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
    assert result.error_code is None, result.error_code
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

    def fail_replace(_descriptor, _source, _destination):
        raise OSError("injected final replace failure")

    monkeypatch.setattr("industry_research.storage._replace_owned_temp", fail_replace)
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


@pytest.mark.parametrize(
    ("section", "mutate"),
    (
        ("cycle", lambda rows: []),
        ("cycle", lambda rows: rows[:-1]),
        ("cycle", lambda rows: [rows[0], rows[0], *rows[2:]]),
        ("cycle", lambda rows: list(reversed(rows))),
        ("metrics", lambda rows: []),
        ("metrics", lambda rows: rows[:-1]),
        ("capital", lambda rows: []),
        ("capital", lambda rows: list(reversed(rows))),
    ),
)
def test_publish_document_rejects_noncanonical_metric_section_shape(
    tmp_path, section, mutate
) -> None:
    # Break caught: API/deserialization/checksum publication accepts a malformed fixed row set.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    document = report("trusted-storage-invalid-shape").to_dict()
    document[section] = mutate(document[section])

    with pytest.raises(ValueError, match="canonical metric rows"):
        storage.publish_document(
            document,
            expected_industry_id="storage",
            expected_raw_snapshot_id="raw-storage-1",
            expected_evidence_snapshot_id="evidence-storage-1",
        )

    assert not storage.trusted_snapshot_path("storage").exists()


def test_checksum_valid_load_still_rejects_noncanonical_metric_shape(tmp_path) -> None:
    # Break caught: a checksum-valid cached API document bypasses canonical row validation.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    publish(storage, report("trusted-storage-invalid-cached-shape"))
    path = storage.trusted_snapshot_path("storage")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["report"]["cycle"] = list(reversed(document["report"]["cycle"]))
    signed = {"lineage": document["lineage"], "report": document["report"]}
    document["checksum"] = hashlib.sha256(_canonical(signed)).hexdigest()
    path.write_text(json.dumps(document), encoding="utf-8")

    assert storage.load_current("storage") is None


def test_current_checksum_tampering_recovers_previous_complete_snapshot(tmp_path) -> None:
    # Break caught: current corruption destroys the only recoverable trusted report.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    old = report("trusted-storage-old")
    publish(storage, old)
    publish(storage, report("trusted-storage-current"))
    path = storage.trusted_snapshot_path("storage")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["report"]["generated_at"] = "2099-01-01T00:00:00+00:00"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert storage.load_current("storage") == old
    assert storage.previous_snapshot_path("storage").exists()


def test_corrupt_current_and_failed_publication_preserve_recoverable_previous(tmp_path, monkeypatch) -> None:
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    old = report("trusted-storage-old")
    publish(storage, old)
    publish(storage, report("trusted-storage-current"))
    storage.trusted_snapshot_path("storage").write_text("{}", encoding="utf-8")
    previous_bytes = storage.previous_snapshot_path("storage").read_bytes()

    monkeypatch.setattr("industry_research.storage._replace_owned_temp", lambda *_args: (_ for _ in ()).throw(OSError("injected")))
    result = publish(storage, report("trusted-storage-next"))

    assert result.displayed_report == old
    assert result.previous_trusted_snapshot_id == "trusted-storage-old"
    assert result.error_code == "storage_error"
    assert storage.previous_snapshot_path("storage").read_bytes() == previous_bytes
    assert storage.load_current("storage") == old


def test_storage_rejects_cross_template_metric_when_admission_is_bypassed(tmp_path) -> None:
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    cross_template = trusted_observation("orders")
    invalid = report("trusted-storage-cross-template").to_dict()
    invalid["counts"] = {"verified": 1, "corroborated": 0}
    invalid["cycle"][0] = cross_template.to_dict()

    with pytest.raises(ValueError, match="canonical metric rows"):
        storage.publish_document(
            invalid,
            expected_industry_id="storage",
            expected_raw_snapshot_id="raw-storage-1",
            expected_evidence_snapshot_id="evidence-storage-1",
        )
    assert not storage.trusted_snapshot_path("storage").exists()


def test_directory_identity_change_during_write_fails_closed_without_replacing_current(tmp_path, monkeypatch) -> None:
    # Break caught: a directory/junction swap after the early pathname check redirects replace.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    old = report("trusted-storage-old")
    publish(storage, old)
    old_bytes = storage.trusted_snapshot_path("storage").read_bytes()
    original = storage._directory_identity
    probes = 0

    def inject_identity_change(path):
        nonlocal probes
        identity = original(path)
        probes += 1
        if probes >= 5:
            return (*identity[:-1], identity[-1] + 1)
        return identity

    monkeypatch.setattr(storage, "_directory_identity", inject_identity_change)
    result = publish(storage, report("trusted-storage-new"))

    assert result.error_code == "storage_error"
    assert result.displayed_report == old
    assert storage.trusted_snapshot_path("storage").read_bytes() == old_bytes
    assert not list(storage.trusted_snapshot_path("storage").parent.glob("*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Win32 competing-handle regression")
def test_windows_owned_temp_rejects_competing_writer_delete_and_rename_handle(tmp_path) -> None:
    # Break caught: FILE_SHARE_WRITE/DELETE lets a rival alter the open temp after publish rename.
    import ctypes
    from ctypes import wintypes

    directory = tmp_path / "industry" / "storage"
    directory.mkdir(parents=True)
    destination = directory / "trusted_snapshot.json"
    descriptor, temp_path = _create_owned_temp(directory)
    attacker = None
    invalid_handle = ctypes.c_void_p(-1).value
    trusted_bytes = b"TRUSTED!"
    attacker_bytes = b"ATTACKER"
    try:
        assert os.write(descriptor, trusted_bytes) == len(trusted_bytes)
        os.fsync(descriptor)
        create_file = ctypes.windll.kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        attacker = create_file(
            str(temp_path),
            0x40000000 | 0x00010000,  # GENERIC_WRITE | DELETE (write/delete/rename authority)
            0x1 | 0x2 | 0x4,
            None,
            3,  # OPEN_EXISTING
            0x80,
            None,
        )
        attacker_opened = attacker != invalid_handle

        _replace_owned_temp(descriptor, temp_path, destination)
        if attacker_opened:
            set_pointer = ctypes.windll.kernel32.SetFilePointer
            set_pointer.argtypes = (wintypes.HANDLE, ctypes.c_long, wintypes.LPVOID, wintypes.DWORD)
            set_pointer.restype = wintypes.DWORD
            assert set_pointer(attacker, 0, None, 0) == 0
            written = wintypes.DWORD()
            write_file = ctypes.windll.kernel32.WriteFile
            write_file.argtypes = (
                wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
            )
            write_file.restype = wintypes.BOOL
            buffer = ctypes.create_string_buffer(attacker_bytes)
            assert write_file(attacker, buffer, len(attacker_bytes), ctypes.byref(written), None)
            assert written.value == len(attacker_bytes)
            assert ctypes.windll.kernel32.FlushFileBuffers(attacker)

        assert not attacker_opened
    finally:
        if attacker is not None and attacker != invalid_handle:
            ctypes.windll.kernel32.CloseHandle(attacker)
        os.close(descriptor)

    assert destination.read_bytes() == trusted_bytes


def test_postcommit_identity_failure_never_reports_old_when_new_current_is_durable(tmp_path, monkeypatch) -> None:
    # Break caught: a post-rename assertion returns storage_error/old while current is already new.
    storage = IndustryResearchStorage(root=tmp_path / "industry")
    old = report("trusted-storage-old")
    new = report("trusted-storage-new")
    publish(storage, old)
    current_path = storage.trusted_snapshot_path("storage")
    original_assert = storage._assert_directory_identities

    def fail_only_after_new_current_is_durable(identities):
        original_assert(identities)
        document = json.loads(current_path.read_text(encoding="utf-8"))
        if document["report"]["trusted_snapshot_id"] == "trusted-storage-new":
            raise OSError("injected postcommit identity failure")

    monkeypatch.setattr(storage, "_assert_directory_identities", fail_only_after_new_current_is_durable)
    result = publish(storage, new)
    persisted = storage.load_current("storage")

    assert persisted is not None
    assert persisted.trusted_snapshot_id == "trusted-storage-new"
    assert result.error_code is None
    assert result.published_trusted_snapshot_id == persisted.trusted_snapshot_id
    assert result.displayed_report == persisted


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
