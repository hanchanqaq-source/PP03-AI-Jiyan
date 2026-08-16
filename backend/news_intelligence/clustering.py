from __future__ import annotations

import hashlib
from datetime import timedelta

from news_intelligence.models import MarketNewsEvent, NewsSourceItem


CATEGORY_PRIORITY = {"policy": 5, "fund_notice": 4, "company": 3, "deep_content": 2, "industry": 1}


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _same_event(left: NewsSourceItem, right: NewsSourceItem) -> bool:
    delta = abs(left.published_at - right.published_at)
    if left.normalized_title == right.normalized_title:
        return delta <= timedelta(days=3)
    if delta > timedelta(hours=48) or not (left.anchors & right.anchors):
        return False
    return _jaccard(left.tokens, right.tokens) >= 0.25


def _event_from_sources(sources: list[NewsSourceItem]) -> MarketNewsEvent:
    sorted_sources = sorted(sources, key=lambda item: (item.published_at, item.source_name, item.original_url))
    ordered: list[NewsSourceItem] = []
    seen_articles: set[tuple[str, ...]] = set()
    for source in sorted_sources:
        article_key = (
            ("url", source.original_url)
            if source.original_url
            else ("fallback", source.source_domain, source.normalized_title, source.published_at.isoformat())
        )
        if article_key in seen_articles:
            continue
        seen_articles.add(article_key)
        ordered.append(source)
    normalized_titles = sorted({source.normalized_title for source in ordered})
    digest_input = "|".join(normalized_titles) + "|" + ordered[0].published_at.date().isoformat()
    event_id = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:20]
    category = max((source.category for source in ordered), key=lambda item: (CATEGORY_PRIORITY.get(item, 0), item))
    tags: dict[str, str] = {}
    for source in ordered:
        for tag_id, name in source.related_tags:
            tags.setdefault(tag_id, name)
    links = list(dict.fromkeys(source.original_url for source in ordered if source.original_url))
    statuses = {source.data_status for source in ordered}
    if "stale" in statuses:
        data_status = "stale"
    elif "realtime" in statuses:
        data_status = "realtime"
    else:
        data_status = sorted(statuses)[0] if statuses else "cache"
    summary = next((source.summary for source in reversed(ordered) if source.summary), "")
    return MarketNewsEvent(
        event_id=event_id,
        title=ordered[-1].title,
        summary=summary,
        category=category,
        published_at_first=ordered[0].published_at,
        published_at_latest=ordered[-1].published_at,
        sources=ordered,
        related_tags=[{"id": tag_id, "name": name} for tag_id, name in tags.items()],
        original_links=links,
        data_status=data_status,
    )


def cluster_items(items: list[NewsSourceItem]) -> list[MarketNewsEvent]:
    ordered = sorted(items, key=lambda item: (item.published_at, item.source_domain, item.original_url, item.title))
    groups: list[list[NewsSourceItem]] = []
    for item in ordered:
        match = next((group for group in groups if any(_same_event(item, member) for member in group)), None)
        if match is None:
            groups.append([item])
        else:
            match.append(item)
    events = [_event_from_sources(group) for group in groups]
    return sorted(events, key=lambda event: (-event.published_at_latest.timestamp(), event.event_id))
