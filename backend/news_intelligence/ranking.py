from __future__ import annotations

from news_intelligence.models import MarketNewsEvent


RELATION_PRIORITY = {"direct_holding": 3, "industry_relation": 2, "watch_tag": 1, "none": 0}


def _importance(event: MarketNewsEvent) -> int:
    if event.relation_level == "direct_holding":
        tier = 500
    elif event.category in {"policy", "fund_notice", "company"}:
        tier = 400
    elif event.relation_level == "industry_relation":
        tier = 300
    elif event.relation_level == "watch_tag":
        tier = 200
    else:
        tier = 100
    return tier + min(event.source_count, 9)


def rank_events(events: list[MarketNewsEvent], sort: str) -> list[MarketNewsEvent]:
    for event in events:
        event.importance_score = _importance(event)
    if sort == "importance":
        key = lambda event: (-event.importance_score, -event.published_at_latest.timestamp(), event.event_id)
    elif sort == "latest":
        key = lambda event: (-event.published_at_latest.timestamp(), event.event_id)
    elif sort == "holding_relevance":
        key = lambda event: (
            -RELATION_PRIORITY.get(event.relation_level, 0),
            -event.importance_score,
            -event.published_at_latest.timestamp(),
            event.event_id,
        )
    else:
        raise ValueError(f"unsupported market-news sort: {sort}")
    return sorted(events, key=key)
