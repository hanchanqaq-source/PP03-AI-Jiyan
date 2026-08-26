from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
_USABLE_SECTION_STATUSES = {"disclosed", "official", "verified", "corroborated"}


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


@dataclass(frozen=True, slots=True)
class CompanyEvidenceBinding:
    evidence_id: str
    supports_fields: frozenset[str]
    as_of_date: str

    def __post_init__(self) -> None:
        if type(self.evidence_id) is not str or not self.evidence_id.strip():
            raise ValueError("company evidence_id must not be blank")
        if any(type(item) is not str or not item.strip() for item in self.supports_fields):
            raise ValueError("company supports_fields must contain non-blank strings")
        try:
            parsed = date.fromisoformat(self.as_of_date)
        except (TypeError, ValueError) as error:
            raise ValueError("company evidence as_of_date must be an ISO date") from error
        if parsed.isoformat() != self.as_of_date:
            raise ValueError("company evidence as_of_date must be canonical")


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
    allowed_chain_node_ids: Iterable[str],
    allowed_metric_ids: Iterable[str],
    evidence_bindings: Mapping[str, CompanyEvidenceBinding],
) -> tuple[IndustryCompanyRelation, ...]:
    """Admit only exact-code company relations backed by official evidence."""
    chain_ids = frozenset(allowed_chain_node_ids)
    metric_ids = frozenset(allowed_metric_ids)
    if not isinstance(evidence_bindings, Mapping) or any(
        type(evidence_id) is not str
        or not evidence_id
        or not isinstance(binding, CompanyEvidenceBinding)
        or binding.evidence_id != evidence_id
        for evidence_id, binding in evidence_bindings.items()
    ):
        raise ValueError("company evidence bindings are invalid")
    if any(type(item) is not str or not item for item in chain_ids | metric_ids):
        raise ValueError("company projection allowlists require non-blank string IDs")
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
        key_metric_ids = _string_tuple(candidate.get("key_metric_ids"))
        company_name = _nonblank(candidate.get("company_name"))
        chain_node_id = _nonblank(candidate.get("chain_node_id"))
        as_of_date = _nonblank(candidate.get("as_of_date"))
        if (
            relation_type not in {"official_disclosure", "public_classification"}
            or not evidence_ids
            or company_name is None
            or chain_node_id is None
            or as_of_date is None
            or chain_node_id not in chain_ids
            or not set(key_metric_ids).issubset(metric_ids)
        ):
            continue
        try:
            candidate_date = date.fromisoformat(as_of_date)
        except ValueError:
            continue
        if candidate_date.isoformat() != as_of_date:
            continue
        selected_bindings = tuple(evidence_bindings.get(item) for item in evidence_ids)
        if any(binding is None for binding in selected_bindings):
            continue
        bindings = tuple(binding for binding in selected_bindings if binding is not None)
        if any(binding.as_of_date != as_of_date for binding in bindings):
            continue
        required_fields = {
            f"security_code:{security_code}",
            f"chain_node:{chain_node_id}",
            f"relation_type:{relation_type}",
            *(f"metric:{metric_id}" for metric_id in key_metric_ids),
        }
        supported_fields = set().union(*(binding.supports_fields for binding in bindings))
        if not required_fields.issubset(supported_fields):
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
            key_metric_ids=tuple(dict.fromkeys(key_metric_ids)),
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


def _section_status(section: Mapping[str, Any]) -> str:
    return str(_mapping(section.get("meta")).get("status") or "").strip().casefold()


def _section_is_usable(section: Mapping[str, Any]) -> bool:
    meta = _mapping(section.get("meta"))
    return (
        _section_status(section) in _USABLE_SECTION_STATUSES
        and not _nonblank(meta.get("availability_reason"))
    )


def _matching_optional_fund_code(value: Mapping[str, Any], fund_code: str) -> bool:
    declared = value.get("fund_code")
    return declared is None or declared == fund_code


def _analysis_identity_matches(analysis: Mapping[str, Any], fund_code: str) -> bool:
    if analysis.get("code") != fund_code:
        return False
    holdings = _mapping(_mapping(analysis.get("holdings")).get("data"))
    exposure = _mapping(_mapping(analysis.get("industry_exposure")).get("data"))
    return (
        _matching_optional_fund_code(holdings, fund_code)
        and _matching_optional_fund_code(exposure, fund_code)
    )


def _lookthrough_relation(
    *,
    industry_id: str,
    fund_code: str,
    analysis: Mapping[str, Any],
    context: TransientFundContext,
) -> IndustryFundRelation | None:
    holdings_section = _mapping(analysis.get("holdings"))
    exposure_section = _mapping(analysis.get("industry_exposure"))
    if not _section_is_usable(holdings_section) or not _section_is_usable(exposure_section):
        return None
    holdings = _mapping(holdings_section.get("data"))
    exposure = _mapping(exposure_section.get("data"))
    if not _matching_optional_fund_code(holdings, fund_code) or not _matching_optional_fund_code(exposure, fund_code):
        return None
    lookthrough = _mapping(exposure.get("lookthrough"))
    if lookthrough.get("status") != "disclosed":
        return None
    disclosure_date = _nonblank(lookthrough.get("disclosure_date"))
    holdings_date = _nonblank(holdings.get("disclosure_date"))
    if disclosure_date is None or holdings_date != disclosure_date:
        return None
    tag = next((
        _mapping(item)
        for item in _sequence(exposure.get("industry_chain_tags"))
        if _mapping(item).get("id") == industry_id
        and _mapping(item).get("evidence_level") == "disclosed_stock_classification"
        and _matching_optional_fund_code(_mapping(item), fund_code)
    ), None)
    if not tag:
        return None
    value = _finite_percent(tag.get("weight_pct"))
    if value is None:
        return None
    disclosed_codes = _holdings_codes(analysis)
    if not disclosed_codes:
        return None
    holding_meta = _mapping(holdings_section.get("meta"))
    holdings_reference = _nonblank(holding_meta.get("source_reference"))
    if holdings_reference is None:
        return None
    evidence_ids = {
        _evidence_id("holding-disclosure", holdings_reference, fund_code, disclosure_date)
    }
    classification_references: set[str] = set()
    evidenced_codes: set[str] = set()
    evidence_rows = _sequence(exposure.get("holding_industry_evidence"))
    if not evidence_rows:
        return None
    for item in evidence_rows:
        row = _mapping(item)
        code = _nonblank(row.get("stock_code"))
        if (
            code is None
            or _SIX_DIGIT_CODE.fullmatch(code) is None
            or code not in disclosed_codes
            or not context.security_matches(industry_id=industry_id, security_code=code)
        ):
            continue
        reference = _nonblank(row.get("source_reference"))
        evidence_date = _nonblank(row.get("holding_disclosure_date"))
        if (
            reference is None
            or evidence_date != disclosure_date
            or not _matching_optional_fund_code(row, fund_code)
        ):
            return None
        classification_references.add(reference)
        evidenced_codes.add(code)
        evidence_ids.add(_evidence_id("security-classification", reference, code, disclosure_date))
    if not evidenced_codes:
        return None
    return IndustryFundRelation(
        industry_id=industry_id,
        fund_code=fund_code,
        relation_layer="disclosed_lookthrough",
        exposure_value=value,
        exposure_unit="percent",
        disclosure_date=disclosure_date,
        evidence_ids=tuple(sorted(evidence_ids)),
        status=VerificationStatus.VERIFIED,
    )


def _official_allocation_relation(
    *,
    industry_id: str,
    fund_code: str,
    analysis: Mapping[str, Any],
    context: TransientFundContext,
) -> tuple[IndustryFundRelation | None, bool]:
    exposure_section = _mapping(analysis.get("industry_exposure"))
    if not _section_is_usable(exposure_section):
        return None, False
    exposure = _mapping(exposure_section.get("data"))
    if not _matching_optional_fund_code(exposure, fund_code):
        return None, False
    allocation = _mapping(exposure.get("official_allocation"))
    as_of_date = _nonblank(allocation.get("as_of_date"))
    source_reference = _nonblank(allocation.get("source_reference"))
    if as_of_date is None or source_reference is None:
        return None, False
    normalized_industry_id = industry_id.strip().casefold()
    normalized_row_names: set[str] = set()
    matching: list[Mapping[str, Any]] = []
    for item in _sequence(allocation.get("exposure")):
        row = _mapping(item)
        official_name = _nonblank(row.get("name"))
        if official_name is None:
            return None, False
        normalized_name = official_name.casefold()
        if normalized_name in normalized_row_names:
            return None, False
        normalized_row_names.add(normalized_name)
        declared_industry = row.get("industry_id")
        if declared_industry is not None:
            declared_industry = _nonblank(declared_industry)
            if declared_industry is None:
                return None, False
            if declared_industry.casefold() != normalized_industry_id:
                continue
        if not _matching_optional_fund_code(row, fund_code):
            continue
        if context.official_allocation_matches(
            industry_id=industry_id,
            official_name=official_name,
        ):
            matching.append(row)
    matching_rows = tuple(matching)
    if len(matching_rows) != 1:
        return None, False
    row = matching_rows[0]
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
    exposure_section = _mapping(analysis.get("industry_exposure"))
    holdings_meta = _mapping(holdings_section.get("meta"))
    exposure_meta = _mapping(exposure_section.get("meta"))
    holdings_status = _section_status(holdings_section)
    exposure_status = _section_status(exposure_section)
    holdings_reason = str(holdings_meta.get("availability_reason") or "").casefold()
    exposure_reason = str(exposure_meta.get("availability_reason") or "").casefold()
    if holdings_status in _SOURCE_FAILURE_STATUSES or exposure_status in _SOURCE_FAILURE_STATUSES:
        return FundResolutionEmptyReason.SOURCE_UNAVAILABLE
    if "source_unavailable" in {holdings_reason, exposure_reason}:
        return FundResolutionEmptyReason.SOURCE_UNAVAILABLE
    if (
        holdings_reason == "not_disclosed"
        and holdings_status in {"unavailable", "not_disclosed"}
        and holdings_section.get("data") is None
    ):
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
            if not _analysis_identity_matches(analysis, selection.fund_code):
                resolutions.append(IndustryFundRelationResolution(
                    selection_id=selection.selection_id,
                    fund_code=selection.fund_code,
                    relation=None,
                    empty_reason=FundResolutionEmptyReason.UNKNOWN,
                ))
                continue
            relation = _lookthrough_relation(
                industry_id=industry_id,
                fund_code=selection.fund_code,
                analysis=analysis,
                context=context,
            )
            requires_lookthrough = False
            if relation is None:
                relation, requires_lookthrough = _official_allocation_relation(
                    industry_id=industry_id,
                    fund_code=selection.fund_code,
                    analysis=analysis,
                    context=context,
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
