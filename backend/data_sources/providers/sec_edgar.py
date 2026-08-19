from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import re
import time
from typing import Any

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.http import SafeHttpClient
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
_INTERNAL_CAPABILITIES = {"company_submissions", "filing_index", "filing_metadata", "company_facts"}
_CAPABILITY_ALIASES = {
    "sec_company_submissions": ("company_submissions", None),
    "sec_filing_index_metadata": ("filing_index", None),
    "sec_10k_metadata": ("filing_metadata", "10-K"),
    "sec_10q_metadata": ("filing_metadata", "10-Q"),
    "sec_8k_metadata": ("filing_metadata", "8-K"),
    "sec_13f_metadata": ("filing_metadata", "13F"),
    "sec_company_facts": ("company_facts", None),
}
_SUPPORTED_CAPABILITIES = _INTERNAL_CAPABILITIES | set(_CAPABILITY_ALIASES)


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
        self._http = http.with_user_agent(contact_identifier.strip()) if isinstance(http, SafeHttpClient) else http
        self._clock = clock
        self._sleeper = sleeper
        self._fetched_at = fetched_at
        self._contact_identifier = contact_identifier.strip()
        self._last_request_at: float | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    @staticmethod
    def _normalise_capability(request: ProviderRequest) -> tuple[str, str, dict[str, object]]:
        parameters = dict(request.parameters)
        alias = _CAPABILITY_ALIASES.get(request.capability_id)
        if alias is None:
            return request.capability_id, request.capability_id, parameters
        capability, expected_form = alias
        if expected_form is not None:
            supplied_form = parameters.get("form")
            if supplied_form is not None and supplied_form != expected_form:
                raise ProviderUnavailable("invalid_request_parameter", reference=_ARCHIVES_REFERENCE)
            parameters["form"] = expected_form
        return request.capability_id, capability, parameters

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

    @staticmethod
    def _mapping(payload: object, reference: str) -> Mapping[str, object]:
        if not isinstance(payload, Mapping) or not payload:
            raise ProviderSchemaChanged("schema_changed", reference=reference)
        return payload

    @classmethod
    def _submissions(cls, payload: object, *, cik: str, reference: str) -> Mapping[str, object]:
        data = cls._mapping(payload, reference)
        if str(data.get("cik") or "").zfill(10) != cik:
            raise ProviderSchemaChanged("schema_changed", reference=reference)
        filings = data.get("filings")
        recent = filings.get("recent") if isinstance(filings, Mapping) else None
        accessions = recent.get("accessionNumber") if isinstance(recent, Mapping) else None
        forms = recent.get("form") if isinstance(recent, Mapping) else None
        if not isinstance(accessions, list) or not isinstance(forms, list) or not accessions or len(accessions) != len(forms):
            raise ProviderSchemaChanged("schema_changed", reference=reference)
        return recent

    @classmethod
    def _company_facts(cls, payload: object, *, cik: str, reference: str) -> None:
        data = cls._mapping(payload, reference)
        if str(data.get("cik") or "").zfill(10) != cik or not isinstance(data.get("facts"), Mapping):
            raise ProviderSchemaChanged("schema_changed", reference=reference)

    @classmethod
    def _index(cls, payload: object, *, reference: str) -> None:
        data = cls._mapping(payload, reference)
        directory = data.get("directory")
        items = directory.get("item") if isinstance(directory, Mapping) else None
        if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
            raise ProviderSchemaChanged("schema_changed", reference=reference)

    @staticmethod
    def _assert_requested_form(recent: Mapping[str, object], *, accession: str, form: str, reference: str) -> None:
        pairs = zip(recent["accessionNumber"], recent["form"])
        if not any(candidate_accession == accession and candidate_form == form for candidate_accession, candidate_form in pairs):
            raise ProviderSchemaChanged("schema_changed", reference=reference)

    def _value(self, capability_id: str, payload: object, *, canonical_url: str, value: dict[str, object]) -> ProviderValue:
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
        output_capability, capability, parameters = self._normalise_capability(request)
        if capability not in _INTERNAL_CAPABILITIES:
            raise ProviderUnavailable("unsupported_capability", reference=_DATA_REFERENCE)
        if capability == "company_submissions":
            cik, _archive_cik = self._cik(parameters)
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            payload = self._one_retry_json(url)
            self._submissions(payload, cik=cik, reference=url)
            return (self._value(output_capability, payload, canonical_url=url, value={"cik": cik, "record_type": "submissions"}),)
        if capability == "company_facts":
            cik, _archive_cik = self._cik(parameters)
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            payload = self._one_retry_json(url)
            self._company_facts(payload, cik=cik, reference=url)
            return (self._value(output_capability, payload, canonical_url=url, value={"cik": cik, "record_type": "company_facts"}),)
        cik, accession, url = self._index_url(parameters)
        form = self._form(parameters) if capability == "filing_metadata" else None
        if form is not None:
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            recent = self._submissions(self._one_retry_json(submissions_url), cik=cik, reference=submissions_url)
            self._assert_requested_form(recent, accession=accession, form=form, reference=submissions_url)
        payload = self._one_retry_json(url)
        self._index(payload, reference=url)
        details: dict[str, object] = {
            "cik": cik,
            "accession": accession,
            "record_type": "filing_index",
            "filing_body_fetched": False,
        }
        if form is not None:
            details["form"] = form
        return (self._value(output_capability, payload, canonical_url=url, value=details),)

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
