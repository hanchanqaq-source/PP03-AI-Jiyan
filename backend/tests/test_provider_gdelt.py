from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable
from data_sources.catalog import build_catalog
from data_sources.models import SourceRole


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def gdelt_fixture():
    return {"articles": [{"url": "https://publisher.example/news/123", "title": "Public report", "seendate": "20260818T120000Z", "domain": "publisher.example", "language": "English", "sourcecountry": "United States"}]}


def request():
    return ProviderRequest("news_discovery", {"query": "inflation report", "timespan": "1week", "max_records": 25})


def test_gdelt_returns_only_collector_candidates_with_original_publisher_identity():
    from data_sources.providers.gdelt import GdeltAdapter

    http = FakeHttp([gdelt_fixture()])
    rows = GdeltAdapter(http=http, fetched_at=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc)).fetch(request())

    assert rows[0].value == {"publisher_url": "https://publisher.example/news/123", "origin_domain": "publisher.example", "title": "Public report", "language": "English", "source_country": "United States", "collector": "gdelt", "candidate": True, "independent_evidence_eligible": False}
    assert rows[0].source_family_id == "gdelt"
    assert rows[0].data_status == "candidate"
    assert rows[0].frequency == "event_driven"
    assert http.calls == [("https://api.gdeltproject.org/api/v2/doc/doc", {"Accept": "application/json"}, {"query": "inflation report", "mode": "artlist", "format": "json", "maxrecords": 25, "timespan": "1week"})]


@pytest.mark.parametrize("payload", [{}, {"articles": []}, {"articles": [{"url": "https://publisher.example/only-url"}]}])
def test_gdelt_rejects_empty_or_incomplete_candidate_payload(payload):
    from data_sources.providers.gdelt import GdeltAdapter

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        GdeltAdapter(http=FakeHttp([payload])).fetch(request())


def test_gdelt_propagates_timeout_and_retries_one_bounded_rate_limit():
    from data_sources.providers.gdelt import GdeltAdapter

    with pytest.raises(ProviderUnavailable, match="timeout"):
        GdeltAdapter(http=FakeHttp([ProviderUnavailable("timeout")])).fetch(request())
    sleeps = []
    assert len(GdeltAdapter(http=FakeHttp([ProviderRateLimited(retry_after_seconds=999), gdelt_fixture()]), sleeper=sleeps.append).fetch(request())) == 1
    assert sleeps == [60.0]


def test_gdelt_rejects_unsafe_query_and_non_https_publisher_url():
    from data_sources.providers.gdelt import GdeltAdapter

    http = FakeHttp([])
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        GdeltAdapter(http=http).fetch(ProviderRequest("news_discovery", {"query": "x", "timespan": "1week", "max_records": True}))
    assert http.calls == []
    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        GdeltAdapter(http=FakeHttp([{ "articles": [{"url": "http://publisher.example/no-tls", "title": "Unsafe", "seendate": "20260818T120000Z", "domain": "publisher.example", "language": "English", "sourcecountry": "US"}]}])).fetch(request())


def test_catalog_registers_macro_sources_and_candidate_only_gdelt_without_connected_claims():
    """Catches macro/discovery adapters being omitted or a fixture being labeled connected."""
    catalog = build_catalog(news_config={"sources": []})

    assert catalog.adapter("world-bank").catalog_status.value == "configured"
    assert catalog.adapter("oecd").billing_model.value == "free_no_key"
    assert catalog.adapter("imf").catalog_status.value == "catalog_only"
    assert catalog.adapter("imf").default_enabled is False
    assert catalog.family("gdelt").source_roles == (SourceRole.COLLECTOR, SourceRole.CANDIDATE)
    assert catalog.family("gdelt").independent_evidence_eligible is False
    assert catalog.adapter("gdelt").capability_ids == ("news_discovery",)
    assert all(family.catalog_status.value != "connected" for family in catalog.families)
