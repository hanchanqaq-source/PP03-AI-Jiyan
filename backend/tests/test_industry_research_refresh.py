from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import threading
import time
from types import SimpleNamespace

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
from industry_research.api import ProductionIndustryResearchService
from industry_research.models import CandidateExternalLineage, RefreshPhase, RefreshRun
from industry_research.refresh import (
    IndustryResearchRefreshOrchestrator,
    RefreshRawSnapshot,
    load_canonical_a2_news_snapshot,
    load_canonical_a2_news_snapshot_by_raw,
)
from industry_research.service import IndustryResearchService
from industry_research.source_qualification import SourceQualificationResult
from industry_research.storage import IndustryResearchStorage
from news_intelligence.models import NewsSourceItem
from news_pipeline.models import RawSnapshot, TrustedSnapshot


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


def _provider_value(
    adapter_id: str,
    value: float = 100.0,
    *,
    industry_id: str = "storage",
) -> ProviderValue:
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
        source_metadata={"product": "dram_price", "industry_id": industry_id},
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
    def __init__(
        self,
        descriptor,
        *,
        values=(),
        error: str | None = None,
        echo_request_industry: bool = False,
    ) -> None:
        self.descriptor = descriptor
        self.values = tuple(values)
        self.error = error
        self.echo_request_industry = echo_request_industry
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.active = 0
        self.max_active = 0
        self.requested_industries: list[str] = []
        self._lock = threading.Lock()

    def fetch(self, request):
        assert request.capability_id == "industry_price_snapshot"
        assert type(request.parameters) is dict
        self.requested_industries.append(request.parameters.get("industry_id"))
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
            if self.echo_request_industry:
                industry_id = request.parameters["industry_id"]
                return tuple(replace(
                    value,
                    source_metadata={**dict(value.source_metadata), "industry_id": industry_id},
                ) for value in self.values)
            return self.values
        finally:
            with self._lock:
                self.active -= 1


class RawBuilder:
    def __init__(
        self,
        *,
        invalid: bool = False,
        raise_error: bool = False,
        official: bool = True,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.invalid = invalid
        self.raise_error = raise_error
        self.official = official
        self.barrier = barrier
        self.calls: list[str] = []

    def __call__(self, industry_id: str, run_id: str, values: tuple[ProviderValue, ...]):
        self.calls.append(industry_id)
        if self.raise_error:
            raise ValueError("raw transformation failed")
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
        self.publish_prepared_calls = 0

    def publish(self, *args, **kwargs):
        self.publish_calls += 1
        return super().publish(*args, **kwargs)

    def publish_prepared(self, *args, **kwargs):
        self.publish_prepared_calls += 1
        return super().publish_prepared(*args, **kwargs)


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
    attempt_tokens=None,
    canonical_news_snapshot_loader=None,
    canonical_news_snapshot_lookup=None,
    company_candidate_loader=None,
):
    descriptor = _descriptor()
    descriptors = tuple(descriptors or (descriptor,))
    providers = providers or {
        descriptor.adapter_id: FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    }
    qualifications = qualifications or {row.adapter_id: _qualification() for row in descriptors}
    ids = iter(run_ids or (f"run-{index}" for index in range(100)))
    tokens = iter(attempt_tokens) if attempt_tokens is not None else None
    evidence_root = tmp_path / "canonical-evidence"
    kwargs = (
        {"attempt_token_factory": lambda: next(tokens)}
        if tokens is not None
        else {}
    )
    if canonical_news_snapshot_loader is None and canonical_news_snapshot_lookup is None:
        canonical_snapshot = _zero_candidate_news_snapshot()
        kwargs["canonical_news_snapshot_loader"] = lambda _industry_id: canonical_snapshot
        kwargs["canonical_news_snapshot_lookup"] = lambda raw_id: (
            canonical_snapshot if raw_id == canonical_snapshot.raw_snapshot_id else None
        )
    elif canonical_news_snapshot_loader is not None:
        kwargs["canonical_news_snapshot_loader"] = canonical_news_snapshot_loader
    if canonical_news_snapshot_lookup is not None:
        kwargs["canonical_news_snapshot_lookup"] = canonical_news_snapshot_lookup
    if company_candidate_loader is not None:
        kwargs["company_candidate_loader"] = company_candidate_loader
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
        **kwargs,
    )


def _a2_news_event(event_id: str, status: A2VerificationStatus, days_ago: int) -> EvidenceEvent:
    primary = EvidenceItem(
        evidence_id=f"{event_id}-primary",
        content_source="official-news.example",
        collector_source="official-news.example",
        canonical_url=f"https://official-news.example/{event_id}",
        published_at=NOW - timedelta(days=days_ago),
        source_role=SourceRole.PRIMARY,
        origin_cluster="official-news",
        supports_claim=True,
        supports_fields=("core_claim",),
        contradicts_claim=False,
        is_official=True,
    )
    independent = EvidenceItem(
        evidence_id=f"{event_id}-independent",
        content_source="independent-news.example",
        collector_source="independent-news.example",
        canonical_url=f"https://independent-news.example/{event_id}",
        published_at=NOW - timedelta(days=days_ago),
        source_role=SourceRole.INDEPENDENT,
        origin_cluster="independent-news",
        supports_claim=True,
        supports_fields=("core_claim",),
        contradicts_claim=False,
        is_official=False,
    )
    contradiction = EvidenceItem(
        evidence_id=f"{event_id}-contradiction",
        content_source="contradiction-news.example",
        collector_source="contradiction-news.example",
        canonical_url=f"https://contradiction-news.example/{event_id}",
        published_at=NOW - timedelta(days=days_ago),
        source_role=SourceRole.INDEPENDENT,
        origin_cluster="contradiction-news",
        supports_claim=False,
        supports_fields=(),
        contradicts_claim=True,
        is_official=False,
    )
    return EvidenceEvent(
        event_id=event_id,
        title=event_id,
        summary=event_id,
        category="industry",
        related_tags=(("storage", "存储"),),
        published_at=NOW - timedelta(days=days_ago),
        core_claim=event_id,
        verification_status=status,
        verification_reason="canonical A2 fixture",
        verified_at=NOW,
        evidence_as_of=NOW - timedelta(days=days_ago),
        primary_evidence=(primary,),
        independent_evidence=(independent,) if status is A2VerificationStatus.CORROBORATED else (),
        contradicting_evidence=(contradiction,) if status is A2VerificationStatus.CONFLICTING else (),
    )


def _canonical_news_snapshot() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id="news-evidence-production",
        raw_snapshot_id="news-raw-production",
        generated_at=NOW,
        events=(
            _a2_news_event("news-verified-3", A2VerificationStatus.VERIFIED, 3),
            _a2_news_event("news-corroborated-20", A2VerificationStatus.CORROBORATED, 20),
            _a2_news_event("news-unverified-2", A2VerificationStatus.UNVERIFIED, 2),
            _a2_news_event("news-conflicting-40", A2VerificationStatus.CONFLICTING, 40),
        ),
    )


def _zero_candidate_news_snapshot(
    *, snapshot_id: str = "news-evidence-zero", raw_snapshot_id: str = "news-raw-zero",
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        snapshot_id=snapshot_id,
        raw_snapshot_id=raw_snapshot_id,
        generated_at=NOW,
        events=(),
    )


def test_default_a2_news_loader_requires_complete_shared_raw_lineage(monkeypatch) -> None:
    snapshot = _canonical_news_snapshot()
    raw = RawSnapshot(snapshot.raw_snapshot_id, NOW, ())
    trusted = TrustedSnapshot(snapshot.raw_snapshot_id, NOW, ())
    service = SimpleNamespace(current_trusted_context=lambda: (trusted, raw, snapshot))
    monkeypatch.setattr("news_pipeline.service.get_service", lambda: service)

    assert load_canonical_a2_news_snapshot("storage") is snapshot

    mismatched = SimpleNamespace(current_trusted_context=lambda: (
        TrustedSnapshot("foreign-raw", NOW, ()), raw, snapshot,
    ))
    monkeypatch.setattr("news_pipeline.service.get_service", lambda: mismatched)
    assert load_canonical_a2_news_snapshot("storage") is None


def test_default_a2_durable_lookup_reads_exact_historical_raw_lineage(monkeypatch) -> None:
    snapshot = _canonical_news_snapshot()
    raw = RawSnapshot(snapshot.raw_snapshot_id, NOW, ())
    trusted = TrustedSnapshot(snapshot.raw_snapshot_id, NOW, ())
    storage = SimpleNamespace(
        load_raw=lambda raw_id: raw if raw_id == snapshot.raw_snapshot_id else None,
        load_evidence=lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None,
        load_trusted=lambda raw_id: trusted if raw_id == snapshot.raw_snapshot_id else None,
    )
    monkeypatch.setattr(
        "news_pipeline.service.get_service",
        lambda: SimpleNamespace(storage=storage),
    )

    assert load_canonical_a2_news_snapshot_by_raw(snapshot.raw_snapshot_id) is snapshot
    assert load_canonical_a2_news_snapshot_by_raw("missing-raw") is None


def test_get_current_history_and_construction_never_trigger_refresh(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(
        descriptor,
        values=(_provider_value(descriptor.adapter_id),),
    )
    orchestrator = _orchestrator(tmp_path, providers={descriptor.adapter_id: provider})

    assert orchestrator.current_run("storage") is None
    assert orchestrator.current_candidate("storage") is None
    assert provider.calls == 0
    orchestrator.shutdown()


def test_refresh_publishes_canonical_a2_news_and_projected_official_companies(tmp_path) -> None:
    storage = RecordingStorage(root=tmp_path / "reports")
    news_calls: list[str] = []
    company_calls: list[tuple[str, str]] = []

    def load_news(industry_id: str) -> EvidenceSnapshot:
        news_calls.append(industry_id)
        return _canonical_news_snapshot()

    def load_companies(industry_id: str, evidence: EvidenceSnapshot):
        company_calls.append((industry_id, evidence.snapshot_id))
        return (
            {
                "industry_id": industry_id,
                "security_code": "688001",
                "company_name": "示例存储公司",
                "chain_node_id": "memory_design_manufacturing",
                "relation_type": "official_disclosure",
                "key_metric_ids": ("dram_price",),
                "evidence_ids": (evidence.events[0].primary_evidence[0].evidence_id,),
                "as_of_date": "2026-08-25",
                "official_evidence": True,
            },
            {
                "industry_id": "semiconductor",
                "security_code": "688002",
                "company_name": "错误行业公司",
                "chain_node_id": "memory_design_manufacturing",
                "relation_type": "official_disclosure",
                "evidence_ids": ("wrong-industry",),
                "as_of_date": "2026-08-25",
                "official_evidence": True,
            },
        )

    verifier = EvidenceVerifier()

    def verify_with_company_binding(raw: RefreshRawSnapshot) -> EvidenceSnapshot:
        snapshot = verifier(raw)
        source_event = snapshot.events[0]
        source_item = source_event.primary_evidence[0]
        company_item = replace(
            source_item,
            supports_fields=source_item.supports_fields + (
                "metric:dram_price",
                "security_code:688001",
                "company_name:示例存储公司",
                "chain_node:memory_design_manufacturing",
                "relation_type:official_disclosure",
            ),
        )
        return replace(
            snapshot,
            events=(replace(source_event, primary_evidence=(company_item,)),),
        )

    orchestrator = _orchestrator(
        tmp_path,
        report_storage=storage,
        evidence_verifier=verify_with_company_binding,
        canonical_news_snapshot_loader=load_news,
        canonical_news_snapshot_lookup=lambda raw_id: (
            _canonical_news_snapshot() if raw_id == "news-raw-production" else None
        ),
        company_candidate_loader=load_companies,
    )
    try:
        run = orchestrator.request_refresh("storage").result(timeout=5)
        report = storage.load_current("storage")
    finally:
        orchestrator.shutdown()

    assert run.phase is RefreshPhase.TRUSTED_PUBLISHED, run.error_code
    assert news_calls == ["storage"]
    assert company_calls == [("storage", run.evidence_snapshot_id)]
    assert report is not None
    assert {event.event_id for event in report.news_risk} == {
        "news-verified-3", "news-corroborated-20",
    }
    assert [company.security_code for company in report.companies] == ["688001"]
    candidate = orchestrator.current_candidate("storage")
    assert candidate is not None
    assert {event.event_id for event in candidate.unverified_events} == {"news-unverified-2"}
    assert {event.event_id for event in candidate.conflicting_events} == {"news-conflicting-40"}
    production = ProductionIndustryResearchService(
        storage=storage,
        now=lambda: NOW,
        refresh_state_reader=orchestrator,
    )
    seven = production.read_report("storage", 7)
    thirty = production.read_report("storage", 30)
    ninety = production.read_report("storage", 90)
    assert {event.event_id for event in seven.displayed_trusted_report.news_risk} == {
        "news-verified-3",
    }
    assert {event.event_id for event in thirty.displayed_trusted_report.news_risk} == {
        "news-verified-3", "news-corroborated-20",
    }
    assert {event.event_id for event in seven.candidate_evidence.unverified_events} == {
        "news-unverified-2",
    }
    assert thirty.candidate_evidence.conflicting_events == ()
    assert {event.event_id for event in ninety.candidate_evidence.conflicting_events} == {
        "news-conflicting-40",
    }


def test_persisted_external_a2_candidate_requires_canonical_durable_lookup(tmp_path) -> None:
    snapshot = _canonical_news_snapshot()
    lookup = lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None
    owner = _orchestrator(
        tmp_path,
        run_ids=("canonical-persisted",),
        canonical_news_snapshot_loader=lambda _industry_id: snapshot,
        canonical_news_snapshot_lookup=lookup,
    )
    completed = owner.request_refresh("storage").result(5)
    expected = owner.current_candidate("storage")
    assert expected is not None and expected.external_lineages
    owner.shutdown()

    no_lookup = _orchestrator(tmp_path)
    assert no_lookup.current_run("storage") is None
    assert no_lookup.current_candidate("storage") is None
    no_lookup.shutdown()

    reloaded = _orchestrator(tmp_path, canonical_news_snapshot_lookup=lookup)
    assert reloaded.current_run("storage") == completed
    assert reloaded.current_candidate("storage") == expected
    reloaded.shutdown()


def _panel_bound_news_candidate(snapshot: EvidenceSnapshot):
    return IndustryResearchService(now=lambda: NOW).assemble_storage_report(
        trusted_snapshot_id="trusted-panel-bound",
        raw_snapshot_id="report-raw-panel-bound",
        evidence_snapshot_id="report-evidence-panel-bound",
        generated_at=NOW,
        trusted_observations=(),
        metric_candidates=None,
        news_snapshot=snapshot,
    ).candidate_evidence


def test_persisted_panel_bound_a2_candidate_requires_canonical_durable_lookup() -> None:
    snapshot = _canonical_news_snapshot()
    candidate = _panel_bound_news_candidate(snapshot)
    assert candidate.external_lineages == ()

    with pytest.raises(ValueError, match="canonical durable lookup"):
        refresh_module._candidate_from_dict(candidate.to_dict())


def test_persisted_panel_bound_a2_candidate_rejects_forged_event_with_lookup() -> None:
    snapshot = _canonical_news_snapshot()
    document = _panel_bound_news_candidate(snapshot).to_dict()
    document["unverified_events"][0]["event_id"] = "forged-panel-bound-event"

    with pytest.raises(ValueError, match="canonical and complete"):
        refresh_module._candidate_from_dict(
            document,
            canonical_snapshot_lookup=(
                lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None
            ),
        )


def test_persisted_panel_bound_a2_candidate_accepts_exact_canonical_projection() -> None:
    snapshot = _canonical_news_snapshot()
    expected = _panel_bound_news_candidate(snapshot)

    restored = refresh_module._candidate_from_dict(
        expected.to_dict(),
        canonical_snapshot_lookup=(
            lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None
        ),
    )

    assert restored == expected


def test_checksum_recomputed_panel_bound_a2_all_events_deleted_fails_closed(
    tmp_path,
) -> None:
    snapshot = replace(
        _canonical_news_snapshot(),
        snapshot_id="candidate-panel-bound-deleted",
        raw_snapshot_id="news-raw-panel-bound-deleted",
    )
    candidate = _panel_bound_news_candidate(snapshot)
    run = RefreshRun(
        industry_id="storage",
        run_id="panel-bound-deleted",
        raw_snapshot_id=candidate.raw_snapshot_id,
        evidence_snapshot_id=candidate.evidence_snapshot_id,
        candidate_snapshot_id=candidate.candidate_snapshot_id,
        phase=RefreshPhase.VERIFYING,
        error_code=None,
        displayed_trusted_snapshot_id=None,
        published_trusted_snapshot_id=None,
    )
    store = refresh_module._RefreshStateStore(
        tmp_path / "panel-bound-state",
        canonical_snapshot_lookup=(
            lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None
        ),
    )
    store.write(
        run,
        candidate,
        canonical_a2_lineages=(CandidateExternalLineage(
            kind="a2_news",
            candidate_snapshot_id=snapshot.snapshot_id,
            raw_snapshot_id=snapshot.raw_snapshot_id,
            evidence_snapshot_id=snapshot.snapshot_id,
        ),),
    )
    document = refresh_module.json.loads(
        store._path("storage").read_text(encoding="utf-8")
    )
    candidate_document = document["state"]["candidate"]
    candidate_document["unverified_events"] = []
    candidate_document["conflicting_events"] = []
    candidate_document["counts"]["unverified_events"] = 0
    candidate_document["counts"]["conflicting_events"] = 0
    document["checksum"] = refresh_module.hashlib.sha256(
        refresh_module._canonical(document["state"])
    ).hexdigest()
    store._writer._atomic_write(
        store._path("storage"),
        refresh_module._canonical(document) + b"\n",
    )

    assert store.load_record("storage") is None


def test_checksum_recomputed_mixed_a2_all_events_and_visible_declaration_deleted_fails_closed(
    tmp_path,
) -> None:
    snapshot = _canonical_news_snapshot()
    lookup = lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None
    owner = _orchestrator(
        tmp_path,
        run_ids=("mixed-events-deleted",),
        canonical_news_snapshot_loader=lambda _industry_id: snapshot,
        canonical_news_snapshot_lookup=lookup,
    )
    owner.request_refresh("storage").result(5)
    path = owner._state._path("storage")
    document = refresh_module.json.loads(path.read_text(encoding="utf-8"))
    candidate = document["state"]["candidate"]
    candidate["unverified_events"] = []
    candidate["conflicting_events"] = []
    candidate["external_lineages"] = []
    candidate["counts"]["unverified_events"] = 0
    candidate["counts"]["conflicting_events"] = 0
    document["checksum"] = refresh_module.hashlib.sha256(
        refresh_module._canonical(document["state"])
    ).hexdigest()
    owner._state._writer._atomic_write(
        path,
        refresh_module._canonical(document) + b"\n",
    )

    assert owner.current_run("storage") is None
    assert owner.current_candidate("storage") is None
    owner.shutdown()


def test_zero_event_canonical_a2_snapshot_is_declared_and_restored(tmp_path) -> None:
    snapshot = _zero_candidate_news_snapshot()
    lookup = lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None
    owner = _orchestrator(
        tmp_path,
        run_ids=("zero-event-canonical",),
        canonical_news_snapshot_loader=lambda _industry_id: snapshot,
        canonical_news_snapshot_lookup=lookup,
    )
    completed = owner.request_refresh("storage").result(5)
    expected = owner.current_candidate("storage")
    path = owner._state._path("storage")
    document = refresh_module.json.loads(path.read_text(encoding="utf-8"))
    owner.shutdown()

    assert expected is not None
    assert expected.unverified_events == ()
    assert expected.conflicting_events == ()
    assert document["schema_version"] == 6
    assert document["state"]["canonical_a2_lineages"] == [{
        "kind": "a2_news",
        "candidate_snapshot_id": snapshot.snapshot_id,
        "raw_snapshot_id": snapshot.raw_snapshot_id,
        "evidence_snapshot_id": snapshot.snapshot_id,
    }]
    reloaded = _orchestrator(tmp_path, canonical_news_snapshot_lookup=lookup)
    assert reloaded.current_run("storage") == completed
    assert reloaded.current_candidate("storage") == expected
    reloaded.shutdown()


def test_zero_event_canonical_a2_state_requires_durable_lookup(tmp_path) -> None:
    snapshot = _zero_candidate_news_snapshot()
    lookup = lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None
    owner = _orchestrator(
        tmp_path,
        run_ids=("zero-event-no-lookup",),
        canonical_news_snapshot_loader=lambda _industry_id: snapshot,
        canonical_news_snapshot_lookup=lookup,
    )
    owner.request_refresh("storage").result(5)
    owner.shutdown()

    no_lookup = _orchestrator(
        tmp_path,
        canonical_news_snapshot_loader=lambda _industry_id: snapshot,
        canonical_news_snapshot_lookup=lambda _raw_id: None,
    )
    assert no_lookup.current_run("storage") is None
    assert no_lookup.current_candidate("storage") is None
    no_lookup.shutdown()


def test_zero_event_state_rotation_uses_declared_historical_raw_lookup(tmp_path) -> None:
    old = _zero_candidate_news_snapshot(
        snapshot_id="news-evidence-old-zero",
        raw_snapshot_id="news-raw-old-zero",
    )
    new = _zero_candidate_news_snapshot(
        snapshot_id="news-evidence-new-zero",
        raw_snapshot_id="news-raw-new-zero",
    )
    owner = _orchestrator(
        tmp_path,
        run_ids=("zero-event-rotation",),
        canonical_news_snapshot_loader=lambda _industry_id: old,
        canonical_news_snapshot_lookup=lambda raw_id: old if raw_id == old.raw_snapshot_id else None,
    )
    completed = owner.request_refresh("storage").result(5)
    expected = owner.current_candidate("storage")
    owner.shutdown()

    looked_up: list[str] = []

    def durable_lookup(raw_id: str) -> EvidenceSnapshot | None:
        looked_up.append(raw_id)
        return {
            old.raw_snapshot_id: old,
            new.raw_snapshot_id: new,
        }.get(raw_id)

    reloaded = _orchestrator(
        tmp_path,
        canonical_news_snapshot_loader=lambda _industry_id: new,
        canonical_news_snapshot_lookup=durable_lookup,
    )
    assert reloaded.current_run("storage") == completed
    assert reloaded.current_candidate("storage") == expected
    assert looked_up and set(looked_up) == {old.raw_snapshot_id}
    reloaded.shutdown()


@pytest.mark.parametrize("tamper", ("raw_lineage", "event_id", "evidence_snapshot"))
def test_checksum_recomputed_forged_external_a2_state_still_fails_closed(
    tmp_path, tamper: str,
) -> None:
    snapshot = _canonical_news_snapshot()
    lookup = lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None
    owner = _orchestrator(
        tmp_path,
        run_ids=("forged-external",),
        canonical_news_snapshot_loader=lambda _industry_id: snapshot,
        canonical_news_snapshot_lookup=lookup,
    )
    owner.request_refresh("storage").result(5)
    path = owner._state._path("storage")
    document = refresh_module.json.loads(path.read_text(encoding="utf-8"))
    candidate = document["state"]["candidate"]
    if tamper == "raw_lineage":
        candidate["external_lineages"][0]["raw_snapshot_id"] = "forged-raw"
        for event_row in candidate["unverified_events"] + candidate["conflicting_events"]:
            event_row["raw_snapshot_id"] = "forged-raw"
    elif tamper == "event_id":
        candidate["unverified_events"][0]["event_id"] = "forged-event"
    else:
        candidate["external_lineages"][0]["evidence_snapshot_id"] = "forged-evidence"
        candidate["external_lineages"][0]["candidate_snapshot_id"] = "forged-evidence"
        for event_row in candidate["unverified_events"] + candidate["conflicting_events"]:
            event_row["evidence_snapshot_id"] = "forged-evidence"
            event_row["candidate_snapshot_id"] = "forged-evidence"
    document["checksum"] = refresh_module.hashlib.sha256(
        refresh_module._canonical(document["state"])
    ).hexdigest()
    owner._state._writer._atomic_write(
        path,
        refresh_module._canonical(document) + b"\n",
    )

    assert owner.current_run("storage") is None
    assert owner.current_candidate("storage") is None
    owner.shutdown()


@pytest.mark.parametrize("tamper", ("missing", "duplicate", "extra", "cross_array"))
def test_persisted_a2_projection_rejects_incomplete_or_duplicate_events_even_with_counts(
    tmp_path, tamper: str,
) -> None:
    snapshot = _canonical_news_snapshot()
    lookup = lambda raw_id: snapshot if raw_id == snapshot.raw_snapshot_id else None
    owner = _orchestrator(
        tmp_path,
        run_ids=("projection-completeness",),
        canonical_news_snapshot_loader=lambda _industry_id: snapshot,
        canonical_news_snapshot_lookup=lookup,
    )
    owner.request_refresh("storage").result(5)
    path = owner._state._path("storage")
    document = refresh_module.json.loads(path.read_text(encoding="utf-8"))
    candidate = document["state"]["candidate"]
    if tamper == "missing":
        candidate["unverified_events"].pop()
    elif tamper == "duplicate":
        candidate["unverified_events"].append(dict(candidate["unverified_events"][0]))
    elif tamper == "extra":
        extra = dict(candidate["unverified_events"][0])
        extra["event_id"] = "foreign-extra-event"
        candidate["unverified_events"].append(extra)
    else:
        moved = candidate["unverified_events"].pop()
        moved["status"] = "conflicting"
        candidate["conflicting_events"].append(moved)
    candidate["counts"]["unverified_events"] = len(candidate["unverified_events"])
    candidate["counts"]["conflicting_events"] = len(candidate["conflicting_events"])
    document["checksum"] = refresh_module.hashlib.sha256(
        refresh_module._canonical(document["state"])
    ).hexdigest()
    owner._state._writer._atomic_write(
        path,
        refresh_module._canonical(document) + b"\n",
    )

    assert owner.current_run("storage") is None
    assert owner.current_candidate("storage") is None
    owner.shutdown()


@pytest.mark.parametrize(
    "malicious_run_id",
    (".", "..", "../escape", "a/b", "a\\b", "x:y", "x\ncontrol", " leading"),
)
def test_malicious_factory_run_id_is_rejected_before_storage_or_provider_use(
    tmp_path, malicious_run_id
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        descriptors=(descriptor,),
        providers={descriptor.adapter_id: provider},
        run_ids=(malicious_run_id,),
    )

    try:
        with pytest.raises(ValueError, match="run_id"):
            orchestrator.request_refresh("storage")

        assert provider.calls == 0
        assert not (tmp_path / "run-state" / "storage" / "refresh_run.json").exists()
    finally:
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


def test_completed_run_id_cannot_be_reused_for_a_new_refresh_attempt(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("reused-run", "reused-run"),
        attempt_tokens=("1" * 32, "2" * 32),
    )

    first = orchestrator.request_refresh("storage").result(5)

    assert first.phase is RefreshPhase.TRUSTED_PUBLISHED
    with pytest.raises(ValueError, match="reused run_id"):
        orchestrator.request_refresh("storage")
    assert provider.calls == 1
    assert orchestrator.current_run("storage") == first
    orchestrator.shutdown()


def test_two_orchestrator_instances_share_one_same_industry_owner_run(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    provider.block = True
    first = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("owner-first",),
    )
    second = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("owner-second",),
    )
    try:
        owner_future = first.request_refresh("storage")
        assert provider.started.wait(2)
        follower_future = second.request_refresh("storage")
        time.sleep(0.15)
        assert provider.calls == 1

        provider.release.set()
        owner = owner_future.result(5)
        follower = follower_future.result(5)
        assert follower == owner
        assert owner.run_id == "owner-first"
        assert provider.calls == 1
    finally:
        provider.release.set()
        first.shutdown()
        second.shutdown()


def test_follower_recovers_exact_prepared_report_after_owner_final_state_failure(
    tmp_path, monkeypatch
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    provider.block = True
    owner_orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("prepared-owner",),
    )
    follower_orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("must-not-run",),
    )
    original_write = owner_orchestrator._state.write
    failed_once = False

    def fail_owner_committed_state(run, candidate, **kwargs) -> int:
        nonlocal failed_once
        publication = kwargs.get("publication")
        if publication is not None and publication.phase == "committed" and not failed_once:
            failed_once = True
            raise OSError("storage_error")
        return original_write(run, candidate, **kwargs)

    monkeypatch.setattr(owner_orchestrator._state, "write", fail_owner_committed_state)
    try:
        owner_future = owner_orchestrator.request_refresh("storage")
        assert provider.started.wait(2)
        follower_future = follower_orchestrator.request_refresh("storage")
        provider.release.set()

        owner = owner_future.result(5)
        follower = follower_future.result(5)

        assert owner.phase is RefreshPhase.TRUSTED_PUBLISHED
        assert follower == owner
        assert follower.run_id == "prepared-owner"
        assert provider.calls == 1
        persisted = follower_orchestrator._state.load_record("storage")
        assert persisted is not None and persisted.run == owner
        assert persisted.publication is not None and persisted.publication.phase == "committed"
    finally:
        provider.release.set()
        owner_orchestrator.shutdown()
        follower_orchestrator.shutdown()


def test_stale_nonterminal_run_is_failed_before_a_later_request_can_collect(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    seed = _orchestrator(tmp_path, providers={descriptor.adapter_id: provider})
    stale = RefreshRun(
        industry_id="storage",
        run_id="stale-owner",
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
        candidate_snapshot_id=None,
        phase=RefreshPhase.COLLECTING,
        error_code=None,
        displayed_trusted_snapshot_id=None,
        published_trusted_snapshot_id=None,
        displayed_raw_snapshot_id=None,
        displayed_evidence_snapshot_id=None,
    )
    seed._state.write(stale, None)
    seed.shutdown()
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("after-interruption",),
    )

    interrupted = orchestrator.request_refresh("storage").result(5)

    assert interrupted.run_id == "stale-owner"
    assert interrupted.phase is RefreshPhase.FAILED
    assert interrupted.error_code == "refresh_interrupted"
    assert provider.calls == 0

    completed = orchestrator.request_refresh("storage").result(5)
    assert completed.run_id == "after-interruption"
    assert completed.phase is RefreshPhase.TRUSTED_PUBLISHED
    assert provider.calls == 1
    orchestrator.shutdown()


def test_owner_setup_write_failure_releases_lease_for_another_orchestrator(
    tmp_path, monkeypatch
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    broken = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("broken-owner",),
    )
    successor = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("successor-owner",),
    )

    def fail_initial_write(*_args, **_kwargs):
        raise OSError("storage_error")

    monkeypatch.setattr(broken._state, "write", fail_initial_write)
    try:
        with pytest.raises(OSError, match="storage_error"):
            broken.request_refresh("storage")

        completed = successor.request_refresh("storage").result(2)
        assert completed.run_id == "successor-owner"
        assert completed.phase is RefreshPhase.TRUSTED_PUBLISHED
        assert provider.calls == 1
    finally:
        broken.shutdown()
        successor.shutdown()


def test_unsafe_lease_parent_fails_request_synchronously_instead_of_polling(
    tmp_path, monkeypatch
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("unsafe-lease",),
    )

    def reject_parent(_path) -> None:
        raise OSError("storage_error")

    monkeypatch.setattr(orchestrator._state._writer, "_verify_parent", reject_parent)
    try:
        with pytest.raises(OSError, match="storage_error"):
            orchestrator.request_refresh("storage")
        assert provider.calls == 0
    finally:
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


def test_storage_only_provider_is_not_scheduled_for_an_unsupported_industry(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("unsupported-industry",),
    )

    result = orchestrator.request_refresh("robotics").result(5)

    assert result.error_code == "no_eligible_provider"
    assert result.published_trusted_snapshot_id is None
    assert provider.calls == 0
    assert orchestrator.provider_resolution_ids == ()
    orchestrator.shutdown()


@pytest.mark.parametrize("echoed_industry", ("robotics", ""))
def test_provider_output_with_wrong_or_missing_industry_echo_is_rejected_before_raw_build(
    tmp_path, echoed_industry
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(
        descriptor,
        values=(_provider_value(descriptor.adapter_id, industry_id=echoed_industry),),
    )
    raw_builder = RawBuilder()
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        raw_builder=raw_builder,
        run_ids=("wrong-provider-industry",),
    )

    result = orchestrator.request_refresh("storage").result(5)

    assert result.error_code == "source_industry_mismatch"
    assert result.raw_snapshot_id is None
    assert result.published_trusted_snapshot_id is None
    assert raw_builder.calls == []
    assert provider.requested_industries == ["storage"]
    orchestrator.shutdown()


def test_any_provider_industry_mismatch_aborts_valid_values_before_raw_build(tmp_path) -> None:
    good_descriptor = _descriptor("a-good")
    wrong_descriptor = _descriptor("z-wrong")
    good_provider = FakeProvider(
        good_descriptor,
        values=(_provider_value("a-good", industry_id="storage"),),
    )
    wrong_provider = FakeProvider(
        wrong_descriptor,
        values=(_provider_value("z-wrong", industry_id="robotics"),),
    )
    raw_builder = RawBuilder()
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        descriptors=(good_descriptor, wrong_descriptor),
        providers={"a-good": good_provider, "z-wrong": wrong_provider},
        qualifications={"a-good": _qualification(), "z-wrong": _qualification()},
        raw_builder=raw_builder,
        report_storage=storage,
        run_ids=("mixed-industry-echo",),
    )

    result = orchestrator.request_refresh("storage").result(5)
    visible = storage.load_current("storage")

    assert result.phase is RefreshPhase.FAILED
    assert result.error_code == "source_industry_mismatch"
    assert result.raw_snapshot_id is None
    assert result.published_trusted_snapshot_id is None
    assert result.displayed_trusted_snapshot_id == "trusted-old"
    assert raw_builder.calls == []
    assert good_provider.calls == wrong_provider.calls == 1
    assert visible is not None and visible.trusted_snapshot_id == "trusted-old"
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
        ("raw-mismatch", "raw_industry_mismatch", False),
        ("raw-build", "raw_build_failed", False),
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
    raw_builder = RawBuilder(
        invalid=case == "raw-mismatch",
        raise_error=case == "raw-build",
    )
    if case == "admission":
        def fail_admission(**_kwargs):
            raise ValueError("admission failed")

        monkeypatch.setattr(refresh_module, "admit_metric_observations", fail_admission)
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
    persisted = orchestrator._state.load_record("storage")
    assert persisted is not None and persisted.publication is None
    if candidate is not None:
        assert candidate.candidate_snapshot_id == f"candidate-{case}"
        assert all(row.current_value is None for row in loaded.cycle)
    orchestrator.shutdown()


def test_cross_industry_work_is_bounded_and_run_state_never_crosses_industries(tmp_path) -> None:
    descriptor = _descriptor(
        supported_industry_ids=("storage", "robotics", "semiconductor"),
    )
    provider = FakeProvider(
        descriptor,
        values=(_provider_value(descriptor.adapter_id),),
        echo_request_industry=True,
    )
    provider.block = True
    raw_builder = RawBuilder(invalid=True)
    orchestrator = _orchestrator(
        tmp_path,
        descriptors=(descriptor,),
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
        "storage", "storage-run", "raw_industry_mismatch"
    )
    assert (robotics_run.industry_id, robotics_run.run_id, robotics_run.error_code) == (
        "robotics", "robotics-run", "admission_failed"
    )
    assert (
        semiconductor_run.industry_id,
        semiconductor_run.run_id,
        semiconductor_run.error_code,
    ) == ("semiconductor", "semiconductor-run", "raw_industry_mismatch")
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


def test_prepare_state_failure_keeps_new_report_invisible_and_old_report_displayed(
    tmp_path, monkeypatch
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    publish_calls_before_refresh = storage.publish_calls
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("prepare-failure",),
    )
    original_write = orchestrator._state.write

    def fail_prepared_state(run, candidate, **kwargs) -> None:
        publication = kwargs.get("publication")
        if publication is not None and publication.phase == "prepared":
            raise OSError("storage_error")
        return original_write(run, candidate, **kwargs)

    monkeypatch.setattr(orchestrator._state, "write", fail_prepared_state)

    result = orchestrator.request_refresh("storage").result(5)
    loaded = storage.load_current("storage")

    assert result.phase is RefreshPhase.FAILED
    assert result.error_code == "storage_error"
    assert result.published_trusted_snapshot_id is None
    assert result.displayed_trusted_snapshot_id == "trusted-old"
    assert loaded is not None and loaded.trusted_snapshot_id == "trusted-old"
    assert storage.publish_calls == publish_calls_before_refresh
    orchestrator.shutdown()


@pytest.mark.parametrize("seed_old", (False, True))
def test_crash_after_prepared_proof_before_publish_keeps_unproved_report_invisible(
    tmp_path, monkeypatch, seed_old
) -> None:
    class SimulatedProcessCrash(BaseException):
        pass

    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    if seed_old:
        _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("prepared-crash",),
    )

    def crash_before_publish(*_args, **_kwargs):
        raise SimulatedProcessCrash()

    monkeypatch.setattr(storage, "publish_prepared", crash_before_publish)

    with pytest.raises(SimulatedProcessCrash):
        orchestrator.request_refresh("storage").result(5)
    prepared = orchestrator._state.load_record("storage")
    visible = storage.load_current("storage")
    assert prepared is not None and prepared.run.phase is RefreshPhase.VERIFYING
    assert prepared.publication is not None and prepared.publication.phase == "prepared"
    assert prepared.publication.prepared_generation == prepared.generation
    assert (visible.trusted_snapshot_id if visible is not None else None) == (
        "trusted-old" if seed_old else None
    )
    orchestrator.shutdown()

    reloaded = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
    )
    recovered = reloaded.current_run("storage")
    assert recovered is not None and recovered.phase is RefreshPhase.FAILED
    assert recovered.error_code == "refresh_interrupted"
    assert recovered.published_trusted_snapshot_id is None
    assert recovered.displayed_trusted_snapshot_id == ("trusted-old" if seed_old else None)
    reloaded.shutdown()


def test_final_state_failure_recovers_success_from_exact_prepared_visible_report(
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
        run_ids=("postpublish-recovery",),
    )
    original_write = orchestrator._state.write
    failed_once = False

    def fail_first_committed_state(run, candidate, **kwargs) -> int:
        nonlocal failed_once
        publication = kwargs.get("publication")
        if publication is not None and publication.phase == "committed" and not failed_once:
            failed_once = True
            raise OSError("storage_error")
        return original_write(run, candidate, **kwargs)

    monkeypatch.setattr(orchestrator._state, "write", fail_first_committed_state)

    result = orchestrator.request_refresh("storage").result(5)
    prepared = orchestrator._state.load_record("storage")
    visible = storage.load_current("storage")

    assert result.phase is RefreshPhase.TRUSTED_PUBLISHED
    assert result.published_trusted_snapshot_id == "trusted-postpublish-recovery"
    assert prepared is not None and prepared.run.phase is RefreshPhase.VERIFYING
    assert prepared.publication is not None and prepared.publication.phase == "prepared"
    assert prepared.publication.prepared_generation == prepared.generation
    visible_publication = storage.load_current_publication("storage")
    assert visible is not None and visible.trusted_snapshot_id == "trusted-postpublish-recovery"
    assert visible_publication is not None and visible_publication.metadata is not None
    assert (
        visible_publication.metadata.publication_token
        == prepared.publication.publication_token
    )
    assert visible_publication.metadata.snapshot_checksum == prepared.publication.snapshot_checksum

    recovered = orchestrator.current_run("storage")
    committed = orchestrator._state.load_record("storage")
    assert recovered == result
    assert committed is not None and committed.run == result
    assert committed.publication is not None and committed.publication.phase == "committed"
    assert committed.publication.prepared_generation == committed.generation - 1
    orchestrator.shutdown()


def test_request_reconciles_exact_prepared_visible_report_before_interrupt_or_provider(
    tmp_path, monkeypatch
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    owner = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("request-recovery",),
        attempt_tokens=("1" * 32,),
    )
    original_write = owner._state.write
    failed_once = False

    def fail_first_committed_state(run, candidate, **kwargs) -> int:
        nonlocal failed_once
        publication = kwargs.get("publication")
        if publication is not None and publication.phase == "committed" and not failed_once:
            failed_once = True
            raise OSError("storage_error")
        return original_write(run, candidate, **kwargs)

    monkeypatch.setattr(owner._state, "write", fail_first_committed_state)
    expected = owner.request_refresh("storage").result(5)
    prepared = owner._state.load_record("storage")
    assert prepared is not None and prepared.publication is not None
    assert prepared.publication.phase == "prepared"
    owner.shutdown()

    reloaded = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("must-not-run",),
        attempt_tokens=("2" * 32,),
    )
    recovered_future = reloaded.request_refresh("storage")

    assert recovered_future.done()
    assert recovered_future.result(0) == expected
    assert provider.calls == 1
    committed = reloaded._state.load_record("storage")
    assert committed is not None and committed.run == expected
    assert committed.publication is not None and committed.publication.phase == "committed"
    reloaded.shutdown()


def test_aba_old_identical_report_token_cannot_recover_reused_run_attempt(
    tmp_path, monkeypatch
) -> None:
    class SimulatedProcessCrash(BaseException):
        pass

    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    first = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("aba-run",),
        attempt_tokens=("1" * 32,),
    )
    first_result = first.request_refresh("storage").result(5)
    first_visible = storage.load_current_publication("storage")
    assert first_visible is not None and first_visible.metadata is not None
    assert first_visible.metadata.publication_token == "1" * 32
    first_state_path = first._state._path("storage")
    first.shutdown()
    first_state_path.unlink()

    second = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("aba-run",),
        attempt_tokens=("2" * 32,),
    )
    original_write = second._state.write

    def crash_after_prepared_state(run, candidate, **kwargs) -> int:
        generation = original_write(run, candidate, **kwargs)
        publication = kwargs.get("publication")
        if publication is not None and publication.phase == "prepared":
            raise SimulatedProcessCrash()
        return generation

    monkeypatch.setattr(second._state, "write", crash_after_prepared_state)
    publish_calls_before_second = storage.publish_prepared_calls
    with pytest.raises(SimulatedProcessCrash):
        second.request_refresh("storage").result(5)
    second_prepared = second._state.load_record("storage")
    assert second_prepared is not None and second_prepared.publication is not None
    assert second_prepared.publication.publication_token == "2" * 32
    assert storage.publish_prepared_calls == publish_calls_before_second
    second.shutdown()

    recovery = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("must-not-run",),
        attempt_tokens=("3" * 32,),
    )
    interrupted = recovery.request_refresh("storage").result(5)
    still_visible = storage.load_current_publication("storage")

    assert interrupted.phase is RefreshPhase.FAILED
    assert interrupted.error_code == "refresh_interrupted"
    assert interrupted.published_trusted_snapshot_id is None
    assert provider.calls == 2
    assert still_visible is not None and still_visible.report == first_visible.report
    assert still_visible.metadata == first_visible.metadata
    assert first_result.published_trusted_snapshot_id == "trusted-aba-run"
    recovery.shutdown()


def test_checksum_valid_replayed_publication_token_cannot_authorize_visible_report(
    tmp_path,
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("token-replay",),
        attempt_tokens=("1" * 32,),
    )
    orchestrator.request_refresh("storage").result(5)
    path = orchestrator._state._path("storage")
    document = refresh_module.json.loads(path.read_text(encoding="utf-8"))
    document["state"]["publication"]["publication_token"] = "2" * 32
    document["checksum"] = refresh_module.hashlib.sha256(
        refresh_module._canonical(document["state"])
    ).hexdigest()
    orchestrator._state._writer._atomic_write(
        path,
        refresh_module._canonical(document) + b"\n",
    )

    current = orchestrator.current_run("storage")

    assert current is not None and current.phase is RefreshPhase.FAILED
    assert current.error_code == "publication_proof_invalid"
    assert current.published_trusted_snapshot_id is None
    assert provider.calls == 1
    orchestrator.shutdown()


def test_checksum_valid_prepared_generation_tamper_fails_state_reload_closed(
    tmp_path,
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("generation-tamper",),
    )
    orchestrator.request_refresh("storage").result(5)
    path = orchestrator._state._path("storage")
    document = refresh_module.json.loads(path.read_text(encoding="utf-8"))
    document["state"]["publication"]["prepared_generation"] += 1
    document["checksum"] = refresh_module.hashlib.sha256(
        refresh_module._canonical(document["state"])
    ).hexdigest()
    orchestrator._state._writer._atomic_write(
        path,
        refresh_module._canonical(document) + b"\n",
    )

    assert orchestrator.current_run("storage") is None
    assert orchestrator.current_candidate("storage") is None
    assert provider.calls == 1
    orchestrator.shutdown()


def test_visible_report_with_same_ids_but_different_content_fails_proof_checksum(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("report-checksum",),
    )
    completed = orchestrator.request_refresh("storage").result(5)
    visible = storage.load_current("storage")
    assert visible is not None
    altered = replace(visible, generated_at="2026-08-25T09:00:00+00:00")
    replacement = storage.publish(
        altered,
        expected_industry_id="storage",
        expected_raw_snapshot_id=visible.raw_snapshot_id,
        expected_evidence_snapshot_id=visible.evidence_snapshot_id,
    )
    assert replacement.published_trusted_snapshot_id == completed.published_trusted_snapshot_id

    current = orchestrator.current_run("storage")

    assert current is not None and current.phase is RefreshPhase.FAILED
    assert current.error_code == "publication_proof_invalid"
    assert current.published_trusted_snapshot_id is None
    orchestrator.shutdown()


def test_invalid_publication_proof_keeps_previous_trusted_display_without_promotion(
    tmp_path
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_storage=storage,
        run_ids=("proof-fallback",),
    )
    completed = orchestrator.request_refresh("storage").result(5)
    assert completed.published_trusted_snapshot_id == "trusted-proof-fallback"
    storage._atomic_write(storage.trusted_snapshot_path("storage"), b"{}\n")
    fallback = storage.load_current("storage")
    assert fallback is not None and fallback.trusted_snapshot_id == "trusted-old"

    current = orchestrator.current_run("storage")

    assert current is not None and current.phase is RefreshPhase.FAILED
    assert current.error_code == "publication_proof_invalid"
    assert current.published_trusted_snapshot_id is None
    assert current.displayed_trusted_snapshot_id == "trusted-old"
    assert current.displayed_raw_snapshot_id == "raw-old"
    assert current.displayed_evidence_snapshot_id == "evidence-old"
    orchestrator.shutdown()


def test_failed_run_is_never_promoted_by_a_report_that_reuses_raw_and_evidence_lineage(
    tmp_path
) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    storage = RecordingStorage(root=tmp_path / "reports")
    _seed_old_report(storage)
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        report_service=FailingService(),
        report_storage=storage,
        run_ids=("failed-lineage",),
    )
    failed = orchestrator.request_refresh("storage").result(5)
    assert failed.error_code == "assembly_failed"
    unrelated = IndustryResearchService(now=lambda: NOW).assemble_storage_report(
        trusted_snapshot_id="trusted-unrelated",
        raw_snapshot_id=failed.raw_snapshot_id,
        evidence_snapshot_id=failed.evidence_snapshot_id,
        generated_at=NOW,
        trusted_observations=(),
        metric_candidates=None,
        news_snapshot=None,
    ).report
    publication = storage.publish(
        unrelated,
        expected_industry_id="storage",
        expected_raw_snapshot_id=failed.raw_snapshot_id,
        expected_evidence_snapshot_id=failed.evidence_snapshot_id,
    )
    assert publication.published_trusted_snapshot_id == "trusted-unrelated"

    persisted = orchestrator.current_run("storage")

    assert persisted == failed
    assert persisted.phase is RefreshPhase.FAILED
    assert persisted.published_trusted_snapshot_id is None
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


def test_refresh_state_read_rejects_checksum_valid_forged_derived_candidate_id(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("derived-lineage",),
    )
    orchestrator.request_refresh("storage").result(5)
    path = orchestrator._state._path("storage")
    document = refresh_module.json.loads(path.read_text(encoding="utf-8"))
    document["state"]["run"]["candidate_snapshot_id"] = "candidate-forged"
    document["state"]["candidate"]["candidate_snapshot_id"] = "candidate-forged"
    document["state"]["publication"]["candidate_snapshot_id"] = "candidate-forged"
    document["checksum"] = refresh_module.hashlib.sha256(
        refresh_module._canonical(document["state"])
    ).hexdigest()
    orchestrator._state._writer._atomic_write(
        path,
        refresh_module._canonical(document) + b"\n",
    )

    assert orchestrator.current_run("storage") is None
    assert orchestrator.current_candidate("storage") is None
    orchestrator.shutdown()


def test_refresh_state_decoder_rejects_unknown_persisted_error_code(tmp_path) -> None:
    descriptor = _descriptor()
    provider = FakeProvider(descriptor, values=(_provider_value(descriptor.adapter_id),))
    orchestrator = _orchestrator(
        tmp_path,
        providers={descriptor.adapter_id: provider},
        run_ids=("unknown-error",),
    )
    orchestrator.request_refresh("storage").result(5)
    path = orchestrator._state._path("storage")
    document = refresh_module.json.loads(path.read_text(encoding="utf-8"))
    document["state"]["run"]["phase"] = "failed"
    document["state"]["run"]["error_code"] = "made_up_error"
    document["state"]["run"]["published_trusted_snapshot_id"] = None
    document["state"]["publication"] = None
    document["checksum"] = refresh_module.hashlib.sha256(
        refresh_module._canonical(document["state"])
    ).hexdigest()
    orchestrator._state._writer._atomic_write(
        path,
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
