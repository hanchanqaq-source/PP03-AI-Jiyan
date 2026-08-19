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


def imf_fixture():
    return {
        "header": {"prepared": "2026-08-18T12:00:00Z"},
        "dataSets": [{"series": {"0:0": {"observations": {"0": [3.2], "1": [None]}}}}],
        "structure": {
            "dimensions": {
                "series": [{"id": "REF_AREA", "values": [{"id": "USA"}]}, {"id": "INDICATOR", "values": [{"id": "NGDP_RPCH"}]}],
                "observation": [{"id": "TIME_PERIOD", "values": [{"id": "2025"}, {"id": "2024"}]}],
            },
            "attributes": {"series": [{"id": "UNIT_MEASURE", "values": [{"id": "PC"}]}], "observation": [{"id": "FREQ", "values": [{"id": "A"}]}]},
        },
    }


def request():
    return ProviderRequest("macro_series", {"dataset": "IFS", "series_key": "USA.NGDP_RPCH", "start_period": "2024", "end_period": "2025"})


def test_imf_sdmx_preserves_series_unit_frequency_revision_and_missing_values():
    from data_sources.providers.imf import ImfAdapter

    http = FakeHttp([imf_fixture()])
    rows = ImfAdapter(http=http, fetched_at=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc)).fetch(request())

    assert [row.value for row in rows] == [3.2, None]
    assert [row.as_of_date.isoformat() for row in rows] == ["2025-01-01", "2024-01-01"]
    assert rows[-1].data_status == "missing"
    assert {row.unit for row in rows} == {"PC"}
    assert {row.frequency for row in rows} == {"annual"}
    assert {row.source_family_id for row in rows} == {"imf"}
    assert {row.source_metadata["dataset"] for row in rows} == {"IFS"}
    assert {row.source_metadata["series_key"] for row in rows} == {"USA.NGDP_RPCH"}
    assert {row.source_metadata["source_revision"] for row in rows} == {"2026-08-18T12:00:00Z"}
    assert http.calls == [("https://sdmxcentral.imf.org/ws/public/sdmxapi/rest/data/IFS/USA.NGDP_RPCH", {"Accept": "application/vnd.sdmx.data+json"}, {"format": "sdmx-json", "startPeriod": "2024", "endPeriod": "2025"})]


@pytest.mark.parametrize("payload", [{}, {"dataSets": []}, {"dataSets": [{"series": {}}], "structure": {}}])
def test_imf_rejects_empty_or_incomplete_sdmx_payload(payload):
    from data_sources.providers.imf import ImfAdapter

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        ImfAdapter(http=FakeHttp([payload])).fetch(request())


def test_imf_catalog_only_descriptor_and_transport_failure_paths_are_honest():
    from data_sources.providers.imf import ImfAdapter

    adapter = ImfAdapter(http=FakeHttp([ProviderUnavailable("authentication")]))
    assert adapter.descriptor.catalog_status.value == "catalog_only"
    assert adapter.probe("macro_series") == {"status": "catalog_only", "connected": False}
    with pytest.raises(ProviderUnavailable, match="authentication"):
        adapter.fetch(request())
    with pytest.raises(ProviderUnavailable, match="timeout"):
        ImfAdapter(http=FakeHttp([ProviderUnavailable("timeout")])).fetch(request())


def test_imf_retries_one_bounded_rate_limit_and_rejects_path_injection():
    from data_sources.providers.imf import ImfAdapter

    sleeps = []
    assert len(ImfAdapter(http=FakeHttp([ProviderRateLimited(retry_after_seconds=999), imf_fixture()]), sleeper=sleeps.append).fetch(request())) == 2
    assert sleeps == [60.0]
    http = FakeHttp([])
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        ImfAdapter(http=http).fetch(ProviderRequest("macro_series", {"dataset": "IFS/../", "series_key": "USA.NGDP_RPCH"}))
    assert http.calls == []
