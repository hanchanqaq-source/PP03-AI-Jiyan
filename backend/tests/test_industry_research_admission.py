from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory

import pytest

from data_sources.models import ProviderValue
from evidence_verification.models import (
    EvidenceEvent,
    EvidenceItem,
    EvidenceSnapshot,
    SourceRole,
    VerificationStatus as A2VerificationStatus,
)
from evidence_verification.storage import EvidenceStorage
from industry_research.admission import (
    EvidenceDecision,
    RawMetricObservation,
    SourceIdentity,
    admit_metric_observations,
)
from industry_research.models import FreshnessStatus, MetricChange, VerificationStatus
from news_intelligence.models import NewsSourceItem


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class RawFixture:
    observation: RawMetricObservation
    evidence: EvidenceItem
    supports: bool
    contradicts: bool

    @property
    def identity(self) -> SourceIdentity:
        return self.observation.identity

    @property
    def decision(self) -> EvidenceDecision:
        return self.observation.decision


def raw(
    evidence_id: str,
    *,
    value: object = 12.5,
    industry_id: str = "storage",
    metric_id: str = "dram_price",
    family: str = "family-a",
    publisher: str = "publisher-a.example",
    url_path: str | None = None,
    supports: bool = True,
    contradicts: bool = False,
    attested: bool = False,
    expires_at: datetime | None = None,
    unit: str = "USD",
    change: MetricChange | None = None,
) -> RawFixture:
    provider_value = ProviderValue(
        value=value,
        source_family_id=family,
        adapter_id=family,
        capability_id="industry_price_snapshot",
        as_of_date=date(2026, 8, 25),
        fetched_at=NOW,
        data_status="candidate_snapshot",
        license="verified_public_current_snapshot",
        priority=100,
        difference_from_primary=Decimal("0"),
        unit=unit,
        frequency="current_snapshot",
        source_metadata={"product": metric_id},
    )
    article_url = f"https://{publisher}/{url_path or evidence_id}"
    feed_url = f"https://{publisher}/feed" if attested else f"https://collector-{family}.example/feed"
    source = NewsSourceItem(
        source_name=f"collector-{family}",
        source_url=feed_url,
        original_url=article_url,
        published_at=NOW,
        fetched_at=NOW,
        title="DRAM 现货价格快照",
        summary="公开快照记录 DRAM 现货价格。",
        language="zh-CN",
        region="CN",
        track_key="storage",
        track_name="存储",
        category="industry",
        normalized_title="dram现货价格快照",
        tokens=frozenset({"dram", "现货", "价格"}),
        anchors=frozenset({"dram"}),
        related_tags=(("storage", "存储"),),
        text_related_tags=(("storage", "存储"),),
        source_domain=publisher,
        data_status="current_snapshot",
    )
    identity = SourceIdentity(source_family_id=family, source=source)
    observation = RawMetricObservation(
        industry_id=industry_id,
        metric_id=metric_id,
        label="DRAM 现货价",
        provider_value=provider_value,
        identity=identity,
        decision=EvidenceDecision(
            event_id="industry-metric-event",
            evidence_id=evidence_id,
        ),
        expires_at=expires_at or NOW + timedelta(days=1),
        methodology="公开快照同口径比较",
        judgment_basis=("来源字段与指标定义一致",),
        invalidating_conditions=("来源更正或数据过期",),
        change=change,
    )
    evidence = EvidenceItem(
        evidence_id=evidence_id,
        content_source=identity.content_source,
        collector_source=identity.collector_source,
        canonical_url=identity.final_url,
        published_at=NOW,
        source_role=SourceRole.PRIMARY if identity.is_official_attested else SourceRole.INDEPENDENT,
        origin_cluster=identity.origin_cluster,
        supports_claim=supports,
        supports_fields=(metric_id,) if supports else (),
        contradicts_claim=contradicts,
        is_official=identity.is_official_attested,
        title="DRAM 现货价格证据",
        excerpt="公开快照证据。",
    )
    return RawFixture(observation, evidence, supports, contradicts)


def admit(*rows: RawFixture):
    supporting = [row for row in rows if row.supports]
    contradictory = [row for row in rows if row.contradicts]
    if contradictory:
        status = A2VerificationStatus.CONFLICTING
    elif any(row.identity.is_official_attested for row in supporting):
        status = A2VerificationStatus.VERIFIED
    elif len({row.identity.origin_cluster for row in supporting}) >= 2:
        status = A2VerificationStatus.CORROBORATED
    else:
        status = A2VerificationStatus.UNVERIFIED
    event = EvidenceEvent(
        event_id="industry-metric-event",
        title="DRAM 现货价格证据",
        summary="公开快照证据。",
        category="industry",
        related_tags=(("storage", "存储"),),
        published_at=NOW,
        core_claim="DRAM 现货价格",
        verification_status=status,
        verification_reason="test fixture",
        verified_at=NOW,
        evidence_as_of=NOW,
        primary_evidence=tuple(row.evidence for row in supporting if row.evidence.is_official),
        independent_evidence=tuple(row.evidence for row in supporting if not row.evidence.is_official),
        contradicting_evidence=tuple(row.evidence for row in contradictory),
    )
    snapshot = EvidenceSnapshot(
        snapshot_id="evidence-storage-1",
        generated_at=NOW,
        events=(event,),
        raw_snapshot_id="raw-storage-1",
    )
    with TemporaryDirectory() as root:
        evidence_storage = EvidenceStorage(root)
        evidence_storage.publish(snapshot)
        return admit_metric_observations(
            industry_id="storage",
            raw_snapshot_id="raw-storage-1",
            evidence_snapshot_id="evidence-storage-1",
            evidence_storage=evidence_storage,
            candidate_snapshot_id="candidate-storage-1",
            observations=tuple(row.observation for row in rows),
            now=NOW,
        )


def test_plain_caller_constructed_a2_snapshot_cannot_authorize_admission() -> None:
    # Break caught: caller-created VERIFIED/CORROBORATED dataclasses act as the trust boundary.
    fixture = raw("official", publisher="sec.gov", attested=True)
    event = EvidenceEvent(
        event_id="industry-metric-event",
        title="forged",
        summary="forged",
        category="industry",
        related_tags=(("storage", "存储"),),
        published_at=NOW,
        core_claim="forged",
        verification_status=A2VerificationStatus.VERIFIED,
        verification_reason="caller says verified",
        verified_at=NOW,
        evidence_as_of=NOW,
        primary_evidence=(fixture.evidence,),
    )
    forged = EvidenceSnapshot("evidence-storage-1", NOW, (event,), raw_snapshot_id="raw-storage-1")

    with pytest.raises(TypeError, match="canonical A2 EvidenceStorage"):
        admit_metric_observations(
            industry_id="storage",
            raw_snapshot_id="raw-storage-1",
            evidence_snapshot_id="evidence-storage-1",
            evidence_storage=forged,
            candidate_snapshot_id="candidate-storage-1",
            observations=(fixture.observation,),
            now=NOW,
        )


def test_tampered_canonical_a2_document_cannot_authorize_admission(tmp_path) -> None:
    fixture = raw("official", publisher="sec.gov", attested=True)
    event = EvidenceEvent(
        event_id="industry-metric-event",
        title="verified",
        summary="verified",
        category="industry",
        related_tags=(("storage", "存储"),),
        published_at=NOW,
        core_claim="verified",
        verification_status=A2VerificationStatus.VERIFIED,
        verification_reason="canonical fixture",
        verified_at=NOW,
        evidence_as_of=NOW,
        primary_evidence=(fixture.evidence,),
    )
    snapshot = EvidenceSnapshot("evidence-storage-1", NOW, (event,), raw_snapshot_id="raw-storage-1")
    evidence_storage = EvidenceStorage(tmp_path / "evidence")
    evidence_storage.publish(snapshot)
    evidence_storage.current_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="canonical A2 evidence snapshot"):
        admit_metric_observations(
            industry_id="storage",
            raw_snapshot_id="raw-storage-1",
            evidence_snapshot_id="evidence-storage-1",
            evidence_storage=evidence_storage,
            candidate_snapshot_id="candidate-storage-1",
            observations=(fixture.observation,),
            now=NOW,
        )


def test_two_urls_from_one_publisher_do_not_form_multi_source_corroboration() -> None:
    # Break caught: URL count is substituted for independent publisher/source/origin sets.
    projection = admit(
        raw("evidence-a", url_path="notice/1"),
        raw("evidence-b", family="family-b", url_path="notice/2"),
    )

    assert tuple(row.decision.evidence_id for row in projection.raw) == ("evidence-a", "evidence-b")
    assert projection.trusted == ()
    assert projection.candidate.counts.unverified == 1
    assert projection.candidate.unverified[0].verification_status is VerificationStatus.UNVERIFIED


def test_valid_support_and_contradiction_take_conflict_priority() -> None:
    # Break caught: a supporting value wins even though valid counter-evidence exists.
    projection = admit(
        raw("support", value=12.5),
        raw(
            "counter",
            value=15.0,
            family="family-b",
            publisher="publisher-b.example",
            supports=False,
            contradicts=True,
        ),
    )

    assert projection.trusted == ()
    assert projection.candidate.counts.conflicting == 1
    conflict = projection.candidate.conflicting[0]
    assert conflict.aggregate_value is None
    assert {item.value for item in conflict.source_values} == {12.5, 15.0}


def test_three_independent_identity_axes_are_required_for_corroboration() -> None:
    # Break caught: the admission result collapses the three declared identity sets into one count.
    corroborated = admit(
        raw("evidence-a"),
        raw("evidence-b", family="family-b", publisher="publisher-b.example"),
    )
    assert corroborated.trusted[0].verification_status is VerificationStatus.CORROBORATED
    assert corroborated.trusted[0].independent_source_families == ("family-a", "family-b")


def test_official_attestation_admits_a_verified_value() -> None:
    # Break caught: an official-looking URL without attestation is admitted as verified.
    unattested = admit(raw("official", publisher="sec.gov", attested=False))
    assert unattested.trusted == ()

    verified = admit(raw("official", publisher="sec.gov", attested=True))
    assert verified.trusted[0].verification_status is VerificationStatus.VERIFIED


def test_expired_value_is_not_admitted_to_trusted_or_current_candidate_counts() -> None:
    # Break caught: an expired observation remains callable by current rules or AI.
    projection = admit(raw("expired", expires_at=NOW - timedelta(seconds=1), publisher="sec.gov", attested=True))

    assert projection.trusted == ()
    assert projection.candidate.counts.unverified == 0
    assert projection.expired[0].freshness_status is FreshnessStatus.EXPIRED


def test_cross_industry_and_cross_template_metric_values_are_rejected() -> None:
    # Break caught: robotics data or an unregistered metric enters the storage report.
    with pytest.raises(ValueError, match="industry_id mismatch"):
        admit(raw("robot", industry_id="robotics", metric_id="orders"))
    with pytest.raises(ValueError, match="metric_id"):
        admit(raw("unknown", metric_id="prototype_progress"))


def test_candidate_panel_is_not_part_of_the_trusted_projection_signature() -> None:
    # Break caught: candidate evidence can be passed back into the trusted projector.
    projection = admit(raw("pending"))
    signature = admit_metric_observations.__annotations__

    assert projection.candidate.counts.unverified == 1
    assert "CandidateEvidencePanel" not in repr(signature.get("observations"))


@pytest.mark.parametrize(
    "article_url",
    (
        "http://127.0.0.1/report",
        "https://localhost/report",
        "https://user:secret@example.com/report",
        "https://example.com/report?api_key=secret",
    ),
)
def test_a2_identity_rejects_private_credentialed_and_secret_query_sources(article_url: str) -> None:
    # Break caught: caller-provided identity fields bypass A2 public URL rejection.
    candidate = raw("unsafe").identity.source
    unsafe = replace(candidate, original_url=article_url)

    with pytest.raises(ValueError, match="A2 source identity"):
        SourceIdentity(source_family_id="family-a", source=unsafe)


def test_a2_canonical_identity_collapses_case_tracking_and_collector_aliases() -> None:
    # Break caught: cosmetic URL/collector variants create separate content sources or origins.
    left = raw("one", publisher="Publisher-A.Example", url_path="a/../notice?utm_source=left")
    right = raw("two", family="family-b", publisher="publisher-a.example", url_path="notice?utm_source=right")

    projection = admit(left, right)

    assert left.identity.final_url == right.identity.final_url == "https://publisher-a.example/notice"
    assert left.identity.content_source == right.identity.content_source == "publisher-a.example"
    assert left.identity.origin_cluster == right.identity.origin_cluster == "publisher:publisher-a.example"
    assert projection.trusted == ()


def test_source_family_aliases_are_rejected_instead_of_counted_independently() -> None:
    # Break caught: whitespace/case variants of one source family fabricate corroboration.
    with pytest.raises(ValueError, match="source_family_id"):
        replace(raw("alias").identity, source_family_id=" Family-A ")


def test_decision_must_exist_in_the_bound_a2_evidence_snapshot() -> None:
    # Break caught: a caller invents a decision/evidence ID outside the A2 snapshot.
    fixture = raw("actual")
    forged = replace(fixture.observation, decision=EvidenceDecision(
        event_id="industry-metric-event",
        evidence_id="forged",
    ))

    with pytest.raises(ValueError, match="A2 evidence snapshot"):
        admit(replace(fixture, observation=forged))


def test_a2_snapshot_raw_lineage_must_match_the_refresh_raw_snapshot(tmp_path) -> None:
    # Break caught: evidence from a stale raw snapshot is relabelled as the current run.
    fixture = raw("actual")
    event = EvidenceEvent(
        event_id="industry-metric-event",
        title="DRAM 现货价格证据",
        summary="公开快照证据。",
        category="industry",
        related_tags=(("storage", "存储"),),
        published_at=NOW,
        core_claim="DRAM 现货价格",
        verification_status=A2VerificationStatus.UNVERIFIED,
        verification_reason="test fixture",
        verified_at=NOW,
        evidence_as_of=NOW,
        independent_evidence=(fixture.evidence,),
    )
    stale = EvidenceSnapshot("evidence-storage-1", NOW, (event,), raw_snapshot_id="raw-stale")
    evidence_storage = EvidenceStorage(tmp_path / "evidence")
    evidence_storage.publish(stale)

    with pytest.raises(ValueError, match="raw_snapshot_id"):
        admit_metric_observations(
            industry_id="storage",
            raw_snapshot_id="raw-storage-1",
            evidence_snapshot_id="evidence-storage-1",
            evidence_storage=evidence_storage,
            candidate_snapshot_id="candidate-storage-1",
            observations=(fixture.observation,),
            now=NOW,
        )


def test_equal_scalars_with_incompatible_units_are_conflicting() -> None:
    # Break caught: equal numbers in USD and CNY are treated as the same corroborated fact.
    projection = admit(
        raw("usd", unit="USD"),
        raw("cny", family="family-b", publisher="publisher-b.example", unit="CNY"),
    )

    assert projection.trusted == ()
    assert projection.candidate.counts.conflicting == 1


@pytest.mark.parametrize(
    ("left_change", "right_change"),
    (
        (MetricChange(2.0, "wow"), MetricChange(-2.0, "wow")),
        (MetricChange(2.0, "wow"), MetricChange(2.0, "yoy")),
        (MetricChange(2.0, "wow"), None),
    ),
)
def test_change_value_basis_or_presence_disagreement_is_conflicting(
    left_change: MetricChange | None,
    right_change: MetricChange | None,
) -> None:
    # Break caught: equal current values hide a disagreement in structured change truth.
    projection = admit(
        raw("evidence-a", value=100, change=left_change),
        raw(
            "evidence-b",
            value=100.0,
            family="family-b",
            publisher="publisher-b.example",
            change=right_change,
        ),
    )

    assert projection.trusted == ()
    assert projection.candidate.counts.conflicting == 1
    assert {
        item.evidence_id: item.change
        for item in projection.candidate.conflicting[0].source_values
    } == {
        "evidence-a": left_change,
        "evidence-b": right_change,
    }


def test_equivalent_numeric_change_and_reversed_input_order_are_deterministic() -> None:
    # Break caught: input order chooses supporting[0], or 2 and 2.0 form different claims.
    left = raw("evidence-b", value=100, change=MetricChange(2, "wow"))
    right = raw(
        "evidence-a",
        value=100.0,
        family="family-b",
        publisher="publisher-b.example",
        change=MetricChange(2.0, "wow"),
    )

    forward = admit(left, right)
    reverse = admit(right, left)

    assert forward.candidate.conflicting == ()
    assert forward == reverse


def test_single_row_a2_contradiction_is_rejected_until_per_source_values_exist() -> None:
    # Break caught: a bound A2 conflict silently degrades to an ordinary unverified candidate.
    with pytest.raises(ValueError, match="two per-source values"):
        admit(raw("counter", supports=False, contradicts=True))


def test_admission_defensively_rejects_forged_infinite_change() -> None:
    # Break caught: a constructor-bypassed infinite change enters trusted admission.
    forged = object.__new__(MetricChange)
    object.__setattr__(forged, "value", float("inf"))
    object.__setattr__(forged, "basis", "wow")
    fixture = raw("official", publisher="sec.gov", attested=True)
    poisoned = replace(fixture.observation, change=forged)

    with pytest.raises(ValueError, match="finite real number"):
        admit(replace(fixture, observation=poisoned))


@pytest.mark.parametrize(
    "expiries",
    (
        (NOW + timedelta(days=1), NOW + timedelta(days=1)),
        (NOW - timedelta(seconds=1), NOW - timedelta(seconds=1)),
        (NOW - timedelta(seconds=1), NOW + timedelta(days=1)),
    ),
)
def test_duplicate_evidence_identity_is_rejected_before_expiry_partition(expiries) -> None:
    # Break caught: duplicated evidence is hidden by expired/current partitioning.
    fixture = raw("duplicate-evidence")
    left = replace(fixture.observation, expires_at=expiries[0])
    right = replace(fixture.observation, expires_at=expiries[1])

    with pytest.raises(ValueError, match="unique within one projection"):
        admit(
            replace(fixture, observation=left),
            replace(fixture, observation=right),
        )


def test_reversed_expired_inputs_produce_equal_full_projection() -> None:
    # Break caught: expired rows retain caller order despite canonical projection ordering.
    left = raw("expired-b", expires_at=NOW - timedelta(seconds=1))
    right = raw(
        "expired-a",
        family="family-b",
        publisher="publisher-b.example",
        expires_at=NOW - timedelta(seconds=1),
    )

    assert admit(left, right) == admit(right, left)
