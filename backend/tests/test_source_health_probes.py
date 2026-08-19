from __future__ import annotations

from datetime import datetime, timedelta, timezone
import socket
import ssl

import pytest
import requests

from fund_data.models import ProviderResult
from source_health.probe_errors import classify_probe_error, redact_url
from source_health.probes.fund_provider import probe_provider_capability
from source_health.registry import build_provider_descriptors


class FakeProvider:
    name = "public-test-provider"

    def __init__(self, result=None, error: BaseException | None = None, as_of_date: str = "2026-08-18"):
        self.result = result
        self.error = error
        self.as_of_date = as_of_date
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
            as_of_date=self.as_of_date,
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
    ("age_seconds", "expected_status"),
    [(60, "success"), (7 * 86400, "success"), (7 * 86400 + 1, "partial")],
)
def test_registry_nav_freshness_window_drives_fresh_boundary_and_stale(age_seconds, expected_status):
    class NavProvider(FakeProvider):
        name = "nav-provider"
        adapter_id = "eastmoney-direct"
        priority = 10
        capabilities = {"nav_history"}

    current = datetime(2026, 8, 18, tzinfo=timezone.utc)
    as_of = (current - timedelta(seconds=age_seconds)).isoformat()
    provider = NavProvider(
        {"points": [{"date": as_of[:10], "unit_nav": 1.2}], "latest": {"unit_nav": 1.2, "nav_date": as_of[:10]}},
        as_of_date=as_of,
    )
    descriptor = build_provider_descriptors([provider])[0]

    result = probe_provider_capability(
        provider, "nav_history", probe_args={"code": "000001"},
        freshness_max_age_seconds=descriptor.freshness_max_age_seconds, now=current,
    )

    assert result["status"] == expected_status
    assert result["error_type"] == ("stale_data" if expected_status == "partial" else "none")


def test_profile_has_no_freshness_window_and_is_not_misclassified_as_stale():
    class ProfileProvider(FakeProvider):
        name = "profile-provider"
        adapter_id = "eastmoney-direct"
        priority = 10
        capabilities = {"profile"}

    provider = ProfileProvider(
        {"code": "000001", "name": "示例基金", "fund_type": "混合型"},
        as_of_date="2020-01-01",
    )
    descriptor = build_provider_descriptors([provider])[0]

    result = probe_provider_capability(
        provider, "profile", probe_args={"code": "000001"},
        freshness_max_age_seconds=descriptor.freshness_max_age_seconds,
        now=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )

    assert descriptor.freshness_max_age_seconds is None
    assert result["status"] == "success"


def test_stock_snapshot_completeness_counts_every_requested_code():
    provider = FakeProvider({
        "600000": {
            "stock_code": "600000",
            "stock_name": "浦发银行",
            "price": 10.5,
            "change_pct": 1.2,
        }
    })

    result = probe_provider_capability(
        provider,
        "stock_snapshot",
        probe_args={"codes": ["600000", "000001"]},
    )

    assert result["status"] == "partial"
    assert result["returned_items"] == 1
    assert result["field_completeness_pct"] == 50.0


def test_stock_snapshot_rejects_mismatched_mapping_key_and_row_code():
    provider = FakeProvider({
        "600000": {
            "stock_code": "000001",
            "stock_name": "错误映射",
            "price": 10.5,
            "change_pct": 1.2,
        }
    })

    result = probe_provider_capability(
        provider,
        "stock_snapshot",
        probe_args={"codes": ["600000"]},
    )

    assert result["status"] == "failure"
    assert result["error_type"] == "schema_changed"
    assert result["field_completeness_pct"] == 0.0


def test_nav_and_quote_completeness_require_finite_non_boolean_numbers():
    nav = probe_provider_capability(
        FakeProvider({
            "points": [{"date": "2026-08-18", "unit_nav": True}],
            "latest": {"unit_nav": True, "nav_date": "2026-08-18"},
        }),
        "nav_history",
        probe_args={"code": "000001"},
    )
    quote = probe_provider_capability(
        FakeProvider({
            "600000": {
                "stock_code": "600000",
                "stock_name": "浦发银行",
                "price": "--",
                "change_pct": True,
            },
            "000001": {
                "stock_code": "000001",
                "stock_name": "平安银行",
                "price": float("nan"),
                "change_pct": float("inf"),
            },
        }),
        "stock_snapshot",
        probe_args={"codes": ["600000", "000001"]},
    )

    assert nav["status"] == "failure"
    assert nav["error_type"] == "schema_changed"
    assert quote["status"] == "partial"
    assert quote["field_completeness_pct"] == 50.0


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


def test_url_redaction_treats_only_exact_code_query_key_as_sensitive():
    redacted = redact_url(
        "https://public.example.test/callback?code=opaque-uuid&decode=public&postcode=200000"
    )

    assert "code=%5Bredacted%5D" in redacted
    assert "decode=public" in redacted
    assert "postcode=200000" in redacted
    assert "opaque-uuid" not in redacted


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\Alice Smith\private folder\trace.log",
        "C:/Users/Alice Smith/private folder/trace.log",
        r"D:\private workspace\source health\debug.txt",
    ],
)
def test_probe_redacts_standalone_windows_paths_with_spaces_and_slashes(path):
    result = classify_probe_error(RuntimeError(path))

    lowered = result.message.lower()
    assert "alice" not in lowered
    assert "private" not in lowered
    assert "trace.log" not in lowered
    assert "debug.txt" not in lowered
    assert "users" not in lowered


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\Alice Smith\private folder\trace.log",
        "C:/Users/Alice Smith/private folder/trace.log",
    ],
)
def test_probe_redacts_windows_path_on_one_line_without_swallowing_the_next(path):
    result = classify_probe_error(RuntimeError(f"{path}\nPublic follow-up detail"))

    lowered = result.message.lower()
    assert "alice" not in lowered
    assert "private" not in lowered
    assert "trace.log" not in lowered
    assert "users" not in lowered
    assert "public follow-up detail" in lowered
