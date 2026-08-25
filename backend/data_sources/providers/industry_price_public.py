from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from data_sources.http import SafeHttpClient
from data_sources.models import (
    AdapterDescriptor,
    BillingModel,
    CatalogStatus,
    ProviderValue,
    SourceRole,
)
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderUnavailable
from industry_research.source_qualification import (
    PUBLIC_PRICE_FAILURE_MODES,
    PUBLIC_PRICE_FIELD_SHAPE,
    PUBLIC_PRICE_RESPONSE_CAP_BYTES,
    PUBLIC_PRICE_SOURCE_IDENTITY,
    PUBLIC_PRICE_TARGET_FIELDS,
    PUBLIC_PRICE_URL,
    SourceQualificationResult,
    UNVERIFIED_LICENSE,
    UNVERIFIED_PUBLIC_PRICE_QUALIFICATION,
    qualification_allows_enabled_adapter,
)

from .base import BaseProvider


_BASE_DESCRIPTOR = AdapterDescriptor(
    "trendforce-public-price",
    "TrendForce public current price snapshot candidate",
    "trendforce_public_price",
    "bounded_public_html",
    (SourceRole.CANDIDATE,),
    ("industry_price_snapshot",),
    BillingModel.FREE_NO_KEY,
    "none",
    (),
    False,
    "Public page reuse license is unverified; current snapshot only and never historical/member download.",
    "Candidate only; no login, Cookie, key, subscription, history, download, or report-value promotion.",
    "Current snapshot only; upstream timestamp required.",
    "One bounded qualification request; no automated production refresh while unconfigured.",
    "Free public no-key candidate; no purchase, fee, or account upgrade.",
    PUBLIC_PRICE_URL,
    200,
    CatalogStatus.UNCONFIGURED,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _SnapshotTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_target_table = False
        self.in_caption = False
        self.in_cell = False
        self.cell_tag = ""
        self.cell_parts: list[str] = []
        self.caption_parts: list[str] = []
        self.headers: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "dram-spot-price":
            self.in_target_table = True
        elif self.in_target_table and tag == "caption":
            self.in_caption = True
        elif self.in_target_table and tag in {"th", "td"}:
            self.in_cell = True
            self.cell_tag = tag
            self.cell_parts = []
        elif self.in_target_table and tag == "tr":
            self.current_row = []

    def handle_data(self, data: str) -> None:
        if self.in_caption:
            self.caption_parts.append(data)
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_target_table and tag in {"th", "td"} and self.in_cell:
            value = " ".join("".join(self.cell_parts).split())
            if self.cell_tag == "th":
                self.headers.append(value)
            else:
                self.current_row.append(value)
            self.in_cell = False
            self.cell_tag = ""
            self.cell_parts = []
        elif self.in_target_table and tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
        elif self.in_target_table and tag == "caption":
            self.in_caption = False
        elif tag == "table" and self.in_target_table:
            self.in_target_table = False

    @property
    def caption(self) -> str:
        return " ".join("".join(self.caption_parts).split())


def _document_parts(document: object) -> tuple[str, int, Mapping[str, object], bytes] | None:
    if isinstance(document, Mapping):
        final_url = document.get("final_url")
        status = document.get("http_status", document.get("status_code"))
        headers = document.get("headers")
        body = document.get("body")
    else:
        final_url = getattr(document, "final_url", None)
        status = getattr(document, "status_code", None)
        headers = getattr(document, "headers", None)
        body = getattr(document, "body", None)
    if (
        not isinstance(final_url, str)
        or type(status) is not int
        or not isinstance(headers, Mapping)
        or not isinstance(body, bytes)
    ):
        return None
    return final_url, status, headers, body


def _header(headers: Mapping[str, object], name: str) -> str:
    return next(
        (str(value) for key, value in headers.items() if str(key).lower() == name.lower()),
        "",
    )


def _forbidden_surface(final_url: str, body: bytes) -> str | None:
    lowered_url = final_url.lower()
    text = body.decode("utf-8", errors="ignore").lower()
    if "login" in lowered_url or re.search(r"type\s*=\s*['\"]password['\"]", text):
        return "login_required"
    if "cookie-wall" in text or "accept cookies to continue" in text or "cookie consent required" in text:
        return "cookie_required"
    member_href = re.search(
        r"href\s*=\s*['\"][^'\"]*(?:member|subscribe|subscription|history)[^'\"]*(?:download|subscribe|subscription|history)[^'\"]*['\"]",
        text,
    )
    if member_href or any(term in lowered_url for term in ("/member/", "/download", "/history", "subscribe")):
        return "member_download"
    return None


def _safe_public_snapshot_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "www.trendforce.com"
            and parsed.port in {None, 443}
            and not parsed.username
            and not parsed.password
            and parsed.path == "/price/dram/dram_spot"
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _parse_snapshot(body: bytes) -> tuple[tuple[tuple[str, Decimal, date], ...], date, str]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProviderUnavailable("structure_changed", reference=PUBLIC_PRICE_URL) from error
    parser = _SnapshotTableParser()
    parser.feed(text)
    normalized_headers = tuple(
        "_".join(header.strip().lower().split()) for header in parser.headers
    )
    if normalized_headers != PUBLIC_PRICE_TARGET_FIELDS or not parser.rows:
        if normalized_headers and "date" not in normalized_headers:
            raise ProviderUnavailable("missing_data_date", reference=PUBLIC_PRICE_URL)
        raise ProviderUnavailable("structure_changed", reference=PUBLIC_PRICE_URL)
    match = re.search(r"\bUnit\s*:\s*([A-Z]{3})\b", parser.caption, re.IGNORECASE)
    if match is None:
        raise ProviderUnavailable("missing_unit", reference=PUBLIC_PRICE_URL)
    unit = match.group(1).upper()
    if unit != "USD":
        raise ProviderUnavailable("missing_unit", reference=PUBLIC_PRICE_URL)
    parsed_rows: list[tuple[str, Decimal, date]] = []
    for row in parser.rows:
        if len(row) != 3 or not row[0]:
            raise ProviderUnavailable("structure_changed", reference=PUBLIC_PRICE_URL)
        try:
            value = Decimal(row[1])
        except (InvalidOperation, ValueError) as error:
            raise ProviderUnavailable("structure_changed", reference=PUBLIC_PRICE_URL) from error
        if not value.is_finite():
            raise ProviderUnavailable("structure_changed", reference=PUBLIC_PRICE_URL)
        try:
            observed_date = date.fromisoformat(row[2])
        except (TypeError, ValueError) as error:
            raise ProviderUnavailable("missing_data_date", reference=PUBLIC_PRICE_URL) from error
        parsed_rows.append((row[0], value, observed_date))
    dates = {row[2] for row in parsed_rows}
    if len(dates) != 1:
        raise ProviderUnavailable("missing_data_date", reference=PUBLIC_PRICE_URL)
    return tuple(parsed_rows), dates.pop(), unit


class TrendForcePublicPriceAdapter(BaseProvider):
    """Fail-closed adapter for one public current-price page; never requests history."""

    descriptor = _BASE_DESCRIPTOR

    def __init__(
        self,
        *,
        http: Any,
        qualification: SourceQualificationResult = UNVERIFIED_PUBLIC_PRICE_QUALIFICATION,
        catalog_descriptor: AdapterDescriptor = _BASE_DESCRIPTOR,
        max_response_bytes: int = PUBLIC_PRICE_RESPONSE_CAP_BYTES,
        fetched_at: Callable[[], datetime] = _now,
    ) -> None:
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._http = http
        self._qualification = qualification
        self._max_response_bytes = max_response_bytes
        self._fetched_at = fetched_at
        if (
            not isinstance(catalog_descriptor, AdapterDescriptor)
            or catalog_descriptor.adapter_id != _BASE_DESCRIPTOR.adapter_id
            or catalog_descriptor.source_family_id != _BASE_DESCRIPTOR.source_family_id
            or catalog_descriptor.capability_ids != _BASE_DESCRIPTOR.capability_ids
            or catalog_descriptor.configured_reference != _BASE_DESCRIPTOR.configured_reference
        ):
            raise ValueError("catalog_descriptor does not match the industry-price adapter")
        self.descriptor = catalog_descriptor

    @staticmethod
    def _redirect_allowed(_current: str, target: str) -> bool:
        return _safe_public_snapshot_url(target)

    def _get_document(self) -> object:
        return self._http.get_document(
            PUBLIC_PRICE_URL,
            headers={"Accept": "text/html,application/xhtml+xml"},
            redirect_validator=self._redirect_allowed,
        )

    def _validate_document_parts(
        self,
        parts: tuple[str, int, Mapping[str, object], bytes],
    ) -> tuple[tuple[tuple[str, Decimal, date], ...], date, str]:
        final_url, status, headers, body = parts
        if status != 200:
            raise ProviderUnavailable("http_status_unavailable", reference=PUBLIC_PRICE_URL)
        if len(body) > self._max_response_bytes:
            raise ProviderUnavailable("response_too_large", reference=PUBLIC_PRICE_URL)
        if not _header(headers, "Content-Type").lower().startswith(("text/html", "application/xhtml+xml")):
            raise ProviderUnavailable("structure_changed", reference=PUBLIC_PRICE_URL)
        protected = _forbidden_surface(final_url, body)
        if protected:
            raise ProviderUnavailable(protected, reference=PUBLIC_PRICE_URL)
        if not _safe_public_snapshot_url(final_url):
            raise ProviderUnavailable("redirect_disallowed", reference=PUBLIC_PRICE_URL)
        return _parse_snapshot(body)

    def _observed(self) -> tuple[str, int, Mapping[str, object], bytes, tuple[tuple[str, Decimal, date], ...], date, str]:
        document = self._get_document()
        parts = _document_parts(document)
        if parts is None:
            raise ProviderUnavailable("structure_changed", reference=PUBLIC_PRICE_URL)
        final_url, status, headers, body = parts
        rows, observed_date, unit = self._validate_document_parts(parts)
        return final_url, status, headers, body, rows, observed_date, unit

    def qualify(self) -> SourceQualificationResult:
        try:
            document = self._get_document()
        except ProviderUnavailable as error:
            code = "redirect_disallowed" if error.code == "redirect_disallowed" else error.code
            return replace(
                self._qualification,
                final_url=None,
                http_status=None,
                response_cap_bytes=self._max_response_bytes,
                response_bytes=None,
                target_fields=PUBLIC_PRICE_TARGET_FIELDS,
                observed_response_fields=(),
                field_shape="",
                data_date=None,
                unit=None,
                license_conclusion=UNVERIFIED_LICENSE,
                failure_reason=code,
                login_required=code == "login_required",
                cookie_required=code == "cookie_required",
                member_download=code == "member_download",
            )
        parts = _document_parts(document)
        if parts is None:
            return replace(
                self._qualification,
                final_url=None,
                http_status=None,
                response_cap_bytes=self._max_response_bytes,
                response_bytes=None,
                target_fields=PUBLIC_PRICE_TARGET_FIELDS,
                observed_response_fields=(),
                field_shape="",
                data_date=None,
                unit=None,
                license_conclusion=UNVERIFIED_LICENSE,
                failure_reason="structure_changed",
            )
        final_url, status, _headers, body = parts
        try:
            _rows, observed_date, unit = self._validate_document_parts(parts)
        except ProviderUnavailable as error:
            code = error.code
            return replace(
                self._qualification,
                final_url=final_url,
                http_status=status,
                response_cap_bytes=self._max_response_bytes,
                response_bytes=len(body),
                target_fields=PUBLIC_PRICE_TARGET_FIELDS,
                observed_response_fields=(),
                field_shape="",
                data_date=None,
                unit=None,
                license_conclusion=UNVERIFIED_LICENSE,
                failure_reason=code,
                login_required=code == "login_required",
                cookie_required=code == "cookie_required",
                member_download=code == "member_download",
            )
        license_conclusion = self._qualification.license_conclusion
        failure_reason = None if license_conclusion != UNVERIFIED_LICENSE else UNVERIFIED_LICENSE
        return replace(
            self._qualification,
            source_identity=PUBLIC_PRICE_SOURCE_IDENTITY,
            request_url=PUBLIC_PRICE_URL,
            final_url=final_url,
            http_status=status,
            response_cap_bytes=self._max_response_bytes,
            response_bytes=len(body),
            target_fields=PUBLIC_PRICE_TARGET_FIELDS,
            observed_response_fields=PUBLIC_PRICE_TARGET_FIELDS,
            field_shape=PUBLIC_PRICE_FIELD_SHAPE,
            data_date_field="date",
            data_date=observed_date,
            unit=unit,
            frequency="current_snapshot",
            failure_modes=PUBLIC_PRICE_FAILURE_MODES,
            failure_reason=failure_reason,
            login_required=False,
            cookie_required=False,
            member_download=False,
        )

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        if request.capability_id != "industry_price_snapshot" or request.parameters:
            raise ProviderUnavailable("invalid_request_parameter", reference=PUBLIC_PRICE_URL)
        if not qualification_allows_enabled_adapter(self._qualification, self.descriptor):
            raise ProviderUnavailable("license_unverified", reference=PUBLIC_PRICE_URL)
        final_url, _status, _headers, _body, rows, _observed_date, unit = self._observed()
        fetched_at = self._fetched_at()
        return tuple(
            ProviderValue(
                value,
                "trendforce_public_price",
                "trendforce-public-price",
                request.capability_id,
                observed_date,
                fetched_at,
                "candidate_snapshot",
                self._qualification.license_conclusion,
                200,
                None,
                unit,
                "current_snapshot",
                {
                    "product": product,
                    "public_reference": final_url,
                    "report_value_status": "not_verified",
                },
            )
            for product, value, observed_date in rows
        )

    def probe(self, capability_id: str) -> Mapping[str, object]:
        if capability_id != "industry_price_snapshot":
            return {"status": "unsupported_capability", "connected": False}
        if not qualification_allows_enabled_adapter(self._qualification, self.descriptor):
            return {"status": "license_unverified", "connected": False}
        return {"status": "not_probed", "connected": False}
