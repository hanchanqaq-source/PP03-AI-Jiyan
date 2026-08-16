from __future__ import annotations

from typing import Any

from fund_data.models import ProviderResult
from fund_data.providers.base import BaseFundProvider, ProviderUnavailable


class TencentQuoteProvider(BaseFundProvider):
    name = "tencent-quote"
    priority = 15
    capabilities = {"stock_snapshot"}

    def __init__(self, astock_module: Any | None = None):
        if astock_module is None:
            import astock as astock_module
        self.astock = astock_module

    def fetch(self, capability: str, **kwargs: Any) -> ProviderResult:
        if capability != "stock_snapshot":
            raise ProviderUnavailable(f"腾讯行情不支持能力：{capability}")
        codes = [str(code) for code in kwargs.get("codes") or []]
        try:
            raw = self.astock.tencent_quote(codes)
        except Exception as error:
            raise ProviderUnavailable(f"腾讯批量行情失败：{type(error).__name__}: {error}") from error
        data = {
            code: {
                "stock_code": code,
                "stock_name": item.get("name") or code,
                "price": item.get("price") if isinstance(item.get("price"), (int, float)) else None,
                "change_pct": item.get("change_pct") if isinstance(item.get("change_pct"), (int, float)) else None,
                "industry": None,
            }
            for code, item in raw.items() if code in codes
        }
        if not data:
            raise ProviderUnavailable("腾讯未返回持仓股票行情")
        return ProviderResult(
            data=data,
            source_name="腾讯证券行情",
            source_reference="https://qt.gtimg.cn/",
            data_type="stock_snapshot",
            as_of_date=None,
            status="disclosed",
            message="备用行情源不提供行业字段；行业暴露可能部分不可识别",
        )
