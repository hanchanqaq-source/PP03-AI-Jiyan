from __future__ import annotations

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from data_sources.http import SafeHttpClient
from data_sources.provider_errors import ProviderUnavailable


class FakeDocumentHttp:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[str] = []

    def get_document(self, url: str):
        self.calls.append(url)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class RedirectResponse:
    def __init__(self, *, status_code: int, headers=None, content: bytes = b"") -> None:
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(headers or {})
        self._content = content

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self._content

    def close(self) -> None:
        pass


class RedirectAdapter(BaseAdapter):
    def __init__(self, responses: list[RedirectResponse]) -> None:
        self.responses = responses
        self.requests: list[object] = []

    def send(self, request, **kwargs):
        del kwargs
        self.requests.append(request)
        response = self.responses.pop(0)
        response.request = request
        response.url = request.url
        return response

    def close(self) -> None:
        pass


def document(*, final_url: str, content_type: str = "application/pdf", body: bytes = b"public filing", headers=None):
    return {
        "final_url": final_url,
        "headers": {"Content-Type": content_type, **(headers or {})},
        "body": body,
    }


@pytest.mark.parametrize(
    ("url", "identity"),
    [
        ("https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/20260819/a.pdf", "sse"),
        ("https://www.szse.cn/disclosure/listed/notice/index.html", "szse"),
        ("https://www.cninfo.com.cn/new/disclosure/detail?stockCode=000001", "cninfo"),
        ("https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0819/a.pdf", "hkexnews"),
        ("https://www.csrc.gov.cn/csrc/c100028/common_list.shtml", "csrc"),
        ("https://announcements.fund.example.cn/notice/1.pdf", "fund_company:fund.example.cn"),
        ("https://www.index.example.cn/notice/1.pdf", "index_company:index.example.cn"),
    ],
)
def test_official_link_accepts_only_explicit_official_publisher_hosts(url: str, identity: str):
    """Catches removing an official publisher from the fixed allowlist or configured company registry."""
    from data_sources.providers.official_evidence import OfficialEvidenceLinkAdapter

    adapter = OfficialEvidenceLinkAdapter(
        http=FakeDocumentHttp(document(final_url=url)),
        fund_company_hosts=("fund.example.cn",),
        index_company_hosts=("index.example.cn",),
    )

    result = adapter.validate(url)

    assert result["status"] == "valid"
    assert result["publisher_identity"] == identity
    assert result["official_evidence_eligible"] is True


@pytest.mark.parametrize(
    "url",
    [
        "https://evilsse.com.cn/notice.pdf",
        "https://sse.com.cn.evil.example/notice.pdf",
        "https://sse.com.cn:444/notice.pdf",
        "https://xn--sse-9la.com.cn/notice.pdf",
    ],
)
def test_official_link_rejects_suffix_confusion_ports_and_idn_before_network(url: str):
    """Catches host matching that accepts lookalike publishers or non-standard ports."""
    from data_sources.providers.official_evidence import OfficialEvidenceLinkAdapter

    http = FakeDocumentHttp(document(final_url="https://www.sse.com.cn/notice.pdf"))

    result = OfficialEvidenceLinkAdapter(http=http).validate(url)

    assert result["status"] == "rejected_untrusted_publisher"
    assert http.calls == []


def test_official_link_accepts_same_publisher_redirect_and_keeps_sanitized_final_reference():
    """Catches accepting redirects without proving both endpoints share an official publisher."""
    from data_sources.providers.official_evidence import OfficialEvidenceLinkAdapter

    adapter = OfficialEvidenceLinkAdapter(http=FakeDocumentHttp(document(
        final_url="https://static.sse.com.cn/notice/final.pdf?utm_source=rss&id=7",
        headers={"Last-Modified": "Tue, 18 Aug 2026 08:00:00 GMT"},
    )))

    result = adapter.validate("https://www.sse.com.cn/notice/old.pdf?utm_source=rss")

    assert result == {
        "status": "valid",
        "publisher_identity": "sse",
        "initial_public_reference": "https://www.sse.com.cn/notice/old.pdf",
        "observed_final_reference": "https://static.sse.com.cn/notice/final.pdf",
        "content_type": "application/pdf",
        "published_at": "Tue, 18 Aug 2026 08:00:00 GMT",
        "official_evidence_eligible": True,
    }


def test_official_link_rejects_secret_query_before_transport():
    """Catches a credential-like query value being sent to an official publisher despite redacting display text."""
    from data_sources.providers.official_evidence import OfficialEvidenceLinkAdapter

    http = FakeDocumentHttp(document(final_url="https://www.sse.com.cn/notice/final.pdf"))

    assert OfficialEvidenceLinkAdapter(http=http).validate(
        "https://www.sse.com.cn/notice/start.pdf?access_token=secret"
    ) == {"status": "rejected_unsafe_reference", "official_evidence_eligible": False}
    assert http.calls == []


@pytest.mark.parametrize(
    "target",
    [
        "https://www.sse.com.cn/notice/final.pdf?access_token=secret",
        "https://www.evil.example/notice/final.pdf",
        "http://www.sse.com.cn/notice/final.pdf",
        "https://www.sse.com.cn:444/notice/final.pdf",
        "https://www.sse.com.cn/login",
        "https://www.sse.com.cn/captcha",
    ],
)
def test_official_link_rejects_every_forbidden_redirect_before_sending_target(target: str):
    """Catches an unsafe redirect target being fetched before the official-link policy rejects it."""
    from data_sources.providers.official_evidence import OfficialEvidenceLinkAdapter

    session = requests.Session()
    transport = RedirectAdapter([RedirectResponse(status_code=302, headers={"Location": target})])
    session.mount("https://", transport)

    result = OfficialEvidenceLinkAdapter(http=SafeHttpClient(session=session)).validate(
        "https://www.sse.com.cn/notice/start.pdf"
    )

    assert result["official_evidence_eligible"] is False
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("final_url", "content_type", "body", "expected"),
    [
        ("http://www.sse.com.cn/notice/final.pdf", "application/pdf", b"x", "rejected_insecure_redirect"),
        ("https://www.evil.example/notice.pdf", "application/pdf", b"x", "rejected_cross_publisher_redirect"),
        ("https://www.sse.com.cn/login", "text/html", b"x", "rejected_protected_content"),
        ("https://www.sse.com.cn/captcha", "text/html", b"x", "rejected_protected_content"),
        ("https://www.sse.com.cn/notice/data.bin", "application/octet-stream", b"x", "rejected_unsupported_content"),
        ("https://www.sse.com.cn/notice/large.pdf", "application/pdf", b"0123456789", "rejected_response_too_large"),
    ],
)
def test_official_link_fails_closed_for_unsafe_redirect_or_protected_content(
    final_url: str, content_type: str, body: bytes, expected: str
):
    """Catches protected, unrelated, unsupported, or oversized content becoming official evidence."""
    from data_sources.providers.official_evidence import OfficialEvidenceLinkAdapter

    adapter = OfficialEvidenceLinkAdapter(
        http=FakeDocumentHttp(document(final_url=final_url, content_type=content_type, body=body)),
        max_document_bytes=8,
    )

    assert adapter.validate("https://www.sse.com.cn/notice/start.pdf")["status"] == expected


def test_official_link_detects_protected_marker_anywhere_in_bounded_html():
    """Catches a padded login or CAPTCHA page escaping a prefix-only protected-content scan."""
    from data_sources.providers.official_evidence import OfficialEvidenceLinkAdapter

    body = b" " * 20_000 + b"<form>login</form>"
    result = OfficialEvidenceLinkAdapter(http=FakeDocumentHttp(document(
        final_url="https://www.sse.com.cn/notice/protected.html", content_type="text/html", body=body
    ))).validate("https://www.sse.com.cn/notice/start.html")

    assert result["status"] == "rejected_protected_content"


def test_official_link_does_not_claim_connection_when_safe_transport_fails():
    """Catches a fixture-only validation failure being represented as a live connection result."""
    from data_sources.providers.official_evidence import OfficialEvidenceLinkAdapter

    result = OfficialEvidenceLinkAdapter(http=FakeDocumentHttp(ProviderUnavailable("timeout"))).validate(
        "https://www.sse.com.cn/notice/start.pdf"
    )

    assert result == {
        "status": "unavailable_timeout",
        "publisher_identity": "sse",
        "initial_public_reference": "https://www.sse.com.cn/notice/start.pdf",
        "observed_final_reference": None,
        "official_evidence_eligible": False,
    }
