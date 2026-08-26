from __future__ import annotations

from industry_research.relationships import CompanyEvidenceBinding, project_company_relations


PROJECTION_SCOPE = {
    "allowed_chain_node_ids": ("memory_design_manufacturing",),
    "allowed_metric_ids": ("dram_price",),
    "evidence_bindings": {
        "evidence-company-1": CompanyEvidenceBinding(
            "evidence-company-1",
            frozenset({
                "security_code:688001",
                "chain_node:memory_design_manufacturing",
                "relation_type:official_disclosure",
                "metric:dram_price",
            }),
            "2026-06-30",
        ),
        "evidence-classification-1": CompanyEvidenceBinding(
            "evidence-classification-1",
            frozenset({
                "security_code:688001",
                "chain_node:memory_design_manufacturing",
                "relation_type:public_classification",
                "metric:dram_price",
            }),
            "2026-06-30",
        ),
    },
}


def _candidate(**overrides):
    value = {
        "industry_id": "storage",
        "security_code": "688001",
        "company_name": "示例存储公司",
        "chain_node_id": "memory_design_manufacturing",
        "relation_type": "official_disclosure",
        "key_metric_ids": ("dram_price",),
        "evidence_ids": ("evidence-company-1",),
        "as_of_date": "2026-06-30",
        "official_evidence": True,
    }
    value.update(overrides)
    return value


def test_company_relation_requires_exact_security_code_and_official_evidence() -> None:
    # Break caught: a company name or an unverified classification becomes a trusted relation.
    result = project_company_relations(
        industry_id="storage",
        candidates=(
            _candidate(),
            _candidate(security_code=""),
            _candidate(security_code="not-a-code"),
            _candidate(security_code="688002", official_evidence=False),
            _candidate(security_code="688003", evidence_ids=()),
            _candidate(security_code="688004", industry_id="semiconductor"),
        ),
        **PROJECTION_SCOPE,
    )

    assert len(result) == 1
    assert result[0].security_code == "688001"
    assert result[0].observation_only is True
    assert result[0].evidence_ids == ("evidence-company-1",)


def test_public_classification_is_admitted_only_when_officially_evidenced() -> None:
    # Break caught: public-classification is inferred from a name without source attestation.
    result = project_company_relations(
        industry_id="storage",
        candidates=(
            _candidate(
                relation_type="public_classification",
                evidence_ids=("evidence-classification-1",),
                official_evidence=True,
            ),
            _candidate(
                security_code="688002",
                relation_type="public_classification",
                evidence_ids=("evidence-classification-2",),
                official_evidence=False,
            ),
        ),
        **PROJECTION_SCOPE,
    )

    assert [item.security_code for item in result] == ["688001"]
    assert result[0].relation_type == "public_classification"


def test_company_projection_rejects_foreign_template_and_evidence_bindings() -> None:
    result = project_company_relations(
        industry_id="storage",
        candidates=(
            _candidate(chain_node_id="foreign-node"),
            _candidate(security_code="688002", key_metric_ids=("foreign-metric",)),
            _candidate(security_code="688003", evidence_ids=("foreign-evidence",)),
        ),
        **PROJECTION_SCOPE,
    )

    assert result == ()


def test_company_projection_rejects_wrong_date_and_incomplete_semantic_proof() -> None:
    result = project_company_relations(
        industry_id="storage",
        candidates=(
            _candidate(as_of_date="2026-07-01"),
            _candidate(security_code="688002"),
            _candidate(key_metric_ids=("dram_price", "nand_price")),
        ),
        allowed_chain_node_ids=("memory_design_manufacturing",),
        allowed_metric_ids=("dram_price", "nand_price"),
        evidence_bindings=PROJECTION_SCOPE["evidence_bindings"],
    )

    assert result == ()
