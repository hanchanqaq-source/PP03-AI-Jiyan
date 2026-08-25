from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from .fund_context import TransientFundContext
from .models import (
    FundResolutionEmptyReason,
    FundSelectionScope,
    IndustryCompanyRelation,
    IndustryFundRelation,
    IndustryFundRelationResolution,
    VerificationStatus,
    WireModel,
)


_SIX_DIGIT_CODE = re.compile(r"^[0-9]{6}$")
_SOURCE_FAILURE_STATUSES = {"error", "failed", "source_failure", "source_unavailable"}


@dataclass(frozen=True, slots=True)
class FundRelationProjection(WireModel):
    state: str
    fund_selection: tuple[FundSelectionScope, ...]
    resolutions: tuple[IndustryFundRelationResolution, ...]
    pending_lookthrough_selection_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state not in {"no_holdings", "resolved"}:
            raise ValueError("invalid fund relation projection state")
        if self.state == "no_holdings" and (
            self.fund_selection or self.resolutions or self.pending_lookthrough_selection_ids
        ):
            raise ValueError("no_holdings projection must be empty")
        if len(self.fund_selection) != len(self.resolutions):
            raise ValueError("fund selection and resolution counts must match")
        selection_ids = {item.selection_id for item in self.fund_selection}
        if any(item not in selection_ids for item in self.pending_lookthrough_selection_ids):
            raise ValueError("pending lookthrough must reference a request selection")


def _nonblank(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())


def project_company_relations(
    *,
    industry_id: str,
    candidates: Iterable[Mapping[str, object]],
) -> tuple[IndustryCompanyRelation, ...]:
    """Admit only exact-code company relations backed by official evidence."""
    projected: list[IndustryCompanyRelation] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or candidate.get("industry_id") != industry_id:
            continue
        security_code = _nonblank(candidate.get("security_code"))
        if security_code is None or _SIX_DIGIT_CODE.fullmatch(security_code) is None:
            continue
        if candidate.get("official_evidence") is not True:
            continue
        relation_type = _nonblank(candidate.get("relation_type"))
        evidence_ids = _string_tuple(candidate.get("evidence_ids"))
        company_name = _nonblank(candidate.get("company_name"))
        chain_node_id = _nonblank(candidate.get("chain_node_id"))
        as_of_date = _nonblank(candidate.get("as_of_date"))
        if (
            relation_type not in {"official_disclosure", "public_classification"}
            or not evidence_ids
            or company_name is None
            or chain_node_id is None
            or as_of_date is None
        ):
            continue
        key = (security_code, chain_node_id, relation_type)
        if key in seen:
            continue
        seen.add(key)
        projected.append(IndustryCompanyRelation(
            industry_id=industry_id,
            security_code=security_code,
            company_name=company_name,
            chain_node_id=chain_node_id,
            relation_type=relation_type,
            key_metric_ids=_string_tuple(candidate.get("key_metric_ids")),
            evidence_ids=tuple(dict.fromkeys(evidence_ids)),
            as_of_date=as_of_date,
            observation_only=True,
        ))
    return tuple(sorted(projected, key=lambda item: (item.chain_node_id, item.security_code)))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, (list, tuple)) else ()


def _finite_percent(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    result = float(value)
    return result if math.isfinite(result) and 0 <= result <= 100 else None


def _evidence_id(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"fund-public-{digest}"


def _holdings_codes(analysis: Mapping[str, Any]) -> set[str]:
    holdings = _mapping(_mapping(analysis.get("holdings")).get("data"))
    codes: set[str] = set()
    for row in _sequence(holdings.get("holdings")):
        code = _nonblank(_mapping(row).get("stock_code"))
        if code is not None and _SIX_DIGIT_CODE.fullmatch(code):
            codes.add(code)
    return codes


def _lookthrough_relation(
    *,
    industry_id: str,
    fund_code: str,
    analysis: Mapping[str, Any],
) -> IndustryFundRelation | None:
    exposure = _mapping(_mapping(analysis.get("industry_exposure")).get("data"))
    lookthrough = _mapping(exposure.get("lookthrough"))
    if lookthrough.get("status") != "disclosed":
        return None
    disclosure_date = _nonblank(lookthrough.get("disclosure_date"))
    if disclosure_date is None:
        disclosure_date = _nonblank(_mapping(_mapping(analysis.get("holdings")).get("data")).get("disclosure_date"))
    if disclosure_date is None:
        return None
    tag = next((
        _mapping(item)
        for item in _sequence(exposure.get("industry_chain_tags"))
        if _mapping(item).get("id") == industry_id
        and _mapping(item).get("evidence_level") == "disclosed_stock_classification"
    ), None)
    if not tag:
        return None
    value = _finite_percent(tag.get("weight_pct"))
    if value is None:
        return None
    disclosed_codes = _holdings_codes(analysis)
    if not disclosed_codes:
        return None
    holding_meta = _mapping(_mapping(analysis.get("holdings")).get("meta"))
    holdings_reference = _nonblank(holding_meta.get("source_reference"))
    if holdings_reference is None:
        return None
    evidence_ids = {
        _evidence_id("holding-disclosure", holdings_reference, fund_code, disclosure_date)
    }
    classification_references: set[str] = set()
    evidenced_codes: set[str] = set()
    for item in _sequence(exposure.get("holding_industry_evidence")):
        row = _mapping(item)
        code = _nonblank(row.get("stock_code"))
        reference = _nonblank(row.get("source_reference"))
        evidence_date = _nonblank(row.get("holding_disclosure_date"))
        if (
            code is None
            or code not in disclosed_codes
            or _SIX_DIGIT_CODE.fullmatch(code) is None
            or reference is None
            or evidence_date != disclosure_date
        ):
            continue
        classification_references.add(reference)
        evidenced_codes.add(code)
        evidence_ids.add(_evidence_id("security-classification", reference, code, disclosure_date))
    if not evidenced_codes:
        return None
    source_count = len(classification_references | {holdings_reference})
    status = VerificationStatus.CORROBORATED if source_count >= 2 else VerificationStatus.VERIFIED
    return IndustryFundRelation(
        industry_id=industry_id,
        fund_code=fund_code,
        relation_layer="disclosed_lookthrough",
        exposure_value=value,
        exposure_unit="percent",
        disclosure_date=disclosure_date,
        evidence_ids=tuple(sorted(evidence_ids)),
        status=status,
    )


def _official_allocation_relation(
    *,
    industry_id: str,
    fund_code: str,
    analysis: Mapping[str, Any],
) -> tuple[IndustryFundRelation | None, bool]:
    exposure = _mapping(_mapping(analysis.get("industry_exposure")).get("data"))
    allocation = _mapping(exposure.get("official_allocation"))
    as_of_date = _nonblank(allocation.get("as_of_date"))
    source_reference = _nonblank(allocation.get("source_reference"))
    if as_of_date is None or source_reference is None:
        return None, False
    row = next((
        _mapping(item)
        for item in _sequence(allocation.get("exposure"))
        if _mapping(item).get("industry_id") == industry_id
    ), None)
    if not row:
        return None, False
    value = _finite_percent(row.get("weight_pct"))
    if value is None:
        return None, False
    relation = IndustryFundRelation(
        industry_id=industry_id,
        fund_code=fund_code,
        relation_layer="official_allocation",
        exposure_value=value,
        exposure_unit="percent",
        disclosure_date=as_of_date,
        evidence_ids=(_evidence_id("official-allocation", source_reference, fund_code, as_of_date),),
        status=VerificationStatus.VERIFIED,
    )
    return relation, row.get("requires_lookthrough") is True


def _unresolved_reason(analysis: Mapping[str, Any]) -> FundResolutionEmptyReason:
    holdings_section = _mapping(analysis.get("holdings"))
    holdings_data = holdings_section.get("data")
    holdings_status = str(_mapping(holdings_section.get("meta")).get("status") or "").casefold()
    exposure_status = str(
        _mapping(_mapping(analysis.get("industry_exposure")).get("meta")).get("status") or ""
    ).casefold()
    if holdings_status in _SOURCE_FAILURE_STATUSES or exposure_status in _SOURCE_FAILURE_STATUSES:
        return FundResolutionEmptyReason.SOURCE_UNAVAILABLE
    if holdings_data is None or holdings_status == "not_disclosed":
        return FundResolutionEmptyReason.NOT_DISCLOSED
    return FundResolutionEmptyReason.UNKNOWN


def _normalize_codes(fund_codes: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for code in fund_codes:
        if not isinstance(code, str):
            raise TypeError("fund code inputs must be strings")
        code = code.strip()
        if _SIX_DIGIT_CODE.fullmatch(code) is None:
            raise ValueError("fund code must be exactly six digits")
        if code not in normalized:
            normalized.append(code)
    return tuple(normalized)


def resolve_fund_relations(
    *,
    industry_id: str,
    fund_codes: Sequence[str],
    context: TransientFundContext,
) -> FundRelationProjection:
    """Resolve only explicitly selected codes through the injected public-analysis adapter."""
    if not isinstance(context, TransientFundContext):
        raise TypeError("context must be TransientFundContext")
    with context:
        codes = _normalize_codes(fund_codes)
        if not codes:
            return FundRelationProjection("no_holdings", (), (), ())
        selections = tuple(
            FundSelectionScope(f"selection-{index}", code, True)
            for index, code in enumerate(codes, start=1)
        )
        resolutions: list[IndustryFundRelationResolution] = []
        pending: list[str] = []
        for selection in selections:
            try:
                analysis = context.analyze(selection.fund_code)
            except Exception:
                resolutions.append(IndustryFundRelationResolution(
                    selection_id=selection.selection_id,
                    fund_code=selection.fund_code,
                    relation=None,
                    empty_reason=FundResolutionEmptyReason.SOURCE_UNAVAILABLE,
                ))
                continue
            relation = _lookthrough_relation(
                industry_id=industry_id,
                fund_code=selection.fund_code,
                analysis=analysis,
            )
            requires_lookthrough = False
            if relation is None:
                relation, requires_lookthrough = _official_allocation_relation(
                    industry_id=industry_id,
                    fund_code=selection.fund_code,
                    analysis=analysis,
                )
            if relation is not None:
                resolutions.append(IndustryFundRelationResolution(
                    selection_id=selection.selection_id,
                    fund_code=selection.fund_code,
                    relation=relation,
                    empty_reason=None,
                ))
                if requires_lookthrough:
                    pending.append(selection.selection_id)
            else:
                resolutions.append(IndustryFundRelationResolution(
                    selection_id=selection.selection_id,
                    fund_code=selection.fund_code,
                    relation=None,
                    empty_reason=_unresolved_reason(analysis),
                ))
        return FundRelationProjection(
            state="resolved",
            fund_selection=selections,
            resolutions=tuple(resolutions),
            pending_lookthrough_selection_ids=tuple(pending),
        )
