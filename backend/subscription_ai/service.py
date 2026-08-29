"""Small W1 service facade for the single formal Codex provider."""

from __future__ import annotations

from .codex_provider import CodexSubscriptionProvider
from .models import SubscriptionProviderStatus


class SubscriptionAIService:
    def __init__(self, codex_provider: CodexSubscriptionProvider | None = None):
        self.codex_provider = codex_provider or CodexSubscriptionProvider()

    def list_providers(self, force: bool = False) -> list[dict]:
        return [self.codex_provider.get_status(force=force).to_public_dict()]

    def codex_status(self, force: bool = False) -> SubscriptionProviderStatus:
        return self.codex_provider.get_status(force=force)

    def start_codex_login(self, confirm_switch: bool = False) -> dict:
        progress = self.codex_provider.start_login(confirm_switch=confirm_switch)
        status = self.codex_provider.get_status(force=True)
        row = status.to_public_dict()
        row["message"] = progress["message"]
        return row

    def test_codex(self) -> dict:
        self.codex_provider.test_connection()
        return self.codex_provider.get_status().to_public_dict()

    def cancel_codex_test(self) -> dict:
        self.codex_provider.cancel_test()
        return self.codex_provider.get_status().to_public_dict()


_SERVICE = SubscriptionAIService()


def get_service() -> SubscriptionAIService:
    return _SERVICE
