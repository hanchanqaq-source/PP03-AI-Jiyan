from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from evidence_verification.models import VerificationStatus
from evidence_verification.service import EvidenceVerificationService, PublicDocument
from evidence_verification.storage import EvidenceStorage
from news_intelligence.models import NewsSourceItem


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def source(name: str, feed: str, article: str, title: str, summary: str) -> NewsSourceItem:
    return NewsSourceItem(
        source_name=name,
        source_url=feed,
        original_url=article,
        published_at=NOW,
        fetched_at=NOW,
        title=title,
        summary=summary,
        language="zh-CN",
        region="CN",
        track_key="semi",
        track_name="半导体",
        category="company",
        normalized_title="公司公告建设算力中心",
        tokens=frozenset({"公司", "公告", "建设", "算力", "中心"}),
        anchors=frozenset({"公司"}),
        related_tags=(("semiconductor", "半导体"),),
        text_related_tags=(("semiconductor", "半导体"),),
        source_domain="example.test",
        data_status="cache",
    )


def event(event_id: str, sources: list[NewsSourceItem]):
    return SimpleNamespace(
        event_id=event_id,
        title=sources[0].title,
        summary=sources[0].summary,
        category="company",
        published_at_first=NOW,
        published_at_latest=NOW,
        related_tags=[{"id": "semiconductor", "name": "半导体"}],
        sources=sources,
    )


def official_source() -> NewsSourceItem:
    return source(
        "SEC",
        "https://www.sec.gov/news/pressreleases.rss",
        "https://www.sec.gov/Archives/edgar/data/1/report.htm",
        "星河科技公告建设算力中心",
        "正式披露建设算力中心。",
    )


def test_refresh_uses_existing_official_feed_without_portfolio_data(tmp_path, monkeypatch):
    monkeypatch.setattr("fund_portfolio.list_fund_holdings", lambda: (_ for _ in ()).throw(AssertionError("portfolio read")))
    service = EvidenceVerificationService(
        storage=EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW),
        event_loader=lambda: [event("a" * 20, [official_source()])],
        now=lambda: NOW,
    )

    snapshot = service.refresh()

    assert snapshot.events[0].verification_status == VerificationStatus.VERIFIED
    stored = service.storage.current_path.read_text(encoding="utf-8").lower()
    assert "portfolio" not in stored
    assert "avg_cost" not in stored
    assert "notes" not in stored


def test_exact_official_link_from_ordinary_collector_requires_successful_bounded_fetch(tmp_path):
    linked = source(
        "普通资讯采集器",
        "https://collector.example/feed",
        "https://www.sec.gov/Archives/edgar/data/1/report.htm",
        "星河科技公告建设算力中心",
        "普通来源转述。",
    )
    calls = []

    def fetcher(url: str) -> PublicDocument:
        calls.append(url)
        return PublicDocument(
            canonical_url=url,
            title="星河科技公告建设算力中心",
            excerpt="SEC 正式披露建设算力中心。",
            published_at=NOW,
        )

    service = EvidenceVerificationService(
        storage=EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW),
        event_loader=lambda: [event("a" * 20, [linked])],
        document_fetcher=fetcher,
        now=lambda: NOW,
    )

    snapshot = service.refresh()

    assert calls == ["https://www.sec.gov/Archives/edgar/data/1/report.htm"]
    assert snapshot.events[0].verification_status == VerificationStatus.VERIFIED


def test_failed_official_fetch_keeps_claim_unverified(tmp_path):
    linked = source(
        "普通资讯采集器",
        "https://collector.example/feed",
        "https://www.sec.gov/Archives/edgar/data/1/report.htm",
        "星河科技公告建设算力中心",
        "普通来源转述。",
    )
    service = EvidenceVerificationService(
        storage=EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW),
        event_loader=lambda: [event("a" * 20, [linked])],
        document_fetcher=lambda _: (_ for _ in ()).throw(TimeoutError("timeout")),
        now=lambda: NOW,
    )

    snapshot = service.refresh()

    assert snapshot.events[0].verification_status == VerificationStatus.UNVERIFIED


def test_safe_redirect_within_same_official_publisher_can_attest_existing_link(tmp_path):
    linked = source(
        "普通资讯采集器",
        "https://collector.example/feed",
        "https://www.sec.gov/old-report",
        "星河科技公告建设算力中心",
        "普通来源转述。",
    )
    service = EvidenceVerificationService(
        storage=EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW),
        event_loader=lambda: [event("a" * 20, [linked])],
        document_fetcher=lambda _: PublicDocument(
            canonical_url="https://www.sec.gov/final-report",
            title="星河科技公告建设算力中心",
            excerpt="SEC 正式披露建设算力中心。",
            published_at=NOW,
        ),
        now=lambda: NOW,
    )

    snapshot = service.refresh()

    assert snapshot.events[0].verification_status == VerificationStatus.VERIFIED
    assert snapshot.events[0].primary_evidence[0].canonical_url == "https://www.sec.gov/final-report"


def test_failed_refresh_preserves_previous_successful_snapshot_bytes(tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    EvidenceVerificationService(
        storage=storage,
        event_loader=lambda: [event("a" * 20, [official_source()])],
        now=lambda: NOW,
    ).refresh()
    before = storage.current_path.read_bytes()
    failing = EvidenceVerificationService(
        storage=storage,
        event_loader=lambda: (_ for _ in ()).throw(RuntimeError("radar unavailable")),
        now=lambda: NOW.replace(hour=13),
    )

    with pytest.raises(RuntimeError, match="radar unavailable"):
        failing.refresh()

    assert storage.current_path.read_bytes() == before
    assert failing.get_event("a" * 20) is not None


def test_refresh_rejects_empty_upstream_instead_of_erasing_trusted_events(tmp_path):
    storage = EvidenceStorage(root=tmp_path / "evidence", now=lambda: NOW)
    EvidenceVerificationService(
        storage=storage,
        event_loader=lambda: [event("a" * 20, [official_source()])],
        now=lambda: NOW,
    ).refresh()

    empty = EvidenceVerificationService(storage=storage, event_loader=lambda: [], now=lambda: NOW)
    with pytest.raises(RuntimeError, match="no events"):
        empty.refresh()

    assert empty.get_event("a" * 20).verification_status == VerificationStatus.VERIFIED
