from __future__ import annotations

from datetime import datetime, timezone

from news_intelligence.models import NewsSourceItem

from evidence_verification.source_identity import canonicalize_public_url, identify_evidence


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def source(*, name: str, feed: str, article: str, title: str = "公司公告建设算力中心") -> NewsSourceItem:
    return NewsSourceItem(
        source_name=name,
        source_url=feed,
        original_url=article,
        published_at=NOW,
        fetched_at=NOW,
        title=title,
        summary="公司公告建设算力中心。",
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
        source_domain="disclosure.example",
        data_status="realtime",
    )


def test_same_content_publisher_uses_one_origin_cluster_but_keeps_collectors_distinct():
    left = identify_evidence(source(
        name="采集渠道 A",
        feed="https://collector-a.example/feed",
        article="https://disclosure.example/notice/1?utm_source=a",
    ))
    right = identify_evidence(source(
        name="采集渠道 B",
        feed="https://collector-b.example/feed",
        article="https://disclosure.example/notice/2?utm_source=b",
    ))

    assert left.collector_source != right.collector_source
    assert left.content_source == right.content_source == "disclosure.example"
    assert left.origin_cluster == right.origin_cluster
    assert left.canonical_url == "https://disclosure.example/notice/1"


def test_explicit_existing_official_domain_is_primary_evidence():
    evidence = identify_evidence(source(
        name="SEC",
        feed="https://www.sec.gov/news/pressreleases.rss",
        article="https://www.sec.gov/Archives/edgar/data/1/report.htm",
    ))

    assert evidence.is_official is True
    assert evidence.source_role.value == "primary"
    assert evidence.content_source == "sec.gov"


def test_private_local_credentialed_and_secret_query_urls_fail_closed():
    assert canonicalize_public_url("http://127.0.0.1/report") is None
    assert canonicalize_public_url("http://[::1]/report") is None
    assert canonicalize_public_url("https://localhost/report") is None
    assert canonicalize_public_url("https://user:secret@example.com/report") is None
    assert canonicalize_public_url("https://example.com/report?api_key=secret") is None
    assert canonicalize_public_url("file:///tmp/report") is None


def test_canonical_url_removes_fragment_and_tracking_without_dropping_public_identity():
    assert canonicalize_public_url(
        "HTTPS://Example.COM:443/a/../report?utm_medium=rss&id=7#section"
    ) == "https://example.com/report?id=7"
