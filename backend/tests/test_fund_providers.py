from __future__ import annotations

import threading
from datetime import datetime, timezone

import pandas as pd
import pytest
import requests

from fund_data.providers.akshare_provider import AkshareEastmoneyProvider
from fund_data.providers.cninfo_industry import CninfoIndustryProvider
from fund_data.providers.base import ProviderUnavailable
from fund_data.providers.eastmoney_direct import EastmoneyDirectProvider
from fund_data.providers.tencent_quote import TencentQuoteProvider


class FakeResponse:
    def __init__(self, *, payload=None, text=""):
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def get(self, url, **_kwargs):
        if "FundSearchAPI" in url:
            return FakeResponse(payload={
                "ErrCode": 0,
                "Datas": [
                    {
                        "CODE": "000001",
                        "NAME": "华夏成长混合",
                        "FundBaseInfo": {
                            "FTYPE": "混合型-灵活",
                            "DWJZ": 1.348,
                            "FSRQ": "2026-08-14",
                            "JJGS": "华夏基金",
                            "JJJL": "郑晓辉,刘睿聪",
                        },
                    },
                    {"CODE": "000001", "NAME": "平安银行", "SecurityTypeName": "深A"},
                    {"CODE": "000001", "NAME": "上证指数", "SecurityTypeName": "指数"},
                ],
            })
        if "pingzhongdata" in url:
            return FakeResponse(text=(
                'var fS_name = "华夏成长混合";'
                'var fS_code = "000001";'
                'var Data_netWorthTrend = '
                '[{"x":1767225600000,"y":1.0,"equityReturn":0.0,"unitMoney":""},'
                '{"x":1767312000000,"y":1.1,"equityReturn":10.0,"unitMoney":""}];'
                'var Data_ACWorthTrend = [[1767225600000,1.0],[1767312000000,1.2]];'
            ))
        if "ulist.np" in url:
            return FakeResponse(payload={"data": {"diff": [{
                "f12": "600000", "f14": "浦发银行", "f2": 10.5, "f3": 1.2, "f100": "银行",
            }]}})
        raise AssertionError(url)


def test_direct_provider_normalizes_search_nav_and_stock_snapshot():
    provider = EastmoneyDirectProvider(session=FakeSession())

    search = provider.fetch("search", query="000001")
    assert search.data == [{
        "code": "000001",
        "name": "华夏成长混合",
        "fund_type": "混合型-灵活",
        "latest_nav": 1.348,
        "latest_nav_date": "2026-08-14",
        "manager_names": ["郑晓辉", "刘睿聪"],
        "management_company": "华夏基金",
    }]
    assert search.status == "disclosed"

    history = provider.fetch("nav_history", code="000001")
    assert history.data["points"][-1] == {
        "date": "2026-01-02", "unit_nav": 1.1, "cumulative_nav": 1.2, "daily_change_pct": 10.0,
    }
    assert history.data["latest"] == {
        "unit_nav": 1.1, "cumulative_nav": 1.2, "nav_date": "2026-01-02",
    }
    assert history.status == "official"

    snapshot = provider.fetch("stock_snapshot", codes=["600000"])
    assert snapshot.data["600000"] == {
        "stock_code": "600000", "stock_name": "浦发银行", "price": 10.5,
        "change_pct": 1.2, "industry": "银行",
    }


class FakeAkshare:
    @staticmethod
    def fund_name_em():
        return pd.DataFrame([["000001", "HXCZHH", "华夏成长混合", "混合型-灵活", "HUAXIA"]], columns=[
            "基金代码", "拼音缩写", "基金简称", "基金类型", "拼音全称",
        ])

    @staticmethod
    def fund_overview_em(_code):
        return pd.DataFrame([{
            "基金全称": "华夏成长证券投资基金",
            "基金简称": "华夏成长混合",
            "基金代码": "000001（前端）、000002（后端）",
            "基金类型": "混合型-灵活",
            "成立日期/规模": "2001年12月18日 / 32.368亿份",
            "净资产规模": "39.38亿元（截止至：2026年06月30日）",
            "基金管理人": "华夏基金",
            "基金经理人": "郑晓辉、刘睿聪",
        }])

    @staticmethod
    def fund_open_fund_info_em(symbol, indicator):
        if indicator == "单位净值走势":
            return pd.DataFrame([
                ["2026-08-13", 1.3, -1.0], ["2026-08-14", 1.348, 0.37],
            ], columns=["净值日期", "单位净值", "日增长率"])
        return pd.DataFrame([
            ["2026-08-13", 3.873], ["2026-08-14", 3.921],
        ], columns=["净值日期", "累计净值"])

    @staticmethod
    def fund_portfolio_hold_em(symbol, date):
        return pd.DataFrame([
            [1, "600000", "浦发银行", 8.0, 10.0, 100.0, "2026年1季度股票投资明细"],
            [2, "000001", "平安银行", 12.0, 20.0, 200.0, "2026年2季度股票投资明细"],
            [3, "600000", "浦发银行", 18.0, 30.0, 300.0, "2026年2季度股票投资明细"],
        ], columns=["序号", "股票代码", "股票名称", "占净值比例", "持股数", "持仓市值", "季度"])

    @staticmethod
    def fund_portfolio_industry_allocation_em(symbol, date):
        return pd.DataFrame([
            [1, "金融业", 40.0, 1000.0, "2026-03-31"],
            [2, "金融业", 60.0, 1200.0, "2026-06-30"],
        ], columns=["序号", "行业类别", "占净值比例", "市值", "截止时间"])


def test_akshare_provider_uses_latest_disclosed_quarter_and_scale_date():
    provider = AkshareEastmoneyProvider(ak_module=FakeAkshare(), now=lambda: datetime(2026, 8, 16, tzinfo=timezone.utc))

    profile = provider.fetch("profile", code="000001")
    assert profile.data["scale"] == 39.38
    assert profile.data["scale_date"] == "2026-06-30"
    assert profile.data["manager_names"] == ["郑晓辉", "刘睿聪"]

    holdings = provider.fetch("holdings", code="000001")
    assert holdings.data["report_period"] == "2026-Q2"
    assert holdings.data["disclosure_date"] == "2026-06-30"
    assert holdings.data["top10_coverage_pct"] == 30.0
    assert [item["stock_code"] for item in holdings.data["holdings"]] == ["000001", "600000"]

    industry = provider.fetch("industry_allocation", code="000001")
    assert industry.data == {
        "as_of_date": "2026-06-30",
        "stock_exposure_pct": 60.0,
        "industries": [{"name": "金融业", "weight_pct": 60.0, "market_value_10k": 1200.0}],
    }


def test_akshare_provider_applies_a_request_timeout(monkeypatch):
    seen = {}

    class TimeoutAwareAkshare(FakeAkshare):
        @staticmethod
        def fund_overview_em(code):
            requests.get("https://example.test/profile")
            return FakeAkshare.fund_overview_em(code)

    class Response:
        def raise_for_status(self):
            return None

    def fake_request(_session, _method, _url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return Response()

    monkeypatch.setattr(requests.sessions.Session, "request", fake_request)
    provider = AkshareEastmoneyProvider(ak_module=TimeoutAwareAkshare(), request_timeout=15)

    provider.fetch("profile", code="000001")

    assert seen["timeout"] == 15


def test_tencent_provider_normalizes_quote_fallback_without_inventing_industry():
    class FakeAstock:
        @staticmethod
        def tencent_quote(codes):
            assert codes == ["600000"]
            return {"600000": {"name": "浦发银行", "price": 10.2, "change_pct": -0.5}}

    result = TencentQuoteProvider(astock_module=FakeAstock()).fetch("stock_snapshot", codes=["600000"])

    assert result.data == {"600000": {
        "stock_code": "600000", "stock_name": "浦发银行", "price": 10.2,
        "change_pct": -0.5, "industry": None,
    }}
    assert result.source_name == "腾讯证券行情"


def cninfo_row(
    code: str,
    standard_code: str,
    standard: str,
    primary: str | None,
    secondary: str | None,
    detail: str | None,
    fine: str | None,
    changed_at: str,
) -> dict:
    return {
        "SECCODE": code,
        "SECNAME": f"证券{code}",
        "VARYDATE": changed_at,
        "F001V": standard_code,
        "F002V": standard,
        "F003V": f"industry-{code}",
        "F004V": primary,
        "F005V": secondary,
        "F006V": detail,
        "F007V": fine,
    }


class FakeTokenFactory:
    def __init__(self, tokens: list[str]):
        self.tokens = iter(tokens)
        self.thread_ids: list[int] = []

    def __call__(self) -> str:
        self.thread_ids.append(threading.get_ident())
        return next(self.tokens)


class FakeCninfoPost:
    def __init__(self, payloads: dict[str, list[dict] | Exception]):
        self.payloads = payloads

    def __call__(self, _url: str, *, params: dict, headers: dict, timeout: float):
        assert headers["Accept-Enckey"]
        assert timeout == 15
        payload = self.payloads[params["scode"]]
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload={"records": payload})


def test_cninfo_provider_prefers_current_sw_latest_record_and_keeps_partial_failures():
    token_factory = FakeTokenFactory(["token-a", "token-b"])
    provider = CninfoIndustryProvider(
        token_factory=token_factory,
        post=FakeCninfoPost({
            "300308": [
                cninfo_row("300308", "008003", "申银万国行业分类标准", "电子", "半导体", "旧分类", "旧分类", "2025-12-31"),
                cninfo_row("300308", "008002", "巨潮行业分类标准", "信息技术", "通信", "通信设备", "光模块", "2026-07-01"),
                cninfo_row("300308", "008003", "申银万国行业分类标准", "电子", "半导体", "半导体设备", "半导体设备", "2026-06-30"),
            ],
            "688256": RuntimeError("temporary upstream failure"),
        }),
        max_workers=2,
    )

    result = provider.fetch("stock_industry_classification", codes=["688256", "300308", "300308"])

    assert result.data["requested_codes"] == ["300308", "688256"]
    assert result.data["failed_codes"] == ["688256"]
    assert result.data["classifications"]["300308"] == {
        "stock_code": "300308",
        "stock_name": "证券300308",
        "primary_industry": "电子",
        "secondary_industry": "半导体",
        "detail_industry": "半导体设备",
        "fine_industry": "半导体设备",
        "classification_standard": "申银万国行业分类标准",
        "classification_code": "008003",
        "classification_industry_code": "industry-300308",
        "classification_changed_at": "2026-06-30",
        "source_name": "巨潮资讯上市公司行业归属",
        "source_reference": "https://webapi.cninfo.com.cn/api/stock/p_stock2110",
    }
    assert token_factory.thread_ids == [threading.get_ident(), threading.get_ident()]


def test_cninfo_provider_reports_total_failure_instead_of_inventing_classification():
    provider = CninfoIndustryProvider(
        token_factory=FakeTokenFactory(["token-a"]),
        post=FakeCninfoPost({"300308": RuntimeError("offline")}),
        max_workers=1,
    )

    with pytest.raises(ProviderUnavailable, match="未返回任何可核验股票记录"):
        provider.fetch("stock_industry_classification", codes=["300308"])


def test_cninfo_provider_rejects_unsupported_capability():
    provider = CninfoIndustryProvider(token_factory=FakeTokenFactory([]), post=FakeCninfoPost({}))

    with pytest.raises(ProviderUnavailable, match="不支持能力"):
        provider.fetch("stock_snapshot", codes=["300308"])
