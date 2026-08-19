from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_bytes(self, url, *, headers=None, params=None):
        self.calls.append((url, headers, params))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def oecd_csv():
    return b'DATAFLOW,REF_AREA,SUBJECT,FREQ,TIME_PERIOD,OBS_VALUE,UNIT_MEASURE,LAST_UPDATE,OBS_STATUS\n"OECD.SDD.STES,DSD_STES@DF_FINMARK,4.1",USA,IR3TIB01,M,2025-01,3.50,PC,2026-08-18T00:00:00Z,A\n"OECD.SDD.STES,DSD_STES@DF_FINMARK,4.1",USA,IR3TIB01,M,2024-12,,PC,2026-08-18T00:00:00Z,M\n'


def request():
    return ProviderRequest("macro_series", {"dataset": "OECD.SDD.STES,DSD_STES@DF_FINMARK,4.1", "series_key": "M.USA.IR3TIB01", "start_period": "2024-12", "end_period": "2025-01"})


def test_oecd_preserves_dataset_series_unit_frequency_revision_and_missing_values():
    from data_sources.providers.oecd import OecdAdapter

    http = FakeHttp([oecd_csv()])
    rows = OecdAdapter(http=http, fetched_at=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc)).fetch(request())

    assert [row.value for row in rows] == [3.5, None]
    assert [row.as_of_date.isoformat() for row in rows] == ["2025-01-01", "2024-12-01"]
    assert rows[-1].data_status == "missing"
    assert {row.unit for row in rows} == {"PC"}
    assert {row.frequency for row in rows} == {"monthly"}
    assert {row.source_family_id for row in rows} == {"oecd"}
    assert {row.source_metadata["dataset"] for row in rows} == {"OECD.SDD.STES,DSD_STES@DF_FINMARK,4.1"}
    assert {row.source_metadata["series_key"] for row in rows} == {"M.USA.IR3TIB01"}
    assert {row.source_metadata["source_revision"] for row in rows} == {"2026-08-18T00:00:00Z"}
    assert http.calls == [("https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES%40DF_FINMARK,4.1/M.USA.IR3TIB01", {"Accept": "text/csv"}, {"format": "csvfile", "startPeriod": "2024-12", "endPeriod": "2025-01"})]


@pytest.mark.parametrize("payload", [b"", b"TIME_PERIOD,OBS_VALUE\n", b"TIME_PERIOD,OBS_VALUE\n2025-01,3.5\n"])
def test_oecd_rejects_empty_or_incomplete_csv_payload(payload):
    from data_sources.providers.oecd import OecdAdapter

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        OecdAdapter(http=FakeHttp([payload])).fetch(request())


def test_oecd_propagates_timeout_and_retries_one_bounded_rate_limit():
    from data_sources.providers.oecd import OecdAdapter

    with pytest.raises(ProviderUnavailable, match="timeout"):
        OecdAdapter(http=FakeHttp([ProviderUnavailable("timeout")])).fetch(request())

    sleeps = []
    rows = OecdAdapter(http=FakeHttp([ProviderRateLimited(retry_after_seconds=3.0), oecd_csv()]), sleeper=sleeps.append).fetch(request())
    assert len(rows) == 2
    assert sleeps == [3.0]


def test_oecd_rejects_path_injection_before_http():
    from data_sources.providers.oecd import OecdAdapter

    http = FakeHttp([])
    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        OecdAdapter(http=http).fetch(ProviderRequest("macro_series", {"dataset": "OECD/../", "series_key": "M.USA"}))
    assert http.calls == []


@pytest.mark.parametrize("raw_value", ["NaN", "Infinity", "-Infinity", "True"])
def test_oecd_rejects_non_finite_or_boolean_observation_text(raw_value):
    """Catches CSV numeric coercion accepting non-finite values as reported data."""
    from data_sources.providers.oecd import OecdAdapter

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        OecdAdapter(http=FakeHttp([oecd_csv().replace(b"3.50", raw_value.encode())])).fetch(request())


@pytest.mark.parametrize("raw_value", [str(10**400), str(-(10**400))])
def test_oecd_preserves_arbitrarily_large_integer_observation_text(raw_value):
    """Catches CSV coercion turning exact public integer text into infinity."""
    from data_sources.providers.oecd import OecdAdapter

    row = OecdAdapter(http=FakeHttp([oecd_csv().replace(b"3.50", raw_value.encode())])).fetch(request())[0]

    assert row.value == int(raw_value)
    assert type(row.value) is int


def test_oecd_returns_an_explicit_empty_result_for_a_valid_header_only_csv():
    from data_sources.providers.oecd import OecdAdapter

    header = oecd_csv().splitlines()[0] + b"\n"
    with pytest.raises(ProviderUnavailable, match="empty_result"):
        OecdAdapter(http=FakeHttp([header])).fetch(request())
