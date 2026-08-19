from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderSchemaChanged, ProviderUnavailable
from data_sources.providers.yahoo_finance import YahooFinanceAdapter


class FakeTicker:
    def __init__(self):
        self.history_calls = []
        self.info = {
            "longName": "Fixture ETF",
            "quoteType": "ETF",
            "currency": "USD",
            "exchange": "NMS",
        }
        self.history_result = None

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        if self.history_result is not None:
            return self.history_result
        return [
            {
                "Date": "2026-08-18T16:00:00+00:00",
                "Open": 10.0,
                "Close": 10.5,
                "Volume": 1000,
                "Currency": "USD",
            }
        ]


class FakeYahooFinance:
    def __init__(self):
        self.tickers = []
        self.ticker = FakeTicker()

    def Ticker(self, symbol):
        self.tickers.append(symbol)
        return self.ticker


def research_request(capability_id="overseas_etf_history"):
    return ProviderRequest(capability_id, {"symbol": "SPY", "usage_mode": "personal_research"})


def test_yahoo_finance_requires_personal_research_before_constructing_ticker():
    fake = FakeYahooFinance()

    with pytest.raises(ProviderUnavailable, match="personal_research_required"):
        YahooFinanceAdapter(client=fake).fetch(ProviderRequest("overseas_etf_history", {"symbol": "SPY"}))

    assert fake.tickers == []


def test_yahoo_finance_normalizes_overseas_history_as_non_official_reference():
    fake = FakeYahooFinance()
    adapter = YahooFinanceAdapter(client=fake, clock=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc))

    rows = adapter.fetch(research_request())

    assert rows[0].source_family_id == "yahoo_finance"
    assert rows[0].adapter_id == "yahoo-finance"
    assert rows[0].capability_id == "overseas_etf_history"
    assert rows[0].unit == "USD"
    assert rows[0].frequency == "daily"
    assert rows[0].data_status == "non_official_reference"
    assert rows[0].as_of_date.isoformat() == "2026-08-18"
    assert rows[0].value["upstream_timestamp"] == "2026-08-18T16:00:00+00:00"
    assert rows[0].value["official_evidence_eligible"] is False


@pytest.mark.parametrize(
    "history_result",
    [
        [{"Close": 10.5, "Currency": "USD"}],
        [{"Date": "2026-08-18T16:00:00+00:00", "Currency": "USD", "unexpected": 10.5}],
    ],
)
def test_yahoo_finance_rejects_history_schema_without_timestamp_or_price_columns(history_result):
    fake = FakeYahooFinance()
    fake.ticker.history_result = history_result

    with pytest.raises(ProviderSchemaChanged, match="schema_changed"):
        YahooFinanceAdapter(client=fake).fetch(research_request())


def test_yahoo_finance_preserves_null_price_and_unknown_currency_when_columns_are_present():
    fake = FakeYahooFinance()
    fake.ticker.history_result = [{"Date": "2026-08-18T16:00:00+00:00", "Close": None}]

    rows = YahooFinanceAdapter(client=fake).fetch(research_request())

    assert rows[0].value["close"] is None
    assert rows[0].value["currency"] is None
    assert rows[0].unit == "unknown"


@pytest.mark.parametrize("capability_id", ["overseas_stock_history", "overseas_etf_history", "overseas_index_history"])
def test_yahoo_finance_limits_history_to_overseas_market_capabilities(capability_id):
    fake = FakeYahooFinance()

    rows = YahooFinanceAdapter(client=fake).fetch(research_request(capability_id))

    assert rows[0].capability_id == capability_id
    assert rows[0].value["official_evidence_eligible"] is False


def test_yahoo_finance_normalizes_profile_as_non_official_reference():
    fake = FakeYahooFinance()

    rows = YahooFinanceAdapter(client=fake).fetch(research_request("overseas_profile_reference"))

    assert rows[0].value == {
        "symbol": "SPY",
        "name": "Fixture ETF",
        "instrument_type": "ETF",
        "exchange": "NMS",
        "currency": "USD",
        "official_evidence_eligible": False,
    }
    assert rows[0].data_status == "non_official_reference"


def test_yahoo_finance_missing_optional_dependency_is_an_explicit_unavailable_capability():
    def unavailable_loader():
        raise ModuleNotFoundError("No module named 'yfinance'")

    with pytest.raises(ProviderUnavailable, match="optional_dependency_unavailable"):
        YahooFinanceAdapter(client_loader=unavailable_loader).fetch(research_request())


def test_yahoo_finance_rejects_unsupported_or_domestic_capabilities_without_ticker_construction():
    fake = FakeYahooFinance()

    with pytest.raises(ProviderUnavailable, match="unsupported_capability"):
        YahooFinanceAdapter(client=fake).fetch(
            ProviderRequest("stock_snapshot", {"symbol": "600000.SS", "usage_mode": "personal_research"})
        )

    assert fake.tickers == []


def test_yahoo_finance_descriptor_is_disabled_non_official_and_personal_research_only():
    descriptor = YahooFinanceAdapter(client=FakeYahooFinance()).descriptor

    assert descriptor.adapter_id == "yahoo-finance"
    assert descriptor.default_enabled is False
    assert descriptor.catalog_status.value == "disabled"
    assert "official_evidence" not in {role.value for role in descriptor.source_roles}
    assert "personal_research" in descriptor.usage_note
