from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderUnavailable
from data_sources.providers.baostock import BaoStockAdapter


class FakeResult:
    def __init__(self, fields, rows, *, error_code="0"):
        self.fields = fields
        self._rows = list(rows)
        self.error_code = error_code
        self.error_msg = "fixture failure" if error_code != "0" else "success"
        self._index = -1

    def next(self):
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self):
        return self._rows[self._index]


class FakeBaoStock:
    def __init__(self):
        self.login_calls = 0
        self.logout_calls = 0
        self.raise_during_query = False
        self.login_error = False
        self.history_calls = []

    def login(self):
        self.login_calls += 1
        return FakeResult((), (), error_code="1" if self.login_error else "0")

    def logout(self):
        self.logout_calls += 1

    def query_history_k_data_plus(self, code, fields, **kwargs):
        if self.raise_during_query:
            raise RuntimeError("query failure")
        self.history_calls.append((code, fields, kwargs))
        requested = tuple(fields.split(","))
        row = {
            "date": "2026-08-18",
            "code": code,
            "open": "10.10",
            "close": "10.50",
            "high": "10.60",
            "low": "10.00",
            "volume": "1000",
            "amount": "10500",
            "adjustflag": kwargs.get("adjustflag", "3"),
            "peTTM": "7.2",
            "psTTM": "1.1",
            "pbMRQ": "0.8",
        }
        return FakeResult(requested, [[row.get(field, "") for field in requested]])

    def query_adjust_factor(self, code, start_date=None, end_date=None):
        return FakeResult(("code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"), [
            [code, "2026-08-18", "1.0", "1.1", "1.0"],
        ])

    def query_profit_data(self, code, year, quarter):
        return FakeResult(("code", "pubDate", "statDate", "roeAvg", "npMargin"), [
            [code, "2026-08-18", f"{year}-06-30", "9.1", "3.2"],
        ])

    def query_stock_industry(self, code=None, date=None):
        return FakeResult(("updateDate", "code", "code_name", "industry", "industryClassification"), [
            ["2026-08-18", code or "sh.600000", "fixture", "银行", "申万"],
        ])

    def query_trade_dates(self, start_date, end_date):
        return FakeResult(("calendar_date", "is_trading_day"), [["2026-08-18", "1"]])


@pytest.fixture
def fake_baostock():
    return FakeBaoStock()


def test_baostock_normalizes_history_with_metadata(fake_baostock):
    adapter = BaoStockAdapter(client=fake_baostock, clock=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc))

    rows = adapter.fetch(ProviderRequest("stock_history", {"code": "sh.600000", "start_date": "2026-08-01"}))

    assert rows[0].source_family_id == "baostock"
    assert rows[0].adapter_id == "baostock"
    assert rows[0].capability_id == "stock_history"
    assert rows[0].unit == "CNY"
    assert rows[0].frequency == "daily"
    assert rows[0].data_status == "upstream_reported"
    assert rows[0].as_of_date.isoformat() == "2026-08-18"
    assert rows[0].value["close"] == "10.50"
    assert fake_baostock.login_calls == 1
    assert fake_baostock.logout_calls == 1


@pytest.mark.parametrize(
    ("capability_id", "parameters", "expected_key", "expected_date"),
    [
        ("stock_history_adjusted", {"code": "sh.600000", "start_date": "2026-08-01"}, "back_adjust_factor", "2026-08-18"),
        ("stock_financials", {"code": "sh.600000", "year": 2026, "quarter": 2}, "roe_avg", "2026-08-18"),
        ("stock_industry_reference", {"code": "sh.600000"}, "industry", "2026-08-18"),
        ("index_calendar", {"start_date": "2026-08-18", "end_date": "2026-08-18"}, "is_trading_day", "2026-08-18"),
        ("stock_valuation", {"code": "sh.600000", "start_date": "2026-08-01"}, "pe_ttm", "2026-08-18"),
    ],
)
def test_baostock_normalizes_supported_plan_capabilities(fake_baostock, capability_id, parameters, expected_key, expected_date):
    rows = BaoStockAdapter(client=fake_baostock).fetch(ProviderRequest(capability_id, parameters))

    assert rows[0].value[expected_key] is not None
    assert rows[0].source_family_id == "baostock"
    assert (rows[0].as_of_date.isoformat() if rows[0].as_of_date else None) == expected_date


def test_baostock_logs_out_after_failure(fake_baostock):
    fake_baostock.raise_during_query = True

    with pytest.raises(ProviderUnavailable, match="baostock_query_failed"):
        BaoStockAdapter(client=fake_baostock).fetch(
            ProviderRequest("stock_history", {"code": "sh.600000", "start_date": "2026-08-01"})
        )

    assert fake_baostock.logout_calls == 1


def test_baostock_login_failure_is_unavailable_and_still_logs_out(fake_baostock):
    fake_baostock.login_error = True

    with pytest.raises(ProviderUnavailable, match="baostock_login_failed"):
        BaoStockAdapter(client=fake_baostock).fetch(
            ProviderRequest("stock_history", {"code": "sh.600000", "start_date": "2026-08-01"})
        )

    assert fake_baostock.logout_calls == 1


def test_baostock_rejects_unsupported_capabilities_without_querying(fake_baostock):
    with pytest.raises(ProviderUnavailable, match="unsupported_capability"):
        BaoStockAdapter(client=fake_baostock).fetch(ProviderRequest("nav_history", {}))

    assert fake_baostock.login_calls == 0
    assert fake_baostock.logout_calls == 0


@pytest.mark.parametrize(
    ("capability_id", "parameters", "error_code"),
    [
        ("stock_history", {"start_date": "2026-08-01"}, "missing_required_parameter"),
        ("stock_history", {"code": "sh.600000", "start_date": "2026-08-40"}, "invalid_request_parameter"),
        ("stock_history_adjusted", {"code": "sh.600000", "start_date": "2026-08-19", "end_date": "2026-08-18"}, "invalid_request_parameter"),
        ("stock_history_adjusted", {"code": 600000}, "invalid_request_parameter"),
        ("stock_financials", {"code": "sh.600000", "quarter": 2}, "missing_required_parameter"),
        ("stock_financials", {"code": "sh.600000", "year": "2026", "quarter": 2}, "invalid_request_parameter"),
        ("stock_financials", {"code": "sh.600000", "year": 2026, "quarter": 5}, "invalid_request_parameter"),
        ("stock_industry_reference", {"code": "sh.600000", "date": "not-a-date"}, "invalid_request_parameter"),
        ("index_calendar", {"start_date": "2026-08-19", "end_date": "2026-08-18"}, "invalid_request_parameter"),
        ("stock_valuation", {"code": "sh.600000", "end_date": 20260818}, "invalid_request_parameter"),
    ],
)
def test_baostock_rejects_invalid_parameters_before_login_or_query(fake_baostock, capability_id, parameters, error_code):
    with pytest.raises(ProviderUnavailable, match=error_code):
        BaoStockAdapter(client=fake_baostock).fetch(ProviderRequest(capability_id, parameters))

    assert fake_baostock.login_calls == 0
    assert fake_baostock.logout_calls == 0
    assert fake_baostock.history_calls == []


def test_baostock_rejects_invalid_parameters_before_optional_client_load():
    client_load_calls = []

    def loader():
        client_load_calls.append("called")
        raise AssertionError("invalid requests must not load BaoStock")

    with pytest.raises(ProviderUnavailable, match="missing_required_parameter"):
        BaoStockAdapter(client_loader=loader).fetch(
            ProviderRequest("stock_financials", {"code": "sh.600000", "year": 2026})
        )

    assert client_load_calls == []


def test_baostock_descriptor_states_independence_and_cninfo_nav_exclusions(fake_baostock):
    descriptor = BaoStockAdapter(client=fake_baostock).descriptor

    assert descriptor.adapter_id == "baostock"
    assert descriptor.source_family_id == "baostock"
    assert descriptor.auth_type == "none"
    assert "stock_industry_classification" not in descriptor.capability_ids
    assert "nav_history" not in descriptor.capability_ids
