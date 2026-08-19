from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import re
import time
from typing import Any

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_DATA_REFERENCE = "https://data.sec.gov/"
_ARCHIVES_REFERENCE = "https://www.sec.gov/Archives/edgar/"
_PUBLIC_CONTACT = "PP03-AI-Jiyan (https://github.com/hanchanqaq-source/PP03-AI-Jiyan)"
_MINIMUM_INTERVAL_SECONDS = 0.125
_MAX_RETRY_AFTER_SECONDS = 60.0
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_FILING_FORMS = {"10-K", "10-Q", "8-K", "13F"}
_SUPPORTED_CAPABILITIES = {"company_submissions", "filing_index", "filing_metadata", "company_facts"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SecEdgarAdapter(BaseProvider):
    """Finite SEC JSON/index metadata adapter; it never requests filing bodies."""

    def __init__(
        self,
        *,
        http: Any,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        fetched_at: Callable[[], datetime] = _utc_now,
        contact_identifier: str = _PUBLIC_CONTACT,
    ) -> None:
        if not isinstance(contact_identifier, str) or "@" in contact_identifier or not contact_identifier.strip():
            raise ValueError("SEC contact identifier must be a public non-email project identifier")
        self._http = http
        self._clock = clock
        self._sleeper = sleeper
        self._fetched_at = fetched_at
        self._contact_identifier = contact_identifier.strip()
        self._last_request_at: float | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._contact_identifier, "Accept": "application/json"}

    @staticmethod
    def _cik(parameters: Mapping[str, object]) -> tuple[str, str]:
        raw = parameters.get("cik")
        if isinstance(raw, bool) or raw is None or not str(raw).isdigit() or not 1 <= len(str(raw)) <= 10:
            raise ProviderUnavailable("invalid_request_parameter", reference=_DATA_REFERENCE)
        cik = str(raw).zfill(10)
        return cik, str(int(cik))

    @staticmethod
    def _accession(parameters: Mapping[str, object]) -> str:
        accession = parameters.get("accession")
        if not isinstance(accession, str) or not _ACCESSION.fullmatch(accession):
            raise ProviderUnavailable("invalid_request_parameter", reference=_ARCHIVES_REFERENCE)
        return accession

    @staticmethod
    def _form(parameters: Mapping[str, object]) -> str:
        form = parameters.get("form")
        if not isinstance(form, str) or form not in _FILING_FORMS:
            raise ProviderUnavailable("invalid_request_parameter", reference=_ARCHIVES_REFERENCE)
        return form

    def _rate_gate(self) -> None:
        now = self._clock()
        if self._last_request_at is not None:
            delay = _MINIMUM_INTERVAL_SECONDS - (now - self._last_request_at)
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
        self._last_request_at = now

    def _get_json(self, url: str) -> object:
        self._rate_gate()
        return self._http.get_json(url, headers=self._headers)

    def _one_retry_json(self, url: str) -> object:
        try:
            return self._get_json(url)
        except ProviderRateLimited as error:
            delay = min(max(float(error.retry_after_seconds), 0.0), _MAX_RETRY_AFTER_SECONDS)
            self._sleeper(delay)
            return self._get_json(url)

    def _index_url(self, parameters: Mapping[str, object]) -> tuple[str, str, str]:
        cik, archive_cik = self._cik(parameters)
        accession = self._accession(parameters)
        if accession[:10] != cik:
            raise ProviderUnavailable("invalid_request_parameter", reference=_ARCHIVES_REFERENCE)
        compact_accession = accession.replace("-", "")
        return cik, accession, f"https://www.sec.gov/Archives/edgar/data/{archive_cik}/{compact_accession}/index.json"

    def _value(self, capability_id: str, payload: object, *, canonical_url: str, value: dict[str, object]) -> ProviderValue:
        if not isinstance(payload, Mapping):
            raise ProviderSchemaChanged("schema_changed", reference=canonical_url)
        return ProviderValue(
            value={**value, "canonical_url": canonical_url, "official_evidence_eligible": True},
            source_family_id="sec_edgar",
            adapter_id="sec-edgar",
            capability_id=capability_id,
            as_of_date=None,
            fetched_at=self._fetched_at(),
            data_status="official_metadata",
            license="SEC public EDGAR data; use subject to SEC terms and fair-access policy",
            priority=10,
            difference_from_primary=None,
            unit="metadata",
            frequency="event_driven",
        )

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        capability = request.capability_id
        if capability not in _SUPPORTED_CAPABILITIES:
            raise ProviderUnavailable("unsupported_capability", reference=_DATA_REFERENCE)
        if capability == "company_submissions":
            cik, _archive_cik = self._cik(request.parameters)
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            payload = self._one_retry_json(url)
            return (self._value(capability, payload, canonical_url=url, value={"cik": cik, "record_type": "submissions"}),)
        if capability == "company_facts":
            cik, _archive_cik = self._cik(request.parameters)
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            payload = self._one_retry_json(url)
            return (self._value(capability, payload, canonical_url=url, value={"cik": cik, "record_type": "company_facts"}),)
        cik, accession, url = self._index_url(request.parameters)
        form = self._form(request.parameters) if capability == "filing_metadata" else None
        payload = self._one_retry_json(url)
        details: dict[str, object] = {
            "cik": cik,
            "accession": accession,
            "record_type": "filing_index",
            "filing_body_fetched": False,
        }
        if form is not None:
            details["form"] = form
        return (self._value(capability, payload, canonical_url=url, value=details),)

    def probe(self, capability_id: str) -> Mapping[str, object]:
        if capability_id not in _SUPPORTED_CAPABILITIES:
            return {"status": "unsupported_capability", "connected": False}
        probe_request = ProviderRequest("company_submissions", {"cik": "0000320193"})
        if capability_id == "company_facts":
            probe_request = ProviderRequest("company_facts", {"cik": "0000320193"})
        try:
            self.fetch(probe_request)
        except ProviderUnavailable as error:
            status = "unconfigured_contact" if error.code == "authentication" else error.code
            return {"status": status, "connected": False}
        return {"status": "success", "connected": True}

    descriptor = AdapterDescriptor(
        "sec-edgar", "SEC EDGAR", "sec_edgar", "http_client",
        (SourceRole.OFFICIAL_EVIDENCE, SourceRole.PRIMARY_DATA),
        ("sec_company_submissions", "sec_filing_index_metadata", "sec_10k_metadata", "sec_10q_metadata", "sec_8k_metadata", "sec_13f_metadata", "sec_company_facts"),
        BillingModel.FREE_NO_KEY, "none", (), True,
        "SEC 公开 EDGAR 数据；使用须遵守 SEC 条款和公平访问策略。",
        "仅请求明确 CIK、表单和索引元数据；不抓取 filing body；不使用邮箱、Cookie 或登录。",
        "以 SEC 披露为准", "低于 10 请求/秒；429 最多遵循一次且上限 60 秒", "免费无需密钥；不自动购买或升级",
        _DATA_REFERENCE, 10, CatalogStatus.CONFIGURED,
    )
