from __future__ import annotations

from typing import Any

from fund_data.models import ProviderResult


class ProviderUnavailable(RuntimeError):
    """A provider could not return a verifiable payload for one capability."""


class BaseFundProvider:
    name = "base"
    priority = 100
    capabilities: set[str] = set()

    def fetch(self, capability: str, **kwargs: Any) -> ProviderResult:
        raise ProviderUnavailable(f"{self.name} 不支持 {capability}")

