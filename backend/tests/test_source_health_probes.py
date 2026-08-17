from __future__ import annotations

import socket
import ssl

import pytest
import requests

from fund_data.models import ProviderResult
from source_health.probe_errors import classify_probe_error
from source_health.probes.fund_provider import probe_provider_capability


class FakeProvider:
    name = "public-test-provider"

    def __init__(self, result=None, error: BaseException | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, capability: str, **kwargs):
        self.calls.append((capability, kwargs))
        if self.error:
            raise self.error
        return ProviderResult(
            data=self.result,
            source_name="公开测试源",
            source_reference="https://public.example.test/data",
            data_type=capability,
            as_of_date="2026-08-18",
            status="disclosed",
        )


@pytest.mark.parametrize(
    ("capability", "probe_args", "payload"),
    [
        ("search", {"query": "000001"}, [{"code": "000001", "name": "示例基金"}]),
        ("profile", {"code": "000001"}, {"code": "000001", "name": "示例基金", "fund_type": "混合型"}),
        (
            "nav_history",
            {"code": "000001"},
            {"points": [{"date": "2026-08-18", "unit_nav": 1.2}], "latest": {"unit_nav": 1.2, "nav_date": "2026-08-18"}},
        ),
        (
            "holdings",
            {"code": "000001"},
            {"report_period": "2026-Q2", "holdings": [{"stock_code": "600000", "stock_name": "浦发银行", "weight_pct": 2.5}]},
        ),
        (
            "industry_allocation",
            {"code": "000001"},
            {"as_of_date": "2026-06-30", "industries": [{"name": "银行", "weight_pct": 2.5}]},
        ),
        (
            "stock_industry_classification",
            {"codes": ["600000"]},
            {
                "requested_codes": ["600000"],
                "classifications": {
                    "600000": {
                        "stock_code": "600000",
                        "primary_industry": "银行",
                        "source_name": "公开分类源",
                        "source_reference": "https://public.example.test/classification",
                    }
                },
            },
        ),
    ],
)
def test_fund_probe_accepts_each_complete_capability_contract(capability, probe_args, payload):
    provider = FakeProvider(payload)

    result = probe_provider_capability(provider, capability, probe_args=probe_args)

    assert provider.calls == [(capability, probe_args)]
    assert result["status"] == "success"
    assert result["error_type"] == "none"
    assert result["field_completeness_pct"] == 100.0
    assert result["returned_items"] >= 1


def test_stock_snapshot_missing_optional_quotes_is_partial_without_fabricating_values():
    payload = {
        "600000": {
            "stock_code": "600000",
            "stock_name": "浦发银行",
            "price": None,
        }
    }
    provider = FakeProvider(payload)

    result = probe_provider_capability(
        provider,
        "stock_snapshot",
        probe_args={"codes": ["600000"]},
    )

    assert result["status"] == "partial"
    assert result["field_completeness_pct"] == 50.0
    assert result["data"]["600000"]["price"] is None
    assert "change_pct" not in result["data"]["600000"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (requests.Timeout("late"), "timeout"),
        (TimeoutError("late"), "timeout"),
        (socket.gaierror("missing"), "dns"),
        (ssl.SSLError("handshake"), "tls"),
        (requests.ConnectionError("offline"), "connection"),
    ],
)
def test_provider_exceptions_are_classified(error, expected):
    result = probe_provider_capability(FakeProvider(error=error), "profile", probe_args={"code": "000001"})

    assert result["status"] == "failure"
    assert result["error_type"] == expected


def test_schema_mismatch_is_reported_instead_of_counted_as_success():
    result = probe_provider_capability(
        FakeProvider({"code": "not-six-digits", "name": "示例基金"}),
        "profile",
        probe_args={"code": "000001"},
    )

    assert result["status"] == "failure"
    assert result["error_type"] == "schema_changed"
    assert result["field_completeness_pct"] < 100


def test_probe_redacts_credentials_and_local_paths():
    error = RuntimeError(
        "GET https://x.test/feed?token=secret "
        "Authorization: Bearer abc Cookie: sid=cookie-secret "
        r"C:\Users\26365\private\trace.log"
    )

    result = classify_probe_error(error)

    assert len(result.message) <= 200
    assert "secret" not in result.message
    assert "Bearer" not in result.message
    assert "26365" not in result.message
    assert "trace.log" not in result.message
