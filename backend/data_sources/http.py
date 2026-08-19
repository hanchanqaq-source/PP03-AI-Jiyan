from __future__ import annotations

import json
import math
import socket
from collections.abc import Mapping
import ssl
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from source_health.probe_errors import retry_delay_seconds

from .provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_TEST_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SENSITIVE_HEADER_TERMS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "api-key",
    "apikey",
    "credential",
    "password",
)


@dataclass(frozen=True, slots=True)
class SafeHttpDocument:
    """Bounded public response metadata for adapters that must validate redirects."""

    body: bytes
    final_url: str
    headers: Mapping[str, str]


class SafeHttpClient:
    """Small GET-only provider transport with bounded, redacted failure modes.

    Callers decide whether and when to retry a failed safe request.  This class
    performs no hidden retry and never exposes credentials or full request URLs.
    It deliberately bypasses all Session request preparation, so an injected
    Session contributes only its transport adapter, not auth, cookies, netrc,
    proxy, certificate, or default-header state.
    """

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 10.0,
        max_bytes: int = 2_000_000,
        user_agent: str = "PP03-AI-Jiyan/1.0",
        max_redirects: int = 3,
        allow_http_test_urls: bool = False,
        max_retry_after_seconds: float = 60.0,
    ) -> None:
        numeric_bounds = (timeout_seconds, max_bytes, max_redirects, max_retry_after_seconds)
        if (
            any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in numeric_bounds)
            or not all(math.isfinite(value) for value in numeric_bounds)
            or timeout_seconds <= 0
            or max_bytes <= 0
            or max_redirects < 0
            or max_retry_after_seconds < 0
        ):
            raise ValueError("SafeHttpClient bounds must be finite and positive")
        if not user_agent.strip():
            raise ValueError("SafeHttpClient requires an explicit user agent")
        self._session = session or requests.Session()
        self._timeout = float(timeout_seconds)
        self._max_bytes = int(max_bytes)
        self._user_agent = user_agent
        self._max_redirects = int(max_redirects)
        self._allow_http_test_urls = allow_http_test_urls
        self._max_retry_after_seconds = float(max_retry_after_seconds)

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | None = None,
    ) -> object:
        payload = self.get_bytes(url, headers=headers, params=params)
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderSchemaChanged("schema_changed", reference=url) from error

    def get_bytes(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | None = None,
    ) -> bytes:
        return self.get_document(url, headers=headers, params=params).body

    def get_document(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        redirect_validator: Callable[[str, str], bool] | None = None,
    ) -> SafeHttpDocument:
        """GET a bounded document, refusing redirects rejected by the caller's identity policy."""
        current_url = url
        current_params = params
        redirects = 0
        while True:
            self._validate_url(current_url)
            response = self._request(current_url, headers=headers, params=current_params)
            current_params = None
            try:
                status = self._status_code(response, current_url)
                if status in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise ProviderUnavailable("redirect_invalid", reference=current_url)
                    if redirects >= self._max_redirects:
                        raise ProviderUnavailable("redirect_limit", reference=current_url)
                    next_url = urljoin(current_url, location)
                    try:
                        self._validate_url(next_url)
                    except ProviderUnavailable as error:
                        raise ProviderUnavailable("insecure_redirect", reference=current_url) from error
                    if redirect_validator is not None and not redirect_validator(current_url, next_url):
                        raise ProviderUnavailable("redirect_disallowed", reference=current_url)
                    current_url = next_url
                    redirects += 1
                    continue
                self._raise_for_status(status, response, current_url)
                return SafeHttpDocument(
                    body=self._read_bounded(response, current_url),
                    final_url=current_url,
                    headers={str(key): str(value) for key, value in response.headers.items()},
                )
            finally:
                response.close()

    def _request(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None,
        params: Mapping[str, object] | None,
    ) -> Any:
        request_headers: dict[str, str] = {}
        for key, value in (headers or {}).items():
            if not isinstance(key, str) or self._is_sensitive_header(key):
                raise ProviderUnavailable("unsafe_request_headers", reference=url)
            if key.lower() != "user-agent":
                request_headers[key] = value
        request_headers["User-Agent"] = self._user_agent
        try:
            prepared = requests.Request(
                "GET", url, headers=request_headers, params=params
            ).prepare()
            return self._session.get_adapter(url).send(
                prepared,
                timeout=(self._timeout, self._timeout),
                verify=True,
                stream=True,
                proxies={},
                cert=None,
            )
        except requests.Timeout as error:
            raise ProviderUnavailable("timeout", reference=url) from error
        except (requests.exceptions.SSLError, ssl.SSLError) as error:
            raise ProviderUnavailable("tls", reference=url) from error
        except socket.gaierror as error:
            raise ProviderUnavailable("dns", reference=url) from error
        except (requests.ConnectionError, ConnectionError, OSError) as error:
            raise ProviderUnavailable("connection", reference=url) from error
        except Exception as error:
            raise ProviderUnavailable("request_failed", reference=url) from error

    def _read_bounded(self, response: Any, url: str) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError) as error:
                raise ProviderUnavailable("invalid_content_length", reference=url) from error
            if declared_size < 0 or declared_size > self._max_bytes:
                raise ProviderUnavailable("response_too_large", reference=url)

        chunks: list[bytes] = []
        received = 0
        try:
            for chunk in response.iter_content(chunk_size=min(self._max_bytes, 64 * 1024)):
                if not chunk:
                    continue
                received += len(chunk)
                if received > self._max_bytes:
                    raise ProviderUnavailable("response_too_large", reference=url)
                chunks.append(chunk)
        except ProviderUnavailable:
            raise
        except (requests.Timeout, TimeoutError) as error:
            raise ProviderUnavailable("timeout", reference=url) from error
        except (requests.exceptions.SSLError, ssl.SSLError) as error:
            raise ProviderUnavailable("tls", reference=url) from error
        except socket.gaierror as error:
            raise ProviderUnavailable("dns", reference=url) from error
        except (requests.ConnectionError, ConnectionError, OSError) as error:
            raise ProviderUnavailable("connection", reference=url) from error
        except Exception as error:
            raise ProviderUnavailable("read_failed", reference=url) from error
        return b"".join(chunks)

    @staticmethod
    def _is_sensitive_header(header_name: str) -> bool:
        normalized = "".join(
            character.lower() for character in header_name if character.isascii() and character.isalnum()
        )
        return any(term in normalized for term in _SENSITIVE_HEADER_TERMS)

    def _validate_url(self, url: str) -> None:
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            _ = parts.port
        except ValueError as error:
            raise ProviderUnavailable("insecure_url", reference=url) from error
        if parts.username or parts.password or not host:
            raise ProviderUnavailable("insecure_url", reference=url)
        if parts.scheme == "https":
            return
        is_test_host = host in _TEST_HTTP_HOSTS or host.endswith(".test")
        if parts.scheme == "http" and self._allow_http_test_urls and is_test_host:
            return
        raise ProviderUnavailable("insecure_url", reference=url)

    @staticmethod
    def _status_code(response: Any, url: str) -> int:
        try:
            return int(response.status_code)
        except (TypeError, ValueError) as error:
            raise ProviderUnavailable("invalid_status", reference=url) from error

    def _raise_for_status(self, status: int, response: Any, url: str) -> None:
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise ProviderUnavailable("authentication", reference=url)
        if status == 429:
            retry_after = retry_delay_seconds(
                response,
                default=0.0,
                maximum=self._max_retry_after_seconds,
            )
            raise ProviderRateLimited(retry_after_seconds=retry_after, reference=url)
        if status == 408:
            raise ProviderUnavailable("timeout", reference=url)
        if 500 <= status < 600:
            raise ProviderUnavailable("server_error", reference=url)
        raise ProviderUnavailable("http_client_error", reference=url)
