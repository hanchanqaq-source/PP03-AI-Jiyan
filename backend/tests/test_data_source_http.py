from __future__ import annotations

import socket

import pytest
import requests

from data_sources.http import SafeHttpClient
from data_sources.provider_errors import (
    ProviderRateLimited,
    ProviderSchemaChanged,
    ProviderUnavailable,
)


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        content: bytes = b"{}",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content
        self.closed = False

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self._content), chunk_size):
            yield self._content[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self) -> None:
        self.responses: list[FakeResponse | BaseException] = []
        self.last_kwargs: dict[str, object] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def response(self, **kwargs: object) -> FakeResponse:
        response = FakeResponse(**kwargs)
        self.responses.append(response)
        return response

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.last_kwargs = kwargs
        self.calls.append((url, kwargs))
        next_response = self.responses.pop(0)
        if isinstance(next_response, BaseException):
            raise next_response
        return next_response


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


def test_http_client_rejects_oversized_response(fake_session: FakeSession):
    """Catches removal of the Content-Length preflight size boundary."""
    client = SafeHttpClient(session=fake_session, max_bytes=1024, timeout_seconds=3)
    response = fake_session.response(headers={"Content-Length": "2048"})

    with pytest.raises(ProviderUnavailable, match="response_too_large"):
        client.get_bytes("https://example.test/data")

    assert response.closed is True


def test_http_client_never_disables_tls(fake_session: FakeSession):
    """Catches a request path that weakens certificate verification."""
    fake_session.response(content=b'{"ok": true}')

    assert SafeHttpClient(session=fake_session).get_json("https://example.test/data") == {"ok": True}

    assert fake_session.last_kwargs["verify"] is True


def test_http_client_enforces_actual_streamed_bytes_when_content_length_lies(fake_session: FakeSession):
    """Catches accepting a body that exceeds the cap after a false header."""
    client = SafeHttpClient(session=fake_session, max_bytes=8)
    response = fake_session.response(headers={"Content-Length": "1"}, content=b"123456789")

    with pytest.raises(ProviderUnavailable, match="response_too_large"):
        client.get_bytes("https://example.test/data")

    assert response.closed is True


def test_http_client_rejects_http_until_explicit_test_url_policy_is_enabled(fake_session: FakeSession):
    """Catches accidental general HTTP access while retaining a deliberate test-only escape hatch."""
    with pytest.raises(ProviderUnavailable, match="insecure_url"):
        SafeHttpClient(session=fake_session).get_bytes("http://example.test/data")

    fake_session.response(content=b"ok")
    client = SafeHttpClient(session=fake_session, allow_http_test_urls=True)
    assert client.get_bytes("http://example.test/data") == b"ok"

    with pytest.raises(ProviderUnavailable, match="insecure_url"):
        client.get_bytes("http://public.example/data")


def test_http_client_rejects_redirect_that_downgrades_transport(fake_session: FakeSession):
    """Catches a redirect path that bypasses the HTTPS policy."""
    client = SafeHttpClient(session=fake_session)
    response = fake_session.response(status_code=302, headers={"Location": "http://example.test/data"})

    with pytest.raises(ProviderUnavailable, match="insecure_redirect"):
        client.get_bytes("https://example.test/start")

    assert response.closed is True


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (requests.Timeout("late"), "timeout"),
        (requests.exceptions.SSLError("bad certificate"), "tls"),
        (socket.gaierror("missing host"), "dns"),
    ],
)
def test_http_client_classifies_transport_failures_without_leaking_details(
    fake_session: FakeSession, error: BaseException, expected_code: str
):
    """Catches raw request exceptions escaping with network details or credentials."""
    fake_session.responses.append(error)

    with pytest.raises(ProviderUnavailable, match=expected_code) as raised:
        SafeHttpClient(session=fake_session).get_bytes("https://example.test/data?token=secret")

    assert "secret" not in str(raised.value)
    assert "example.test" not in str(raised.value)


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(401, "authentication"), (403, "authentication"), (503, "server_error")],
)
def test_http_client_classifies_http_failures(fake_session: FakeSession, status: int, expected_code: str):
    """Catches non-success HTTP statuses being parsed as successful provider payloads."""
    fake_session.response(status_code=status, content=b'{"token":"secret"}')

    with pytest.raises(ProviderUnavailable, match=expected_code) as raised:
        SafeHttpClient(session=fake_session).get_json("https://example.test/data?token=secret")

    assert "secret" not in str(raised.value)


def test_http_client_bounds_retry_after_for_rate_limits(fake_session: FakeSession):
    """Catches an unbounded upstream Retry-After value controlling local scheduling."""
    fake_session.response(status_code=429, headers={"Retry-After": "999999"})

    with pytest.raises(ProviderRateLimited, match="rate_limited") as raised:
        SafeHttpClient(session=fake_session).get_bytes("https://example.test/data")

    assert raised.value.retry_after_seconds == 60.0


def test_http_client_reports_invalid_json_as_schema_change(fake_session: FakeSession):
    """Catches malformed JSON being converted into an empty successful provider value."""
    fake_session.response(content=b"not-json token=secret")

    with pytest.raises(ProviderSchemaChanged, match="schema_changed") as raised:
        SafeHttpClient(session=fake_session).get_json("https://example.test/data")

    assert "secret" not in str(raised.value)


def test_http_client_has_finite_timeout_redirect_limit_and_explicit_user_agent(fake_session: FakeSession):
    """Catches an unbounded request configuration or a missing product user-agent."""
    fake_session.response(content=b"ok")

    SafeHttpClient(session=fake_session, timeout_seconds=3, max_redirects=2).get_bytes(
        "https://example.test/data"
    )

    assert fake_session.last_kwargs["timeout"] == (3.0, 3.0)
    assert fake_session.last_kwargs["stream"] is True
    assert fake_session.last_kwargs["allow_redirects"] is False
    assert fake_session.last_kwargs["headers"] == {"User-Agent": "PP03-AI-Jiyan/1.0"}


def test_http_client_preserves_its_explicit_user_agent_and_redacts_public_reference(fake_session: FakeSession):
    """Catches caller headers or an error reference exposing a secret request identity."""
    fake_session.response(status_code=503)

    with pytest.raises(ProviderUnavailable) as raised:
        SafeHttpClient(session=fake_session).get_bytes(
            "https://example.test/data?token=secret&mid=public",
            headers={"user-agent": "untrusted-client", "Accept": "application/json"},
        )

    assert fake_session.last_kwargs["headers"] == {
        "Accept": "application/json",
        "User-Agent": "PP03-AI-Jiyan/1.0",
    }
    assert raised.value.public_reference == "https://example.test/data?mid=public"


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": float("nan")},
        {"max_redirects": float("inf")},
        {"max_retry_after_seconds": float("nan")},
    ],
)
def test_http_client_rejects_non_finite_safety_bounds(options: dict[str, float]):
    """Catches an unbounded transport limit being accepted as a safe default."""
    with pytest.raises(ValueError, match="finite"):
        SafeHttpClient(**options)
