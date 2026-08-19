from __future__ import annotations

from data_sources.references import public_source_reference
from source_health.probe_errors import redact_probe_message


class ProviderError(RuntimeError):
    """A redacted, transport-safe failure returned by a provider boundary."""

    def __init__(self, code: str, *, reference: str = "") -> None:
        self.code = code
        self.public_reference = public_source_reference(reference)
        super().__init__(redact_probe_message(f"provider_error:{code}"))


class ProviderUnavailable(ProviderError):
    """The provider could not safely return a response for this request."""


class ProviderRateLimited(ProviderError):
    """The provider refused the request and supplied a bounded retry delay."""

    def __init__(self, code: str = "rate_limited", *, retry_after_seconds: float, reference: str = "") -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code, reference=reference)


class ProviderSchemaChanged(ProviderError):
    """A provider response could not be decoded as the contracted format."""
