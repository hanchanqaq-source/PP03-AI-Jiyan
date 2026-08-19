from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderUnavailable


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class FakeSleep:
    def __init__(self, clock: FakeClock) -> None:
        self.calls: list[float] = []
        self.clock = clock

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.value += seconds


class FakeHttp:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, url: str, *, headers=None, params=None):
        del params
        self.calls.append((url, dict(headers or {})))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def submissions_fixture() -> dict[str, object]:
    return {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "filings": {"recent": {"accessionNumber": ["0000320193-24-000123"], "form": ["10-K"]}},
    }


def test_sec_submissions_uses_public_project_contact_and_stays_under_ten_requests_per_second():
    """Catches an SEC request that omits the approved public contact or removes the rate gate."""
    from data_sources.providers.sec_edgar import SecEdgarAdapter

    clock = FakeClock()
    http = FakeHttp([submissions_fixture(), submissions_fixture()])
    sleeper = FakeSleep(clock)
    adapter = SecEdgarAdapter(http=http, clock=clock, sleeper=sleeper)

    first = adapter.fetch(ProviderRequest("company_submissions", {"cik": "0000320193"}))
    second = adapter.fetch(ProviderRequest("company_submissions", {"cik": "320193"}))

    assert first[0].value["canonical_url"] == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert second[0].source_family_id == "sec_edgar"
    assert all("PP03-AI-Jiyan" in headers["User-Agent"] for _url, headers in http.calls)
    assert all("github.com/hanchanqaq-source/PP03-AI-Jiyan" in headers["User-Agent"] for _url, headers in http.calls)
    assert "@" not in http.calls[0][1]["User-Agent"]
    assert sleeper.calls == [0.125]


def test_sec_honours_one_bounded_retry_after_then_returns_probe_success():
    """Catches an unbounded or repeated SEC Retry-After loop."""
    from data_sources.providers.sec_edgar import SecEdgarAdapter

    clock = FakeClock()
    sleeper = FakeSleep(clock)
    http = FakeHttp([
        ProviderRateLimited(retry_after_seconds=2.0, reference="https://data.sec.gov/submissions/CIK0000320193.json"),
        submissions_fixture(),
    ])

    result = SecEdgarAdapter(http=http, clock=clock, sleeper=sleeper).probe("company_submissions")

    assert result == {"status": "success", "connected": True}
    assert sleeper.calls == [2.0]
    assert len(http.calls) == 2


@pytest.mark.parametrize(
    "provider_request",
    [
        ProviderRequest("company_submissions", {"cik": "32/0193"}),
        ProviderRequest("filing_index", {"cik": "0000320193", "accession": "../../secret"}),
        ProviderRequest("filing_index", {"cik": "0000320193", "accession": "0000789019-24-000123"}),
        ProviderRequest("filing_metadata", {"cik": "0000320193", "accession": "0000320193-24-000123", "form": "6-K"}),
    ],
)
def test_sec_rejects_invalid_cik_form_and_accession_before_network(provider_request: ProviderRequest):
    """Catches path traversal or unsupported filing forms reaching the SEC transport."""
    from data_sources.providers.sec_edgar import SecEdgarAdapter

    http = FakeHttp([])

    with pytest.raises(ProviderUnavailable, match="invalid_request_parameter"):
        SecEdgarAdapter(http=http).fetch(provider_request)

    assert http.calls == []


def test_sec_never_fetches_filing_body_and_returns_only_index_metadata():
    """Catches a filing adapter expanding a requested index lookup into body crawling."""
    from data_sources.providers.sec_edgar import SecEdgarAdapter

    index = {"directory": {"name": "/Archives/edgar/data/320193/000032019324000123", "item": [{"name": "form10k.htm"}]}}
    http = FakeHttp([index])

    result = SecEdgarAdapter(http=http).fetch(ProviderRequest(
        "filing_metadata", {"cik": "0000320193", "accession": "0000320193-24-000123", "form": "10-K"}
    ))

    assert result[0].value["form"] == "10-K"
    assert result[0].value["filing_body_fetched"] is False
    assert [url for url, _headers in http.calls] == [
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/index.json"
    ]


def test_sec_marks_contact_unconfigured_when_official_service_rejects_the_public_identifier():
    """Catches a rejected SEC public contact being mislabeled as a connected service."""
    from data_sources.providers.sec_edgar import SecEdgarAdapter

    http = FakeHttp([ProviderUnavailable("authentication", reference="https://data.sec.gov/submissions/CIK0000320193.json")])

    assert SecEdgarAdapter(http=http).probe("company_submissions") == {
        "status": "unconfigured_contact", "connected": False
    }
