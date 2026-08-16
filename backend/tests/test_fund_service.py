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
CLASSIFICATIONS = ({
    "classifications": {
        "600000": {
            "stock_code": "600000", "stock_name": "浦发银行",
            "primary_industry": "金融", "secondary_industry": "银行",
            "detail_industry": "股份制银行", "fine_industry": "股份制银行",
            "classification_standard": "申银万国行业分类标准", "classification_code": "008003",
            "classification_changed_at": "2026-06-30", "source_name": "巨潮资讯上市公司行业归属",
            "source_reference": "https://webapi.cninfo.com.cn/api/stock/p_stock2110",
        },
        "688001": {
            "stock_code": "688001", "stock_name": "测试芯片",
            "primary_industry": "电子", "secondary_industry": "半导体",
            "detail_industry": "半导体设备", "fine_industry": "半导体设备",
            "classification_standard": "申银万国行业分类标准", "classification_code": "008003",
            "classification_changed_at": "2026-06-30", "source_name": "巨潮资讯上市公司行业归属",
            "source_reference": "https://webapi.cninfo.com.cn/api/stock/p_stock2110",
        },
    },
    "failed_codes": [],
    "requested_codes": ["600000", "688001"],
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
        "stock_snapshot": SNAPSHOT, "stock_industry_classification": CLASSIFICATIONS,
        "industry_allocation": INDUSTRY,
    })

    result = make_service(tmp_path, clock, [provider]).get_fund_analysis("000001")

    exposure = result["industry_exposure"]["data"]
    assert exposure["lookthrough"]["primary"] == [
        {"name": "电子", "weight_pct": 30.0},
        {"name": "金融", "weight_pct": 30.0},
    ]
    assert exposure["lookthrough"]["secondary"] == [
        {"name": "半导体", "weight_pct": 30.0},
        {"name": "银行", "weight_pct": 30.0},
    ]
    assert exposure["lookthrough"]["disclosed_coverage_pct"] == 60.0
    assert exposure["lookthrough"]["identified_coverage_pct"] == 60.0
    assert exposure["lookthrough"]["unknown_pct"] == 0.0
    assert exposure["lookthrough"]["undisclosed_stock_pct"] == 20.0
    assert exposure["lookthrough"]["non_stock_pct"] == 20.0
    assert exposure["industry_chain_tags"] == [
        {
            "id": "semiconductor-equipment", "name": "半导体设备", "weight_pct": 30.0,
            "evidence_level": "disclosed_stock_classification", "source_name": "巨潮资讯上市公司行业归属",
        },
        {
            "id": "semiconductor", "name": "半导体", "weight_pct": 30.0,
            "evidence_level": "disclosed_stock_classification", "source_name": "巨潮资讯上市公司行业归属",
        },
    ]
    assert result["intraday_estimate"]["data"]["status"] == "estimated"


def test_analysis_separates_official_allocation_from_stock_lookthrough(tmp_path):
    clock = Clock()
    snapshot_without_industry = ({
        code: {**row, "industry": None}
        for code, row in SNAPSHOT[0].items()
    }, "disclosed")
    provider = FakeProvider("primary", 10, payloads={
        "profile": PROFILE, "nav_history": NAV, "holdings": HOLDINGS,
        "stock_snapshot": snapshot_without_industry,
        "stock_industry_classification": CLASSIFICATIONS, "industry_allocation": INDUSTRY,
    })

    result = make_service(tmp_path, clock, [provider]).get_fund_analysis("000001")

    exposure = result["industry_exposure"]["data"]
    assert exposure["official_allocation"] == {
        "exposure": [{
            "name": "制造业", "display_name": "制造业（待穿透）",
            "weight_pct": 80.0, "requires_lookthrough": True,
        }],
        "stock_exposure_pct": 80.0,
        "as_of_date": "2026-06-30",
        "source_name": "primary",
        "source_reference": "https://example.test/industry_allocation",
    }
    assert exposure["lookthrough"]["primary"] == [
        {"name": "电子", "weight_pct": 30.0},
        {"name": "金融", "weight_pct": 30.0},
    ]
    assert all(item["name"] != "其他" for item in exposure["lookthrough"]["primary"])
    assert exposure["lookthrough"]["identified_coverage_pct"] == 60.0
    assert exposure["lookthrough"]["undisclosed_stock_pct"] == 20.0
    assert exposure["lookthrough"]["non_stock_pct"] == 20.0
    assert result["data_quality"]["stock_industry_classification"]["data_type"] == "stock_industry_classification"


def test_analysis_distinguishes_other_from_unknown_constituents(tmp_path):
    clock = Clock()
    partial = ({
        **CLASSIFICATIONS[0],
        "classifications": {
            "600000": {**CLASSIFICATIONS[0]["classifications"]["600000"], "primary_industry": None},
        },
        "failed_codes": ["688001"],
    }, "disclosed")
    provider = FakeProvider("primary", 10, payloads={
        "profile": PROFILE, "nav_history": NAV, "holdings": HOLDINGS,
        "stock_snapshot": SNAPSHOT, "stock_industry_classification": partial,
        "industry_allocation": INDUSTRY,
    })

    exposure = make_service(tmp_path, clock, [provider]).get_fund_analysis("000001")["industry_exposure"]["data"]

    assert exposure["lookthrough"]["other_pct"] == 30.0
    assert exposure["lookthrough"]["unknown_pct"] == 30.0
    assert exposure["other_constituents"] == [{
        "stock_code": "600000", "stock_name": "浦发银行", "weight_pct": 30.0,
        "reason": "已取得行业记录，但缺少一级行业名称",
    }]
    assert exposure["unknown_constituents"][0] == {
        "stock_code": "688001", "stock_name": "测试芯片", "weight_pct": 30.0,
        "reason": "股票行业分类缺失或请求失败",
    }


def test_analysis_keeps_official_evidence_separate_when_lookthrough_is_unavailable(tmp_path):
    clock = Clock()
    provider = FakeProvider("primary", 10, payloads={
        "profile": PROFILE, "nav_history": NAV, "holdings": HOLDINGS,
        "stock_snapshot": SNAPSHOT, "industry_allocation": INDUSTRY,
    }, failures={"stock_industry_classification"})

    result = make_service(tmp_path, clock, [provider]).get_fund_analysis("000001")
    exposure = result["industry_exposure"]["data"]

    assert exposure["lookthrough"]["status"] == "unavailable"
    assert exposure["lookthrough"]["message"] == "股票行业穿透暂不可用；官方行业配置不代替穿透结果"
    assert exposure["lookthrough"]["primary"] == []
    assert exposure["official_allocation"]["exposure"][0]["display_name"] == "制造业（待穿透）"
    assert result["data_quality"]["stock_industry_classification"]["status"] == "error"


def test_portfolio_analysis_calculates_cost_value_overlap_and_date_warning(tmp_path):
    clock = Clock()
    provider = FakeProvider("primary", 10, payloads={
        "profile": PROFILE, "nav_history": NAV, "holdings": HOLDINGS,
        "stock_snapshot": SNAPSHOT, "stock_industry_classification": CLASSIFICATIONS,
        "industry_allocation": INDUSTRY,
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
    assert result["industry_concentration"]["unknown_pct"] == 0.0
    assert result["industry_concentration"]["undisclosed_stock_pct"] == 20.0
    assert result["industry_concentration"]["non_stock_pct"] == 20.0
    assert result["industry_concentration"]["primary"] == [
        {"name": "电子", "weight_pct": 30.0},
        {"name": "金融", "weight_pct": 30.0},
    ]


def test_quick_portfolio_revalues_inferred_shares_with_latest_official_nav(tmp_path):
    clock = Clock()
    provider = FakeProvider("primary", 10, payloads={
        "profile": PROFILE, "nav_history": NAV, "holdings": HOLDINGS,
        "stock_snapshot": SNAPSHOT, "stock_industry_classification": CLASSIFICATIONS,
        "industry_allocation": INDUSTRY,
    })
    service = make_service(tmp_path, clock, [provider])

    result = service.get_portfolio_analysis([{
        "code": "000001",
        "input_mode": "amount_pnl",
        "amount_snapshot": 1000,
        "cumulative_pnl_snapshot": 100,
        "snapshot_at": "2026-08-15T10:00:00+08:00",
        "shares": 800,
        "shares_source": "inferred",
        "basis_nav": 1.25,
        "basis_nav_date": "2026-08-14",
        "avg_cost": None,
        "avg_unit_cost": None,
        "buy_date": "",
        "notes": "",
        "custom_tag_ids": [],
    }])

    position = result["holdings"][0]["position"]
    assert position["user_amount_snapshot"] == 1000
    assert position["official_market_value"] == 880
    assert position["position_value"] == 880
    assert position["position_value_basis"] == "official_nav_from_inferred_shares"
    assert position["reference_total_cost"] == 900
    assert result["overview"]["total_holding_value"] == 880
    assert result["overview"]["market_value"] == 880
    assert result["holdings"][0]["weight_pct"] == 100
    assert result["overview"]["latest_nav_date"] == "2026-08-14"


def test_quick_portfolio_falls_back_to_snapshot_only_without_reliable_inferred_shares(tmp_path):
    clock = Clock()
    provider = FakeProvider("primary", 10, payloads={
        "profile": PROFILE, "nav_history": NAV, "holdings": HOLDINGS,
        "stock_snapshot": SNAPSHOT, "stock_industry_classification": CLASSIFICATIONS,
        "industry_allocation": INDUSTRY,
    })
    service = make_service(tmp_path, clock, [provider])
    holdings = [
        {
            "code": "000001", "input_mode": "amount_pnl", "amount_snapshot": 1000,
            "cumulative_pnl_snapshot": None, "snapshot_at": "2026-08-15T10:00:00+08:00",
            "shares": 800, "shares_source": "inferred", "avg_cost": None, "avg_unit_cost": None,
            "buy_date": "", "notes": "", "custom_tag_ids": [],
        },
        {
            "code": "000002", "input_mode": "amount_pnl", "amount_snapshot": 1200,
            "cumulative_pnl_snapshot": None, "snapshot_at": "2026-08-15T11:00:00+08:00",
            "shares": None, "shares_source": None, "avg_cost": None, "avg_unit_cost": None,
            "buy_date": "", "notes": "", "custom_tag_ids": [],
        },
    ]

    result = service.get_portfolio_analysis(holdings)

    assert result["overview"]["total_holding_value"] == 2080
    assert result["holdings"][0]["weight_pct"] == pytest.approx(42.3077, abs=1e-4)
    assert result["holdings"][1]["weight_pct"] == pytest.approx(57.6923, abs=1e-4)
    assert result["holdings"][1]["position"]["position_value_basis"] == "user_amount_snapshot"
    assert result["overview"]["profit_loss"] is None
    assert result["overview"]["return_rate"] is None
    assert result["overview"]["pnl_complete"] is False
