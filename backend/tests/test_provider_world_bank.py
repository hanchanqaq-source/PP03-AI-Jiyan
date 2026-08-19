from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable


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


def world_bank_fixture():
    return [
        {"page": 1, "pages": 1, "per_page": 2, "total": 2, "lastupdated": "2026-08-18"},
        [
            {"indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"}, "country": {"id": "CN", "value": "China"}, "countryiso3code": "CHN", "date": "2025", "value": 18743803170832.0, "unit": "current US$", "obs_status": ""},
            {"indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"}, "country": {"id": "CN", "value": "China"}, "countryiso3code": "CHN", "date": "2024", "value": None, "unit": "current US$", "obs_status": ""},
        ],
    ]


def request():
    return ProviderRequest("macro_indicator", {"country": "CHN", "indicator": "NY.GDP.MKTP.CD", "date": "2024:2025", "frequency": "annual"})


def test_world_bank_preserves_official_keys_dates_units_and_missing_values():
    from data_sources.providers.world_bank import WorldBankAdapter

    http = FakeHttp([world_bank_fixture()])
    rows = WorldBankAdapter(http=http, fetched_at=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc)).fetch(request())

    assert [row.value for row in rows] == [18743803170832.0, None]
    assert [row.as_of_date.isoformat() for row in rows] == ["2025-01-01", "2024-01-01"]
    assert rows[-1].data_status == "missing"
    assert {row.unit for row in rows} == {"current US$"}
    assert {row.frequency for row in rows} == {"annual"}
    assert {row.source_family_id for row in rows} == {"world_bank"}
    assert {row.source_metadata["country"] for row in rows} == {"CHN"}
    assert {row.source_metadata["indicator"] for row in rows} == {"NY.GDP.MKTP.CD"}
    assert {row.source_metadata["source_revision"] for row in rows} == {"2026-08-18"}
    assert http.calls == [("https://api.worldbank.org/v2/country/CHN/indicator/NY.GDP.MKTP.CD", {"Accept": "application/json"}, {"format": "json", "per_page": 1000, "date": "2024:2025"})]


@pytest.mark.parametrize("payload", [[], [{"page": 1}, []], [{"page": 1}, [{"date": "2025"}]]])
def test_world_bank_rejects_empty_or_incomplete_public_payload(payload):
    from data_sources.providers.world_bank import WorldBankAdapter

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        WorldBankAdapter(http=FakeHttp([payload])).fetch(request())


def test_world_bank_propagates_timeout_and_retries_one_bounded_rate_limit():
    from data_sources.providers.world_bank import WorldBankAdapter

    with pytest.raises(ProviderUnavailable, match="timeout"):
        WorldBankAdapter(http=FakeHttp([ProviderUnavailable("timeout")])).fetch(request())

    sleeps = []
    rows = WorldBankAdapter(http=FakeHttp([ProviderRateLimited(retry_after_seconds=999, reference="https://api.worldbank.org/"), world_bank_fixture()]), sleeper=sleeps.append).fetch(request())

    assert len(rows) == 2
    assert sleeps == [60.0]


def test_world_bank_rejects_untrusted_path_values_before_http():
    from data_sources.providers.world_bank import WorldBankAdapter

    http = FakeHttp([])
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        WorldBankAdapter(http=http).fetch(ProviderRequest("macro_indicator", {"country": "CHN/../", "indicator": "NY.GDP.MKTP.CD"}))
    assert http.calls == []
