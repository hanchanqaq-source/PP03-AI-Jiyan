from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.models import SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.routing import CapabilityRouter


NOW = datetime(2025, 7, 2, 12, tzinfo=timezone.utc)
SECRET = "newsapi-secret-value"


class FakeHttp:
    def __init__(self, responses=()): self.responses, self.calls = list(responses), []
    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params)); value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


class FakeBudget:
    def __init__(self): self.calls = []
    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now))
        raise AssertionError("authorization must not run without a supported credential transport")


def credentials(configured=True):
    store = MemoryCredentialStore({"news-api": ("NEWS_API_KEY",)})
    if configured: store.set("news-api", "NEWS_API_KEY", SECRET)
    return store


def request(**overrides):
    values = {"query": "semiconductor", "from": "2025-07-01", "to": "2025-07-02", "page_size": 10}; values.update(overrides)
    return ProviderRequest("news_discovery", values)


def fixture():
    return {"status": "ok", "totalResults": 1, "articles": [{
        "source": {"id": "reuters", "name": "Reuters"}, "author": "Reporter", "title": "Chip update", "description": "A bounded public excerpt.",
        "url": "https://www.reuters.com/technology/chip-update", "urlToImage": None, "publishedAt": "2025-07-02T10:00:00Z",
        "content": "copyrighted full-ish body must not be stored",
    }]}


def adapter(http=None, *, configured=True, guard=None):
    from data_sources.providers.news_api import NewsApiAdapter
    return NewsApiAdapter(http=http or FakeHttp(), credentials=credentials(configured), budget_guard=guard, fetched_at=lambda: NOW)


def parse(payload, *, cached=False):
    return adapter()._parse(payload, request(), now=NOW, cached=cached)


def test_news_api_no_key_and_query_auth_fail_before_request_budget_transport():
    http, guard = FakeHttp(), FakeBudget()
    missing = adapter(http, configured=False, guard=guard)
    assert missing.probe("news_discovery", parameters={"query": object()})["status"] == "unconfigured"
    with pytest.raises(ProviderUnavailable, match="unconfigured"): missing.fetch(ProviderRequest("news_discovery", {"query": object()}))
    active = adapter(http, guard=guard)
    assert active.probe("news_discovery")["status"] == "unsupported_credential_transport"
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"): active.fetch(request())
    assert guard.calls == [] and http.calls == []


def test_news_api_parser_retains_publisher_but_never_content_source_or_corroboration():
    rows = parse(fixture()); row = rows[0]
    assert row.value == {"title": "Chip update", "summary": "A bounded public excerpt.", "publisher_name": "Reuters", "publisher_url": "https://www.reuters.com/technology/chip-update", "origin_domain": "www.reuters.com", "published_at": "2025-07-02T10:00:00+00:00", "collector": "news_api", "candidate": True, "independent_evidence_eligible": False}
    assert "content" not in row.value and "copyrighted" not in repr(rows)
    assert row.data_status == "candidate" and row.unit == "candidate"
    assert row.source_metadata == {"collector": "news_api", "collector_relation": "discovery_only", "origin_domain": "www.reuters.com", "origin_identity": "www.reuters.com", "publisher_id": "reuters", "delay_seconds": "7200", "reported_total_results": "1", "source_reference": "https://newsapi.org/"}
    assert SECRET not in repr(rows)
    catalog = build_catalog({"sources": []}); descriptor = catalog.adapter("news-api")
    assert catalog.family("news_api").independent_evidence_eligible is False
    assert descriptor.source_roles == (SourceRole.COLLECTOR, SourceRole.CANDIDATE)
    assert SourceRole.NEWS_PUBLISHER not in descriptor.source_roles and SourceRole.OFFICIAL_EVIDENCE not in descriptor.source_roles
    assert "news-api" not in CapabilityRouter(catalog).route("news_discovery").evidence_adapter_ids


@pytest.mark.parametrize("payload,error_type,code", [
    ({"status": "ok", "totalResults": 0, "articles": []}, ProviderUnavailable, "empty_result"),
    ({"status": "error", "code": "apiKeyInvalid", "message": "bad key"}, ProviderUnavailable, "authentication"),
    ({"status": "error", "code": "apiKeyExhausted", "message": "quota"}, ProviderUnavailable, "plan_unavailable"),
    ({"status": "error", "code": "rateLimited", "message": "slow down", "retry_after": 6}, ProviderRateLimited, "rate_limited"),
    ({"status": "ok", "totalResults": 1, "articles": "bad"}, ProviderSchemaChanged, "schema_changed"),
])
def test_news_api_parser_distinguishes_empty_auth_plan_rate_schema(payload, error_type, code):
    with pytest.raises(error_type) as captured: parse(payload)
    assert captured.value.code == code
    if code == "rate_limited": assert captured.value.retry_after_seconds == 6


@pytest.mark.parametrize("payload", [
    {**fixture(), "articles": [{**fixture()["articles"][0], "publishedAt": "2099-01-01T00:00:00Z"}]},
    {**fixture(), "articles": [{**fixture()["articles"][0], "url": "http://www.reuters.com/bad"}]},
    {**fixture(), "articles": [{**fixture()["articles"][0], "url": "https://user:pass@www.reuters.com/bad"}]},
    {**fixture(), "articles": [{**fixture()["articles"][0], "title": "x" * 5000}]},
    {**fixture(), "articles": [{**fixture()["articles"][0], "source": {"id": "x", "name": {"nested": 1}}}]},
    {**fixture(), "articles": fixture()["articles"] * 1001},
    {**fixture(), "totalResults": True},
])
def test_news_api_parser_rejects_future_insecure_credentialed_unbounded_nested_boolean(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"): parse(payload)


def test_news_api_cached_parser_remains_candidate():
    row = parse(fixture(), cached=True)[0]
    assert row.data_status == "cached_candidate" and row.source_metadata["cache_status"] == "fallback"


def test_news_api_request_is_bounded_cannot_supply_url_secret_and_prepared_url_is_clean():
    active = adapter(); params = active._request(request())
    prepared = requests.Request("GET", "https://newsapi.org/v2/everything", params=params).prepare()
    assert prepared.url is not None and SECRET not in prepared.url and "apikey" not in prepared.url.lower()
    for parameters in ({"query": "x", "url": "https://evil.test"}, {"query": "x", "apiKey": "attacker"}, {"query": "x\nsecret"}):
        with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"): active._request(ProviderRequest("news_discovery", parameters))


def test_news_api_catalog_is_stable_default_disabled_collector_only():
    row = build_catalog({"sources": []}).adapter("news-api")
    assert row.source_family_id == "news_api" and row.capability_ids == ("news_discovery",) and row.credential_env_names == ("NEWS_API_KEY",)
    assert row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert "发现" in row.usage_note and "独立证据" in row.usage_note and "未知" in row.cost_policy
