from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .models import AdapterDescriptor, ProviderValue


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    capability_id: str
    parameters: Mapping[str, object]


class ProviderAdapter(Protocol):
    descriptor: AdapterDescriptor

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]: ...

    def probe(self, capability_id: str) -> Mapping[str, object]: ...
