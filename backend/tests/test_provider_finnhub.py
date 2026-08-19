from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import requests

from data_sources.catalog import build_catalog
from data_sources.credentials import MemoryCredentialStore
from data_sources.models import SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.routing import CapabilityRouter


NOW = datetime(2025, 7, 2, 12, tzinfo=timezone.utc)
SECRET = "finnhub-secret-value"


class FakeHttp:
    def __init__(self, responses=()): self.responses, self.calls = list(responses), []
    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


class FakeBudget:
    def __init__(self): self.calls = []
    def authorize(self, descriptor, *, estimated_cost, now):
        self.calls.append((descriptor.adapter_id, estimated_cost, now))
        raise AssertionError("authorization must not run without a supported credential transport")


def credentials(configured=True):
    store = MemoryCredentialStore({"finnhub": ("FINNHUB_API_KEY",)})
    if configured: store.set("finnhub", "FINNHUB_API_KEY", SECRET)
    return store


def adapter(http=None, *, configured=True, guard=None):
    from data_sources.providers.finnhub import FinnhubAdapter
    return FinnhubAdapter(http=http or FakeHttp(), credentials=credentials(configured), budget_guard=guard, fetched_at=lambda: NOW)


def news_request(): return ProviderRequest("news_discovery", {"symbol": "AAPL", "from": "2025-07-01", "to": "2025-07-02"})


def news_fixture():
    return [{"category": "company", "datetime": 1751378400, "headline": "Issuer update", "id": 123, "image": "", "related": "AAPL", "source": "Reuters", "summary": "A bounded public summary.", "url": "https://www.reuters.com/markets/example"}]


def test_finnhub_no_key_and_query_auth_short_circuit_before_request_budget_transport():
    http, guard = FakeHttp(), FakeBudget()
    missing = adapter(http, configured=False, guard=guard)
    assert missing.probe("news_discovery", parameters={"symbol": object()})["status"] == "unconfigured"
    with pytest.raises(ProviderUnavailable, match="unconfigured"): missing.fetch(news_request())
    active = adapter(http, guard=guard)
    assert active.probe("news_discovery")["status"] == "unsupported_credential_transport"
    with pytest.raises(ProviderUnavailable, match="unsupported_credential_transport"): active.fetch(news_request())
    assert guard.calls == [] and http.calls == []


def test_finnhub_news_parser_preserves_publisher_and_remains_collector_candidate_only():
    active = adapter()
    rows = active._parse_news(news_fixture(), news_request(), now=NOW, cached=False)
    row = rows[0]
    assert row.value == {"title": "Issuer update", "summary": "A bounded public summary.", "publisher_name": "Reuters", "publisher_url": "https://www.reuters.com/markets/example", "origin_domain": "www.reuters.com", "published_at": "2025-07-01T14:00:00+00:00", "category": "company", "collector": "finnhub", "candidate": True, "independent_evidence_eligible": False}
    assert row.data_status == "candidate"
    assert row.source_metadata["collector_relation"] == "discovery_only" and row.source_metadata["origin_identity"] == "www.reuters.com"
    assert SECRET not in repr(rows)
    catalog = build_catalog({"sources": []})
    assert catalog.family("finnhub").independent_evidence_eligible is False
    assert catalog.adapter("finnhub").source_roles == (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.COLLECTOR, SourceRole.CANDIDATE)
    assert "finnhub" not in CapabilityRouter(
        catalog, configuration={"free_only": True, "adapters": {}},
    ).route("news_discovery").evidence_adapter_ids


def test_finnhub_quote_parser_preserves_symbol_timestamp_and_unknown_unit():
    req = ProviderRequest("stock_snapshot", {"symbol": "AAPL"})
    payload = {"c": 210.5, "d": 1.5, "dp": 0.72, "h": 212, "l": 207, "o": 208, "pc": 209, "t": 1751378400}
    row = adapter()._parse_quote(payload, req, now=NOW, cached=False)[0]
    assert row.value["current"] == Decimal("210.5") and row.as_of_date.isoformat() == "2025-07-01"
    assert row.unit == "unknown" and row.frequency == "intraday" and row.source_metadata["publisher_role"] == "market_provider"


@pytest.mark.parametrize("response,error_type,code", [
    ([], ProviderUnavailable, "empty_result"),
    ({"error": "Invalid API key"}, ProviderUnavailable, "authentication"),
    ({"error": "You don't have access to this resource"}, ProviderUnavailable, "plan_unavailable"),
    ({"bad": "shape"}, ProviderSchemaChanged, "schema_changed"),
])
def test_finnhub_news_parser_distinguishes_empty_auth_plan_and_schema(response, error_type, code):
    with pytest.raises(error_type) as captured:
        adapter()._parse_news(response, news_request(), now=NOW, cached=False)
    assert captured.value.code == code and SECRET not in str(captured.value)


@pytest.mark.parametrize("payload", [
    [{**news_fixture()[0], "datetime": 4102444800}],
    [{**news_fixture()[0], "url": "http://www.reuters.com/markets/example"}],
    [{**news_fixture()[0], "url": "https://user:pass@www.reuters.com/example"}],
    [{**news_fixture()[0], "headline": "x" * 5000}],
    [{**news_fixture()[0], "summary": {"nested": 1}}],
    [news_fixture()[0]] * 1001,
])
def test_finnhub_news_parser_rejects_future_insecure_credentialed_unbounded_nested(payload):
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        adapter()._parse_news(payload, news_request(), now=NOW, cached=False)


def test_finnhub_cached_news_stays_candidate_and_explicitly_cached():
    row = adapter()._parse_news(news_fixture(), news_request(), now=NOW, cached=True)[0]
    assert row.data_status == "cached_candidate" and row.source_metadata["cache_status"] == "fallback"
    assert row.value["independent_evidence_eligible"] is False


def test_finnhub_public_request_shape_is_secret_free_and_exact():
    active = adapter()
    endpoint, params = active._request(news_request())
    prepared = requests.Request("GET", endpoint, params=params).prepare()
    assert prepared.url is not None and SECRET not in prepared.url and "token" not in prepared.url.lower()
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        active._request(ProviderRequest("news_discovery", {"symbol": object()}))


def test_finnhub_catalog_is_default_disabled_stable_and_secret_scoped():
    row = build_catalog({"sources": []}).adapter("finnhub")
    assert row.capability_ids == ("stock_snapshot", "news_discovery") and row.credential_env_names == ("FINNHUB_API_KEY",)
    assert row.default_enabled is False and row.catalog_status.value == "unconfigured" and row.billing_model.value == "freemium"
    assert "未知" in row.cost_policy
