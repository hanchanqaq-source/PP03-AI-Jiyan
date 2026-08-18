from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class NewsSourceItem:
    source_name: str
    source_url: str
    original_url: str
    published_at: datetime | None
    fetched_at: datetime
    title: str
    summary: str
    language: str
    region: str
    track_key: str
    track_name: str
    category: str
    normalized_title: str
    tokens: frozenset[str]
    anchors: frozenset[str]
    related_tags: tuple[tuple[str, str], ...]
    text_related_tags: tuple[tuple[str, str], ...]
    source_domain: str
    data_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_url": self.source_url,
            "original_url": self.original_url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "fetched_at": self.fetched_at.isoformat(),
            "title": self.title,
            "summary_or_excerpt": self.summary,
            "language": self.language,
            "region": self.region,
            "data_status": self.data_status,
        }


@dataclass(slots=True)
class MarketNewsEvent:
    event_id: str
    title: str
    summary: str
    category: str
    published_at_first: datetime | None
    published_at_latest: datetime | None
    sources: list[NewsSourceItem]
    related_tags: list[dict[str, str]]
    original_links: list[str]
    data_status: str
    tag_evidence: list[dict[str, str]]
    related_companies: list[dict[str, Any]] = field(default_factory=list)
    related_funds: list[dict[str, Any]] = field(default_factory=list)
    relation_level: str = "none"
    relation_evidence: list[dict[str, Any]] = field(default_factory=list)
    impact_tendency: str = "unclear"
    impact_basis: list[str] = field(default_factory=list)
    confidence: str = "unavailable"
    missing_information: list[str] = field(default_factory=lambda: ["AI摘要暂不可用"])
    importance_score: int = 0
    verification_status: str | None = None
    verification_reason: str | None = None
    verified_at: str | None = None
    verified_key_fields: list[dict[str, Any]] = field(default_factory=list)

    @property
    def source_count(self) -> int:
        return len(self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "summary": self.summary or "AI摘要暂不可用",
            "summary_status": "source_excerpt" if self.summary else "ai_unavailable",
            "category": self.category,
            "published_at_first": self.published_at_first.isoformat() if self.published_at_first else None,
            "published_at_latest": self.published_at_latest.isoformat() if self.published_at_latest else None,
            "sources": [source.to_dict() for source in self.sources],
            "source_count": self.source_count,
            "related_tags": self.related_tags,
            "tag_evidence": self.tag_evidence,
            "related_companies": self.related_companies,
            "related_funds": self.related_funds,
            "relation_level": self.relation_level,
            "relation_evidence": self.relation_evidence,
            "impact_tendency": self.impact_tendency,
            "impact_basis": self.impact_basis,
            "confidence": self.confidence,
            "original_links": self.original_links,
            "data_status": self.data_status,
            "missing_information": self.missing_information,
            "importance_score": self.importance_score,
            "verification_status": self.verification_status,
            "verification_reason": self.verification_reason,
            "verified_at": self.verified_at,
            "verified_key_fields": self.verified_key_fields,
        }
