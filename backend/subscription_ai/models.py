"""Public, credential-free models for subscription AI providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


AuthStatus = Literal[
    "not_installed",
    "installed_not_logged_in",
    "logged_in_chatgpt",
    "logged_in_api_key",
    "unsupported_version",
    "status_failed",
]
ConnectionStatus = Literal[
    "not_tested",
    "running",
    "success",
    "not_installed",
    "not_logged_in",
    "wrong_auth_mode",
    "quota_or_rate_limited",
    "timeout",
    "cancelled",
    "process_failed",
    "unexpected_output",
]


@dataclass(frozen=True)
class SubscriptionProviderStatus:
    provider_id: str
    display_name: str
    installed: bool
    version: str | None
    auth_status: AuthStatus
    available: bool
    supports_streaming: bool
    supports_project_tools: bool
    last_test_status: ConnectionStatus
    last_test_at: str | None
    error_code: str | None
    message: str

    def to_public_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "installed": self.installed,
            "version": self.version,
            "auth_status": self.auth_status,
            "available": self.available,
            "test_status": self.last_test_status,
            "message": self.message,
            "last_test_at": self.last_test_at,
        }


@dataclass(frozen=True)
class ConnectionTestResult:
    status: ConnectionStatus
    message: str
    tested_at: str
    diagnostic: str | None = None

    def to_public_dict(self) -> dict:
        return asdict(self)
