from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import threading
import time

import pytest

import industry_research.refresh as refresh_module
from data_sources.catalog import build_catalog
from data_sources.models import BillingModel, CatalogStatus, ProviderValue
from data_sources.provider_errors import ProviderUnavailable
from evidence_verification.models import (
    EvidenceEvent,
    EvidenceItem,
    EvidenceSnapshot,
    SourceRole,
    VerificationStatus as A2VerificationStatus,
)
from evidence_verification.storage import EvidenceStorage
from industry_research.admission import EvidenceDecision, RawMetricObservation, SourceIdentity
from industry_research.models import RefreshPhase
from industry_research.refresh import IndustryResearchRefreshOrchestrator, RefreshRawSnapshot
from industry_research.service import IndustryResearchService
from industry_research.source_qualification import SourceQualificationResult
from industry_research.storage import IndustryResearchStorage
from news_intelligence.models import NewsSourceItem


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


def _qualification(**changes: object) -> SourceQualificationResult:
    value = SourceQualificationResult(
        source_identity="trendforce_public_price",
        request_url="https://www.trendforce.com/price/dram/dram_spot",
        final_url="https://www.trendforce.com/price/dram/dram_spot",
        http_status=200,
        response_cap_bytes=500_000,
        response_bytes=480,
        target_fields=("product", "session_average", "date"),
        observed_response_fields=("product", "session_average", "date"),
        field_shape="html_table:product,session_average,date",
        data_date_field="date",
        data_date=date(2026, 8, 24),
        unit="USD",
        frequency="current_snapshot",
        license_conclusion="verified_public_current_snapshot",
        failure_modes=(
            "structure_changed",
            "missing_data_date",
            "missing_unit",
            "login_required",
            "cookie_required",
            "member_download",
            "response_too_large",
            "redirect_disallowed",
            "license_unverified",
        ),
        failure_reason=None,
        login_required=False,
        cookie_required=False,
        member_download=False,
    )
    return replace(value, **changes)


def _descriptor(adapter_id: str = "trendforce-public-price", **changes: object):
    value = replace(
        build_catalog({"sources": []}).adapter("trendforce-public-price"),
        adapter_id=adapter_id,
        default_enabled=True,
        catalog_status=CatalogStatus.CONFIGURED,
    )
    return replace(value, **changes)


def _provider_value(adapter_id: str, value: float = 100.0) -> ProviderValue:
    return ProviderValue(
        value=value,
        source_family_id="trendforce_public_price",
        adapter_id=adapter_id,
        capability_id="industry_price_snapshot",
        as_of_date=date(2026, 8, 24),
        fetched_at=NOW,
        data_status="candidate_snapshot",
        license="verified_public_current_snapshot",
        priority=100,
        difference_from_primary=Decimal("0"),
        unit="USD",
        frequency="current_snapshot",
        source_metadata={"product": "dram_price"},
    )


class FakeCatalog:
    def __init__(self, *descriptors) -> None:
        self.adapters = descriptors


class FakeRegistry:
    def __init__(self, providers: dict[str, object]) -> None:
        self.providers = providers
        self.resolved: list[str] = []

    def adapter(self, adapter_id: str):
        self.resolved.append(adapter_id)
        return self.providers[adapter_id]


class FakeProvider:
    def __init__(self, descriptor, *, values=(), error: str | None = None) -> None:
        self.descriptor = descriptor
        self.values = tuple(values)
        self.error = error
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def fetch(self, request):
        assert request.capability_id == "industry_price_snapshot"
        assert request.parameters == {}
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.block:
                assert self.release.wait(5)
            if self.error:
                raise ProviderUnavailable(self.error)
            return self.values
        finally:
            with self._lock:
                self.active -= 1


class RawBuilder:
    def __init__(
        self,
        *,
        invalid: bool = False,
        official: bool = True,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.invalid = invalid
        self.official = official
        self.barrier = barrier
        self.calls: list[str] = []

    def __call__(self, industry_id: str, run_id: str, values: tuple[ProviderValue, ...]):
        self.calls.append(industry_id)
        if self.barrier is not None:
            self.barrier.wait(5)
        observations = []
        for index, value in enumerate(values):
            publisher = "www.sec.gov" if self.official else f"publisher{index}.example"
            source = NewsSourceItem(
                source_name="deterministic-public-fixture",
                source_url=(
                    "https://www.sec.gov/Archives/edgar/data/1/"
                    if self.official else "https://collector.example/feed"
                ),
                original_url=f"https://{publisher}/Archives/{industry_id}/dram-{index}.htm",
                published_at=NOW,
                fetched_at=NOW,
                title="DRAM public snapshot",
                summary="Deterministic public snapshot fixture.",
                language="en",
                region="global",
                track_key=industry_id,
                track_name=industry_id,
                category="industry",
                normalized_title="dram public snapshot",
                tokens=frozenset({"dram", "snapshot"}),
                anchors=frozenset({"dram"}),
                related_tags=((industry_id, industry_id),),
                text_related_tags=((industry_id, industry_id),),
                source_domain=publisher,
                data_status="current_snapshot",
            )
            identity = SourceIdentity(source_family_id=value.source_family_id, source=source)
            observation = RawMetricObservation(
                industry_id=industry_id,
                metric_id="dram_price",
                label="DRAM 价格",
                provider_value=value,
                identity=identity,
                decision=EvidenceDecision(event_id=f"event-{run_id}", evidence_id=f"evidence-{run_id}-{index}"),
                expires_at=NOW + timedelta(days=1),
                methodology="Deterministic current snapshot fixture.",
                judgment_basis=("Injected public-provider value.",),
                invalidating_conditions=("Provider correction or expiry.",),
            )
            observations.append(
                replace(observation, industry_id="robotics") if self.invalid else observation
            )
        return RefreshRawSnapshot(
            industry_id=industry_id,
            raw_snapshot_id=f"raw-{run_id}",
            observations=tuple(observations),
        )


class EvidenceVerifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, raw: RefreshRawSnapshot) -> EvidenceSnapshot:
        self.calls.append(raw.industry_id)
        if self.fail:
            raise ValueError("verification failed")
        distinct = len({row.provider_value.value for row in raw.observations}) > 1
        evidence = []
        for index, row in enumerate(raw.observations):
            contradicts = distinct and index > 0
            evidence.append(EvidenceItem(
                evidence_id=row.decision.evidence_id,
                content_source=row.identity.content_source,
                collector_source=row.identity.collector_source,
                canonical_url=row.identity.final_url,
                published_at=NOW,
                source_role=(
                    SourceRole.PRIMARY if row.identity.is_official_attested else SourceRole.INDEPENDENT
                ),
                origin_cluster=row.identity.origin_cluster,
                supports_claim=not contradicts,
                supports_fields=(row.metric_id,) if not contradicts else (),
                contradicts_claim=contradicts,
                is_official=row.identity.is_official_attested,
                title="Deterministic evidence fixture",
                excerpt="Deterministic evidence fixture.",
            ))
        supporting = tuple(item for item in evidence if not item.contradicts_claim)
        contradicting = tuple(item for item in evidence if item.contradicts_claim)
        official = any(row.identity.is_official_attested for row in raw.observations)
        event = EvidenceEvent(
            event_id=raw.observations[0].decision.event_id,
            title="Deterministic evidence fixture",
            summary="Deterministic evidence fixture.",
            category="industry",
            related_tags=((raw.industry_id, raw.industry_id),),
            published_at=NOW,
            core_claim="dram_price",
            verification_status=(
                A2VerificationStatus.CONFLICTING
                if contradicting
                else A2VerificationStatus.VERIFIED
                if official
                else A2VerificationStatus.UNVERIFIED
            ),
            verification_reason="deterministic fixture",
            verified_at=NOW,
            evidence_as_of=NOW,
            primary_evidence=supporting if official else (),
            independent_evidence=() if official else supporting,
            contradicting_evidence=contradicting,
        )
        return EvidenceSnapshot(
            snapshot_id=f"evidence-{raw.raw_snapshot_id}",
            raw_snapshot_id=raw.raw_snapshot_id,
            generated_at=NOW,
            events=(event,),
        )


class BlockingEvidenceVerifier(EvidenceVerifier):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, raw: RefreshRawSnapshot) -> EvidenceSnapshot:
        self.started.set()
        assert self.release.wait(5)
        return super().__call__(raw)


class FailingService:
    production = True

    def assemble_storage_report(self, **_kwargs):
        raise ValueError("assembly failed")


class RecordingStorage(IndustryResearchStorage):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.publish_calls = 0

    def publish(self, *args, **kwargs):
        self.publish_calls += 1
        return super().publish(*args, **kwargs)


def _seed_old_report(storage: IndustryResearchStorage) -> None:
    service = IndustryResearchService(now=lambda: NOW)
    old = service.assemble_storage_report(
        trusted_snapshot_id="trusted-old",
        raw_snapshot_id="raw-old",
        evidence_snapshot_id="evidence-old",
        generated_at=NOW,
        trusted_observations=(),
        metric_candidates=None,
        news_snapshot=None,
    ).report
    result = storage.publish(
        old,
        expected_industry_id="storage",
        expected_raw_snapshot_id="raw-old",
        expected_evidence_snapshot_id="evidence-old",
    )
    assert result.published_trusted_snapshot_id == "trusted-old"


def _orchestrator(
    tmp_path,
    *,
    descriptors=None,
    providers=None,
    qualifications=None,
    raw_builder=None,
    evidence_verifier=None,
    report_service=None,
    report_storage=None,
    max_workers: int = 2,
    run_ids=None,
):
    descriptor = _descriptor()
    descriptors = tuple(descriptors or (descriptor,))
    providers = providers or {
        descriptor.adapter_id: FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    }
    qualifications = qualifications or {row.adapter_id: _qualification() for row in descriptors}
    ids = iter(run_ids or (f"run-{index}" for index in range(100)))
    evidence_root = tmp_path / "canonical-evidence"
    return IndustryResearchRefreshOrchestrator(
        catalog=FakeCatalog(*descriptors),
        provider_registry=FakeRegistry(providers),
        qualifications=qualifications,
        raw_builder=raw_builder or RawBuilder(),
        evidence_verifier=evidence_verifier or EvidenceVerifier(),
        evidence_storage_factory=lambda industry_id, run_id: EvidenceStorage(
            evidence_root / industry_id / run_id
        ),
        report_service=report_service or IndustryResearchService(now=lambda: NOW),
        report_storage=report_storage or RecordingStorage(root=tmp_path / "reports"),
        state_root=tmp_path / "run-state",
        max_workers=max_workers,
        now=lambda: NOW,
        run_id_factory=lambda: next(ids),
    )


def test_get_current_history_and_construction_never_trigger_refresh(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(tmp_path, providers={descriptor.adapter_id: provider})

    assert orchestrator.current_run("storage") is None
    assert orchestrator.current_candidate("storage") is None
    assert provider.calls == 0
    orchestrator.shutdown()


def test_success_runs_provider_raw_canonical_evidence_admission_assembly_then_atomic_publish(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    provider.block = True
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("success",),
    )

    future = orchestrator.request_refresh("storage")
    assert provider.started.wait(2)
    during = orchestrator.current_run("storage")
    assert during is not None
    assert during.phase is RefreshPhase.COLLECTING
    assert during.displayed_trusted_snapshot_id == "trusted-old"
    assert during.published_trusted_snapshot_id is None
    assert storage.load_current("storage").trusted_snapshot_id == "trusted-old"

    provider.release.set()
    result = future.result(5)
    loaded = storage.load_current("storage")

    assert result.phase is RefreshPhase.TRUSTED_PUBLISHED
    assert result.industry_id == "storage"
    assert result.raw_snapshot_id == "raw-success"
    assert result.evidence_snapshot_id == "evidence-raw-success"
    assert result.candidate_snapshot_id == "candidate-success"
    assert result.published_trusted_snapshot_id == "trusted-success"
    assert result.displayed_trusted_snapshot_id == "trusted-success"
    assert loaded is not None
    assert (loaded.industry_id, loaded.raw_snapshot_id, loaded.evidence_snapshot_id) == (
        "storage", "raw-success", "evidence-raw-success"
    )
    candidate = orchestrator.current_candidate("storage")
    assert candidate is not None
    assert candidate.candidate_snapshot_id == "candidate-success"
    assert candidate.counts.unverified == 0
    assert loaded.cycle[0].metric_id == "dram_price"
    assert loaded.cycle[0].current_value == 100.0
    orchestrator.shutdown()


def test_unverified_only_candidate_never_replaces_the_previous_trusted_report(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        raw_builder=RawBuilder(official=False),
        report_storage=storage,
        run_ids=("unverified-only",),
    )

    result = orchestrator.request_refresh("storage").result(5)

    assert result.phase is RefreshPhase.FAILED
    assert result.error_code == "no_trusted_observations"
    assert result.published_trusted_snapshot_id is None
    assert result.displayed_trusted_snapshot_id == "trusted-old"
    assert storage.load_current("storage").trusted_snapshot_id == "trusted-old"
    candidate = orchestrator.current_candidate("storage")
    assert candidate is not None and candidate.counts.unverified == 1
    orchestrator.shutdown()


def test_same_industry_requests_return_the_same_future_and_call_provider_once(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    provider.block = True
    orchestrator = _orchestrator(tmp_path, providers={descriptor.adapter_id: provider})

    first = orchestrator.request_refresh("storage")
    assert provider.started.wait(2)
    second = orchestrator.request_refresh("storage")

    assert first is second
    provider.release.set()
    assert first.result(5).phase is RefreshPhase.TRUSTED_PUBLISHED
    assert provider.calls == 1
    orchestrator.shutdown()


def test_allowlist_never_resolves_or_calls_key_paid_login_cookie_or_member_providers(tmp_path) -> None:
    cases = {
        "good": (_descriptor("good"), _qualification()),
        "free-key": (_descriptor("free-key", billing_model=BillingModel.FREE_KEY), _qualification()),
        "paid": (_descriptor("paid", billing_model=BillingModel.PAID_API), _qualification()),
        "login": (_descriptor("login"), _qualification(login_required=True, failure_reason="login_required")),
        "cookie": (_descriptor("cookie"), _qualification(cookie_required=True, failure_reason="cookie_required")),
        "member": (_descriptor("member"), _qualification(member_download=True, failure_reason="member_download")),
    }
    providers = {
        name: FakeProvider(descriptor, values=(_provider_value(name),))
        for name, (descriptor, _qualification_value) in cases.items()
    }
    orchestrator = _orchestrator(
        tmp_path,
        descriptors=tuple(item[0] for item in cases.values()),
        providers=providers,
        qualifications={name: item[1] for name, item in cases.items()},
        run_ids=("allowlist",),
    )

    result = orchestrator.request_refresh("storage").result(5)

    assert result.phase is RefreshPhase.TRUSTED_PUBLISHED
    assert providers["good"].calls == 1
    for name in ("free-key", "paid", "login", "cookie", "member"):
        assert providers[name].calls == 0
    assert orchestrator.provider_resolution_ids == ("good",)
    orchestrator.shutdown()


def test_no_eligible_provider_fails_closed_without_registry_resolution(tmp_path) -> None:
    descriptor = _descriptor("paid", billing_model=BillingModel.PAID_API)
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        descriptors=(descriptor,),
        providers={descriptor.adapter_id: provider},
        qualifications={descriptor.adapter_id: _qualification()},
        report_storage=storage,
        run_ids=("no-eligible",),
    )

    result = orchestrator.request_refresh("storage").result(5)

    assert result.phase is RefreshPhase.FAILED
    assert result.error_code == "no_eligible_provider"
    assert result.published_trusted_snapshot_id is None
    assert result.displayed_trusted_snapshot_id == "trusted-old"
    assert provider.calls == 0
    assert orchestrator.provider_resolution_ids == ()
    assert storage.load_current("storage").trusted_snapshot_id == "trusted-old"
    assert orchestrator.current_candidate("storage") is None
    orchestrator.shutdown()


def test_evidence_verification_failure_retains_old_display_without_candidate(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        evidence_verifier=EvidenceVerifier(fail=True),
        report_storage=storage,
        run_ids=("evidence-failure",),
    )

    result = orchestrator.request_refresh("storage").result(5)

    assert result.phase is RefreshPhase.FAILED
    assert result.error_code == "evidence_verification_failed"
    assert result.raw_snapshot_id == "raw-evidence-failure"
    assert result.evidence_snapshot_id is None
    assert result.candidate_snapshot_id is None
    assert result.published_trusted_snapshot_id is None
    assert result.displayed_trusted_snapshot_id == "trusted-old"
    assert storage.load_current("storage").trusted_snapshot_id == "trusted-old"
    assert orchestrator.current_candidate("storage") is None
    orchestrator.shutdown()


@pytest.mark.parametrize(
    ("case", "expected_error", "expect_candidate"),
    [
        ("partial", "partial_source_failure", True),
        ("all", "all_sources_failed", False),
        ("conflict", "conflicting_evidence", True),
        ("admission", "admission_failed", True),
        ("assembly", "assembly_failed", True),
        ("write", "storage_error", True),
    ],
)
def test_failure_matrix_retains_old_display_and_never_publishes_candidate(
    tmp_path, monkeypatch, case, expected_error, expect_candidate
) -> None:
    first_descriptor = _descriptor("first")
    second_descriptor = _descriptor("second")
    descriptors = (first_descriptor, second_descriptor) if case in {"partial", "all"} else (first_descriptor,)
    first_values = (
        (_provider_value("first", 100.0), _provider_value("first", 110.0))
        if case == "conflict" else (_provider_value("first"),)
    )
    first_error = "source_failed" if case == "all" else None
    providers = {"first": FakeProvider(first_descriptor, values=first_values, error=first_error)}
    if case in {"partial", "all"}:
        providers["second"] = FakeProvider(
            second_descriptor,
            values=(_provider_value("second"),),
            error="source_failed",
        )
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    if case == "write":
        original = storage._atomic_write
        writes = 0

        def fail_new_current(path, payload):
            nonlocal writes
            writes += 1
            if path.name == "trusted_snapshot.json":
                raise OSError("storage_error")
            return original(path, payload)

        monkeypatch.setattr(storage, "_atomic_write", fail_new_current)
    raw_builder = RawBuilder(invalid=case == "admission")
    service = FailingService() if case == "assembly" else None
    orchestrator = _orchestrator(
        tmp_path,
        descriptors=descriptors,
        providers=providers,
        qualifications={row.adapter_id: _qualification() for row in descriptors},
        raw_builder=raw_builder,
        report_service=service,
        report_storage=storage,
        run_ids=(case,),
    )

    result = orchestrator.request_refresh("storage").result(5)
    loaded = storage.load_current("storage")

    assert result.phase is RefreshPhase.FAILED
    assert result.error_code == expected_error
    assert result.published_trusted_snapshot_id is None
    assert result.displayed_trusted_snapshot_id == "trusted-old"
    assert loaded is not None and loaded.trusted_snapshot_id == "trusted-old"
    candidate = orchestrator.current_candidate("storage")
    assert (candidate is not None) is expect_candidate
    if candidate is not None:
        assert candidate.candidate_snapshot_id == f"candidate-{case}"
        assert all(row.current_value is None for row in loaded.cycle)
    orchestrator.shutdown()


def test_cross_industry_work_is_bounded_and_run_state_never_crosses_industries(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    provider.block = True
    raw_builder = RawBuilder(invalid=True)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        raw_builder=raw_builder,
        max_workers=2,
        run_ids=("storage-run", "robotics-run", "semiconductor-run"),
    )

    storage_future = orchestrator.request_refresh("storage")
    robotics_future = orchestrator.request_refresh("robotics")
    semiconductor_future = orchestrator.request_refresh("semiconductor")
    deadline = time.monotonic() + 2
    while provider.calls < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert provider.calls == 2
    assert semiconductor_future.running() is False
    provider.release.set()
    storage_run = storage_future.result(5)
    robotics_run = robotics_future.result(5)
    semiconductor_run = semiconductor_future.result(5)

    assert provider.max_active == 2
    assert set(raw_builder.calls) == {"storage", "robotics", "semiconductor"}
    assert (storage_run.industry_id, storage_run.run_id, storage_run.error_code) == (
        "storage", "storage-run", "admission_failed"
    )
    assert (robotics_run.industry_id, robotics_run.run_id, robotics_run.error_code) == (
        "robotics", "robotics-run", "admission_failed"
    )
    assert (
        semiconductor_run.industry_id,
        semiconductor_run.run_id,
        semiconductor_run.error_code,
    ) == ("semiconductor", "semiconductor-run", "admission_failed")
    assert orchestrator.current_run("storage") == storage_run
    assert orchestrator.current_run("robotics") == robotics_run
    assert orchestrator.current_run("semiconductor") == semiconductor_run
    orchestrator.shutdown()


def test_run_and_candidate_state_are_checksum_bound_and_reload_without_provider_calls(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("persisted",),
    )
    completed = orchestrator.request_refresh("storage").result(5)
    candidate = orchestrator.current_candidate("storage")
    orchestrator.shutdown()

    reloaded = _orchestrator(tmp_path, providers={descriptor.adapter_id: provider})

    assert reloaded.current_run("storage") == completed
    assert reloaded.current_candidate("storage") == candidate
    assert provider.calls == 1
    reloaded.shutdown()


def test_postcommit_run_state_failure_never_reports_old_when_new_report_is_durable(
    tmp_path, monkeypatch
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("postcommit",),
    )
    original_write = orchestrator._state.write

    def fail_completed_state(run, candidate) -> None:
        if run.phase is RefreshPhase.TRUSTED_PUBLISHED:
            raise OSError("storage_error")
        original_write(run, candidate)

    monkeypatch.setattr(orchestrator._state, "write", fail_completed_state)

    result = orchestrator.request_refresh("storage").result(5)
    loaded = storage.load_current("storage")
    recovered = orchestrator.current_run("storage")

    assert loaded is not None and loaded.trusted_snapshot_id == "trusted-postcommit"
    assert result.phase is RefreshPhase.TRUSTED_PUBLISHED
    assert result.published_trusted_snapshot_id == "trusted-postcommit"
    assert recovered is not None and recovered.phase is RefreshPhase.TRUSTED_PUBLISHED
    assert recovered.displayed_trusted_snapshot_id == "trusted-postcommit"
    assert recovered.published_trusted_snapshot_id == "trusted-postcommit"
    orchestrator.shutdown()


def test_checksum_bound_state_reader_handles_short_regular_file_reads(tmp_path, monkeypatch) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("short-read",),
    )
    completed = orchestrator.request_refresh("storage").result(5)
    expected_candidate = orchestrator.current_candidate("storage")
    original_read = refresh_module.os.read

    def short_read(descriptor_number: int, size: int) -> bytes:
        return original_read(descriptor_number, min(size, 17))

    monkeypatch.setattr(refresh_module.os, "read", short_read)

    assert orchestrator.current_run("storage") == completed
    assert orchestrator.current_candidate("storage") == expected_candidate
    orchestrator.shutdown()


def test_refresh_state_read_fails_closed_when_parent_directory_identity_is_not_safe(
    tmp_path, monkeypatch
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("unsafe-parent",),
    )
    orchestrator.request_refresh("storage").result(5)

    def reject_parent(_path) -> None:
        raise OSError("storage_error")

    monkeypatch.setattr(orchestrator._state._writer, "_verify_parent", reject_parent)

    assert orchestrator.current_run("storage") is None
    assert orchestrator.current_candidate("storage") is None
    orchestrator.shutdown()


def test_refresh_state_read_rejects_checksum_valid_cross_industry_candidate(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("cross-state",),
    )
    completed = orchestrator.request_refresh("storage").result(5)
    candidate = orchestrator.current_candidate("storage")
    assert candidate is not None
    state = {"run": completed.to_dict(), "candidate": candidate.to_dict()}
    state["candidate"]["industry_id"] = "robotics"
    document = {
        "schema_version": 1,
        "checksum": refresh_module.hashlib.sha256(refresh_module._canonical(state)).hexdigest(),
        "state": state,
    }
    orchestrator._state._writer._atomic_write(
        orchestrator._state._path("storage"),
        refresh_module._canonical(document) + b"\n",
    )

    assert orchestrator.current_run("storage") is None
    assert orchestrator.current_candidate("storage") is None
    orchestrator.shutdown()


def test_shutdown_cancels_never_started_waits_for_running_and_blocks_late_publish(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    provider.block = True
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        max_workers=1,
        run_ids=("running", "queued"),
    )
    running = orchestrator.request_refresh("storage")
    assert provider.started.wait(2)
    queued = orchestrator.request_refresh("robotics")
    shutdown_thread = threading.Thread(target=orchestrator.shutdown)
    shutdown_thread.start()

    deadline = time.monotonic() + 2
    while not queued.cancelled() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert queued.cancelled()
    provider.release.set()
    shutdown_thread.join(5)

    assert not shutdown_thread.is_alive()
    with pytest.raises(CancelledError):
        queued.result()
    result = running.result(5)
    assert result.phase is RefreshPhase.FAILED
    assert result.error_code == "refresh_shutdown"
    assert result.published_trusted_snapshot_id is None
    assert storage.load_current("storage").trusted_snapshot_id == "trusted-old"
    assert orchestrator.current_run("robotics").error_code == "refresh_cancelled"
    assert not [thread for thread in threading.enumerate() if thread.name.startswith("industry-refresh")]
    with pytest.raises(RuntimeError, match="shut down"):
        orchestrator.request_refresh("storage")
    orchestrator.shutdown()


def test_shutdown_after_evidence_commit_retains_the_auditable_candidate_shell(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    verifier = BlockingEvidenceVerifier()
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        evidence_verifier=verifier,
        report_storage=storage,
        run_ids=("shutdown-verifying",),
    )
    future = orchestrator.request_refresh("storage")
    assert verifier.started.wait(2)
    shutdown_thread = threading.Thread(target=orchestrator.shutdown)
    shutdown_thread.start()
    verifier.release.set()
    shutdown_thread.join(5)

    result = future.result(5)
    candidate = orchestrator.current_candidate("storage")

    assert result.phase is RefreshPhase.FAILED
    assert result.error_code == "refresh_shutdown"
    assert result.candidate_snapshot_id == "candidate-shutdown-verifying"
    assert candidate is not None
    assert candidate.candidate_snapshot_id == result.candidate_snapshot_id
    assert candidate.raw_snapshot_id == result.raw_snapshot_id
    assert candidate.evidence_snapshot_id == result.evidence_snapshot_id
    assert storage.load_current("storage").trusted_snapshot_id == "trusted-old"
