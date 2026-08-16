from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fund_data.cache import FundCache
from fund_data.models import ProviderResult
from fund_data.providers.base import ProviderUnavailable
from fund_data.service import FundDataService


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 16, 8, tzinfo=timezone.utc)

    def now(self):
        return self.value


class FakeProvider:
    def __init__(self, name, priority, payloads=None, failures=None):
        self.name = name
        self.priority = priority
        self.payloads = payloads or {}
        self.failures = set(failures or [])
        self.calls = []
        self.capabilities = set(self.payloads) | self.failures

    def fetch(self, capability, **kwargs):
        self.calls.append((capability, kwargs))
        if capability in self.failures:
            raise ProviderUnavailable(f"{self.name} failed {capability}")
        data, status = self.payloads[capability]
        as_of = None
        if capability == "nav_history":
            as_of = data["latest"]["nav_date"]
        elif capability == "holdings":
            as_of = data["disclosure_date"]
        return ProviderResult(
            data=data,
            source_name=self.name,
            source_reference=f"https://example.test/{capability}",
            data_type=capability,
            as_of_date=as_of,
            status=status,
        )


PROFILE = ({
    "code": "000001", "name": "测试混合基金", "fund_type": "混合型-偏股",
    "manager_names": ["测试经理"], "scale": 10.0, "scale_date": "2026-06-30",
}, "disclosed")
NAV = ({
    "points": [
        {"date": "2026-08-13", "unit_nav": 1.0, "cumulative_nav": 1.0, "daily_change_pct": 0.0},
        {"date": "2026-08-14", "unit_nav": 1.1, "cumulative_nav": 1.2, "daily_change_pct": 10.0},
    ],
    "latest": {"unit_nav": 1.1, "cumulative_nav": 1.2, "nav_date": "2026-08-14"},
}, "official")
HOLDINGS = ({
    "report_period": "2026-Q2", "disclosure_date": "2026-06-30", "public_date": None,
    "top10_coverage_pct": 60.0,
    "holdings": [
        {"stock_code": "600000", "stock_name": "浦发银行", "weight_pct": 30.0, "market_value_10k": 100.0},
        {"stock_code": "688001", "stock_name": "测试芯片", "weight_pct": 30.0, "market_value_10k": 100.0},
    ],
}, "disclosed")
SNAPSHOT = ({
    "600000": {"stock_code": "600000", "stock_name": "浦发银行", "change_pct": 1.0, "industry": "银行"},
    "688001": {"stock_code": "688001", "stock_name": "测试芯片", "change_pct": 2.0, "industry": "半导体"},
}, "disclosed")
INDUSTRY = ({
    "as_of_date": "2026-06-30", "stock_exposure_pct": 80.0,
    "industries": [{"name": "制造业", "weight_pct": 80.0, "market_value_10k": 1000.0}],
}, "disclosed")


def make_service(tmp_path, clock, providers):
    return FundDataService(providers=providers, cache=FundCache(tmp_path, now=clock.now), now=clock.now)


def test_search_uses_fallback_and_reports_it(tmp_path):
    clock = Clock()
    primary = FakeProvider("primary", 10, failures={"search"})
    backup = FakeProvider("backup", 20, payloads={"search": ([{"code": "000001", "name": "测试基金"}], "disclosed")})

    result = make_service(tmp_path, clock, [primary, backup]).search_funds("测试")

    assert result["data"][0]["code"] == "000001"
    assert result["meta"]["provider"] == "backup"
    assert result["meta"]["fallback_used"] is True


def test_analysis_returns_partial_success_when_holdings_fail(tmp_path):
    clock = Clock()
    provider = FakeProvider("primary", 10, payloads={"profile": PROFILE, "nav_history": NAV}, failures={"holdings", "industry_allocation"})

    result = make_service(tmp_path, clock, [provider]).get_fund_analysis("000001")

    assert result["profile"]["data"]["name"] == "测试混合基金"
    assert result["latest_nav"]["data"]["unit_nav"] == 1.1
    assert result["holdings"]["data"] is None
    assert result["holdings"]["meta"]["status"] == "error"
    assert result["industry_exposure"]["meta"]["status"] == "unavailable"


def test_expired_success_cache_is_returned_explicitly_when_all_providers_fail(tmp_path):
    clock = Clock()
    provider = FakeProvider("primary", 10, payloads={"search": ([{"code": "000001", "name": "测试基金"}], "disclosed")})
    service = make_service(tmp_path, clock, [provider])
    first = service.search_funds("测试")
    assert first["meta"]["is_cached"] is False

    clock.value += timedelta(hours=25)
    provider.failures.add("search")
    stale = service.search_funds("测试")

    assert stale["data"][0]["name"] == "测试基金"
    assert stale["meta"]["status"] == "stale"
    assert stale["meta"]["original_status"] == "disclosed"
    assert stale["meta"]["is_cached"] is True
    assert stale["meta"]["is_stale"] is True


@pytest.mark.parametrize("fund_type,expected", [
    ("货币型", "货币基金不适用股票前十大持仓"),
    ("FOF-混合型", "FOF 主要资产为其他基金，当前不进行股票穿透"),
])
def test_fund_type_capability_limits_are_explicit(tmp_path, fund_type, expected):
    clock = Clock()
    profile = ({**PROFILE[0], "fund_type": fund_type}, "disclosed")
    provider = FakeProvider("primary", 10, payloads={"profile": profile, "nav_history": NAV, "holdings": HOLDINGS})

    result = make_service(tmp_path, clock, [provider]).get_fund_analysis("000001")

    assert result["holdings"]["data"] is None
    assert result["holdings"]["meta"]["status"] == "unavailable"
    assert result["holdings"]["meta"]["message"] == expected
    assert not any(call[0] == "holdings" for call in provider.calls)


def test_analysis_computes_disclosed_industry_without_hiding_unknown_assets(tmp_path):
    clock = Clock()
    provider = FakeProvider("primary", 10, payloads={
        "profile": PROFILE, "nav_history": NAV, "holdings": HOLDINGS,
        "stock_snapshot": SNAPSHOT, "industry_allocation": INDUSTRY,
    })

    result = make_service(tmp_path, clock, [provider]).get_fund_analysis("000001")

    exposure = result["industry_exposure"]["data"]
    assert exposure["broad"] == [
        {"name": "科技", "weight_pct": 30.0},
        {"name": "金融", "weight_pct": 30.0},
    ]
    assert exposure["identified_coverage_pct"] == 60.0
    assert exposure["unidentified_disclosed_pct"] == 0.0
    assert exposure["undisclosed_stock_pct"] == 20.0
    assert exposure["non_stock_pct"] == 20.0
    assert exposure["system_tags"] == [
        {"id": "semiconductor", "name": "半导体", "weight_pct": 30.0},
    ]
    assert result["intraday_estimate"]["data"]["status"] == "estimated"


def test_analysis_uses_official_fund_industry_allocation_when_stock_industries_are_missing(tmp_path):
    clock = Clock()
    snapshot_without_industry = ({
        code: {**row, "industry": None}
        for code, row in SNAPSHOT[0].items()
    }, "disclosed")
    provider = FakeProvider("primary", 10, payloads={
        "profile": PROFILE, "nav_history": NAV, "holdings": HOLDINGS,
        "stock_snapshot": snapshot_without_industry, "industry_allocation": INDUSTRY,
    })

    result = make_service(tmp_path, clock, [provider]).get_fund_analysis("000001")

    exposure = result["industry_exposure"]["data"]
    assert exposure["primary"] == [{"name": "制造业", "weight_pct": 80.0}]
    assert exposure["broad"] == [{"name": "其他", "weight_pct": 80.0}]
    assert exposure["identified_coverage_pct"] == 80.0
    assert exposure["unidentified_disclosed_pct"] == 0.0
    assert exposure["undisclosed_stock_pct"] == 0.0
    assert exposure["non_stock_pct"] == 20.0
    assert exposure["calculation_basis"] == "东方财富公开行业配置（覆盖基金全部股票资产）"


def test_portfolio_analysis_calculates_cost_value_overlap_and_date_warning(tmp_path):
    clock = Clock()
    provider = FakeProvider("primary", 10, payloads={
        "profile": PROFILE, "nav_history": NAV, "holdings": HOLDINGS,
        "stock_snapshot": SNAPSHOT, "industry_allocation": INDUSTRY,
    })
    service = make_service(tmp_path, clock, [provider])
    holdings = [
        {"code": "000001", "shares": 100, "avg_cost": 1.0, "buy_date": "2026-01-01", "notes": "", "custom_tag_ids": []},
        {"code": "000002", "shares": 200, "avg_cost": 1.0, "buy_date": "2026-01-01", "notes": "", "custom_tag_ids": []},
    ]
    result = service.get_portfolio_analysis(holdings)
    assert result["overview"]["fund_count"] == 2
    assert result["overview"]["total_cost"] == 300.0
    assert result["overview"]["market_value"] == 330.0
    assert result["overview"]["profit_loss"] == 30.0
    assert result["overview"]["return_rate"] == 10.0
    assert result["overlap"][0]["stock_code"] == "600000"
    assert result["industry_concentration"]["unknown_pct"] == 40.0
