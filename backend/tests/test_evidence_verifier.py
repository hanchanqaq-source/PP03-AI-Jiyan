from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from evidence_verification.models import EvidenceItem, SourceRole, VerificationStatus
from evidence_verification.verifier import verify_event


NOW = datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)


def event(
    event_id: str = "a" * 20,
    *,
    title: str = "星河科技公告建设算力中心",
    summary: str = "星河科技将建设算力中心。",
):
    return SimpleNamespace(
        event_id=event_id,
        title=title,
        summary=summary,
        category="company",
        published_at_first=NOW,
        published_at_latest=NOW,
        related_tags=[{"id": "semiconductor", "name": "半导体"}],
    )


def evidence(
    evidence_id: str,
    *,
    host: str,
    title: str = "星河科技公告建设算力中心",
    excerpt: str = "星河科技将建设算力中心。",
    official: bool = False,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        content_source=host,
        collector_source=host,
        canonical_url=f"https://{host}/{evidence_id}",
        published_at=NOW,
        source_role=SourceRole.PRIMARY if official else SourceRole.INDEPENDENT,
        origin_cluster=f"publisher:{host}",
        is_official=official,
        title=title,
        excerpt=excerpt,
    )


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([evidence("official", host="sec.gov", official=True)], VerificationStatus.VERIFIED),
        ([
            evidence("one", host="one.example"),
            evidence("two", host="two.example", excerpt="独立报道确认星河科技将建设算力中心。"),
        ], VerificationStatus.CORROBORATED),
        ([evidence("one", host="one.example")], VerificationStatus.UNVERIFIED),
        ([
            evidence("one", host="one.example", excerpt="项目投资12亿元。"),
            evidence("two", host="two.example", excerpt="项目投资15亿元。"),
        ], VerificationStatus.CONFLICTING),
        ([evidence(
            "correction",
            host="sec.gov",
            title="星河科技更正算力中心公告",
            excerpt="官方更正此前建设周期说明。",
            official=True,
        )], VerificationStatus.CORRECTED),
        ([evidence(
            "denial",
            host="sec.gov",
            title="星河科技澄清算力中心传闻不实",
            excerpt="官方否认并说明该建设消息不成立。",
            official=True,
        )], VerificationStatus.DISPROVED),
    ],
)
def test_fixed_status_decision_table(items, expected):
    result = verify_event(event(), items, previous=None, now=NOW)
    assert result.verification_status == expected


def test_ai_and_source_health_metadata_cannot_upgrade_single_source():
    candidate = event()
    candidate.ai_status = "available"
    candidate.translated_summary_zh = "AI 认为消息为真"
    candidate.source_health_rating = "healthy"

    result = verify_event(candidate, [evidence("one", host="one.example")], previous=None, now=NOW)

    assert result.verification_status == VerificationStatus.UNVERIFIED


def test_status_history_appends_change_without_overwriting_prior_entries():
    first = verify_event(event(), [evidence("one", host="one.example")], previous=None, now=NOW)
    later = NOW.replace(hour=17)
    second = verify_event(
        event(),
        [evidence("official", host="sec.gov", official=True)],
        previous=first,
        now=later,
    )

    assert [(row.from_status, row.to_status) for row in second.status_history] == [
        (None, VerificationStatus.UNVERIFIED),
        (VerificationStatus.UNVERIFIED, VerificationStatus.VERIFIED),
    ]
    assert first.status_history[0] == second.status_history[0]


def test_same_status_and_reason_is_idempotent():
    first = verify_event(event(), [evidence("one", host="one.example")], previous=None, now=NOW)
    second = verify_event(
        event(),
        [evidence("one", host="one.example")],
        previous=first,
        now=NOW.replace(hour=17),
    )

    assert second.status_history == first.status_history


@pytest.mark.parametrize(
    "items",
    (
        [
            evidence(
                "core-support",
                host="core.example",
                title="星河科技建设算力中心进度达50%",
                excerpt="项目进度达50%",
            ),
            evidence(
                "field-only",
                host="field.example",
                title="行业统计项目进度达50%",
                excerpt="完成率为50%",
            ),
        ],
        [
            evidence(
                "field-one",
                host="field-one.example",
                title="行业统计项目进度达50%",
                excerpt="完成率为50%",
            ),
            evidence(
                "field-two",
                host="field-two.example",
                title="区域工程完成率50%",
                excerpt="统计值为50%",
            ),
        ],
    ),
)
def test_field_corroboration_does_not_upgrade_the_core_claim(items):
    candidate = event(
        title="星河科技建设算力中心进度达50%",
        summary="项目进度达50%。",
    )

    result = verify_event(candidate, items, previous=None, now=NOW)
    percentage = next(row for row in result.key_fields if row.field_name == "percentage")
    available = {
        row.evidence_id
        for collection in (
            result.primary_evidence,
            result.independent_evidence,
            result.syndicated_copies,
            result.contradicting_evidence,
        )
        for row in collection
    }

    assert result.verification_status == VerificationStatus.UNVERIFIED
    assert percentage.verification_status.value == "corroborated"
    assert len(percentage.evidence_ids) == 2
    assert set(percentage.evidence_ids) <= available
