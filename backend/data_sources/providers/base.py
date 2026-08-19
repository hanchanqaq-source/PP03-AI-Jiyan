from __future__ import annotations

from typing import Mapping

from data_sources.provider_contract import ProviderRequest


class BaseProvider:
    """Lazy provider adapter base; concrete adapters own request construction and parsing."""

    def fetch(self, request: ProviderRequest) -> tuple[object, ...]:
        raise NotImplementedError

    def probe(self, capability_id: str) -> Mapping[str, object]:
        raise NotImplementedError
