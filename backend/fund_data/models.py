from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DataMeta:
    source_name: str
    source_reference: str
    data_type: str
    as_of_date: str | None
    fetched_at: str
    status: str
    is_cached: bool = False
    is_stale: bool = False
    provider: str = ""
    fallback_used: bool = False
    message: str = ""
    original_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderResult:
    data: Any
    source_name: str
    source_reference: str
    data_type: str
    as_of_date: str | None
    status: str
    message: str = ""
