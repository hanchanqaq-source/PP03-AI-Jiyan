from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from data_sources.http import SafeHttpClient
from data_sources.references import public_source_reference
from data_sources.provider_errors import ProviderUnavailable


_OFFICIAL_PUBLISHERS = {
    "sse.com.cn": "sse",
    "szse.cn": "szse",
    "cninfo.com.cn": "cninfo",
    "hkexnews.hk": "hkexnews",
    "csrc.gov.cn": "csrc",
}
_ALLOWED_CONTENT_TYPES = {"application/pdf", "application/json", "text/html", "application/xhtml+xml"}
_PROTECTED_TERMS = ("login", "sign-in", "signin", "captcha", "verify", "paywall", "subscribe", "authentication")
_SENSITIVE_QUERY_TERMS = ("authorization", "cookie", "token", "secret", "apikey", "credential", "password")


def _normalised_host(value: str) -> str | None:
    try:
        parsed = urlsplit(value if "://" in value else f"https://{value}")
        if parsed.username or parsed.password or parsed.port not in {None, 443} or not parsed.hostname:
            return None
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None
    if host.startswith("xn--") or ".xn--" in host:
        return None
    return host


def _registered_hosts(hosts: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in hosts:
        host = _normalised_host(value)
        if host is None:
            raise ValueError("official company host must be an HTTPS DNS host on port 443")
        if host not in result:
            result.append(host)
    return tuple(result)


def _host_matches(host: str, registered: str) -> bool:
    return host == registered or host.endswith(f".{registered}")


class OfficialEvidenceLinkAdapter:
    """Validates bounded public documents without scraping their protected content."""

    def __init__(
        self,
        *,
        http: Any | None = None,
        fund_company_hosts: Iterable[str] = (),
        index_company_hosts: Iterable[str] = (),
        max_document_bytes: int = 2_000_000,
    ) -> None:
        if not isinstance(max_document_bytes, int) or isinstance(max_document_bytes, bool) or max_document_bytes <= 0:
            raise ValueError("max_document_bytes must be positive")
        self._http = http if http is not None else SafeHttpClient(max_bytes=max_document_bytes)
        self._fund_hosts = _registered_hosts(fund_company_hosts)
        self._index_hosts = _registered_hosts(index_company_hosts)
        self._max_document_bytes = max_document_bytes

    def _identity(self, url: str) -> str | None:
        try:
            parsed = urlsplit(str(url or "").strip())
            if parsed.scheme.lower() != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
                return None
        except ValueError:
            return None
        host = _normalised_host(str(url or ""))
        if host is None:
            return None
        for domain, identity in _OFFICIAL_PUBLISHERS.items():
            if _host_matches(host, domain):
                return identity
        for domain in self._fund_hosts:
            if _host_matches(host, domain):
                return f"fund_company:{domain}"
        for domain in self._index_hosts:
            if _host_matches(host, domain):
                return f"index_company:{domain}"
        return None

    @staticmethod
    def _document_parts(document: object) -> tuple[str, Mapping[str, object], bytes] | None:
        if isinstance(document, Mapping):
            final_url, headers, body = document.get("final_url"), document.get("headers"), document.get("body")
        else:
            final_url, headers, body = (
                getattr(document, "final_url", None), getattr(document, "headers", None), getattr(document, "body", None)
            )
        if not isinstance(final_url, str) or not isinstance(headers, Mapping) or not isinstance(body, bytes):
            return None
        return final_url, headers, body

    @staticmethod
    def _protected(url: str, body: bytes) -> bool:
        parts = urlsplit(url)
        target = f"{parts.path}?{parts.query}".lower()
        excerpt = body[:16_384].decode("utf-8", errors="ignore").lower()
        return any(term in target or term in excerpt for term in _PROTECTED_TERMS)

    @staticmethod
    def _has_sensitive_query(url: str) -> bool:
        try:
            pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
        except ValueError:
            return True
        for key, _value in pairs:
            normalized = "".join(character.lower() for character in key if character.isascii() and character.isalnum())
            if any(term in normalized for term in _SENSITIVE_QUERY_TERMS):
                return True
        return False

    def validate(self, url: str) -> dict[str, object]:
        if self._has_sensitive_query(url):
            return {"status": "rejected_unsafe_reference", "official_evidence_eligible": False}
        initial_identity = self._identity(url)
        if initial_identity is None:
            return {"status": "rejected_untrusted_publisher", "official_evidence_eligible": False}
        initial_reference = public_source_reference(url)
        try:
            if isinstance(self._http, SafeHttpClient):
                document = self._http.get_document(
                    url,
                    redirect_validator=lambda _current, target: self._identity(target) == initial_identity,
                )
            else:
                document = self._http.get_document(url)
        except ProviderUnavailable as error:
            return {
                "status": f"unavailable_{error.code}", "publisher_identity": initial_identity,
                "initial_public_reference": initial_reference, "observed_final_reference": None,
                "official_evidence_eligible": False,
            }
        parts = self._document_parts(document)
        if parts is None:
            return {"status": "unavailable_schema_changed", "publisher_identity": initial_identity, "initial_public_reference": initial_reference, "observed_final_reference": None, "official_evidence_eligible": False}
        final_url, headers, body = parts
        try:
            final_scheme = urlsplit(final_url).scheme.lower()
        except ValueError:
            final_scheme = ""
        if final_scheme != "https":
            return {"status": "rejected_insecure_redirect", "publisher_identity": initial_identity, "initial_public_reference": initial_reference, "observed_final_reference": None, "official_evidence_eligible": False}
        final_identity = self._identity(final_url)
        if final_identity != initial_identity:
            return {"status": "rejected_cross_publisher_redirect", "publisher_identity": initial_identity, "initial_public_reference": initial_reference, "observed_final_reference": None, "official_evidence_eligible": False}
        if len(body) > self._max_document_bytes:
            return {"status": "rejected_response_too_large", "publisher_identity": initial_identity, "initial_public_reference": initial_reference, "observed_final_reference": None, "official_evidence_eligible": False}
        content_type = str(next((value for key, value in headers.items() if str(key).lower() == "content-type"), "")).split(";", 1)[0].strip().lower()
        if content_type not in _ALLOWED_CONTENT_TYPES:
            return {"status": "rejected_unsupported_content", "publisher_identity": initial_identity, "initial_public_reference": initial_reference, "observed_final_reference": None, "official_evidence_eligible": False}
        if self._protected(final_url, body):
            return {"status": "rejected_protected_content", "publisher_identity": initial_identity, "initial_public_reference": initial_reference, "observed_final_reference": None, "official_evidence_eligible": False}
        published_at = next((str(value) for key, value in headers.items() if str(key).lower() in {"last-modified", "date"}), None)
        return {
            "status": "valid", "publisher_identity": initial_identity,
            "initial_public_reference": initial_reference,
            "observed_final_reference": public_source_reference(final_url),
            "content_type": content_type, "published_at": published_at,
            "official_evidence_eligible": True,
        }
