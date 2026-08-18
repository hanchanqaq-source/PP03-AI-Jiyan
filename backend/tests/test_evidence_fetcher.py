from __future__ import annotations

from email.message import Message
from urllib.request import Request

import pytest

from evidence_verification.service import SafeRedirectHandler, fetch_public_document


def public_resolver(host, *_args, **_kwargs):
    if host in {"127.0.0.1", "localhost"}:
        return [(2, 1, 6, "", ("127.0.0.1", 443))]
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


class Response:
    def __init__(self, body: bytes, *, url: str = "https://www.sec.gov/report"):
        self.body = body
        self.url = url
        self.headers = Message()
        self.headers.add_header("Content-Type", "text/html; charset=utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        return self.body

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        return self.response


def test_fetcher_reads_only_bounded_public_html_without_auth_or_cookie_headers():
    opener = Opener(Response(
        b"<html><head><title>Official filing</title></head><body><script>secret()</script><p>Investment CNY 12 billion.</p></body></html>"
    ))

    document = fetch_public_document(
        "https://www.sec.gov/report",
        resolver=public_resolver,
        opener=opener,
        timeout=5.0,
        max_bytes=1024,
    )

    request, timeout = opener.calls[0]
    assert timeout == 5.0
    assert request.get_header("Authorization") is None
    assert request.get_header("Cookie") is None
    assert document.canonical_url == "https://www.sec.gov/report"
    assert document.title == "Official filing"
    assert document.excerpt == "Official filing Investment CNY 12 billion."


def test_redirect_handler_rejects_private_destination_before_following_it():
    handler = SafeRedirectHandler(resolver=public_resolver, max_redirects=3)
    request = Request("https://www.sec.gov/report")

    with pytest.raises(PermissionError, match="public network"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1/private")


def test_redirect_handler_rejects_cross_publisher_official_redirect():
    handler = SafeRedirectHandler(resolver=public_resolver, max_redirects=3)
    request = Request("https://www.sec.gov/report")

    with pytest.raises(PermissionError, match="official publisher"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://www.nasa.gov/report")


def test_fetcher_rejects_oversize_and_unsupported_content_without_returning_partial_evidence():
    oversize = Opener(Response(b"x" * 1025))
    with pytest.raises(ValueError, match="size limit"):
        fetch_public_document(
            "https://www.sec.gov/report",
            resolver=public_resolver,
            opener=oversize,
            max_bytes=1024,
        )

    unsupported_response = Response(b"%PDF")
    unsupported_response.headers.replace_header("Content-Type", "application/pdf")
    unsupported = Opener(unsupported_response)
    with pytest.raises(ValueError, match="content type"):
        fetch_public_document(
            "https://www.sec.gov/report",
            resolver=public_resolver,
            opener=unsupported,
        )
