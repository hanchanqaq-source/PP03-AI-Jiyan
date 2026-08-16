from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import fund_portfolio
import newsradar
from fund_data import service as fund_service
from news_intelligence.clustering import cluster_items
from news_intelligence.normalizer import normalize_radar
from news_intelligence.ranking import rank_events
from news_intelligence.relationships import apply_watch_relations, relate_events


BEIJING = timezone(timedelta(hours=8))
TECH_TRACKS = {"ai", "semi", "robot", "auto", "energy", "bio", "space", "security", "tech", "consumer", "science"}
MODES = {"my_focus", "my_holdings", "global_tech", "domestic_policy"}
CATEGORIES = {"all", "policy", "industry", "company", "fund_notice", "deep_content"}
SORTS = {"importance", "latest", "holding_relevance"}


def _default_portfolio_loader() -> dict[str, Any]:
    holdings = fund_portfolio.list_fund_holdings().get("holdings") or []
    if not holdings:
        return {"overview": {"fund_count": 0, "updated_at": None}, "holdings": []}
    return fund_service.get_service().get_portfolio_analysis(holdings)


class MarketNewsService:
    def __init__(
        self,
        *,
        radar_loader: Callable[[], dict] = newsradar.get_radar,
        radar_refresher: Callable[[], dict] = newsradar.fetch_radar,
        portfolio_loader: Callable[[], dict] = _default_portfolio_loader,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.radar_loader = radar_loader
        self.radar_refresher = radar_refresher
        self.portfolio_loader = portfolio_loader
        self.now = now
        self._snapshot_lock = threading.RLock()
        self._base_snapshots: OrderedDict[str, list[Any]] = OrderedDict()
        self._detail_snapshots: OrderedDict[str, dict[str, dict[str, Any]]] = OrderedDict()

    def _load_radar(self, refresh: bool) -> tuple[dict, bool]:
        refresh_failed = False
        if refresh:
            try:
                return self.radar_refresher(), False
            except Exception:
                refresh_failed = True
        try:
            return self.radar_loader(), refresh_failed
        except Exception:
            return {
                "generated_at": None,
                "cache_status": "source_failure",
                "source_statuses": [],
                "stats": {"industries": 0, "total_sources": 0, "failed_sources": 0},
                "industries": [],
            }, True

    def _load_portfolio(self) -> tuple[dict[str, Any], str]:
        try:
            portfolio = self.portfolio_loader()
        except Exception:
            return {"overview": {"fund_count": 0}, "holdings": []}, "error"
        count = int((portfolio.get("overview") or {}).get("fund_count") or len(portfolio.get("holdings") or []))
        return portfolio, "ready" if count else "empty"

    @staticmethod
    def _fingerprint(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def _stable_facts(cls, value: Any, *, drop_updated_at: bool) -> Any:
        runtime_keys = {"fetched_at", "cache_age_seconds", "latency_ms", "request_time", "elapsed_ms"}
        if drop_updated_at:
            runtime_keys.add("updated_at")
        if isinstance(value, dict):
            return {
                key: cls._stable_facts(item, drop_updated_at=drop_updated_at)
                for key, item in value.items()
                if key not in runtime_keys
            }
        if isinstance(value, list):
            return [cls._stable_facts(item, drop_updated_at=drop_updated_at) for item in value]
        return value

    def _base_events(
        self,
        radar: dict[str, Any],
        portfolio: dict[str, Any],
        portfolio_status: str,
        now: datetime,
    ) -> tuple[str, list[Any]]:
        base_key = self._fingerprint({
            "radar": self._stable_facts(radar, drop_updated_at=False),
            "portfolio": (
                self._stable_facts(portfolio, drop_updated_at=True)
                if portfolio_status == "ready" else {"status": portfolio_status}
            ),
        })
        with self._snapshot_lock:
            cached = self._base_snapshots.get(base_key)
            if cached is not None:
                self._base_snapshots.move_to_end(base_key)
                return base_key, copy.deepcopy(cached)

        events = cluster_items(normalize_radar(radar, now=now))
        relate_events(events, portfolio if portfolio_status == "ready" else None, [])
        events = rank_events(events, "importance")
        with self._snapshot_lock:
            self._base_snapshots[base_key] = copy.deepcopy(events)
            self._base_snapshots.move_to_end(base_key)
            while len(self._base_snapshots) > 4:
                self._base_snapshots.popitem(last=False)
        return base_key, events

    def _remember_details(self, snapshot_id: str, events: list[Any]) -> None:
        details = {event.event_id: event.to_dict() for event in events}
        with self._snapshot_lock:
            self._detail_snapshots[snapshot_id] = details
            self._detail_snapshots.move_to_end(snapshot_id)
            while len(self._detail_snapshots) > 16:
                self._detail_snapshots.popitem(last=False)

    def get_events(
        self,
        *,
        mode: str = "my_focus",
        tag_ids: list[str] | None = None,
        category: str = "all",
        days: int = 7,
        sort: str = "importance",
        refresh: bool = False,
    ) -> dict[str, Any]:
        if mode not in MODES or category not in CATEGORIES or days not in {1, 3, 7, 30} or sort not in SORTS:
            raise ValueError("invalid market-news filters")
        selected_tags = list(dict.fromkeys(tag_ids or []))
        radar, refresh_failed = self._load_radar(refresh)
        portfolio, portfolio_status = self._load_portfolio()
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        base_key, all_events = self._base_events(radar, portfolio, portfolio_status, now)
        apply_watch_relations(all_events, selected_tags)
        all_events = rank_events(all_events, "importance")
        snapshot_id = hashlib.sha256(
            f"{base_key}|{'|'.join(sorted(selected_tags))}".encode("utf-8")
        ).hexdigest()[:20]
        self._remember_details(snapshot_id, all_events)

        cutoff = now - timedelta(days=days)
        filtered = [
            event for event in all_events
            if event.published_at_latest is not None and event.published_at_latest >= cutoff
        ]
        if category != "all":
            filtered = [event for event in filtered if event.category == category]

        empty_reason = None
        if mode == "my_focus":
            if not selected_tags:
                filtered = []
                empty_reason = "no_tags"
            else:
                selected = set(selected_tags)
                filtered = [event for event in filtered if selected & {tag["id"] for tag in event.related_tags}]
        elif mode == "my_holdings":
            if portfolio_status == "empty":
                filtered = []
                empty_reason = "no_holdings"
            elif portfolio_status == "error":
                filtered = []
                empty_reason = "portfolio_error"
            else:
                filtered = [event for event in filtered if event.relation_level in {"direct_holding", "industry_relation"}]
        elif mode == "global_tech":
            filtered = [event for event in filtered if any(source.track_key in TECH_TRACKS for source in event.sources)]
        elif mode == "domestic_policy":
            filtered = [
                event for event in filtered
                if event.category == "policy" and any(source.region.upper() == "CN" for source in event.sources)
            ]

        filtered = rank_events(filtered, sort)
        if not filtered and empty_reason is None:
            empty_reason = "no_events"

        today = now.astimezone(BEIJING).date()
        today_events = [
            event for event in all_events
            if event.published_at_latest is not None and event.published_at_latest.astimezone(BEIJING).date() == today
        ]
        today_ranked = rank_events(today_events, "importance")
        relation_counts = Counter(event.relation_level for event in today_events)
        fund_counts: Counter[tuple[str, str]] = Counter()
        for event in today_events:
            for fund in event.related_funds:
                fund_counts[(str(fund.get("fund_code") or ""), str(fund.get("fund_name") or ""))] += 1
        failed_sources = int((radar.get("stats") or {}).get("failed_sources") or 0)
        data_status = str(radar.get("cache_status") or "cache")
        return {
            "events": [event.to_dict() for event in filtered],
            "today_focus": [event.to_dict() for event in today_ranked[:5]],
            "impact_summary": None if portfolio_status == "error" else {
                "holding_related_count": relation_counts["direct_holding"] + relation_counts["industry_relation"],
                "direct_count": relation_counts["direct_holding"],
                "industry_count": relation_counts["industry_relation"],
                "watch_count": relation_counts["watch_tag"],
                "funds": [
                    {"fund_code": code, "fund_name": name, "event_count": count}
                    for (code, name), count in sorted(fund_counts.items(), key=lambda item: (-item[1], item[0][0]))
                ],
            },
            "generated_at": radar.get("generated_at"),
            "data_status": data_status,
            "source_summary": {
                "total_sources": int((radar.get("stats") or {}).get("total_sources") or 0),
                "failed_sources": failed_sources,
                "cache_status": data_status,
                "refresh_failed": refresh_failed,
                "source_statuses": radar.get("source_statuses") or [],
            },
            "portfolio_status": portfolio_status,
            "snapshot_id": snapshot_id,
            "ai_status": "unavailable",
            "empty_reason": empty_reason,
            "filters": {
                "mode": mode, "tag_ids": selected_tags, "category": category, "days": days, "sort": sort,
            },
            "filter_options": {
                "modes": sorted(MODES), "categories": sorted(CATEGORIES), "days": [1, 3, 7, 30], "sorts": sorted(SORTS),
            },
        }

    def get_event(self, event_id: str, snapshot_id: str | None = None):
        with self._snapshot_lock:
            if snapshot_id:
                snapshot = self._detail_snapshots.get(snapshot_id)
                return snapshot.get(event_id) if snapshot else None
            for snapshot in reversed(self._detail_snapshots.values()):
                if event_id in snapshot:
                    return snapshot[event_id]
        self.get_events(mode="global_tech")
        with self._snapshot_lock:
            for snapshot in reversed(self._detail_snapshots.values()):
                if event_id in snapshot:
                    return snapshot[event_id]
        return None


_service: MarketNewsService | None = None


def get_service() -> MarketNewsService:
    global _service
    if _service is None:
        _service = MarketNewsService()
    return _service


def reset_service() -> None:
    global _service
    _service = None
