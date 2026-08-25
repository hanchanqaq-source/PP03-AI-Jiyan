from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from industry_research.fund_context import TransientFundContext
from industry_research.models import FundResolutionEmptyReason, VerificationStatus
from industry_research.relationships import resolve_fund_relations


class MemoryAdapter:
    storage_mode = "memory"

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

    def __init__(self, analyses: dict[str, dict[str, Any] | BaseException]):
        super().__init__(analyses)
        self.bound_root: Path | None = None

    def bind_transient_root(self, root: Path) -> None:
        self.bound_root = root

    def get_fund_analysis(self, code: str, force_refresh: bool = False) -> dict[str, Any]:
        assert self.bound_root is not None
        (self.bound_root / "provider.tmp").write_text(code, encoding="utf-8")
        return super().get_fund_analysis(code, force_refresh=force_refresh)


def _analysis(
    *,
    fund_name: str = "普通基金",
    holdings: dict[str, Any] | None = None,
    holdings_status: str = "disclosed",
    exposure: dict[str, Any] | None = None,
    exposure_status: str = "disclosed",
) -> dict[str, Any]:
    return {
        "code": "900001",
        "profile": {"data": {"name": fund_name}, "meta": {"status": "disclosed"}},
        "holdings": {
            "data": holdings,
            "meta": {
                "status": holdings_status,
                "source_name": "公开基金披露",
                "source_reference": "https://example.test/fund/holdings",
                "as_of_date": "2026-06-30",
            },
        },
        "industry_exposure": {
            "data": exposure,
            "meta": {"status": exposure_status},
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
            _analysis(holdings=None, holdings_status="unavailable", exposure=None, exposure_status="unavailable"),
            FundResolutionEmptyReason.NOT_DISCLOSED,
        ),
        (
            "900002",
            _analysis(holdings=None, holdings_status="error", exposure=None, exposure_status="error"),
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
            "industry_id": "storage",
            "name": "制造业",
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
        })),
    )

    relation = result.resolutions[0].relation
    assert relation is not None
    assert relation.relation_layer == "official_allocation"
    assert relation.disclosure_date == "2026-06-30"
    assert result.pending_lookthrough_selection_ids == ("selection-1",)
    assert relation.status is VerificationStatus.VERIFIED


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
                holdings={"holdings": [{"stock_code": "688001"}], "disclosure_date": "2026-06-30"},
                exposure=exposure,
            )
        })),
    )

    relation = result.resolutions[0].relation
    assert relation is not None
    assert relation.relation_layer == "disclosed_lookthrough"
    assert relation.exposure_value == 12.5
    assert relation.exposure_unit == "percent"
    assert relation.disclosure_date == "2026-06-30"
    assert relation.evidence_ids
    assert result.pending_lookthrough_selection_ids == ()
    _assert_no_private_fields(result.to_dict())


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
