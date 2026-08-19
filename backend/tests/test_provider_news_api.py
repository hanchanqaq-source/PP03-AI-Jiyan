from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from data_sources.budgets import BudgetDecision
from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.models import SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.routing import CapabilityRouter


NOW = datetime(2025, 7, 2, 12, tzinfo=timezone.utc)
SECRET = "newsapi-secret-value"


class FakeHttp:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params)); value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


class FakeBudget:
    def __init__(self, decision=None): self.decision, self.calls = decision or BudgetDecision(True, "authorized", None, Decimal("0")), []
    def authorize(self, descriptor, *, estimated_cost, now): self.calls.append((descriptor.adapter_id, estimated_cost, now)); return self.decision


def credentials(configured=True):
    store = MemoryCredentialStore({"news-api": ("NEWS_API_KEY",)})
    if configured: store.set("news-api", "NEWS_API_KEY", SECRET)
    return store


def entitlement(**overrides):
    from data_sources.providers.news_api import NewsApiEntitlement
    values = dict(capability_id="news_discovery", plan_name="fixture-developer", available=True, estimated_cost=Decimal("0"), quota_remaining=30)
    values.update(overrides); return NewsApiEntitlement(**values)


def request(**overrides):
    values = {"query": "semiconductor", "from": "2025-07-01", "to": "2025-07-02", "page_size": 10}
    values.update(overrides); return ProviderRequest("news_discovery", values)


def fixture():
    return {"status": "ok", "totalResults": 1, "articles": [{
        "source": {"id": "reuters", "name": "Reuters"}, "author": "Reporter", "title": "Chip update",
        "description": "A bounded public excerpt.", "url": "https://www.reuters.com/technology/chip-update",
        "urlToImage": None, "publishedAt": "2025-07-02T10:00:00Z", "content": "copyrighted full-ish body must not be stored",
    }]}


def adapter(http, *, configured=True, guard=None, entitlements=(), cache_getter=None):
    from data_sources.providers.news_api import NewsApiAdapter
    return NewsApiAdapter(http=http, credentials=credentials(configured), budget_guard=guard, entitlements=entitlements, cache_getter=cache_getter, fetched_at=lambda: NOW)


def test_news_api_no_key_guard_entitlement_plan_and_quota_fail_before_request_or_transport():
    http, guard = FakeHttp([]), FakeBudget()
    assert adapter(http, configured=False, guard=guard).probe("news_discovery", parameters={"query": object()})["status"] == "unconfigured"
    assert guard.calls == [] and http.calls == []
    assert adapter(http, entitlements=(entitlement(),)).probe("news_discovery")["status"] == "budget_guard_unavailable"
    assert adapter(http, guard=guard).probe("news_discovery")["status"] == "cost_unknown"
    assert adapter(http, guard=guard, entitlements=(entitlement(available=False),)).probe("news_discovery")["status"] == "plan_unavailable"
    assert adapter(http, guard=guard, entitlements=(entitlement(quota_remaining=0),)).probe("news_discovery")["status"] == "quota_exhausted"
    assert http.calls == []


def test_news_api_retains_publisher_identity_but_never_becomes_content_source_or_corroboration():
    http = FakeHttp([fixture()])
    rows = adapter(http, guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())
    row = rows[0]
    assert row.value == {
        "title": "Chip update", "summary": "A bounded public excerpt.", "publisher_name": "Reuters",
        "publisher_url": "https://www.reuters.com/technology/chip-update", "origin_domain": "www.reuters.com",
        "published_at": "2025-07-02T10:00:00+00:00", "collector": "news_api", "candidate": True,
        "independent_evidence_eligible": False,
    }
    assert "content" not in row.value and "copyrighted" not in repr(rows)
    assert row.data_status == "candidate" and row.unit == "candidate"
    assert row.source_metadata == {
        "collector": "news_api", "collector_relation": "discovery_only", "origin_domain": "www.reuters.com",
        "origin_identity": "www.reuters.com", "publisher_id": "reuters", "delay_seconds": "7200",
        "plan_name": "fixture-developer", "quota_remaining": "30", "source_reference": "https://newsapi.org/",
    }
    assert SECRET not in repr(rows)

    catalog = build_catalog({"sources": []})
    family, descriptor = catalog.family("news_api"), catalog.adapter("news-api")
    assert family.independent_evidence_eligible is False
    assert descriptor.source_roles == (SourceRole.COLLECTOR, SourceRole.CANDIDATE)
    assert SourceRole.NEWS_PUBLISHER not in descriptor.source_roles and SourceRole.OFFICIAL_EVIDENCE not in descriptor.source_roles
    assert "news-api" not in CapabilityRouter(catalog).route("news_discovery").evidence_adapter_ids


@pytest.mark.parametrize(
    "payload,error_type,code",
    [
        ({"status": "ok", "totalResults": 0, "articles": []}, ProviderUnavailable, "empty_result"),
        ({"status": "error", "code": "apiKeyInvalid", "message": "bad key"}, ProviderUnavailable, "authentication"),
        ({"status": "error", "code": "apiKeyExhausted", "message": "quota"}, ProviderUnavailable, "plan_unavailable"),
        ({"status": "error", "code": "rateLimited", "message": "slow down", "retry_after": 6}, ProviderRateLimited, "rate_limited"),
        ({"status": "ok", "totalResults": 1, "articles": "bad"}, ProviderSchemaChanged, "schema_changed"),
    ],
)
def test_news_api_distinguishes_empty_auth_plan_rate_and_schema(payload, error_type, code):
    with pytest.raises(error_type) as captured:
        adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())
    assert captured.value.code == code
    if code == "rate_limited": assert captured.value.retry_after_seconds == 6


def test_news_api_transient_cache_remains_candidate_and_does_not_hide_auth():
    rows = adapter(FakeHttp([ProviderUnavailable("timeout", reference="https://newsapi.org/")]), guard=FakeBudget(), entitlements=(entitlement(),), cache_getter=lambda _: fixture()).fetch(request())
    assert rows[0].data_status == "cached_candidate" and rows[0].source_metadata["cache_status"] == "fallback"
    with pytest.raises(ProviderUnavailable, match="authentication"):
        adapter(FakeHttp([ProviderUnavailable("authentication", reference="https://newsapi.org/")]), guard=FakeBudget(), entitlements=(entitlement(),), cache_getter=lambda _: fixture()).fetch(request())


@pytest.mark.parametrize(
    "payload",
    [
        {**fixture(), "articles": [{**fixture()["articles"][0], "publishedAt": "2099-01-01T00:00:00Z"}]},
        {**fixture(), "articles": [{**fixture()["articles"][0], "url": "http://www.reuters.com/bad"}]},
        {**fixture(), "articles": [{**fixture()["articles"][0], "url": "https://user:pass@www.reuters.com/bad"}]},
        {**fixture(), "articles": [{**fixture()["articles"][0], "title": "x" * 5000}]},
        {**fixture(), "articles": [{**fixture()["articles"][0], "source": {"id": "x", "name": {"nested": 1}}}]},
        {**fixture(), "articles": fixture()["articles"] * 1001},
        {**fixture(), "totalResults": True},
    ],
)
def test_news_api_rejects_future_insecure_credentialed_unbounded_nested_and_boolean_payload(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(request())


def test_news_api_request_is_bounded_and_cannot_supply_url_or_secret_parameter():
    http = FakeHttp([])
    for parameters in ({"query": "x", "url": "https://evil.test"}, {"query": "x", "apiKey": "attacker"}, {"query": "x\nsecret"}):
        with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
            adapter(http, guard=FakeBudget(), entitlements=(entitlement(),)).fetch(ProviderRequest("news_discovery", parameters))
    assert http.calls == []


def test_news_api_catalog_is_stable_default_disabled_collector_only():
    row = build_catalog({"sources": []}).adapter("news-api")
    assert row.source_family_id == "news_api" and row.capability_ids == ("news_discovery",)
    assert row.credential_env_names == ("NEWS_API_KEY",)
    assert row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert "发现" in row.usage_note and "独立证据" in row.usage_note and "未知" in row.cost_policy


def test_news_api_copies_trusted_entitlement_before_caller_mutation():
    trusted = entitlement()
    active = adapter(FakeHttp([fixture()]), guard=FakeBudget(), entitlements=(trusted,))
    object.__setattr__(trusted, "plan_name", "mutated-plan")
    assert active.fetch(request())[0].source_metadata["plan_name"] == "fixture-developer"
