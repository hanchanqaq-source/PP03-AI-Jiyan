from __future__ import annotations

import asyncio
import gc
import os
import shutil
import weakref
from pathlib import Path
from typing import Any

import pytest

import industry_research.fund_context as fund_context_module
from industry_research.fund_context import TransientFundContext, prepare_acceptance_root
from industry_research.models import FundResolutionEmptyReason, VerificationStatus
from industry_research.relationships import resolve_fund_relations


class MemoryAdapter:
    storage_mode = "memory"
    requires_transient_disk = False

    def __init__(self, analyses: dict[str, dict[str, Any] | BaseException]):
        self.analyses = analyses
        self.calls: list[str] = []
        self.closed = 0

    def get_fund_analysis(self, code: str, force_refresh: bool = False) -> dict[str, Any]:
        assert force_refresh is False
        self.calls.append(code)
        value = self.analyses[code]
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed += 1


class DiskAdapter(MemoryAdapter):
    storage_mode = "request_temp"
    requires_transient_disk = True

    def __init__(self, analyses: dict[str, dict[str, Any] | BaseException]):
        super().__init__(analyses)
        self.bound_root: Path | None = None

    def bind_transient_root(self, root: Path) -> None:
        self.bound_root = root

    def get_fund_analysis(self, code: str, force_refresh: bool = False) -> dict[str, Any]:
        assert self.bound_root is not None
        (self.bound_root / "provider.tmp").write_text(code, encoding="utf-8")
        return super().get_fund_analysis(code, force_refresh=force_refresh)


class FailingBindAdapter(DiskAdapter):
    def __init__(self, failure: BaseException):
        super().__init__({})
        self.failure = failure

    def bind_transient_root(self, root: Path) -> None:
        self.bound_root = root
        (root / "opened.tmp").write_text("request resource", encoding="utf-8")
        raise self.failure


def _analysis(
    *,
    code: str = "900001",
    fund_name: str = "普通基金",
    holdings: dict[str, Any] | None = None,
    holdings_status: str = "disclosed",
    holdings_reason: str | None = None,
    exposure: dict[str, Any] | None = None,
    exposure_status: str = "disclosed",
    exposure_reason: str | None = None,
) -> dict[str, Any]:
    holdings_meta = {
        "status": holdings_status,
        "source_name": "公开基金披露",
        "source_reference": "https://example.test/fund/holdings",
        "as_of_date": "2026-06-30",
    }
    exposure_meta = {"status": exposure_status}
    if holdings_reason is not None:
        holdings_meta["availability_reason"] = holdings_reason
    if exposure_reason is not None:
        exposure_meta["availability_reason"] = exposure_reason
    return {
        "code": code,
        "profile": {"data": {"name": fund_name}, "meta": {"status": "disclosed"}},
        "holdings": {
            "data": holdings,
            "meta": holdings_meta,
        },
        "industry_exposure": {
            "data": exposure,
            "meta": exposure_meta,
        },
        "data_quality": {
            "holdings": {"status": holdings_status},
            "industry_exposure": {"status": exposure_status},
        },
    }


def _empty_exposure() -> dict[str, Any]:
    return {
        "official_allocation": {"exposure": []},
        "lookthrough": {"status": "disclosed", "disclosure_date": "2026-06-30"},
        "industry_chain_tags": [],
        "holding_industry_evidence": [],
    }


def _official_config(
    *,
    allocation: dict[str, tuple[str, ...]] | None = None,
    securities: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, object]:
    return {
        "official_allocation_name_to_industry_ids": allocation or {},
        "security_code_to_industry_ids": securities or {},
    }


def _assert_no_private_fields(value: object) -> None:
    forbidden = {"amount", "cost", "notes", "account", "cookie", "key"}
    if isinstance(value, dict):
        assert forbidden.isdisjoint({str(item).casefold() for item in value})
        for item in value.values():
            _assert_no_private_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_private_fields(item)


def test_empty_explicit_input_returns_exact_no_holdings_state_without_adapter_reads() -> None:
    # Break caught: an empty request falls back to a default user holdings cache/database.
    adapter = MemoryAdapter({})
    context = TransientFundContext(adapter=adapter)

    result = resolve_fund_relations(industry_id="storage", fund_codes=[], context=context)

    assert result.state == "no_holdings"
    assert result.fund_selection == ()
    assert result.resolutions == ()
    assert result.pending_lookthrough_selection_ids == ()
    assert adapter.calls == []
    assert context.closed is True


def test_name_keywords_without_disclosed_security_codes_remain_unknown(monkeypatch) -> None:
    # Break caught: fund names containing 存储/芯片 are treated as industry evidence.
    default_store_access = {"reads": 0, "writes": 0}

    def forbidden_default_service(*_args, **_kwargs):
        default_store_access["reads"] += 1
        raise AssertionError("default FundDataService must not be instantiated or read")

    monkeypatch.setattr("fund_data.service.get_service", forbidden_default_service)
    monkeypatch.setattr("fund_data.service.FundDataService", forbidden_default_service)
    adapter = MemoryAdapter({
        "900001": _analysis(
            fund_name="存储芯片精选基金",
            holdings={"holdings": [], "disclosure_date": "2026-06-30"},
            exposure=_empty_exposure(),
        )
    })

    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(adapter=adapter),
    )

    assert result.fund_selection[0].to_dict() == {
        "selection_id": "selection-1",
        "fund_code": "900001",
        "selected_in_request": True,
    }
    assert result.resolutions[0].relation is None
    assert result.resolutions[0].empty_reason is FundResolutionEmptyReason.UNKNOWN
    assert default_store_access == {"reads": 0, "writes": 0}


@pytest.mark.parametrize(
    ("code", "analysis", "expected"),
    [
        (
            "900001",
            _analysis(
                code="900001",
                holdings=None,
                holdings_status="unavailable",
                holdings_reason="not_disclosed",
                exposure=None,
                exposure_status="unavailable",
            ),
            FundResolutionEmptyReason.NOT_DISCLOSED,
        ),
        (
            "900002",
            _analysis(code="900002", holdings=None, holdings_status="error", exposure=None, exposure_status="error"),
            FundResolutionEmptyReason.SOURCE_UNAVAILABLE,
        ),
        (
            "900003",
            RuntimeError("provider unavailable"),
            FundResolutionEmptyReason.SOURCE_UNAVAILABLE,
        ),
    ],
)
def test_resolution_reasons_keep_not_disclosed_and_source_unavailable_distinct(
    code: str,
    analysis: dict[str, Any] | BaseException,
    expected: FundResolutionEmptyReason,
) -> None:
    # Break caught: all unresolved funds collapse into a generic unknown state.
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=[code],
        context=TransientFundContext(adapter=MemoryAdapter({code: analysis})),
    )

    assert result.resolutions[0].relation is None
    assert result.resolutions[0].empty_reason is expected


def test_broad_official_allocation_is_explicitly_pending_not_disclosed_lookthrough() -> None:
    # Break caught: a broad official configuration masquerades as disclosed holdings.
    exposure = _empty_exposure()
    exposure["official_allocation"] = {
        "as_of_date": "2026-06-30",
        "source_name": "基金定期报告",
        "source_reference": "https://example.test/fund/allocation",
        "exposure": [{
            "name": "制造业",
            "display_name": "制造业（待穿透）",
            "weight_pct": 80.0,
            "requires_lookthrough": True,
        }],
    }
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(adapter=MemoryAdapter({
            "900001": _analysis(
                holdings={"holdings": [], "disclosure_date": "2026-06-30"},
                exposure=exposure,
            )
        }), official_industry_config=_official_config(
            allocation={"制造业": ("storage",)},
        )),
    )

    relation = result.resolutions[0].relation
    assert relation is not None
    assert relation.relation_layer == "official_allocation"
    assert relation.disclosure_date == "2026-06-30"
    assert result.pending_lookthrough_selection_ids == ("selection-1",)
    assert relation.status is VerificationStatus.VERIFIED


def test_official_allocation_uses_normalized_authoritative_identity() -> None:
    # Break caught: harmless case/space aliases cannot join to the authoritative config.
    exposure = _empty_exposure()
    exposure["official_allocation"] = {
        "as_of_date": "2026-06-30",
        "source_name": "基金定期报告",
        "source_reference": "https://example.test/fund/allocation",
        "exposure": [{
            "name": "MANUFACTURING",
            "display_name": "Manufacturing (pending lookthrough)",
            "weight_pct": 80.0,
            "requires_lookthrough": True,
        }],
    }

    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(
            adapter=MemoryAdapter({"900001": _analysis(exposure=exposure)}),
            official_industry_config=_official_config(
                allocation={" Manufacturing ": (" Storage ",)},
            ),
        ),
    )

    assert result.resolutions[0].relation is not None
    assert result.resolutions[0].relation.relation_layer == "official_allocation"


@pytest.mark.parametrize(
    "config",
    [
        _official_config(allocation={"Manufacturing": ("storage",), " manufacturing ": ("robotics",)}),
        _official_config(allocation={"Manufacturing": ("storage", " STORAGE ")}),
        _official_config(securities={"688001": ("storage",), " 688001 ": ("robotics",)}),
    ],
    ids=("official-key", "industry-id", "security-key"),
)
def test_authoritative_config_rejects_post_normalization_collisions(config: dict[str, object]) -> None:
    # Break caught: configuration meaning depends on insertion order after aliases normalize.
    adapter = MemoryAdapter({})

    with pytest.raises(ValueError, match="collision"):
        TransientFundContext(adapter=adapter, official_industry_config=config)

    assert adapter.closed == 1


def test_duplicate_normalized_official_row_identities_fail_closed() -> None:
    # Break caught: duplicate aliases let one official allocation row win by ordering.
    exposure = _empty_exposure()
    exposure["official_allocation"] = {
        "as_of_date": "2026-06-30",
        "source_name": "基金定期报告",
        "source_reference": "https://example.test/fund/allocation",
        "exposure": [
            {"name": "Manufacturing", "display_name": "Manufacturing", "weight_pct": 80.0,
             "requires_lookthrough": True},
            {"name": " manufacturing ", "display_name": "Manufacturing alias", "weight_pct": 20.0,
             "requires_lookthrough": True},
        ],
    }

    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(
            adapter=MemoryAdapter({"900001": _analysis(exposure=exposure)}),
            official_industry_config=_official_config(
                allocation={"Manufacturing": ("storage",)},
            ),
        ),
    )

    assert result.resolutions[0].relation is None
    assert result.resolutions[0].empty_reason is FundResolutionEmptyReason.UNKNOWN


def test_disclosed_lookthrough_uses_exact_service_tag_and_security_code_evidence() -> None:
    # Break caught: disclosed exposure is re-calculated or inferred from a fund/stock name.
    exposure = _empty_exposure()
    exposure.update({
        "industry_chain_tags": [{
            "id": "storage",
            "name": "存储",
            "weight_pct": 12.5,
            "evidence_level": "disclosed_stock_classification",
            "source_name": "公开分类",
        }],
        "holding_industry_evidence": [{
            "stock_code": "688001",
            "stock_name": "示例芯片",
            "weight_pct": 12.5,
            "source_name": "公开分类",
            "source_reference": "https://example.test/classification/688001",
            "holding_disclosure_date": "2026-06-30",
        }],
        "lookthrough": {
            "status": "disclosed",
            "disclosure_date": "2026-06-30",
            "calculation_basis": "existing fund service output",
        },
    })
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(adapter=MemoryAdapter({
            "900001": _analysis(
                holdings={"holdings": [{"stock_code": "688001", "weight_pct": 12.5}], "disclosure_date": "2026-06-30"},
                exposure=exposure,
            )
        }), official_industry_config=_official_config(
            securities={"688001": ("storage",)},
        )),
    )

    relation = result.resolutions[0].relation
    assert relation is not None
    assert relation.relation_layer == "disclosed_lookthrough"
    assert relation.exposure_value == 12.5
    assert relation.exposure_unit == "percent"
    assert relation.disclosure_date == "2026-06-30"
    assert relation.evidence_ids
    assert relation.status is VerificationStatus.VERIFIED
    assert result.pending_lookthrough_selection_ids == ()
    _assert_no_private_fields(result.to_dict())


def test_real_service_mixed_holding_evidence_ignores_unrelated_industries() -> None:
    # Break caught: one unrelated classified holding invalidates a valid target aggregate tag.
    exposure = _empty_exposure()
    exposure.update({
        "industry_chain_tags": [{
            "id": "semiconductor-equipment",
            "name": "半导体设备",
            "weight_pct": 12.5,
            "evidence_level": "disclosed_stock_classification",
            "source_name": "巨潮资讯上市公司行业归属",
        }],
        "holding_industry_evidence": [
            {
                "stock_code": "688001",
                "stock_name": "示例设备",
                "weight_pct": 12.5,
                "primary_industry": "制造业",
                "secondary_industry": "专用设备制造业",
                "detail_industry": "半导体设备",
                "fine_industry": "半导体专用设备",
                "classification_standard": "上市公司行业分类指引",
                "source_name": "巨潮资讯上市公司行业归属",
                "source_reference": "https://example.test/classification/688001",
                "holding_disclosure_date": "2026-06-30",
            },
            {
                "stock_code": "600000",
                "stock_name": "示例银行",
                "weight_pct": 5.0,
                "primary_industry": "金融业",
                "secondary_industry": "货币金融服务",
                "detail_industry": "银行",
                "fine_industry": "商业银行",
                "classification_standard": "上市公司行业分类指引",
                "source_name": "巨潮资讯上市公司行业归属",
                "source_reference": "https://example.test/classification/600000",
                "holding_disclosure_date": "2026-06-30",
            },
        ],
    })

    result = resolve_fund_relations(
        industry_id="semiconductor-equipment",
        fund_codes=["900001"],
        context=TransientFundContext(
            adapter=MemoryAdapter({"900001": _analysis(
                holdings={
                    "holdings": [
                        {"stock_code": "688001", "weight_pct": 12.5},
                        {"stock_code": "600000", "weight_pct": 5.0},
                    ],
                    "disclosure_date": "2026-06-30",
                },
                exposure=exposure,
            )}),
            official_industry_config=_official_config(
                securities={
                    "688001": ("semiconductor-equipment",),
                    "600000": ("finance",),
                },
            ),
        ),
    )

    relation = result.resolutions[0].relation
    assert relation is not None
    assert relation.industry_id == "semiconductor-equipment"
    assert relation.exposure_value == 12.5
    assert len(relation.evidence_ids) == 2


@pytest.mark.parametrize(
    ("holdings_rows", "evidence_rows", "tag_weight"),
    (
        ([{"stock_code": "688001"}], [{"stock_code": "688001", "weight_pct": 99.0}], 99.0),
        ([{"stock_code": "688001", "weight_pct": 12.5}], [{"stock_code": "688001"}], 12.5),
        ([{"stock_code": "688001", "weight_pct": 12.5}], [{"stock_code": "688001", "weight_pct": 11.0}], 12.5),
        ([{"stock_code": "688001", "weight_pct": 12.5}, {"stock_code": "688001", "weight_pct": 12.5}], [{"stock_code": "688001", "weight_pct": 12.5}], 12.5),
        ([{"stock_code": "688001", "weight_pct": 12.5}], [{"stock_code": "688001", "weight_pct": 12.5}], 99.0),
        ([{"stock_code": "688001", "weight_pct": float("nan")}], [{"stock_code": "688001", "weight_pct": 12.5}], 12.5),
        ([{"stock_code": "688001", "weight_pct": -1.0}], [{"stock_code": "688001", "weight_pct": -1.0}], 0.0),
        ([{"stock_code": "688001", "weight_pct": 101.0}], [{"stock_code": "688001", "weight_pct": 101.0}], 100.0),
    ),
    ids=(
        "missing-holding-weight", "missing-evidence-weight", "wrong-evidence-weight",
        "duplicate-code", "sum-mismatch", "nan", "negative", "over-100",
    ),
)
def test_disclosed_lookthrough_rejects_unreconciled_weights(
    holdings_rows: list[dict[str, object]],
    evidence_rows: list[dict[str, object]],
    tag_weight: float,
) -> None:
    exposure = _empty_exposure()
    exposure.update({
        "industry_chain_tags": [{
            "id": "storage",
            "weight_pct": tag_weight,
            "evidence_level": "disclosed_stock_classification",
        }],
        "holding_industry_evidence": [{
            **row,
            "source_reference": "https://example.test/classification/688001",
            "holding_disclosure_date": "2026-06-30",
        } for row in evidence_rows],
    })
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(
            adapter=MemoryAdapter({"900001": _analysis(
                holdings={"holdings": holdings_rows, "disclosure_date": "2026-06-30"},
                exposure=exposure,
            )}),
            official_industry_config=_official_config(securities={"688001": ("storage",)}),
        ),
    )

    assert result.resolutions[0].relation is None
    assert result.resolutions[0].empty_reason is FundResolutionEmptyReason.UNKNOWN


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
@pytest.mark.parametrize(
    "outcome",
    [
        _analysis(holdings={"holdings": [], "disclosure_date": "2026-06-30"}, exposure=_empty_exposure()),
        RuntimeError("provider failed"),
        asyncio.CancelledError(),
    ],
    ids=("success", "provider-exception", "cancellation"),
)
def test_disk_adapter_cleanup_is_finally_bound_for_all_exit_paths(tmp_path: Path, outcome: object) -> None:
    # Break caught: request-scoped fund codes remain on disk after success, failure, or cancellation.
    acceptance_root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    acceptance_root.mkdir(parents=True)
    adapter = DiskAdapter({"900001": outcome})
    context = TransientFundContext(adapter=adapter, acceptance_root=acceptance_root)

    if isinstance(outcome, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            resolve_fund_relations(industry_id="storage", fund_codes=["900001"], context=context)
    else:
        resolve_fund_relations(industry_id="storage", fund_codes=["900001"], context=context)

    assert context.closed is True
    assert adapter.closed == 1
    assert context.temp_root is not None
    assert not context.temp_root.exists()
    assert not any(path.is_file() for path in acceptance_root.rglob("*"))


def test_close_is_idempotent_and_memory_mode_never_creates_a_temp_root() -> None:
    # Break caught: repeated request shutdown performs a second write/cleanup or memory mode spills to disk.
    adapter = MemoryAdapter({})
    context = TransientFundContext(adapter=adapter)

    context.close()
    context.close()

    assert context.closed is True
    assert context.temp_root is None
    assert adapter.closed == 1


def test_prepare_acceptance_root_rejects_reparse_before_first_write(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / ".tmp" / "acceptance" / "fund-requests"
    observed_writes: list[Path] = []
    original_mkdir = Path.mkdir

    def record_mkdir(path: Path, *args, **kwargs):
        observed_writes.append(path)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", record_mkdir)
    monkeypatch.setattr(
        fund_context_module,
        "_path_is_reparse",
        lambda path: path == tmp_path,
    )

    with pytest.raises(RuntimeError, match="reparse"):
        prepare_acceptance_root(root)

    assert observed_writes == []
    assert not root.exists()


def test_request_contract_rejects_non_code_objects_before_any_adapter_access() -> None:
    # Break caught: amount/cost/notes/account-bearing objects enter the resolver boundary.
    adapter = MemoryAdapter({})
    context = TransientFundContext(adapter=adapter)

    with pytest.raises(TypeError, match="fund code"):
        resolve_fund_relations(
            industry_id="storage",
            fund_codes=[{"code": "900001", "amount": 1}],  # type: ignore[list-item]
            context=context,
        )

    assert adapter.calls == []
    assert context.closed is True


def test_analysis_identity_must_equal_the_requested_fund_code() -> None:
    # Break caught: a cached analysis for another fund is projected under the requested code.
    exposure = _empty_exposure()
    exposure["official_allocation"] = {
        "as_of_date": "2026-06-30",
        "source_name": "基金定期报告",
        "source_reference": "https://example.test/fund/allocation",
        "exposure": [{"name": "制造业", "display_name": "制造业（待穿透）", "weight_pct": 80.0,
                      "requires_lookthrough": True}],
    }
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(
            adapter=MemoryAdapter({"900001": _analysis(code="900099", exposure=exposure)}),
            official_industry_config=_official_config(allocation={"制造业": ("storage",)}),
        ),
    )

    assert result.resolutions[0].relation is None
    assert result.resolutions[0].empty_reason is FundResolutionEmptyReason.UNKNOWN


@pytest.mark.parametrize(
    ("holdings_status", "exposure_status", "reason"),
    [
        ("error", "disclosed", FundResolutionEmptyReason.SOURCE_UNAVAILABLE),
        ("disclosed", "unavailable", FundResolutionEmptyReason.UNKNOWN),
    ],
)
def test_unusable_consumed_section_status_never_forms_lookthrough_relation(
    holdings_status: str,
    exposure_status: str,
    reason: FundResolutionEmptyReason,
) -> None:
    # Break caught: relation payload data is trusted even though its section meta reports failure.
    exposure = _empty_exposure()
    exposure.update({
        "industry_chain_tags": [{"id": "storage", "weight_pct": 12.5,
                                  "evidence_level": "disclosed_stock_classification"}],
        "holding_industry_evidence": [{"stock_code": "688001", "source_reference": "https://example.test/c/1",
                                        "holding_disclosure_date": "2026-06-30"}],
    })
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(
            adapter=MemoryAdapter({"900001": _analysis(
                holdings={"holdings": [{"stock_code": "688001"}], "disclosure_date": "2026-06-30"},
                holdings_status=holdings_status,
                exposure=exposure,
                exposure_status=exposure_status,
            )}),
            official_industry_config=_official_config(securities={"688001": ("storage",)}),
        ),
    )

    assert result.resolutions[0].relation is None
    assert result.resolutions[0].empty_reason is reason


def test_ambiguous_unavailable_without_structured_reason_stays_unknown() -> None:
    # Break caught: an ambiguous unavailable status is guessed to mean no disclosure.
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(adapter=MemoryAdapter({
            "900001": _analysis(holdings=None, holdings_status="unavailable", exposure=None,
                                exposure_status="unavailable")
        })),
    )

    assert result.resolutions[0].empty_reason is FundResolutionEmptyReason.UNKNOWN


@pytest.mark.parametrize(
    ("holdings", "status"),
    [
        (None, "disclosed"),
        ({"holdings": [], "disclosure_date": "2026-06-30"}, "unavailable"),
    ],
    ids=("usable-status-conflict", "data-present-conflict"),
)
def test_not_disclosed_requires_consistent_structured_absence(
    holdings: dict[str, Any] | None,
    status: str,
) -> None:
    # Break caught: a contradictory reason marker is accepted as proof of disclosure absence.
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(adapter=MemoryAdapter({
            "900001": _analysis(
                holdings=holdings,
                holdings_status=status,
                holdings_reason="not_disclosed",
                exposure=None,
                exposure_status="unavailable",
            )
        })),
    )

    assert result.resolutions[0].empty_reason is FundResolutionEmptyReason.UNKNOWN


def test_structured_source_failure_blocks_conflicting_usable_payload() -> None:
    # Break caught: a disclosed status masks an explicit source-failure reason and forms a relation.
    exposure = _empty_exposure()
    exposure["official_allocation"] = {
        "as_of_date": "2026-06-30",
        "source_name": "基金定期报告",
        "source_reference": "https://example.test/fund/allocation",
        "exposure": [{"name": "制造业", "display_name": "制造业（待穿透）", "weight_pct": 80.0,
                      "requires_lookthrough": True}],
    }
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(
            adapter=MemoryAdapter({"900001": _analysis(
                exposure=exposure,
                exposure_status="disclosed",
                exposure_reason="source_unavailable",
            )}),
            official_industry_config=_official_config(allocation={"制造业": ("storage",)}),
        ),
    )

    assert result.resolutions[0].relation is None
    assert result.resolutions[0].empty_reason is FundResolutionEmptyReason.SOURCE_UNAVAILABLE


@pytest.mark.parametrize(
    ("holdings_date", "lookthrough_date", "evidence_date", "security_industries"),
    [
        ("2026-03-31", "2026-06-30", "2026-06-30", ("storage",)),
        ("2026-06-30", "2026-06-30", "2026-03-31", ("storage",)),
        ("2026-06-30", "2026-06-30", "2026-06-30", ("semiconductor",)),
    ],
    ids=("holdings-date-mismatch", "evidence-date-mismatch", "foreign-industry"),
)
def test_lookthrough_rejects_mismatched_dates_or_foreign_industry_evidence(
    holdings_date: str,
    lookthrough_date: str,
    evidence_date: str,
    security_industries: tuple[str, ...],
) -> None:
    # Break caught: unrelated disclosure/classification evidence supports a storage relation.
    exposure = _empty_exposure()
    exposure.update({
        "industry_chain_tags": [{"id": "storage", "weight_pct": 12.5,
                                  "evidence_level": "disclosed_stock_classification"}],
        "holding_industry_evidence": [{"stock_code": "688001", "source_reference": "https://example.test/c/1",
                                        "holding_disclosure_date": evidence_date}],
        "lookthrough": {"status": "disclosed", "disclosure_date": lookthrough_date},
    })
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(
            adapter=MemoryAdapter({"900001": _analysis(
                holdings={"holdings": [{"stock_code": "688001"}], "disclosure_date": holdings_date},
                exposure=exposure,
            )}),
            official_industry_config=_official_config(securities={"688001": security_industries}),
        ),
    )

    assert result.resolutions[0].relation is None
    assert result.resolutions[0].empty_reason is FundResolutionEmptyReason.UNKNOWN


def test_lookthrough_rejects_foreign_security_and_fund_evidence() -> None:
    # Break caught: evidence for another fund/security is admitted under the selected fund.
    exposure = _empty_exposure()
    exposure.update({
        "fund_code": "900099",
        "industry_chain_tags": [{"id": "storage", "weight_pct": 12.5,
                                  "evidence_level": "disclosed_stock_classification"}],
        "holding_industry_evidence": [{"fund_code": "900099", "stock_code": "688002",
                                        "source_reference": "https://example.test/c/2",
                                        "holding_disclosure_date": "2026-06-30"}],
    })
    result = resolve_fund_relations(
        industry_id="storage",
        fund_codes=["900001"],
        context=TransientFundContext(
            adapter=MemoryAdapter({"900001": _analysis(
                holdings={"fund_code": "900001", "holdings": [{"stock_code": "688001"}],
                          "disclosure_date": "2026-06-30"},
                exposure=exposure,
            )}),
            official_industry_config=_official_config(securities={"688002": ("storage",)}),
        ),
    )

    assert result.resolutions[0].relation is None
    assert result.resolutions[0].empty_reason is FundResolutionEmptyReason.UNKNOWN


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
@pytest.mark.parametrize("failure", [RuntimeError("bind failed"), asyncio.CancelledError()],
                         ids=("exception", "cancellation"))
def test_constructor_bind_failure_closes_adapter_before_removing_temp(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    # Break caught: an already-open request adapter leaks when bind raises BaseException.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = FailingBindAdapter(failure)

    with pytest.raises(type(failure)) as raised:
        TransientFundContext(adapter=adapter, acceptance_root=root)

    assert raised.value is failure
    assert adapter.closed == 1
    assert not any(root.iterdir())


@pytest.mark.parametrize("capability", [None, "false", 0], ids=("missing", "string", "integer"))
def test_ambiguous_disk_capability_fails_before_filesystem_mutation(
    tmp_path: Path,
    capability: object,
) -> None:
    # Break caught: an adapter without an exact disk capability reaches temp-root creation.
    class AmbiguousAdapter:
        storage_mode = "request_temp"

        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    adapter = AmbiguousAdapter()
    if capability is not None:
        adapter.requires_transient_disk = capability  # type: ignore[attr-defined]
    root = tmp_path / ".tmp" / "acceptance" / "must-not-exist"

    with pytest.raises(ValueError, match="requires_transient_disk"):
        TransientFundContext(adapter=adapter, acceptance_root=root)  # type: ignore[arg-type]

    assert adapter.closed == 1
    assert not root.exists()


def test_memory_adapter_never_touches_or_binds_transient_path(tmp_path: Path) -> None:
    # Break caught: a memory-only adapter is forced through disk-root validation.
    adapter = MemoryAdapter({"900001": _analysis()})
    root = tmp_path / ".tmp" / "acceptance" / "must-not-exist"

    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    try:
        assert context.temp_root is None
        assert context.analyze("900001")["code"] == "900001"
        assert not root.exists()
    finally:
        context.close()

    assert adapter.closed == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX pathless memory contract")
def test_posix_memory_adapter_works_without_any_transient_disk(tmp_path: Path) -> None:
    root = tmp_path / ".tmp" / "acceptance" / "must-not-exist"
    adapter = MemoryAdapter({"900001": _analysis()})

    with TransientFundContext(adapter=adapter, acceptance_root=root) as context:
        assert context.temp_root is None
        assert context.analyze("900001")["code"] == "900001"

    assert adapter.closed == 1
    assert not root.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX disk-backed context is fail-closed")
def test_posix_disk_adapter_is_rejected_before_temp_creation_or_binding(tmp_path: Path) -> None:
    # Break caught: POSIX creates a child before discovering safe recursive deletion is unavailable.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    before = tuple(root.iterdir())
    adapter = DiskAdapter({})

    with pytest.raises(RuntimeError, match="POSIX.*disk"):
        TransientFundContext(adapter=adapter, acceptance_root=root)

    assert adapter.closed == 1
    assert adapter.bound_root is None
    assert tuple(root.iterdir()) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_nonexistent_acceptance_parent_is_rejected_and_adapter_is_closed(tmp_path: Path) -> None:
    # Break caught: the context creates an unvalidated parent chain before checking its identity.
    root = tmp_path / ".tmp" / "acceptance" / "missing"
    adapter = DiskAdapter({})
    context = None
    try:
        with pytest.raises(ValueError, match="must already exist"):
            context = TransientFundContext(adapter=adapter, acceptance_root=root)
    finally:
        if context is not None:
            context.close()

    assert adapter.closed == 1
    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_directory_identity_swap_refuses_delete_then_allows_safe_retry(tmp_path: Path, monkeypatch) -> None:
    # Break caught: a replaced request directory is recursively deleted by pathname alone.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    original = getattr(context, "_directory_identity", lambda _path: (1, 1, 0, 0))

    def swapped(path: Path):
        identity = original(path)
        return (identity[0], identity[1] + 1, *identity[2:]) if path == context.temp_root else identity

    monkeypatch.setattr(context, "_directory_identity", swapped, raising=False)
    try:
        with pytest.raises(RuntimeError, match="identity"):
            context.close()
        assert context.closed is False
        assert adapter.closed == 1
        assert context.temp_root is not None and context.temp_root.exists()
    finally:
        monkeypatch.setattr(context, "_directory_identity", original, raising=False)
        if not context.closed:
            context.close()

    assert context.closed is True
    assert adapter.closed == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_reparse_detection_refuses_delete_then_allows_safe_retry(tmp_path: Path, monkeypatch) -> None:
    # Break caught: a junction/reparse target is followed during recursive cleanup.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    original = getattr(context, "_directory_identity", lambda _path: (1, 1, 0, 0))

    def reparse(path: Path):
        if path == context.temp_root:
            raise RuntimeError("reparse directory rejected")
        return original(path)

    monkeypatch.setattr(context, "_directory_identity", reparse, raising=False)
    try:
        with pytest.raises(RuntimeError, match="reparse"):
            context.close()
        assert context.closed is False
        assert context.temp_root is not None and context.temp_root.exists()
    finally:
        monkeypatch.setattr(context, "_directory_identity", original, raising=False)
        if not context.closed:
            context.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows directory-handle rename guard")
@pytest.mark.parametrize("swap_target", ["acceptance-parent", "transient-child"])
def test_guarded_cleanup_blocks_parent_or_child_swap_inside_delete(
    tmp_path: Path,
    monkeypatch,
    swap_target: str,
) -> None:
    # Break caught: a path is swapped after final lstat and its replacement is recursively deleted.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    transient = context.temp_root
    target = root if swap_target == "acceptance-parent" else transient
    moved = target.with_name(f"{target.name}-original")
    guard_type = fund_context_module._DirectoryGuard
    original_delete_empty = guard_type.delete_empty
    attempted = False
    swap_blocked = False

    def inject_swap(self):
        nonlocal attempted, swap_blocked
        if self.path == transient and not attempted:
            attempted = True
            try:
                target.rename(moved)
            except OSError:
                swap_blocked = True
            else:
                target.mkdir(parents=True, exist_ok=True)
                (target / "replacement.marker").write_text("must survive", encoding="utf-8")
        return original_delete_empty(self)

    monkeypatch.setattr(guard_type, "delete_empty", inject_swap)
    try:
        context.close()

        assert attempted is True
        assert swap_blocked is True
        assert context.closed is True
        assert adapter.closed == 1
        assert not transient.exists()
        assert not moved.exists()
    finally:
        monkeypatch.setattr(guard_type, "delete_empty", original_delete_empty)
        if not context.closed:
            if moved.exists():
                if target.exists():
                    shutil.rmtree(target)
                moved.rename(target)
            context.close()
        if moved.exists():
            if target.exists():
                shutil.rmtree(target)
            shutil.rmtree(moved)


@pytest.mark.skipif(os.name != "nt", reason="Windows nested directory-handle rename guard")
def test_guarded_cleanup_blocks_nested_directory_swap_before_enumeration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: a nested directory is replaced after lstat and its replacement is traversed.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    payload = context.temp_root / "payload"
    payload.mkdir()
    (payload / "public.tmp").write_text("public fixture", encoding="utf-8")
    moved = payload.with_name("payload-original")
    original_iterdir = Path.iterdir
    attempted = False
    swap_blocked = False

    def inject_swap(path: Path):
        nonlocal attempted, swap_blocked
        if path == payload and not attempted:
            attempted = True
            try:
                payload.rename(moved)
            except OSError:
                swap_blocked = True
            else:
                payload.mkdir()
                (payload / "replacement.marker").write_text("must survive", encoding="utf-8")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", inject_swap)
    try:
        context.close()
        assert attempted is True
        assert swap_blocked is True
        assert context.closed is True
        assert not moved.exists()
    finally:
        monkeypatch.setattr(Path, "iterdir", original_iterdir)
        if not context.closed:
            if moved.exists():
                if payload.exists():
                    shutil.rmtree(payload)
                moved.rename(payload)
            context.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded write-share contract")
def test_guarded_cleanup_denies_writable_handle_before_nested_enumeration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: a concurrent writable handle can mutate a validated directory into a reparse point.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    payload = context.temp_root / "payload"
    payload.mkdir()
    (payload / "public.tmp").write_text("public fixture", encoding="utf-8")
    original_iterdir = Path.iterdir
    attempted = False
    writable_opened = False

    def probe_write_share(path: Path):
        nonlocal attempted, writable_opened
        if path == payload and not attempted:
            attempted = True
            handle = fund_context_module._KERNEL32.CreateFileW(
                str(payload),
                0x40000000,
                0x00000001 | 0x00000002 | 0x00000004,
                None,
                3,
                0x02000000 | 0x00200000,
                None,
            )
            if handle != fund_context_module.ctypes.c_void_p(-1).value:
                writable_opened = True
                assert fund_context_module._KERNEL32.CloseHandle(handle)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", probe_write_share)
    context.close()

    assert attempted is True
    assert writable_opened is False
    assert context.closed is True


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_guarded_delete_failure_keeps_context_retryable_without_double_closing_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: a failed delete marks the context closed and prevents residue cleanup retry.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    transient = context.temp_root
    guard_type = fund_context_module._DirectoryGuard
    original = guard_type.delete_empty
    calls = 0

    def fail_once(self):
        nonlocal calls
        if self.path == transient:
            calls += 1
            if calls == 1:
                raise OSError("injected guarded delete failure")
        return original(self)

    monkeypatch.setattr(guard_type, "delete_empty", fail_once)
    with pytest.raises(OSError, match="injected guarded delete failure"):
        context.close()

    assert context.closed is False
    assert adapter.closed == 1
    assert context.temp_root is not None and context.temp_root.exists()

    context.close()

    assert context.closed is True
    assert adapter.closed == 1
    assert context.temp_root is not None and not context.temp_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_delete_pending_handle_close_failure_retries_handle_before_path_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: retry lstat runs before closing a retained delete-pending child handle.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    transient = context.temp_root
    guard_type = fund_context_module._DirectoryGuard
    original_close = guard_type.close
    failed = False

    def fail_once_after_disposition(self) -> None:
        nonlocal failed
        if self.path == transient and not failed:
            failed = True
            raise OSError("injected post-disposition handle close failure")
        original_close(self)

    monkeypatch.setattr(guard_type, "close", fail_once_after_disposition)
    with pytest.raises(OSError, match="post-disposition handle close failure"):
        context.close()

    assert context.closed is False
    assert adapter.closed == 1
    assert context._temp_guard is not None

    context.close()
    assert context.closed is True
    assert adapter.closed == 1
    assert not transient.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_abort_cancels_delete_pending_disposition_and_preserves_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: abandonment closes a delete-pending handle and deletes the verified path.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    transient = context.temp_root
    guard_type = fund_context_module._DirectoryGuard
    original_close = guard_type.close
    failed = False

    def fail_once_after_disposition(self) -> None:
        nonlocal failed
        if self.path == transient and not failed:
            failed = True
            raise OSError("injected pending-delete close failure before abort")
        original_close(self)

    monkeypatch.setattr(guard_type, "close", fail_once_after_disposition)
    with pytest.raises(OSError, match="pending-delete close failure"):
        context.close()

    assert context._temp_guard is not None
    assert context._temp_guard.delete_pending is True
    context.abort()

    assert context.closed is True
    assert adapter.closed == 1
    assert transient.exists()
    moved = transient.with_name(f"{transient.name}-abort-preserved")
    transient.rename(moved)
    moved.rename(transient)
    shutil.rmtree(transient)


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_cross_device_cleanup_entry_is_refused_without_recursion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: recursive cleanup crosses a mounted/reparse device boundary.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    payload = context.temp_root / "payload"
    payload.mkdir()
    (payload / "public.tmp").write_text("public fixture", encoding="utf-8")
    original_lstat = context._cleanup_entry_lstat

    def foreign_device(path: Path):
        info = original_lstat(path)
        if path == payload:
            class ForeignDevice:
                st_dev = info.st_dev + 1
                st_ino = info.st_ino
                st_mode = info.st_mode
                st_reparse_tag = getattr(info, "st_reparse_tag", 0)

            return ForeignDevice()
        return info

    monkeypatch.setattr(context, "_cleanup_entry_lstat", foreign_device)
    with pytest.raises(RuntimeError, match="device boundary"):
        context.close()

    assert context.closed is False
    assert adapter.closed == 1
    assert (payload / "public.tmp").exists()

    monkeypatch.setattr(context, "_cleanup_entry_lstat", original_lstat)
    context.close()
    assert context.closed is True


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_guard_close_failure_after_temp_removal_remains_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: _temp_removed skips a still-open failed ancestor guard on retry.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    transient = context.temp_root
    failed_guard = context._ancestor_guards[-1][2]
    original_close = failed_guard.close
    calls = 0

    def fail_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected guard close failure")
        original_close()

    monkeypatch.setattr(failed_guard, "close", fail_once)
    with pytest.raises(OSError, match="injected guard close failure"):
        context.close()

    assert context.closed is False
    assert not transient.exists()
    assert adapter.closed == 1
    assert [entry[2] for entry in context._ancestor_guards] == [failed_guard]

    context.close()
    assert context.closed is True
    assert context._ancestor_guards == []
    assert adapter.closed == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_abort_guard_close_failure_keeps_context_open_for_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: abort marks closed even though a raw guard close failed.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    transient = context.temp_root
    failed_guard = context._ancestor_guards[-1][2]
    original_close = failed_guard.close
    calls = 0

    def fail_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected abort guard close failure")
        original_close()

    monkeypatch.setattr(failed_guard, "close", fail_once)
    with pytest.raises(OSError, match="injected abort guard close failure"):
        context.abort()

    assert context.closed is False
    assert transient.exists()
    assert adapter.closed == 1
    assert [entry[2] for entry in context._ancestor_guards] == [failed_guard]

    context.abort()
    assert context.closed is True
    assert transient.exists()
    shutil.rmtree(transient)


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_guard_constructor_closes_raw_resource_when_post_open_identity_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: a handle/fd opened before identity validation leaks when validation raises.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    guard_type = fund_context_module._DirectoryGuard
    original_identity = guard_type.identity
    original_close = guard_type.close
    closed_paths: list[Path] = []
    failed = False

    def fail_root_identity(self):
        nonlocal failed
        if self.path == root and not failed:
            failed = True
            raise RuntimeError("injected post-open identity failure")
        return original_identity(self)

    def record_close(self):
        closed_paths.append(self.path)
        return original_close(self)

    monkeypatch.setattr(guard_type, "identity", fail_root_identity)
    monkeypatch.setattr(guard_type, "close", record_close)

    with pytest.raises(RuntimeError, match="post-open identity failure"):
        TransientFundContext(adapter=adapter, acceptance_root=root)

    assert root in closed_paths
    assert adapter.closed == 1
    moved = root.with_name("v0.2-w3-resource-released")
    root.rename(moved)
    moved.rename(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_abort_releases_resources_without_deleting_unverified_temp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: abandoning a retryable cleanup either leaks guards or deletes an unverified path.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    context = TransientFundContext(adapter=adapter, acceptance_root=root)
    assert context.temp_root is not None
    transient = context.temp_root
    original_identity = context._directory_identity
    original_rmtree = shutil.rmtree

    def changed_identity(path: Path):
        identity = original_identity(path)
        return (identity[0], identity[1] + 1, *identity[2:]) if path == transient else identity

    monkeypatch.setattr(context, "_directory_identity", changed_identity)
    try:
        with pytest.raises(RuntimeError, match="identity"):
            context.close()

        context.abort()

        assert context.closed is True
        assert adapter.closed == 1
        assert transient.exists()
        with pytest.raises(RuntimeError, match="closed"):
            context.analyze("900001")
        moved = transient.with_name(f"{transient.name}-aborted")
        transient.rename(moved)
        moved.rename(transient)
    finally:
        monkeypatch.setattr(context, "_directory_identity", original_identity)
        if not context.closed:
            context.close()
        elif transient.exists():
            original_rmtree(transient)


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded disk cleanup")
def test_failed_context_manager_cleanup_dropped_reference_releases_guards_without_delete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Break caught: a failed __exit__ followed by GC leaves raw handles open indefinitely.
    root = tmp_path / ".tmp" / "acceptance" / "v0.2-w3"
    root.mkdir(parents=True)
    adapter = DiskAdapter({})
    original_rmtree = shutil.rmtree
    guard_type = fund_context_module._DirectoryGuard
    original_delete_empty = guard_type.delete_empty

    def fail_delete(self):
        if self.path.name.startswith("fund-context-"):
            raise OSError("injected context-manager cleanup failure")
        return original_delete_empty(self)

    monkeypatch.setattr(guard_type, "delete_empty", fail_delete)

    def leave_failed_context() -> tuple[weakref.ReferenceType[TransientFundContext], Path]:
        context = TransientFundContext(adapter=adapter, acceptance_root=root)
        assert context.temp_root is not None
        payload = context.temp_root / "payload"
        payload.mkdir()
        (payload / "public.tmp").write_text("public fixture", encoding="utf-8")
        reference = weakref.ref(context)
        transient = context.temp_root
        try:
            with context:
                pass
        except OSError as error:
            assert "context-manager cleanup failure" in str(error)
        return reference, transient

    reference, transient = leave_failed_context()
    monkeypatch.setattr(guard_type, "delete_empty", original_delete_empty)
    gc.collect()

    assert reference() is None
    assert adapter.closed == 1
    assert transient.exists()
    moved = transient.with_name(f"{transient.name}-gc-released")
    transient.rename(moved)
    moved.rename(transient)
    original_rmtree(transient)
