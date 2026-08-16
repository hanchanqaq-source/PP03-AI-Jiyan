from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from fund_data.models import ProviderResult
from fund_data.providers.base import BaseFundProvider, ProviderUnavailable

BEIJING = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


class EastmoneyDirectProvider(BaseFundProvider):
    name = "eastmoney-direct"
    priority = 10
    capabilities = {"search", "nav_history", "stock_snapshot"}

    def __init__(self, session: Any | None = None, timeout: float = 15):
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch(self, capability: str, **kwargs: Any) -> ProviderResult:
        try:
            if capability == "search":
                return self._search(str(kwargs.get("query") or ""))
            if capability == "nav_history":
                return self._nav_history(str(kwargs.get("code") or ""))
            if capability == "stock_snapshot":
                return self._stock_snapshot([str(code) for code in kwargs.get("codes") or []])
        except ProviderUnavailable:
            raise
        except Exception as error:
            raise ProviderUnavailable(f"东方财富 {capability} 请求失败：{type(error).__name__}: {error}") from error
        raise ProviderUnavailable(f"东方财富不支持能力：{capability}")

    def _search(self, query: str) -> ProviderResult:
        url = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
        response = self.session.get(url, params={"m": "1", "key": query}, headers={"User-Agent": UA}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if payload.get("ErrCode") not in (0, "0"):
            raise ProviderUnavailable(f"东方财富基金搜索返回错误：{payload.get('ErrMsg') or payload.get('ErrCode')}")
        funds: list[dict[str, Any]] = []
        for item in payload.get("Datas") or []:
            code = str(item.get("CODE") or item.get("_id") or "")
            if not re.fullmatch(r"\d{6}", code):
                continue
            base = item.get("FundBaseInfo") or {}
            managers = [name.strip() for name in re.split(r"[,、]", str(base.get("JJJL") or "")) if name.strip()]
            funds.append({
                "code": code,
                "name": item.get("NAME") or base.get("SHORTNAME") or code,
                "fund_type": base.get("FTYPE") or None,
                "latest_nav": _number(base.get("DWJZ")),
                "latest_nav_date": base.get("FSRQ") or None,
                "manager_names": managers,
                "management_company": base.get("JJGS") or None,
            })
        return ProviderResult(
            data=funds[:20], source_name="东方财富-天天基金", source_reference=url,
            data_type="fund_search", as_of_date=max((fund.get("latest_nav_date") or "" for fund in funds), default="") or None,
            status="disclosed",
        )

    @staticmethod
    def _extract_js(text: str, variable: str) -> Any:
        match = re.search(rf"var\s+{re.escape(variable)}\s*=\s*(.*?);", text, flags=re.DOTALL)
        if not match:
            raise ProviderUnavailable(f"东方财富净值脚本缺少 {variable}")
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise ProviderUnavailable(f"东方财富净值脚本 {variable} 结构异常") from error

    def _nav_history(self, code: str) -> ProviderResult:
        url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
        response = self.session.get(url, headers={"User-Agent": UA, "Referer": "https://fund.eastmoney.com/"}, timeout=self.timeout)
        response.raise_for_status()
        units = self._extract_js(response.text, "Data_netWorthTrend")
        cumulative = self._extract_js(response.text, "Data_ACWorthTrend")
        cumulative_by_date: dict[str, float | None] = {}
        for item in cumulative or []:
            if not isinstance(item, list) or len(item) < 2:
                continue
            nav_date = datetime.fromtimestamp(float(item[0]) / 1000, tz=BEIJING).date().isoformat()
            cumulative_by_date[nav_date] = _number(item[1])
        points: list[dict[str, Any]] = []
        for item in units or []:
            if not isinstance(item, dict) or item.get("x") is None:
                continue
            nav_date = datetime.fromtimestamp(float(item["x"]) / 1000, tz=BEIJING).date().isoformat()
            unit_nav = _number(item.get("y"))
            if unit_nav is None:
                continue
            points.append({
                "date": nav_date,
                "unit_nav": unit_nav,
                "cumulative_nav": cumulative_by_date.get(nav_date),
                "daily_change_pct": _number(item.get("equityReturn")),
            })
        points.sort(key=lambda item: item["date"])
        if not points:
            raise ProviderUnavailable("东方财富未返回可核验的官方净值序列")
        latest_point = points[-1]
        latest = {
            "unit_nav": latest_point["unit_nav"],
            "cumulative_nav": latest_point["cumulative_nav"],
            "nav_date": latest_point["date"],
        }
        return ProviderResult(
            data={"points": points, "latest": latest},
            source_name="东方财富-天天基金",
            source_reference=url,
            data_type="official_nav_history",
            as_of_date=latest["nav_date"],
            status="official",
            message="正式公布的基金净值；净值日期不代表盘中实时价格",
        )

    def _stock_snapshot(self, codes: list[str]) -> ProviderResult:
        if not codes:
            raise ProviderUnavailable("没有可查询的公开股票持仓")
        secids = ",".join(f"{1 if code.startswith(('5', '6', '9')) else 0}.{code}" for code in codes)
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        response = self.session.get(
            url,
            params={"secids": secids, "fields": "f12,f14,f2,f3,f100", "fltt": "2", "invt": "2"},
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        rows = ((response.json().get("data") or {}).get("diff") or [])
        if isinstance(rows, dict):
            rows = list(rows.values())
        output = {
            str(row.get("f12")): {
                "stock_code": str(row.get("f12")),
                "stock_name": row.get("f14") or str(row.get("f12")),
                "price": _number(row.get("f2")),
                "change_pct": _number(row.get("f3")),
                "industry": row.get("f100") or None,
            }
            for row in rows if re.fullmatch(r"\d{6}", str(row.get("f12") or ""))
        }
        if not output:
            raise ProviderUnavailable("东方财富未返回持仓股票行情或行业")
        return ProviderResult(
            data=output, source_name="东方财富证券行情", source_reference=url,
            data_type="stock_snapshot", as_of_date=datetime.now(BEIJING).date().isoformat(), status="disclosed",
        )

