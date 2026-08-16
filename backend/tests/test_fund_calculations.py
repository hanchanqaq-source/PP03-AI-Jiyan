from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fund_data.calculations import (
    calculate_industry_concentration,
    calculate_intraday_estimate,
    calculate_overlap,
    calculate_performance,
    calculate_position,
)


def test_position_uses_shares_avg_cost_and_official_nav():
    assert calculate_position(shares=500, avg_cost=2, official_nav=2.4) == {
        "total_cost": 1000.0,
        "market_value": 1200.0,
        "profit_loss": 200.0,
        "return_rate": 20.0,
    }


def test_position_does_not_invent_return_when_cost_is_zero_or_unconfirmed():
    assert calculate_position(shares=500, avg_cost=0, official_nav=2.4)["return_rate"] is None
    assert calculate_position(shares=500, avg_cost=None, official_nav=2.4) == {
        "total_cost": None,
        "market_value": 1200.0,
        "profit_loss": None,
        "return_rate": None,
    }


def test_performance_calculates_drawdown_and_annualized_volatility_from_nav():
    history = [
        {"date": "2026-01-02", "unit_nav": 1.0},
        {"date": "2026-01-05", "unit_nav": 1.1},
        {"date": "2026-01-06", "unit_nav": 0.99},
    ]

    result = calculate_performance(history)

    assert result["since_inception"] == pytest.approx(-1.0)
    assert result["max_drawdown"] == pytest.approx(-10.0)
    assert result["annualized_volatility"] == pytest.approx(224.499443, rel=1e-6)


def test_overlap_lists_each_fund_and_weighted_combined_exposure():
    funds = [
        {
            "code": "000001",
            "name": "甲基金",
            "market_value": 1000.0,
            "holdings": [{"stock_code": "600000", "stock_name": "浦发银行", "weight_pct": 10.0}],
        },
        {
            "code": "000002",
            "name": "乙基金",
            "market_value": 2000.0,
            "holdings": [{"stock_code": "600000", "stock_name": "浦发银行", "weight_pct": 20.0}],
        },
    ]

    result = calculate_overlap(funds)

    assert result == [{
        "stock_code": "600000",
        "stock_name": "浦发银行",
        "funds": [
            {"fund_code": "000001", "fund_name": "甲基金", "weight_pct": 10.0, "portfolio_exposure_pct": 3.3333},
            {"fund_code": "000002", "fund_name": "乙基金", "weight_pct": 20.0, "portfolio_exposure_pct": 13.3333},
        ],
        "portfolio_exposure_pct": 16.6666,
        "calculation_basis": "基金官方净值对应市值权重 × 最新公开持仓比例",
    }]


def test_industry_concentration_preserves_unknown_exposure_instead_of_normalizing():
    funds = [
        {"market_value": 1000.0, "broad_exposure": {"科技": 60.0, "医疗": 10.0}, "unknown_pct": 30.0},
        {"market_value": 2000.0, "broad_exposure": {"科技": 20.0, "消费": 30.0}, "unknown_pct": 50.0},
    ]

    result = calculate_industry_concentration(funds)

    assert result["exposure"] == [
        {"name": "科技", "weight_pct": 33.3333},
        {"name": "消费", "weight_pct": 20.0},
        {"name": "医疗", "weight_pct": 3.3333},
    ]
    assert result["identified_coverage_pct"] == pytest.approx(56.6666, abs=1e-4)
    assert result["unknown_pct"] == pytest.approx(43.3333, abs=1e-4)


def test_intraday_estimate_requires_reliable_disclosed_and_quote_coverage():
    unavailable = calculate_intraday_estimate(
        latest_nav=1.25,
        fund_type="混合型-偏股",
        holdings=[{"stock_code": "600000", "weight_pct": 40.0}],
        quotes={"600000": {"change_pct": 2.0}},
        disclosure_date=date(2026, 6, 30),
        estimate_time=datetime(2026, 8, 16, 8, tzinfo=timezone.utc),
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["message"] == "盘中估算暂不可用：当前基金类型或公开持仓不足以形成可靠估算"

    available = calculate_intraday_estimate(
        latest_nav=1.25,
        fund_type="混合型-偏股",
        holdings=[
            {"stock_code": "600000", "weight_pct": 30.0},
            {"stock_code": "000001", "weight_pct": 30.0},
        ],
        quotes={"600000": {"change_pct": 2.0}, "000001": {"change_pct": -1.0}},
        disclosure_date=date(2026, 6, 30),
        estimate_time=datetime(2026, 8, 16, 8, tzinfo=timezone.utc),
    )
    assert available["status"] == "estimated"
    assert available["estimated_nav"] == pytest.approx(1.25375)
    assert available["estimated_change_pct"] == pytest.approx(0.3)
    assert available["holdings_coverage_pct"] == 60.0
    assert available["quote_coverage_pct"] == 100.0
    assert available["formula"] == "最新官方净值 × (1 + Σ公开持仓比例×对应股票涨跌幅)"

