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
from evidence_verification.models import EvidenceSnapshot, FieldVerificationStatus, VerificationStatus
from evidence_verification.storage import event_document, field_document
from news_intelligence.clustering import cluster_items
from news_intelligence.models import MarketNewsEvent, NewsSourceItem
from news_intelligence.normalizer import normalize_radar
from news_intelligence.ranking import rank_events
from news_intelligence.relationships import apply_watch_relations, relate_events
from news_pipeline.models import RawSnapshot, TrustedSnapshot


TECH_TRACKS = {"ai", "semi", "robot", "auto", "energy", "bio", "space", "security", "tech", "consumer", "science"}
GLOBAL_REGIONS = {"GLOBAL", "US", "CA", "EU", "UK", "JP", "KR", "TW", "HK", "SG", "AU"}
MODES = {"my_focus", "my_holdings", "global_tech", "domestic_policy"}
CATEGORIES = {"all", "policy", "industry", "company", "fund_notice", "deep_content"}
SORTS = {"importance", "latest", "holding_relevance"}


def _default_portfolio_loader() -> dict[str, Any]:
    holdings = fund_portfolio.list_fund_holdings().get("holdings") or []
    if not holdings:
        return {"overview": {"fund_count": 0, "updated_at": None}, "holdings": []}
    return fund_service.get_service().get_portfolio_analysis(holdings)


def _default_evidence_admitter(events: list[Any]) -> tuple[str, list[Any]]:
    from evidence_verification.service import get_service

    return get_service().admit(events)


def _default_evidence_version() -> str:
    from evidence_verification.service import get_service

    snapshot = get_service().get_snapshot()
    return snapshot.snapshot_id if snapshot else "unavailable"


def project_trusted_snapshot(
    evidence_snapshot: EvidenceSnapshot,
    raw_snapshot: RawSnapshot,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> TrustedSnapshot:
    """Project the complete evidence-approved event set from one raw snapshot."""
    if type(evidence_snapshot) is not EvidenceSnapshot or type(raw_snapshot) is not RawSnapshot:
        raise ValueError("invalid pipeline snapshot")
    if evidence_snapshot.raw_snapshot_id != raw_snapshot.raw_snapshot_id:
        raise ValueError("evidence and raw snapshot identities differ")
    raw_by_id: dict[str, dict[str, Any]] = {}
    for item in raw_snapshot.items:
        if type(item) is not dict or type(item.get("event_id")) is not str:
            raise ValueError("raw snapshot must contain normalized events")
        event_id = item["event_id"]
        if event_id in raw_by_id:
            raise ValueError("raw event identity is ambiguous")
        raw_by_id[event_id] = item
    approved_statuses = {VerificationStatus.VERIFIED, VerificationStatus.CORROBORATED}
    approved_field_statuses = {FieldVerificationStatus.VERIFIED, FieldVerificationStatus.CORROBORATED}
    rows: list[dict[str, Any]] = []
    for evidence in evidence_snapshot.events:
        if evidence.verification_status not in approved_statuses:
            continue
        raw = raw_by_id.get(evidence.event_id)
        if raw is None:
            raise ValueError("approved evidence event is absent from the raw snapshot")
        row = copy.deepcopy(raw)
        canonical = event_document(evidence)
        untrusted_values = tuple(
            field.raw_value for field in evidence.key_fields
            if field.verification_status not in approved_field_statuses and field.raw_value
        )
        row["title"] = canonical["title"]
        row["summary"] = canonical["summary"]
        row["impact_basis"] = [
            basis for basis in row.get("impact_basis") or []
            if not any(value in basis for value in untrusted_values)
        ]
        row["verification_status"] = evidence.verification_status.value
        row["verification_reason"] = evidence.verification_reason
        row["verified_at"] = evidence.verified_at.isoformat()
        row["verified_key_fields"] = [
            field_document(field) for field in evidence.key_fields
            if field.verification_status in approved_field_statuses
        ]
        rows.append(row)
    published_at = now()
    if not isinstance(published_at, datetime) or published_at.tzinfo is None or published_at.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError("trusted projection clock must be aware UTC")
    return TrustedSnapshot(raw_snapshot.raw_snapshot_id, published_at, tuple(rows))


def _trusted_time(value: object) -> datetime | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("invalid trusted event timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("trusted event timestamp requires timezone")
    return parsed


def _trusted_event(document: dict[str, Any]) -> MarketNewsEvent:
    tags = [(str(row.get("id") or ""), str(row.get("name") or "")) for row in document.get("related_tags") or []]
    text_tags = {
        (str(row.get("id") or ""), str(row.get("name") or ""))
        for row in document.get("tag_evidence") or [] if row.get("provenance") == "article_text"
    }
    sources = []
    for row in document.get("sources") or []:
        region = str(row.get("region") or "unknown")
        sources.append(NewsSourceItem(
            source_name=str(row.get("source_name") or "未知公开来源"),
            source_url=str(row.get("source_url") or ""),
            original_url=str(row.get("original_url") or ""),
            published_at=_trusted_time(row.get("published_at")),
            fetched_at=_trusted_time(row.get("fetched_at")) or datetime.now(timezone.utc),
            title=str(row.get("title") or ""),
            summary=str(row.get("summary_or_excerpt") or ""),
            language=str(row.get("language") or "unknown"),
            region=region,
            track_key="tech" if region.upper() in GLOBAL_REGIONS else (tags[0][0] if tags else ""),
            track_name=tags[0][1] if tags else "",
            category=str(document.get("category") or "industry"),
            normalized_title=str(row.get("title") or "").casefold(),
            tokens=frozenset(),
            anchors=frozenset(),
            related_tags=tuple(tags),
            text_related_tags=tuple(tag for tag in tags if tag in text_tags),
            source_domain="",
            data_status=str(row.get("data_status") or document.get("data_status") or "cache"),
        ))
    return MarketNewsEvent(
        event_id=str(document["event_id"]),
        title=str(document.get("title") or ""),
        summary=str(document.get("summary") or ""),
        category=str(document.get("category") or "industry"),
        published_at_first=_trusted_time(document.get("published_at_first")),
        published_at_latest=_trusted_time(document.get("published_at_latest")),
        sources=sources,
        related_tags=[dict(row) for row in document.get("related_tags") or []],
        original_links=list(document.get("original_links") or []),
        data_status=str(document.get("data_status") or "cache"),
        tag_evidence=[dict(row) for row in document.get("tag_evidence") or []],
        related_companies=[dict(row) for row in document.get("related_companies") or []],
        related_funds=[dict(row) for row in document.get("related_funds") or []],
        relation_level=str(document.get("relation_level") or "none"),
        relation_evidence=[dict(row) for row in document.get("relation_evidence") or []],
        impact_tendency=str(document.get("impact_tendency") or "unclear"),
        impact_basis=list(document.get("impact_basis") or []),
        confidence=str(document.get("confidence") or "unavailable"),
        missing_information=list(document.get("missing_information") or []),
        importance_score=int(document.get("importance_score") or 0),
        verification_status=document.get("verification_status"),
        verification_reason=document.get("verification_reason"),
        verified_at=document.get("verified_at"),
        verified_key_fields=[dict(row) for row in document.get("verified_key_fields") or []],
    )


class MarketNewsService:
    def __init__(
        self,
        *,
        radar_loader: Callable[[], dict] = newsradar.get_radar,
        radar_refresher: Callable[[], dict] = newsradar.fetch_radar,
        portfolio_loader: Callable[[], dict] = _default_portfolio_loader,
        evidence_admitter: Callable[[list[Any]], tuple[str, list[Any]]] = _default_evidence_admitter,
        evidence_version: Callable[[], str] = _default_evidence_version,
        trusted_loader: Callable[[], TrustedSnapshot | None] | None = None,
        trusted_context_loader: Callable[
            [], tuple[TrustedSnapshot, RawSnapshot, EvidenceSnapshot] | None
        ] | None = None,
        pipeline_state_loader: Callable[[], bool] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.radar_loader = radar_loader
        self.radar_refresher = radar_refresher
        self.portfolio_loader = portfolio_loader
        self.evidence_admitter = evidence_admitter
        self.evidence_version = evidence_version
        self.trusted_loader = trusted_loader
        self.trusted_context_loader = trusted_context_loader
        self.pipeline_state_loader = pipeline_state_loader
        self.now = now
        self._snapshot_lock = threading.RLock()
        self._base_snapshots: OrderedDict[str, list[Any]] = OrderedDict()
        self._detail_snapshots: OrderedDict[str, dict[str, dict[str, Any]]] = OrderedDict()
        self._detail_authorities: OrderedDict[str, str | None] = OrderedDict()
        self._current_detail_snapshot_id: str | None = None

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
    ) -> tuple[str, str, list[Any]]:
        evidence_snapshot_id = self.evidence_version()
        base_key = self._fingerprint({
            "radar": self._stable_facts(radar, drop_updated_at=False),
            "portfolio": (
                self._stable_facts(portfolio, drop_updated_at=True)
                if portfolio_status == "ready" else {"status": portfolio_status}
            ),
            "evidence_snapshot_id": evidence_snapshot_id,
        })
        with self._snapshot_lock:
            cached = self._base_snapshots.get(base_key)
            if cached is not None:
                self._base_snapshots.move_to_end(base_key)
                return base_key, evidence_snapshot_id, copy.deepcopy(cached)

        raw_events = cluster_items(normalize_radar(radar, now=now))
        actual_evidence_snapshot_id, admitted_events = self.evidence_admitter(raw_events)
        if actual_evidence_snapshot_id != evidence_snapshot_id:
            evidence_snapshot_id = actual_evidence_snapshot_id
            base_key = self._fingerprint({
                "radar": self._stable_facts(radar, drop_updated_at=False),
                "portfolio": (
                    self._stable_facts(portfolio, drop_updated_at=True)
                    if portfolio_status == "ready" else {"status": portfolio_status}
                ),
                "evidence_snapshot_id": evidence_snapshot_id,
            })
            with self._snapshot_lock:
                cached = self._base_snapshots.get(base_key)
                if cached is not None:
                    self._base_snapshots.move_to_end(base_key)
                    return base_key, evidence_snapshot_id, copy.deepcopy(cached)
        events = admitted_events
        relate_events(events, portfolio if portfolio_status == "ready" else None, [])
        events = rank_events(events, "importance")
        with self._snapshot_lock:
            self._base_snapshots[base_key] = copy.deepcopy(events)
            self._base_snapshots.move_to_end(base_key)
            while len(self._base_snapshots) > 4:
                self._base_snapshots.popitem(last=False)
        return base_key, evidence_snapshot_id, events

    def _remember_details(
        self,
        snapshot_id: str,
        events: list[Any],
        *,
        trusted_authority: str | None = None,
    ) -> None:
        details = {event.event_id: event.to_dict() for event in events}
        with self._snapshot_lock:
            self._detail_snapshots[snapshot_id] = details
            self._detail_snapshots.move_to_end(snapshot_id)
            self._detail_authorities[snapshot_id] = trusted_authority
            self._detail_authorities.move_to_end(snapshot_id)
            self._current_detail_snapshot_id = snapshot_id
            while len(self._detail_snapshots) > 16:
                expired, _ = self._detail_snapshots.popitem(last=False)
                self._detail_authorities.pop(expired, None)

    def _load_trusted_context(
        self,
    ) -> tuple[TrustedSnapshot, RawSnapshot, EvidenceSnapshot] | None:
        if self.trusted_context_loader is None:
            return None
        context = self.trusted_context_loader()
        if context is None:
            return None
        if (
            type(context) is not tuple
            or len(context) != 3
            or type(context[0]) is not TrustedSnapshot
            or type(context[1]) is not RawSnapshot
            or type(context[2]) is not EvidenceSnapshot
            or context[0].raw_snapshot_id != context[1].raw_snapshot_id
            or context[2].raw_snapshot_id != context[1].raw_snapshot_id
        ):
            raise OSError("storage_corrupt")
        return context

    def _pipeline_pending_events(
        self,
        *,
        mode: str,
        selected_tags: list[str],
        category: str,
        days: int,
        sort: str,
    ) -> dict[str, Any]:
        return {
            "events": [],
            "focus_events": [],
            "impact_summary": None,
            "generated_at": None,
            "data_status": "pipeline_pending",
            "source_summary": {
                "total_sources": 0,
                "failed_sources": 0,
                "cache_status": "pipeline_pending",
                "source_state": "pipeline_pending",
                "refresh_failed": False,
                "source_statuses": [],
            },
            "portfolio_status": "unavailable",
            "snapshot_id": None,
            "raw_snapshot_id": None,
            "trusted_snapshot_id": None,
            "evidence_snapshot_id": None,
            "ai_status": "unavailable",
            "empty_reason": "no_trusted_snapshot",
            "empty_message": "资讯流水线尚无成功发布的可信快照。",
            "filters": {
                "mode": mode,
                "tag_ids": sorted(selected_tags),
                "category": category,
                "days": days,
                "sort": sort,
            },
            "filter_options": {
                "modes": sorted(MODES),
                "categories": sorted(CATEGORIES),
                "days": [1, 3, 7, 30],
                "sorts": sorted(SORTS),
            },
        }

    @staticmethod
    def _article_tag_ids(event: Any) -> set[str]:
        return {
            str(tag.get("id") or "")
            for tag in event.tag_evidence
            if tag.get("provenance") == "article_text" and tag.get("id")
        }

    def _trusted_events(
        self,
        trusted: TrustedSnapshot,
        raw: RawSnapshot | None,
        evidence: EvidenceSnapshot | None,
        *,
        mode: str,
        selected_tags: list[str],
        category: str,
        days: int,
        sort: str,
    ) -> dict[str, Any]:
        events = [_trusted_event(copy.deepcopy(document)) for document in trusted.events]
        portfolio_status = "unavailable"
        events = rank_events(events, "importance")
        apply_watch_relations(events, selected_tags)
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = now - timedelta(days=days)
        filtered = [event for event in events if event.published_at_latest is not None and event.published_at_latest >= cutoff]
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
            filtered = []
            empty_reason = "portfolio_unavailable"
        elif mode == "global_tech":
            filtered = [
                event for event in filtered
                if any(source.track_key in TECH_TRACKS and source.region.upper() in GLOBAL_REGIONS for source in event.sources)
            ]
        elif mode == "domestic_policy":
            filtered = [
                event for event in filtered
                if event.category == "policy" and any(source.region.upper() == "CN" for source in event.sources)
            ]
        if selected_tags:
            selected = set(selected_tags)
            filtered = [event for event in filtered if selected & self._article_tag_ids(event)]

        filtered = rank_events(filtered, sort)
        if not filtered and empty_reason is None:
            empty_reason = "no_events"
        normalized_query = {
            "mode": mode,
            "tag_ids": sorted(selected_tags),
            "category": category,
            "days": days,
            "sort": sort,
        }
        relationship_facts = [
            {
                "event_id": event.event_id,
                "relation_level": event.relation_level,
                "related_companies": copy.deepcopy(event.related_companies),
                "related_funds": copy.deepcopy(event.related_funds),
                "relation_evidence": copy.deepcopy(event.relation_evidence),
            }
            for event in filtered
        ]
        snapshot_id = hashlib.sha256(
            (
                f"{trusted.raw_snapshot_id}|{self._fingerprint(normalized_query)}|"
                f"{self._fingerprint([event.event_id for event in filtered])}|"
                f"{self._fingerprint(relationship_facts)}"
            ).encode("utf-8")
        ).hexdigest()[:20]
        self._remember_details(
            snapshot_id,
            filtered,
            trusted_authority=trusted.raw_snapshot_id,
        )
        if raw is None:
            source_ids = {
                (source.source_name, source.source_url, source.original_url)
                for event in events for source in event.sources
            }
            source_summary = {
                "total_sources": len(source_ids),
                "failed_sources": 0,
                "cache_status": "trusted",
                "source_state": "trusted",
                "refresh_failed": False,
                "source_statuses": [],
            }
        else:
            source_summary = {
                "total_sources": raw.total_source_count,
                "failed_sources": raw.failed_source_count,
                "cache_status": raw.cache_status,
                "source_state": raw.source_state,
                "refresh_failed": False,
                "source_statuses": copy.deepcopy(list(raw.source_statuses)),
            }
        return {
            "events": [event.to_dict() for event in filtered],
            "focus_events": [event.to_dict() for event in filtered[:5]],
            "impact_summary": None,
            "generated_at": trusted.published_at.isoformat(),
            "data_status": "trusted",
            "source_summary": source_summary,
            "portfolio_status": portfolio_status,
            "snapshot_id": snapshot_id,
            "raw_snapshot_id": trusted.raw_snapshot_id,
            "trusted_snapshot_id": trusted.raw_snapshot_id,
            "evidence_snapshot_id": evidence.snapshot_id if evidence is not None else None,
            "ai_status": "unavailable",
            "empty_reason": empty_reason,
            "empty_message": (
                "当前筛选暂无完成核验的资讯，可前往证据中心查看待核验内容。"
                if empty_reason == "no_events" else None
            ),
            "filters": normalized_query,
            "filter_options": {
                "modes": sorted(MODES), "categories": sorted(CATEGORIES), "days": [1, 3, 7, 30], "sorts": sorted(SORTS),
            },
        }

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
        context = self._load_trusted_context()
        if context is not None:
            trusted, raw, evidence = context
            return self._trusted_events(
                trusted,
                raw,
                evidence,
                mode=mode,
                selected_tags=selected_tags,
                category=category,
                days=days,
                sort=sort,
            )
        if self.pipeline_state_loader is not None and self.pipeline_state_loader():
            return self._pipeline_pending_events(
                mode=mode,
                selected_tags=selected_tags,
                category=category,
                days=days,
                sort=sort,
            )
        if self.trusted_loader is not None:
            trusted = self.trusted_loader()
            if trusted is not None:
                return self._trusted_events(
                    trusted,
                    None,
                    None,
                    mode=mode,
                    selected_tags=selected_tags,
                    category=category,
                    days=days,
                    sort=sort,
                )
        radar, refresh_failed = self._load_radar(refresh)
        portfolio, portfolio_status = self._load_portfolio()
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        base_key, evidence_snapshot_id, all_events = self._base_events(radar, portfolio, portfolio_status, now)
        apply_watch_relations(all_events, selected_tags)

        # One canonical query pipeline: time -> category -> mode -> article tags -> sort.
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
            filtered = [
                event for event in filtered
                if any(source.track_key in TECH_TRACKS and source.region.upper() in GLOBAL_REGIONS for source in event.sources)
            ]
        elif mode == "domestic_policy":
            filtered = [
                event for event in filtered
                if event.category == "policy" and any(source.region.upper() == "CN" for source in event.sources)
            ]

        if selected_tags:
            selected = set(selected_tags)
            filtered = [event for event in filtered if selected & self._article_tag_ids(event)]

        filtered = rank_events(filtered, sort)
        if not filtered and empty_reason is None:
            empty_reason = "no_events"
        empty_message = (
            "当前筛选暂无完成核验的资讯，可前往证据中心查看待核验内容。"
            if empty_reason == "no_events" else None
        )

        normalized_query = {
            "mode": mode,
            "tag_ids": sorted(selected_tags),
            "category": category,
            "days": days,
            "sort": sort,
        }
        filtered_fingerprint = self._fingerprint([event.event_id for event in filtered])
        snapshot_id = hashlib.sha256(
            f"{base_key}|{self._fingerprint(normalized_query)}|{filtered_fingerprint}".encode("utf-8")
        ).hexdigest()[:20]
        self._remember_details(snapshot_id, filtered)

        relation_counts = Counter(event.relation_level for event in filtered)
        fund_counts: Counter[tuple[str, str]] = Counter()
        for event in filtered:
            for fund in event.related_funds:
                fund_counts[(str(fund.get("fund_code") or ""), str(fund.get("fund_name") or ""))] += 1
        failed_sources = int((radar.get("stats") or {}).get("failed_sources") or 0)
        data_status = str(radar.get("cache_status") or "cache")
        source_state = str(radar.get("source_state") or {
            "realtime": "all_success",
            "partial": "partial_failure",
            "cache": "cached",
            "stale": "stale_cache",
            "source_failure": "all_failed",
        }.get(data_status, "cached"))
        return {
            "events": [event.to_dict() for event in filtered],
            "focus_events": [event.to_dict() for event in filtered[:5]],
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
                "source_state": source_state,
                "refresh_failed": refresh_failed,
                "source_statuses": radar.get("source_statuses") or [],
            },
            "portfolio_status": portfolio_status,
            "snapshot_id": snapshot_id,
            "evidence_snapshot_id": evidence_snapshot_id,
            "ai_status": "unavailable",
            "empty_reason": empty_reason,
            "empty_message": empty_message,
            "filters": normalized_query,
            "filter_options": {
                "modes": sorted(MODES), "categories": sorted(CATEGORIES), "days": [1, 3, 7, 30], "sorts": sorted(SORTS),
            },
        }

    def get_event(self, event_id: str, snapshot_id: str | None = None):
        with self._snapshot_lock:
            if snapshot_id:
                snapshot = self._detail_snapshots.get(snapshot_id)
                authority = self._detail_authorities.get(snapshot_id)
            else:
                selected_snapshot_id = self._current_detail_snapshot_id
                snapshot = self._detail_snapshots.get(selected_snapshot_id) if selected_snapshot_id else None
                authority = self._detail_authorities.get(selected_snapshot_id) if selected_snapshot_id else None
        if snapshot is not None:
            if (
                authority is None
                and self.pipeline_state_loader is not None
                and self.pipeline_state_loader()
            ):
                return None
            if authority is not None:
                context = self._load_trusted_context()
                current_authority = context[0].raw_snapshot_id if context is not None else None
                if current_authority is None and self.trusted_loader is not None:
                    trusted = self.trusted_loader()
                    current_authority = trusted.raw_snapshot_id if trusted is not None else None
                if current_authority != authority:
                    return None
            return snapshot.get(event_id)
        if snapshot_id is not None:
            return None
        self.get_events(mode="global_tech")
        with self._snapshot_lock:
            selected_snapshot_id = self._current_detail_snapshot_id
            snapshot = self._detail_snapshots.get(selected_snapshot_id) if selected_snapshot_id else None
        return snapshot.get(event_id) if snapshot else None


_service: MarketNewsService | None = None


def _default_trusted_context_loader() -> tuple[TrustedSnapshot, RawSnapshot, EvidenceSnapshot] | None:
    from news_pipeline.service import get_service as get_pipeline_service

    return get_pipeline_service().current_trusted_context()


def _default_pipeline_state_loader() -> bool:
    from news_pipeline.service import get_service as get_pipeline_service

    return get_pipeline_service().has_pipeline_state()


def get_service() -> MarketNewsService:
    global _service
    if _service is None:
        _service = MarketNewsService(
            trusted_context_loader=_default_trusted_context_loader,
            pipeline_state_loader=_default_pipeline_state_loader,
        )
    return _service


def reset_service() -> None:
    global _service
    _service = None
