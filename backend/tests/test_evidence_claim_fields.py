from __future__ import annotations

from datetime import datetime, timezone

from evidence_verification.claim_fields import extract_core_claim, extract_key_fields
from evidence_verification.evidence_matcher import match_evidence
from evidence_verification.models import EvidenceItem, SourceRole


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def item(
    evidence_id: str,
    *,
    host: str,
    title: str,
    excerpt: str,
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


def test_money_percentage_quantity_and_effective_date_are_independent_fields():
    fields = extract_key_fields(
        "公司公告投资12亿元建设项目",
        "预计产能提升20%，新增3条产线，2026年8月18日生效。",
    )

    assert [(row.field_name, row.normalized_value) for row in fields] == [
        ("money", "CNY:1200000000"),
        ("percentage", "20%"),
        ("quantity", "3:产线"),
        ("effective_date", "2026-08-18"),
    ]
    assert {row.verification_status.value for row in fields} == {"unverified"}


def test_core_claim_excludes_unverified_numeric_and_date_details():
    assert extract_core_claim(
        "公司公告投资12亿元建设项目",
        "预计2026年8月18日生效。",
    ) == "公司公告投资建设项目"


def test_official_document_verifies_money_but_not_omitted_build_cycle():
    fields = extract_key_fields("公司公告投资12亿元建设算力中心", "建设周期三年。")
    official = item(
        "official",
        host="sec.gov",
        title="公司公告投资12亿元建设算力中心",
        excerpt="正式披露投资金额12亿元。",
        official=True,
    )

    result = match_evidence("公司公告投资建设算力中心", fields, [official])
    by_name = {row.field_name: row for row in result.key_fields}

    assert by_name["money"].verification_status.value == "verified"
    assert by_name["money"].evidence_ids == ("official",)
    assert by_name["build_cycle"].verification_status.value == "unverified"


def test_two_independent_sources_verify_same_field_while_conflicting_value_is_flagged():
    fields = extract_key_fields("公司拟投资12亿元扩产", "")
    supporters = [
        item("one", host="publisher-one.example", title="公司拟投资12亿元扩产", excerpt="投资12亿元"),
        item(
            "two",
            host="publisher-two.example",
            title="行业媒体确认公司拟投资12亿元扩产",
            excerpt="第二条独立采访记录了同一投资安排",
        ),
    ]
    corroborated = match_evidence("公司拟投资扩产", fields, supporters)
    assert corroborated.key_fields[0].verification_status.value == "corroborated"

    conflicting = match_evidence(
        "公司拟投资扩产",
        fields,
        supporters + [item(
            "three",
            host="publisher-three.example",
            title="公司拟投资15亿元扩产",
            excerpt="投资15亿元",
        )],
    )
    assert conflicting.key_fields[0].verification_status.value == "conflicting"


def test_ten_exact_syndicated_copies_count_as_one_origin_chain():
    fields = extract_key_fields("公司拟投资12亿元扩产", "")
    copies = [
        item(
            f"copy-{index}",
            host=f"collector-{index}.example",
            title="公司拟投资12亿元扩产",
            excerpt="同一通讯稿：公司拟投资12亿元扩产。",
        )
        for index in range(10)
    ]

    result = match_evidence("公司拟投资扩产", fields, copies)

    assert len(result.independent_evidence) == 1
    assert len(result.syndicated_copies) == 9
    assert result.key_fields[0].verification_status.value == "unverified"
