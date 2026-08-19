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
SECRET = "finnhub-secret-value"


class FakeHttp:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


class FakeBudget:
    def __init__(self, decision=None): self.decision, self.calls = decision or BudgetDecision(True, "authorized", None, Decimal("0")), []
    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now)); return self.decision


def credentials(configured=True):
    store = MemoryCredentialStore({"finnhub": ("FINNHUB_API_KEY",)})
    if configured: store.set("finnhub", "FINNHUB_API_KEY", SECRET)
    return store


def entitlement(capability="news_discovery", **overrides):
    from data_sources.providers.finnhub import FinnhubEntitlement
    values = dict(capability_id=capability, plan_name="fixture-plan", available=True, estimated_cost=Decimal("0"), quota_remaining=9)
    values.update(overrides)
    return FinnhubEntitlement(**values)


def adapter(http, *, configured=True, guard=None, entitlements=(), cache_getter=None):
    from data_sources.providers.finnhub import FinnhubAdapter
    return FinnhubAdapter(http=http, credentials=credentials(configured), budget_guard=guard, entitlements=entitlements, cache_getter=cache_getter, fetched_at=lambda: NOW)


def news_request():
    return ProviderRequest("news_discovery", {"symbol": "AAPL", "from": "2025-07-01", "to": "2025-07-02"})


def news_fixture():
    return [{
        "category": "company", "datetime": 1751378400, "headline": "Issuer update",
        "id": 123, "image": "", "related": "AAPL", "source": "Reuters",
        "summary": "A bounded public summary.", "url": "https://www.reuters.com/markets/example",
    }]


def test_finnhub_no_key_guard_and_entitlement_fail_closed_before_request_or_transport():
    http, guard = FakeHttp([]), FakeBudget()
    assert adapter(http, configured=False, guard=guard).probe("news_discovery", parameters={"symbol": object()})["status"] == "unconfigured"
    assert guard.calls == [] and http.calls == []
    assert adapter(http, entitlements=(entitlement(),)).probe("news_discovery")["status"] == "budget_guard_unavailable"
    assert adapter(http, guard=guard).probe("news_discovery")["status"] == "cost_unknown"
    assert http.calls == []


def test_finnhub_news_preserves_original_publisher_and_remains_collector_candidate_only():
    http = FakeHttp([news_fixture()])
    rows = adapter(http, guard=FakeBudget(), entitlements=(entitlement(),)).fetch(news_request())
    row = rows[0]
    assert row.value == {
        "title": "Issuer update", "summary": "A bounded public summary.",
        "publisher_name": "Reuters", "publisher_url": "https://www.reuters.com/markets/example",
        "origin_domain": "www.reuters.com", "published_at": "2025-07-01T14:00:00+00:00",
        "category": "company", "collector": "finnhub", "candidate": True,
        "independent_evidence_eligible": False,
    }
    assert row.data_status == "candidate"
    assert row.source_metadata == {
        "collector": "finnhub", "collector_relation": "discovery_only", "origin_domain": "www.reuters.com",
        "origin_identity": "www.reuters.com", "plan_name": "fixture-plan", "quota_remaining": "9",
        "source_reference": "https://finnhub.io/", "delay_seconds": "79200",
    }
    assert SECRET not in repr(rows)
    catalog = build_catalog({"sources": []})
    family = catalog.family("finnhub")
    descriptor = catalog.adapter("finnhub")
    assert family.independent_evidence_eligible is False
    assert descriptor.source_roles == (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.COLLECTOR, SourceRole.CANDIDATE)
    route = CapabilityRouter(catalog).route("news_discovery")
    assert "finnhub" not in route.evidence_adapter_ids


def test_finnhub_market_quote_preserves_symbol_provider_timestamp_and_unknown_unit():
    request = ProviderRequest("stock_snapshot", {"symbol": "AAPL"})
    payload = {"c": 210.5, "d": 1.5, "dp": 0.72, "h": 212, "l": 207, "o": 208, "pc": 209, "t": 1751378400}
    rows = adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement("stock_snapshot"),)).fetch(request)
    row = rows[0]
    assert row.value["current"] == Decimal("210.5")
    assert row.as_of_date.isoformat() == "2025-07-01"
    assert row.unit == "unknown" and row.frequency == "intraday"
    assert row.source_metadata["symbol"] == "AAPL" and row.source_metadata["publisher_role"] == "market_provider"


@pytest.mark.parametrize(
    "response,error_type,code",
    [
        ([], ProviderUnavailable, "empty_result"),
        ({"error": "Invalid API key"}, ProviderUnavailable, "authentication"),
        ({"error": "You don't have access to this resource"}, ProviderUnavailable, "plan_unavailable"),
        ({"bad": "shape"}, ProviderSchemaChanged, "schema_changed"),
        (ProviderRateLimited(retry_after_seconds=7, reference="https://finnhub.io/"), ProviderRateLimited, "rate_limited"),
    ],
)
def test_finnhub_distinguishes_empty_auth_plan_schema_and_retry_after(response, error_type, code):
    with pytest.raises(error_type) as captured:
        adapter(FakeHttp([response]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(news_request())
    assert captured.value.code == code
    if code == "rate_limited": assert captured.value.retry_after_seconds == 7
    assert SECRET not in str(captured.value)


def test_finnhub_cached_news_stays_candidate_and_explicitly_cached():
    failure = ProviderUnavailable("timeout", reference="https://finnhub.io/")
    rows = adapter(FakeHttp([failure]), guard=FakeBudget(), entitlements=(entitlement(),), cache_getter=lambda _: news_fixture()).fetch(news_request())
    assert rows[0].data_status == "cached_candidate"
    assert rows[0].source_metadata["cache_status"] == "fallback"
    assert rows[0].value["independent_evidence_eligible"] is False


@pytest.mark.parametrize(
    "payload",
    [
        [{**news_fixture()[0], "datetime": 4102444800}],
        [{**news_fixture()[0], "url": "http://www.reuters.com/markets/example"}],
        [{**news_fixture()[0], "url": "https://user:pass@www.reuters.com/example"}],
        [{**news_fixture()[0], "headline": "x" * 5000}],
        [{**news_fixture()[0], "summary": {"nested": 1}}],
        [news_fixture()[0]] * 1001,
    ],
)
def test_finnhub_rejects_future_insecure_credentialed_unbounded_and_nested_news(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        adapter(FakeHttp([payload]), guard=FakeBudget(), entitlements=(entitlement(),)).fetch(news_request())


def test_finnhub_catalog_is_default_disabled_with_stable_capabilities_and_secret_scope():
    row = build_catalog({"sources": []}).adapter("finnhub")
    assert row.capability_ids == ("stock_snapshot", "news_discovery")
    assert row.credential_env_names == ("FINNHUB_API_KEY",)
    assert row.default_enabled is False and row.catalog_status.value == "unconfigured"
    assert row.billing_model.value == "freemium" and "未知" in row.cost_policy


def test_finnhub_copies_trusted_entitlement_before_caller_mutation():
    trusted = entitlement()
    active = adapter(FakeHttp([news_fixture()]), guard=FakeBudget(), entitlements=(trusted,))
    object.__setattr__(trusted, "quota_remaining", 0)
    assert len(active.fetch(news_request())) == 1
