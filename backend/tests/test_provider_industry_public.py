from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from data_sources.http import SafeHttpClient
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderUnavailable


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "providers" / "industry_price_public" / "recorded_responses.json"


def recorded(name: str) -> dict[str, object]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))[name]
    payload["body"] = payload["body"].encode("utf-8")
    return payload


class FakeHttp:
    def __init__(self, document: object) -> None:
        self.document = document
        self.calls: list[tuple[str, object, object]] = []

    def get_document(self, url, *, headers=None, redirect_validator=None):
        self.calls.append((url, headers, redirect_validator))
        if isinstance(self.document, BaseException):
            raise self.document
        return self.document


class RedirectResponse:
    def __init__(self, *, status_code: int, headers=None, content: bytes = b"") -> None:
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(headers or {})
        self._content = content

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self._content

    def close(self) -> None:
        pass


class RedirectTransport(BaseAdapter):
    def __init__(self, responses: list[RedirectResponse]) -> None:
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


def verified_qualification():
    from industry_research.source_qualification import SourceQualificationResult

    return SourceQualificationResult(
        source_identity="trendforce_public_price",
        request_url="https://www.trendforce.com/price/dram/dram_spot",
        final_url="https://www.trendforce.com/price/dram/dram_spot",
        http_status=200,
        response_cap_bytes=500_000,
        response_bytes=len(recorded("valid_public_snapshot")["body"]),
        target_fields=("product", "session_average", "date"),
        field_shape="html_table:product,session_average,date",
        data_date_field="date",
        data_date=date(2026, 8, 24),
        unit="USD",
        frequency="current_snapshot",
        license_conclusion="verified_public_current_snapshot",
        failure_modes=(
            "structure_changed",
            "missing_data_date",
            "missing_unit",
            "login_required",
            "cookie_required",
            "member_download",
            "response_too_large",
            "redirect_disallowed",
            "license_unverified",
        ),
        failure_reason=None,
        login_required=False,
        cookie_required=False,
        member_download=False,
    )


def test_public_industry_provider_module_is_present():
    """Catches the only approved public price candidate adapter being omitted."""
    from data_sources.providers.industry_price_public import TrendForcePublicPriceAdapter

    assert TrendForcePublicPriceAdapter is not None


def test_recorded_public_snapshot_preserves_fields_date_unit_and_candidate_status():
    """Catches row/date/unit provenance being dropped or a candidate becoming a verified report value."""
    from data_sources.providers.industry_price_public import TrendForcePublicPriceAdapter

    http = FakeHttp(recorded("valid_public_snapshot"))
    adapter = TrendForcePublicPriceAdapter(
        http=http,
        qualification=verified_qualification(),
        fetched_at=lambda: datetime(2026, 8, 25, tzinfo=timezone.utc),
    )

    rows = adapter.fetch(ProviderRequest("industry_price_snapshot", {}))

    assert [row.value for row in rows] == [Decimal("6.742"), Decimal("3.215")]
    assert {row.as_of_date for row in rows} == {date(2026, 8, 24)}
    assert {row.unit for row in rows} == {"USD"}
    assert {row.frequency for row in rows} == {"current_snapshot"}
    assert {row.data_status for row in rows} == {"candidate_snapshot"}
    assert {row.source_family_id for row in rows} == {"trendforce_public_price"}
    assert {row.source_metadata["report_value_status"] for row in rows} == {"not_verified"}
    assert [row.source_metadata["product"] for row in rows] == [
        "DDR5 16G (2Gx8) 4800/5600",
        "DDR4 16G (2Gx8) 3200",
    ]
    assert len(http.calls) == 1
    assert http.calls[0][0] == "https://www.trendforce.com/price/dram/dram_spot"
    assert http.calls[0][1] == {"Accept": "text/html,application/xhtml+xml"}


def test_qualification_records_complete_bounded_response_evidence_without_values():
    """Catches live qualification evidence omitting status, bytes, shape, date, unit, or failure policy."""
    from data_sources.providers.industry_price_public import TrendForcePublicPriceAdapter

    result = TrendForcePublicPriceAdapter(
        http=FakeHttp(recorded("valid_public_snapshot")),
        qualification=verified_qualification(),
    ).qualify()

    assert result.http_status == 200
    assert result.response_bytes == len(recorded("valid_public_snapshot")["body"])
    assert result.response_bytes <= result.response_cap_bytes
    assert result.field_shape == "html_table:product,session_average,date"
    assert result.data_date == date(2026, 8, 24)
    assert result.unit == "USD"
    assert result.frequency == "current_snapshot"
    assert not hasattr(result, "values")


@pytest.mark.parametrize(
    ("fixture_name", "failure_code"),
    [
        ("structure_changed", "structure_changed"),
        ("missing_date", "missing_data_date"),
        ("missing_unit", "missing_unit"),
        ("login_wall", "login_required"),
        ("cookie_wall", "cookie_required"),
        ("member_download", "member_download"),
    ],
)
def test_recorded_public_page_failures_are_license_unverified_and_never_return_values(fixture_name, failure_code):
    """Catches changed, incomplete, login, or member HTML being promoted to a price value."""
    from data_sources.providers.industry_price_public import TrendForcePublicPriceAdapter

    adapter = TrendForcePublicPriceAdapter(
        http=FakeHttp(recorded(fixture_name)),
        qualification=verified_qualification(),
    )
    result = adapter.qualify()

    assert result.license_conclusion == "license_unverified"
    assert result.failure_reason == failure_code
    with pytest.raises(ProviderUnavailable, match=failure_code):
        adapter.fetch(ProviderRequest("industry_price_snapshot", {}))


def test_complete_recorded_response_over_cap_fails_closed_before_parsing():
    """Catches an oversized HTML response being parsed or represented as qualified."""
    from data_sources.providers.industry_price_public import TrendForcePublicPriceAdapter

    adapter = TrendForcePublicPriceAdapter(
        http=FakeHttp(recorded("oversized_response")),
        qualification=verified_qualification(),
        max_response_bytes=512,
    )

    result = adapter.qualify()
    assert result.license_conclusion == "license_unverified"
    assert result.failure_reason == "response_too_large"
    with pytest.raises(ProviderUnavailable, match="response_too_large"):
        adapter.fetch(ProviderRequest("industry_price_snapshot", {}))


def test_schema_failure_keeps_observed_transport_evidence_but_clears_unverified_shape():
    """Catches an HTTP response being erased or an expected shape being reported as actually observed."""
    from data_sources.providers.industry_price_public import TrendForcePublicPriceAdapter

    response = recorded("structure_changed")
    result = TrendForcePublicPriceAdapter(
        http=FakeHttp(response),
        qualification=verified_qualification(),
    ).qualify()

    assert result.final_url == response["final_url"]
    assert result.http_status == 200
    assert result.response_bytes == len(response["body"])
    assert result.target_fields == ()
    assert result.field_shape == ""
    assert result.data_date is None
    assert result.unit is None
    assert result.failure_reason == "structure_changed"


@pytest.mark.parametrize(
    "target",
    [
        "https://www.trendforce.com/login?return=%2Fprice%2Fdram%2Fdram_spot",
        "https://www.trendforce.com/member/download/dram-price-history",
        "https://wsts.org/subscribe",
        "https://www.trendforce.com/price/dram/history",
    ],
)
def test_safe_redirect_policy_never_requests_login_member_subscription_or_history_target(target):
    """Catches a forbidden redirect being followed before the industry-source policy rejects it."""
    from data_sources.providers.industry_price_public import TrendForcePublicPriceAdapter

    session = requests.Session()
    transport = RedirectTransport([RedirectResponse(status_code=302, headers={"Location": target})])
    session.mount("https://", transport)
    adapter = TrendForcePublicPriceAdapter(
        http=SafeHttpClient(session=session, max_bytes=500_000, max_redirects=1),
        qualification=verified_qualification(),
    )

    result = adapter.qualify()

    assert result.license_conclusion == "license_unverified"
    assert result.failure_reason == "redirect_disallowed"
    assert len(transport.requests) == 1


def test_unconfigured_default_registry_adapter_cannot_fetch_or_claim_connection():
    """Catches registry registration bypassing the unverified-license default."""
    from data_sources.provider_registry import ProviderRegistry

    adapter = ProviderRegistry(http_factory=lambda: FakeHttp(recorded("valid_public_snapshot"))).adapter(
        "trendforce-public-price"
    )

    assert adapter.descriptor.default_enabled is False
    assert adapter.descriptor.catalog_status.value == "unconfigured"
    with pytest.raises(ProviderUnavailable, match="license_unverified"):
        adapter.fetch(ProviderRequest("industry_price_snapshot", {}))
    assert adapter.probe("industry_price_snapshot") == {
        "status": "license_unverified",
        "connected": False,
    }


def test_transport_failure_is_recorded_as_unavailable_without_substitute_source():
    """Catches a failed public page being silently replaced by another source or a fabricated success."""
    from data_sources.providers.industry_price_public import TrendForcePublicPriceAdapter

    result = TrendForcePublicPriceAdapter(
        http=FakeHttp(ProviderUnavailable("timeout")),
        qualification=verified_qualification(),
    ).qualify()

    assert result.license_conclusion == "license_unverified"
    assert result.failure_reason == "timeout"
    assert result.final_url is None
    assert result.http_status is None
    assert result.response_bytes is None
