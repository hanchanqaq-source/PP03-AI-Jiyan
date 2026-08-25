from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Protocol, Sequence

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .models import (
    CandidateEvidenceCounts,
    CandidateEvidencePanel,
    FundResolutionEmptyReason,
    FundSelectionScope,
    IndustryFundRelationResolution,
    IndustryReportResponse,
    RefreshPhase,
    RefreshRun,
    TemplateStatus,
)
from .relationships import FundRelationProjection
from .storage import IndustryResearchStorage
from .templates import get_industry_template


router = APIRouter(prefix="/api/industry-research", tags=["industry-research"])

_INDUSTRY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.ASCII)
_FUND_CODE = re.compile(r"^[0-9]{6}$", re.ASCII)
_WINDOWS = frozenset({7, 30, 90})
_FUND_RELATION_BODY_MAX_BYTES = 4096


class IndustryResearchService(Protocol):
    def read_report(self, industry_id: str, window_days: int) -> IndustryReportResponse: ...

    def has_qualified_refresh(self, industry_id: str) -> bool: ...

    def request_refresh(self, industry_id: str) -> RefreshRun: ...

    def resolve_fund_relations(
        self,
        industry_id: str,
        fund_codes: tuple[str, ...],
    ) -> FundRelationProjection: ...

    def shutdown(self) -> None: ...


def _industry_id(value: str) -> str:
    if type(value) is not str or _INDUSTRY_ID.fullmatch(value) is None:
        raise HTTPException(400, "invalid_industry_id")
    return value


def _idle_refresh(industry_id: str, report: Any | None = None) -> RefreshRun:
    return RefreshRun(
        industry_id=industry_id,
        run_id=None,
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
        candidate_snapshot_id=None,
        phase=RefreshPhase.IDLE,
        error_code=None,
        displayed_trusted_snapshot_id=(
            report.trusted_snapshot_id if report is not None else None
        ),
        published_trusted_snapshot_id=None,
        displayed_raw_snapshot_id=(report.raw_snapshot_id if report is not None else None),
        displayed_evidence_snapshot_id=(
            report.evidence_snapshot_id if report is not None else None
        ),
    )


def _empty_response(industry_id: str) -> IndustryReportResponse:
    return IndustryReportResponse(
        requested_industry_id=industry_id,
        displayed_industry_id=None,
        displayed_trusted_report=None,
        candidate_evidence=None,
        refresh_run=_idle_refresh(industry_id),
    )


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _filter_report_window(
    response: IndustryReportResponse,
    *,
    window_days: int,
    now: datetime,
) -> IndustryReportResponse:
    if window_days not in _WINDOWS:
        raise ValueError("unsupported report window")
    cutoff = now - timedelta(days=window_days)
    report = response.displayed_trusted_report
    candidate = response.candidate_evidence
    if report is not None:
        trusted_events = tuple(
            event
            for event in report.news_risk
            if (occurred_at := _parse_timestamp(event.occurred_at)) is not None
            and cutoff <= occurred_at <= now
        )
        report = replace(report, news_risk=trusted_events)
    if candidate is not None:
        unverified_events = tuple(
            event
            for event in candidate.unverified_events
            if (occurred_at := _parse_timestamp(event.occurred_at)) is not None
            and cutoff <= occurred_at <= now
        )
        conflicting_events = tuple(
            event
            for event in candidate.conflicting_events
            if (occurred_at := _parse_timestamp(event.occurred_at)) is not None
            and cutoff <= occurred_at <= now
        )
        candidate = replace(
            candidate,
            counts=CandidateEvidenceCounts(
                unverified=len(candidate.unverified),
                conflicting=len(candidate.conflicting),
                unverified_events=len(unverified_events),
                conflicting_events=len(conflicting_events),
            ),
            unverified_events=unverified_events,
            conflicting_events=conflicting_events,
        )
    return replace(
        response,
        displayed_trusted_report=report,
        candidate_evidence=candidate,
    )


class ProductionIndustryResearchService:
    """Read-only production boundary; the current qualified refresh set is empty."""

    def __init__(
        self,
        *,
        storage: IndustryResearchStorage | None = None,
        now=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._storage = storage or IndustryResearchStorage(production=True)
        self._now = now

    def read_report(self, industry_id: str, window_days: int) -> IndustryReportResponse:
        report = self._storage.load_current(industry_id)
        if report is None:
            return _empty_response(industry_id)
        report.validate_for_mode(production=True)
        response = IndustryReportResponse(
            requested_industry_id=industry_id,
            displayed_industry_id=industry_id,
            displayed_trusted_report=report,
            candidate_evidence=None,
            refresh_run=_idle_refresh(industry_id, report),
        )
        return _filter_report_window(response, window_days=window_days, now=self._now())

    def has_qualified_refresh(self, industry_id: str) -> bool:
        del industry_id
        # Task 2 concluded UNCONFIGURED / license_unverified.  Do not create a
        # provider or executor until a later, separately evidenced qualification.
        return False

    def request_refresh(self, industry_id: str) -> RefreshRun:
        del industry_id
        raise RuntimeError("source_unconfigured")

    def resolve_fund_relations(
        self,
        industry_id: str,
        fund_codes: tuple[str, ...],
    ) -> FundRelationProjection:
        del industry_id
        selections = tuple(
            FundSelectionScope(f"selection-{index}", code, True)
            for index, code in enumerate(fund_codes, start=1)
        )
        resolutions = tuple(
            IndustryFundRelationResolution(
                selection_id=selection.selection_id,
                fund_code=selection.fund_code,
                relation=None,
                empty_reason=FundResolutionEmptyReason.SOURCE_UNAVAILABLE,
            )
            for selection in selections
        )
        return FundRelationProjection(
            state="resolved" if selections else "no_holdings",
            fund_selection=selections,
            resolutions=resolutions,
            pending_lookthrough_selection_ids=(),
        )

    def shutdown(self) -> None:
        return None


def create_production_industry_research_service() -> IndustryResearchService:
    return ProductionIndustryResearchService()


def _service(request: Request) -> IndustryResearchService:
    service = getattr(request.app.state, "industry_research_service", None)
    if service is None:
        raise HTTPException(503, "industry_research_unavailable")
    return service


def _wire_response(
    response: IndustryReportResponse,
    *,
    template_status: TemplateStatus,
    allow_demo: bool,
) -> dict[str, object]:
    if type(response) is not IndustryReportResponse:
        raise HTTPException(502, "industry_research_response_invalid")
    report = response.displayed_trusted_report
    if report is not None:
        if report.demo and not allow_demo:
            raise HTTPException(503, "demo_fixture_rejected")
        try:
            report.validate_for_mode(production=not allow_demo)
        except (TypeError, ValueError) as error:
            raise HTTPException(502, "industry_research_response_invalid") from error
        if report.template_status is not template_status:
            raise HTTPException(502, "industry_research_response_invalid")
    document = response.to_dict()
    document["template_status"] = template_status.value
    return document


class FundRelationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fund_codes: tuple[str, ...]

    @field_validator("fund_codes")
    @classmethod
    def validate_fund_codes(cls, value: Sequence[str]) -> tuple[str, ...]:
        if len(value) > 32:
            raise ValueError("too many fund codes")
        normalized: list[str] = []
        for code in value:
            if type(code) is not str or _FUND_CODE.fullmatch(code) is None:
                raise ValueError("fund codes must be exactly six digits")
            if code not in normalized:
                normalized.append(code)
        return tuple(normalized)


class _InvalidFundRelationRequest(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidFundRelationRequest
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise _InvalidFundRelationRequest


def _raw_header_values(request: Request, name: bytes) -> list[bytes]:
    return [
        value
        for key, value in request.scope.get("headers", ())
        if key.lower() == name
    ]


def _declared_fund_body_length(request: Request) -> int | None:
    values = _raw_header_values(request, b"content-length")
    transfer_encoding = _raw_header_values(request, b"transfer-encoding")
    if len(values) > 1 or len(transfer_encoding) > 1:
        raise HTTPException(400, "invalid_fund_relation_request")
    if transfer_encoding:
        if values or transfer_encoding[0].strip().lower() != b"chunked":
            raise HTTPException(400, "invalid_fund_relation_request")
    if not values:
        return None
    try:
        value = values[0].decode("ascii")
    except UnicodeDecodeError:
        raise HTTPException(400, "invalid_fund_relation_request") from None
    if re.fullmatch(r"[0-9]+", value, re.ASCII) is None:
        raise HTTPException(400, "invalid_fund_relation_request")
    declared = int(value)
    if declared > _FUND_RELATION_BODY_MAX_BYTES:
        raise HTTPException(413, "fund_relation_request_too_large")
    return declared


async def _read_fund_relation_request(request: Request) -> FundRelationRequest:
    declared = _declared_fund_body_length(request)
    body = bytearray()
    try:
        async for chunk in request.stream():
            observed = len(body) + len(chunk)
            if observed > _FUND_RELATION_BODY_MAX_BYTES:
                raise HTTPException(413, "fund_relation_request_too_large")
            if declared is not None and observed > declared:
                raise HTTPException(400, "invalid_fund_relation_request")
            body.extend(chunk)
    except HTTPException:
        raise
    except (OSError, RuntimeError):
        raise HTTPException(400, "invalid_fund_relation_request") from None
    if declared is not None and len(body) != declared:
        raise HTTPException(400, "invalid_fund_relation_request")
    try:
        text = bytes(body).decode("utf-8")
        document = json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _InvalidFundRelationRequest):
        raise HTTPException(400, "invalid_fund_relation_request") from None
    if type(document) is not dict:
        raise HTTPException(400, "invalid_fund_relation_request")
    try:
        return FundRelationRequest.model_validate(document)
    except (TypeError, ValueError, ValidationError):
        raise HTTPException(422, "invalid_fund_relation_request") from None


@router.post("/{industry_id}/fund-relations/resolve")
async def resolve_fund_relations(
    request: Request,
    industry_id: str,
):
    industry_id = _industry_id(industry_id)
    payload = await _read_fund_relation_request(request)
    service = _service(request)
    try:
        projection = service.resolve_fund_relations(industry_id, payload.fund_codes)
    except (TypeError, ValueError, RuntimeError):
        raise HTTPException(502, "fund_relation_resolution_failed") from None
    if type(projection) is not FundRelationProjection:
        raise HTTPException(502, "fund_relation_response_invalid")
    return JSONResponse(
        projection.to_dict(),
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


@router.post("/{industry_id}/refresh", status_code=202)
def request_refresh(request: Request, industry_id: str):
    industry_id = _industry_id(industry_id)
    template = get_industry_template(industry_id)
    if template.status is TemplateStatus.BUILDING:
        raise HTTPException(409, "source_unconfigured")
    service = _service(request)
    try:
        eligible = service.has_qualified_refresh(industry_id)
    except Exception:
        raise HTTPException(503, "refresh_state_unavailable") from None
    if eligible is not True:
        raise HTTPException(409, "source_unconfigured")
    try:
        run = service.request_refresh(industry_id)
    except (TypeError, ValueError, RuntimeError):
        raise HTTPException(503, "refresh_queue_failed") from None
    if type(run) is not RefreshRun or run.industry_id != industry_id:
        raise HTTPException(502, "refresh_response_invalid")
    return run.to_dict()


@router.get("/{industry_id}")
def get_industry_report(
    request: Request,
    industry_id: str,
    window_days: int = Query(7),
):
    industry_id = _industry_id(industry_id)
    if window_days not in _WINDOWS:
        raise HTTPException(422, "window_days must be 7, 30, or 90")
    template = get_industry_template(industry_id)
    if template.status is TemplateStatus.BUILDING:
        return _wire_response(
            _empty_response(industry_id),
            template_status=TemplateStatus.BUILDING,
            allow_demo=bool(getattr(request.app.state, "industry_research_allow_demo", False)),
        )
    service = _service(request)
    try:
        response = service.read_report(industry_id, window_days)
    except ValueError as error:
        if "demo" in str(error).casefold():
            raise HTTPException(503, "demo_fixture_rejected") from None
        raise HTTPException(502, "industry_research_response_invalid") from None
    except (OSError, RuntimeError, TypeError):
        raise HTTPException(503, "industry_research_unavailable") from None
    if response.requested_industry_id != industry_id:
        raise HTTPException(502, "industry_research_response_invalid")
    return _wire_response(
        response,
        template_status=template.status,
        allow_demo=bool(getattr(request.app.state, "industry_research_allow_demo", False)),
    )


@router.api_route(
    "/{invalid_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def reject_invalid_industry_path(invalid_path: str):
    del invalid_path
    raise HTTPException(400, "invalid_industry_id")


__all__ = [
    "IndustryResearchService",
    "ProductionIndustryResearchService",
    "create_production_industry_research_service",
    "router",
]
