from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from data_sources.http import SafeHttpClient
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable


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


class MountedResponse:
    def __init__(self, *, status_code: int = 200, headers=None, content: bytes = b"{}") -> None:
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(headers or {})
        self._content = content

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self._content

    def close(self) -> None:
        pass


class MountedAdapter(BaseAdapter):
    def __init__(self, responses: list[MountedResponse]) -> None:
        self.responses = responses
        self.requests: list[object] = []

    def send(self, request, **kwargs):
        del kwargs
        self.requests.append(request)
        response = self.responses.pop(0)
        response.request = request
        response.url = request.url
        return response

    def close(self) -> None:
        pass


def submissions_fixture(form: str = "10-K") -> dict[str, object]:
    return {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "filings": {"recent": {"accessionNumber": ["0000320193-24-000123"], "form": [form]}},
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
    assert all(headers == {"Accept": "application/json"} for _url, headers in http.calls)
    assert sleeper.calls == [0.125]


def test_sec_owns_its_real_safehttp_user_agent_without_caller_header_override():
    """Catches SafeHttp discarding the required SEC contact or accepting an adapter-supplied override."""
    from data_sources.providers.sec_edgar import SecEdgarAdapter

    session = requests.Session()
    transport = MountedAdapter([MountedResponse(content=json.dumps(submissions_fixture()).encode("utf-8"))])
    session.mount("https://", transport)
    adapter = SecEdgarAdapter(http=SafeHttpClient(session=session))

    adapter.fetch(ProviderRequest("company_submissions", {"cik": "0000320193"}))

    headers = {key.lower(): value for key, value in transport.requests[0].headers.items()}
    assert headers["user-agent"] == "PP03-AI-Jiyan (https://github.com/hanchanqaq-source/PP03-AI-Jiyan)"
    assert headers["user-agent"] != "untrusted-client"


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
    http = FakeHttp([submissions_fixture(), index])

    result = SecEdgarAdapter(http=http).fetch(ProviderRequest(
        "filing_metadata", {"cik": "0000320193", "accession": "0000320193-24-000123", "form": "10-K"}
    ))

    assert result[0].value["form"] == "10-K"
    assert result[0].value["filing_body_fetched"] is False
    assert [url for url, _headers in http.calls] == [
        "https://data.sec.gov/submissions/CIK0000320193.json",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/index.json"
    ]


@pytest.mark.parametrize(
    ("provider_request", "responses"),
    [
        (ProviderRequest("company_submissions", {"cik": "0000320193"}), [{}]),
        (ProviderRequest("company_facts", {"cik": "0000320193"}), [{"cik": "0000320193", "facts": []}]),
        (ProviderRequest("filing_index", {"cik": "0000320193", "accession": "0000320193-24-000123"}), [{}]),
        (ProviderRequest("filing_metadata", {"cik": "0000320193", "accession": "0000320193-24-000123", "form": "10-K"}), [
            {**submissions_fixture(), "filings": {"recent": {"accessionNumber": ["0000320193-24-000123"], "form": ["8-K"]}}},
            {"directory": {"name": "index", "item": []}},
        ]),
    ],
)
def test_sec_rejects_empty_and_wrong_form_schemas(provider_request: ProviderRequest, responses: list[object]):
    """Catches JSON objects that are syntactically valid but not SEC submissions, facts, or index metadata."""
    from data_sources.providers.sec_edgar import SecEdgarAdapter

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        SecEdgarAdapter(http=FakeHttp(responses)).fetch(provider_request)


@pytest.mark.parametrize(
    "capability_id",
    [
        "sec_company_submissions", "sec_filing_index_metadata", "sec_10k_metadata",
        "sec_10q_metadata", "sec_8k_metadata", "sec_13f_metadata", "sec_company_facts",
    ],
)
def test_sec_catalog_capability_ids_route_to_their_public_contract(capability_id: str):
    """Catches Catalog-visible SEC capability IDs being rejected as private adapter names."""
    from data_sources.providers.sec_edgar import SecEdgarAdapter

    parameters = {"cik": "0000320193"}
    responses: list[object] = [submissions_fixture()]
    if capability_id == "sec_filing_index_metadata":
        parameters["accession"] = "0000320193-24-000123"
        responses = [{"directory": {"name": "index", "item": []}}]
    elif capability_id in {"sec_10k_metadata", "sec_10q_metadata", "sec_8k_metadata", "sec_13f_metadata"}:
        parameters["accession"] = "0000320193-24-000123"
        form = {"sec_10k_metadata": "10-K", "sec_10q_metadata": "10-Q", "sec_8k_metadata": "8-K", "sec_13f_metadata": "13F"}[capability_id]
        responses = [submissions_fixture(form), {"directory": {"name": "index", "item": []}}]
    elif capability_id == "sec_company_facts":
        responses = [{"cik": "0000320193", "facts": {}}]

    value = SecEdgarAdapter(http=FakeHttp(responses)).fetch(ProviderRequest(capability_id, parameters))[0]

    assert value.capability_id == capability_id


def test_sec_marks_contact_unconfigured_when_official_service_rejects_the_public_identifier():
    """Catches a rejected SEC public contact being mislabeled as a connected service."""
    from data_sources.providers.sec_edgar import SecEdgarAdapter

    http = FakeHttp([ProviderUnavailable("authentication", reference="https://data.sec.gov/submissions/CIK0000320193.json")])

    assert SecEdgarAdapter(http=http).probe("company_submissions") == {
        "status": "unconfigured_contact", "connected": False
    }
