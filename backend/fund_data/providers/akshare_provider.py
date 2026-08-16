from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import requests

from fund_data.models import ProviderResult
from fund_data.providers.base import BaseFundProvider, ProviderUnavailable

BEIJING = timezone(timedelta(hours=8))
_REQUEST_PATCH_LOCK = threading.RLock()


@contextmanager
def _bounded_requests(timeout: float):
    """AKShare omits timeouts in these adapters; add one at the requests boundary."""
    with _REQUEST_PATCH_LOCK:
        original = requests.sessions.Session.request

        def bounded(session, method, url, **kwargs):
            kwargs.setdefault("timeout", timeout)
            return original(session, method, url, **kwargs)

        requests.sessions.Session.request = bounded
        try:
            yield
        finally:
            requests.sessions.Session.request = original


def _akshare():
    import akshare as ak
    return ak


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_date(value: Any) -> str | None:
    text = str(value or "")
    match = re.search(r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})", text)
    if not match:
        return None
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()


def _split_names(value: Any) -> list[str]:
    return [name.strip() for name in re.split(r"[,，、\s]+", str(value or "")) if name.strip()]


class AkshareEastmoneyProvider(BaseFundProvider):
    name = "akshare-eastmoney"
    priority = 20
    capabilities = {"search", "profile", "nav_history", "holdings", "industry_allocation"}

    def __init__(
        self,
        ak_module: Any | None = None,
        now: Callable[[], datetime] | None = None,
        request_timeout: float = 15,
    ):
        self.ak = ak_module or _akshare()
        self._now = now or (lambda: datetime.now(BEIJING))
        self.request_timeout = request_timeout

    def fetch(self, capability: str, **kwargs: Any) -> ProviderResult:
        try:
            with _bounded_requests(self.request_timeout):
                if capability == "search":
                    return self._search(str(kwargs.get("query") or ""))
                if capability == "profile":
                    return self._profile(str(kwargs.get("code") or ""))
                if capability == "nav_history":
                    return self._nav_history(str(kwargs.get("code") or ""))
                if capability == "holdings":
                    return self._holdings(str(kwargs.get("code") or ""))
                if capability == "industry_allocation":
                    return self._industry_allocation(str(kwargs.get("code") or ""))
        except ProviderUnavailable:
            raise
        except Exception as error:
            raise ProviderUnavailable(f"AKShare 东方财富 {capability} 失败：{type(error).__name__}: {error}") from error
        raise ProviderUnavailable(f"AKShare 东方财富不支持能力：{capability}")

    def _search(self, query: str) -> ProviderResult:
        frame = self.ak.fund_name_em()
        if frame is None or frame.empty:
            raise ProviderUnavailable("AKShare 基金目录为空")
        needle = query.strip().lower()
        matches: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            code, short, name, fund_type, full = list(row.iloc[:5])
            haystack = " ".join(str(value) for value in (code, short, name, full)).lower()
            if needle not in haystack:
                continue
            matches.append({
                "code": str(code).zfill(6), "name": str(name), "fund_type": str(fund_type),
                "latest_nav": None, "latest_nav_date": None, "manager_names": [], "management_company": None,
            })
            if len(matches) >= 20:
                break
        return ProviderResult(
            data=matches, source_name="AKShare / 东方财富基金目录",
            source_reference="https://fund.eastmoney.com/js/fundcode_search.js",
            data_type="fund_search", as_of_date=None, status="disclosed",
        )

    def _profile(self, code: str) -> ProviderResult:
        frame = self.ak.fund_overview_em(code)
        if frame is None or frame.empty:
            raise ProviderUnavailable("东方财富基金档案未返回基本资料")
        row = frame.iloc[0].to_dict()
        scale_text = str(row.get("净资产规模") or "")
        scale_match = re.search(r"([\d.]+)\s*亿元", scale_text)
        scale_date = _iso_date(scale_text)
        established = _iso_date(row.get("成立日期/规模"))
        data = {
            "code": code,
            "name": row.get("基金简称") or row.get("基金全称") or code,
            "full_name": row.get("基金全称") or None,
            "fund_type": row.get("基金类型") or None,
            "established_date": established,
            "scale": float(scale_match.group(1)) if scale_match else None,
            "scale_unit": "亿元" if scale_match else None,
            "scale_date": scale_date,
            "manager_names": _split_names(row.get("基金经理人")),
            "management_company": row.get("基金管理人") or None,
            "custodian": row.get("基金托管人") or None,
            "risk_level": None,
            "risk_note": "产品风险等级请以基金法律文件和销售机构适当性材料为准",
        }
        return ProviderResult(
            data=data, source_name="AKShare / 东方财富基金档案",
            source_reference=f"https://fundf10.eastmoney.com/jbgk_{code}.html",
            data_type="fund_profile", as_of_date=scale_date, status="disclosed",
        )

    def _nav_history(self, code: str) -> ProviderResult:
        unit = self.ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        cumulative = self.ak.fund_open_fund_info_em(symbol=code, indicator="累计净值走势")
        if unit is None or unit.empty:
            raise ProviderUnavailable("AKShare 未返回官方净值序列")
        cumulative_by_date = {
            str(row.iloc[0])[:10]: _number(row.iloc[1]) for _, row in cumulative.iterrows()
        } if cumulative is not None and not cumulative.empty else {}
        points = []
        for _, row in unit.iterrows():
            nav_date = str(row.iloc[0])[:10]
            nav = _number(row.iloc[1])
            if nav is None:
                continue
            points.append({
                "date": nav_date, "unit_nav": nav, "cumulative_nav": cumulative_by_date.get(nav_date),
                "daily_change_pct": _number(row.iloc[2]) if len(row) > 2 else None,
            })
        points.sort(key=lambda item: item["date"])
        if not points:
            raise ProviderUnavailable("AKShare 官方净值序列没有有效数值")
        latest_point = points[-1]
        latest = {"unit_nav": latest_point["unit_nav"], "cumulative_nav": latest_point["cumulative_nav"], "nav_date": latest_point["date"]}
        return ProviderResult(
            data={"points": points, "latest": latest}, source_name="AKShare / 东方财富-天天基金",
            source_reference=f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
            data_type="official_nav_history", as_of_date=latest["nav_date"], status="official",
            message="正式公布的基金净值；净值日期不代表盘中实时价格",
        )

    @staticmethod
    def _quarter(value: Any) -> tuple[int, int] | None:
        match = re.search(r"(\d{4})年([1-4])季度", str(value or ""))
        return (int(match.group(1)), int(match.group(2))) if match else None

    def _holdings(self, code: str) -> ProviderResult:
        frame = self.ak.fund_portfolio_hold_em(symbol=code, date=str(self._now().year))
        if frame is None or frame.empty:
            raise ProviderUnavailable("东方财富未返回公开股票持仓")
        quarters = [self._quarter(value) for value in frame["季度"].tolist()]
        valid = [quarter for quarter in quarters if quarter]
        if not valid:
            raise ProviderUnavailable("公开股票持仓缺少可识别报告期")
        latest_quarter = max(valid)
        latest_label = f"{latest_quarter[0]}年{latest_quarter[1]}季度"
        selected = frame[frame["季度"].astype(str).str.contains(latest_label, regex=False)].copy()
        selected = selected.sort_values("序号").head(10)
        holdings = [{
            "stock_code": str(row.get("股票代码") or "").zfill(6),
            "stock_name": row.get("股票名称") or str(row.get("股票代码") or ""),
            "weight_pct": _number(row.get("占净值比例")) or 0.0,
            "shares_10k": _number(row.get("持股数")),
            "market_value_10k": _number(row.get("持仓市值")),
        } for _, row in selected.iterrows()]
        month_day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[latest_quarter[1]]
        disclosure_date = date(latest_quarter[0], *month_day).isoformat()
        return ProviderResult(
            data={
                "report_period": f"{latest_quarter[0]}-Q{latest_quarter[1]}",
                "disclosure_date": disclosure_date,
                "public_date": None,
                "is_top_ten": True,
                "top10_coverage_pct": round(sum(item["weight_pct"] for item in holdings), 4),
                "holdings": holdings,
            },
            source_name="AKShare / 东方财富基金档案",
            source_reference=f"https://fundf10.eastmoney.com/ccmx_{code}.html",
            data_type="disclosed_holdings", as_of_date=disclosure_date, status="disclosed",
            message="基金持仓来自定期报告披露，不代表基金当前实时持仓；公开日期字段暂缺",
        )

    def _industry_allocation(self, code: str) -> ProviderResult:
        frame = self.ak.fund_portfolio_industry_allocation_em(symbol=code, date=str(self._now().year))
        if frame is None or frame.empty:
            raise ProviderUnavailable("东方财富未返回公开行业配置")
        latest = max(str(value)[:10] for value in frame["截止时间"].tolist())
        selected = frame[frame["截止时间"].astype(str).str.slice(0, 10) == latest]
        industries = [{
            "name": row.get("行业类别") or "未命名行业",
            "weight_pct": _number(row.get("占净值比例")) or 0.0,
            "market_value_10k": _number(row.get("市值")),
        } for _, row in selected.iterrows()]
        return ProviderResult(
            data={"as_of_date": latest, "stock_exposure_pct": round(sum(item["weight_pct"] for item in industries), 4), "industries": industries},
            source_name="AKShare / 东方财富基金档案",
            source_reference=f"https://fundf10.eastmoney.com/hytz_{code}.html",
            data_type="disclosed_industry_allocation", as_of_date=latest, status="disclosed",
        )


class AkshareDanjuanProvider(BaseFundProvider):
    name = "akshare-danjuan"
    priority = 30
    capabilities = {"profile"}

    def __init__(self, ak_module: Any | None = None, request_timeout: float = 15):
        self.ak = ak_module or _akshare()
        self.request_timeout = request_timeout

    def fetch(self, capability: str, **kwargs: Any) -> ProviderResult:
        if capability != "profile":
            raise ProviderUnavailable(f"蛋卷基金不支持能力：{capability}")
        code = str(kwargs.get("code") or "")
        try:
            with _bounded_requests(self.request_timeout):
                frame = self.ak.fund_individual_basic_info_xq(symbol=code, timeout=self.request_timeout)
        except Exception as error:
            raise ProviderUnavailable(f"蛋卷基金资料失败：{type(error).__name__}: {error}") from error
        if frame is None or frame.empty:
            raise ProviderUnavailable("蛋卷基金未返回基本资料")
        values = {str(row.iloc[0]): row.iloc[1] for _, row in frame.iterrows()}
        scale_text = str(values.get("最新规模") or "")
        scale_match = re.search(r"([\d.]+)\s*亿", scale_text)
        data = {
            "code": code,
            "name": values.get("基金名称") or code,
            "full_name": values.get("基金全称") or None,
            "fund_type": values.get("基金类型") or None,
            "established_date": _iso_date(values.get("成立时间")),
            "scale": float(scale_match.group(1)) if scale_match else None,
            "scale_unit": "亿元" if scale_match else None,
            "scale_date": None,
            "manager_names": _split_names(values.get("基金经理")),
            "management_company": values.get("基金公司") or None,
            "custodian": values.get("托管银行") or None,
            "risk_level": None,
            "risk_note": "产品风险等级请以基金法律文件和销售机构适当性材料为准",
        }
        return ProviderResult(
            data=data, source_name="AKShare / 蛋卷基金",
            source_reference=f"https://danjuanfunds.com/djapi/fund/{code}",
            data_type="fund_profile", as_of_date=None, status="disclosed",
            message="备用基金资料源；规模日期缺失时不推断",
        )
