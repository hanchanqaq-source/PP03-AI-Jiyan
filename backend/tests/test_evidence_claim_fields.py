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


def test_field_only_evidence_is_retained_without_counting_as_core_support():
    fields = extract_key_fields("星河科技建设算力中心进度达50%", "")
    rows = [
        item(
            "field-only",
            host="field.example",
            title="行业统计项目进度达50%",
            excerpt="统计口径显示完成率为50%",
        ),
        item(
            "core-support",
            host="core.example",
            title="星河科技建设算力中心进度达50%",
            excerpt="项目进度达50%",
        ),
        item(
            "unrelated",
            host="weather.example",
            title="天气晴朗",
            excerpt="今日无降水",
        ),
    ]

    result = match_evidence("星河科技建设算力中心", fields, rows)
    percentage = result.key_fields[0]
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

    assert percentage.verification_status.value == "corroborated"
    assert percentage.evidence_ids == ("core-support", "field-only")
    assert [row.evidence_id for row in result.independent_evidence] == [
        "field-only",
        "core-support",
    ]
    assert [row.supports_claim for row in result.independent_evidence] == [False, True]
    assert result.independent_support_count == 1
    assert "unrelated" not in available
    assert set(percentage.evidence_ids) <= available


def test_two_field_only_origins_corroborate_field_without_core_support():
    fields = extract_key_fields("星河科技建设算力中心进度达50%", "")
    rows = [
        item(
            "field-one",
            host="field-one.example",
            title="行业统计项目进度达50%",
            excerpt="完成率为50%",
        ),
        item(
            "field-two",
            host="field-two.example",
            title="区域工程完成率50%",
            excerpt="统计值为50%",
        ),
    ]

    result = match_evidence("星河科技建设算力中心", fields, rows)

    assert result.key_fields[0].verification_status.value == "corroborated"
    assert [row.evidence_id for row in result.independent_evidence] == [
        "field-one",
        "field-two",
    ]
    assert all(not row.supports_claim for row in result.independent_evidence)
    assert result.independent_support_count == 0


def test_conflicting_field_references_remain_closed_across_base_and_conflict_roles():
    fields = extract_key_fields("星河科技建设算力中心进度达50%", "")
    rows = [
        item(
            "core-support",
            host="core.example",
            title="星河科技建设算力中心进度达50%",
            excerpt="项目进度达50%",
        ),
        item(
            "field-only-conflict",
            host="field.example",
            title="行业统计项目进度达40%",
            excerpt="完成率为40%",
        ),
    ]

    result = match_evidence("星河科技建设算力中心", fields, rows)
    percentage = result.key_fields[0]
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

    assert percentage.verification_status.value == "conflicting"
    assert percentage.evidence_ids == ("core-support", "field-only-conflict")
    assert [row.evidence_id for row in result.independent_evidence] == [
        "core-support",
        "field-only-conflict",
    ]
    assert [row.evidence_id for row in result.contradicting_evidence] == [
        "core-support",
        "field-only-conflict",
    ]
    assert result.independent_support_count == 1
    assert set(percentage.evidence_ids) <= available
