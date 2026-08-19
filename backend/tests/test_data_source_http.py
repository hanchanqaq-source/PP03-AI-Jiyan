from __future__ import annotations

import socket

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

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
        self.headers = CaseInsensitiveDict(headers or {})
        self._content = content
        self.closed = False
        self.history: list[object] = []
        self.raw = None

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self._content), chunk_size):
            yield self._content[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308} and "Location" in self.headers

    @property
    def content(self) -> bytes:
        return self._content


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

    def send(self, request, **kwargs: object) -> FakeResponse:
        self.last_request = request
        self.last_kwargs = kwargs
        self.calls.append((request.url, kwargs))
        next_response = self.responses.pop(0)
        if isinstance(next_response, BaseException):
            raise next_response
        return next_response

    def get_adapter(self, url: str) -> "FakeSession":
        return self


class CaptureAdapter(BaseAdapter):
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.requests: list[object] = []
        self.kwargs: list[dict[str, object]] = []

    def send(self, request, **kwargs: object) -> FakeResponse:
        self.requests.append(request)
        self.kwargs.append(kwargs)
        response = self._responses.pop(0)
        response.request = request
        response.url = request.url
        return response

    def close(self) -> None:
        pass


class GuardedSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.send_calls = 0

    def send(self, request, **kwargs: object):
        self.send_calls += 1
        raise AssertionError("SafeHttpClient must not invoke Session.send")


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
    assert fake_session.last_kwargs["proxies"] == {}
    assert fake_session.last_kwargs["cert"] is None
    assert dict(fake_session.last_request.headers) == {"User-Agent": "PP03-AI-Jiyan/1.0"}


def test_http_client_preserves_its_explicit_user_agent_and_redacts_public_reference(fake_session: FakeSession):
    """Catches caller headers or an error reference exposing a secret request identity."""
    fake_session.response(status_code=503)

    with pytest.raises(ProviderUnavailable) as raised:
        SafeHttpClient(session=fake_session).get_bytes(
            "https://example.test/data?token=secret&mid=public",
            headers={"user-agent": "untrusted-client", "Accept": "application/json"},
        )

    assert dict(fake_session.last_request.headers) == {
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


@pytest.mark.parametrize(
    "header_name",
    [
        "Authorization",
        "authorization",
        "AUTHORIZATION",
        "Cookie",
        "cOoKiE",
        "Proxy-Authorization",
        "X-Api-Key",
        "X-Auth-Token",
    ],
)
def test_http_client_rejects_sensitive_caller_headers_before_the_session(
    fake_session: FakeSession, header_name: str
):
    """Catches forwarding caller authentication material into the transport session."""
    with pytest.raises(ProviderUnavailable, match="unsafe_request_headers") as raised:
        SafeHttpClient(session=fake_session).get_bytes(
            "https://example.test/data?token=secret",
            headers={header_name: "credential-secret", "Accept": "application/json"},
        )

    assert fake_session.calls == []
    assert "secret" not in str(raised.value)


def test_http_client_ignores_injected_session_identity_cookie_and_environment_on_redirects():
    """Catches inherited Session auth, Cookie, proxy/netrc state leaking on either redirect hop."""
    session = requests.Session()
    session.auth = ("alice", "auth-secret")
    session.headers["Authorization"] = "Bearer inherited-secret"
    session.cookies.set("session", "cookie-secret", domain="example.test")
    session.trust_env = True
    adapter = CaptureAdapter(
        [
            FakeResponse(status_code=302, headers={"Location": "/next"}),
            FakeResponse(content=b"ok"),
        ]
    )
    session.mount("https://", adapter)

    assert SafeHttpClient(session=session).get_bytes(
        "https://example.test/start", headers={"Accept": "application/octet-stream"}
    ) == b"ok"

    assert session.auth == ("alice", "auth-secret")
    assert session.cookies.get("session", domain="example.test") == "cookie-secret"
    assert session.trust_env is True
    assert len(adapter.requests) == 2
    for request, kwargs in zip(adapter.requests, adapter.kwargs):
        lowered_headers = {key.lower(): value for key, value in request.headers.items()}
        assert lowered_headers["user-agent"] == "PP03-AI-Jiyan/1.0"
        assert lowered_headers["accept"] == "application/octet-stream"
        assert "authorization" not in lowered_headers
        assert "cookie" not in lowered_headers
        assert kwargs["proxies"] == {}


def test_http_client_uses_only_the_injected_sessions_transport_adapter():
    """Catches Session.send lifecycle behavior adding cookies or redirect state to an injected Session."""
    session = GuardedSession()
    adapter = CaptureAdapter([FakeResponse(content=b"ok")])
    session.mount("https://", adapter)

    assert SafeHttpClient(session=session).get_bytes("https://example.test/data") == b"ok"

    assert session.send_calls == 0
    assert len(adapter.requests) == 1


@pytest.mark.parametrize("port", ["bad-port", "65536", "-1"])
def test_http_client_rejects_malformed_or_out_of_range_ports_as_redacted_unavailable(
    fake_session: FakeSession, port: str
):
    """Catches URL port parsing errors escaping the provider error taxonomy."""
    with pytest.raises(ProviderUnavailable, match="insecure_url") as raised:
        SafeHttpClient(session=fake_session).get_bytes(f"https://example.test:{port}/data?token=secret")

    assert fake_session.calls == []
    assert raised.value.public_reference == ""
    assert "secret" not in str(raised.value)


def test_http_client_redacts_malformed_port_on_a_redirect_hop(fake_session: FakeSession):
    """Catches a malformed redirect URL escaping as a raw ValueError."""
    response = fake_session.response(status_code=302, headers={"Location": "https://example.test:bad-port/next"})

    with pytest.raises(ProviderUnavailable, match="insecure_redirect") as raised:
        SafeHttpClient(session=fake_session).get_bytes("https://example.test/start?token=secret")

    assert response.closed is True
    assert raised.value.public_reference == "https://example.test/start"
    assert "secret" not in str(raised.value)
