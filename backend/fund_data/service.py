from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fund_data.cache import FundCache
from fund_data.calculations import (
    calculate_industry_concentration,
    calculate_intraday_estimate,
    calculate_overlap,
    calculate_performance,
    calculate_position,
)
from fund_data.models import DataMeta, ProviderResult
from fund_data.providers import AkshareDanjuanProvider, AkshareEastmoneyProvider, EastmoneyDirectProvider, TencentQuoteProvider
from fund_data.providers.base import ProviderUnavailable

BEIJING = timezone(timedelta(hours=8))
TTL = {
    "search": 24 * 3600,
    "profile": 24 * 3600,
    "nav_history": 12 * 3600,
    "holdings": 24 * 3600,
    "industry_allocation": 24 * 3600,
    "stock_snapshot": 60,
}


def _default_data_dir() -> str:
    return os.environ.get("VR_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".vibe-research")


def _broad_industry(industry: str) -> str:
    rules = [
        ("科技", ("半导体", "电子", "计算机", "通信", "软件", "互联网")),
        ("医疗", ("医药", "医疗", "生物")),
        ("消费", ("食品", "饮料", "家电", "消费", "零售", "纺织", "美容", "汽车")),
        ("金融", ("银行", "保险", "证券", "非银", "金融")),
        ("新能源", ("电池", "光伏", "新能源", "电力设备", "风电")),
        ("周期资源", ("有色", "煤炭", "石油", "钢铁", "化工", "建材", "采掘")),
    ]
    for broad, keywords in rules:
        if any(keyword in industry for keyword in keywords):
            return broad
    return "其他"


def _system_tags(industry: str, stock_name: str) -> list[tuple[str, str]]:
    text = f"{industry} {stock_name}"
    rules = [
        ("storage", "存储", ("存储", "DRAM", "NAND", "HBM")),
        ("semiconductor-equipment", "半导体设备", ("半导体设备",)),
        ("consumer-electronics", "消费电子", ("消费电子",)),
        ("robotics", "机器人", ("机器人", "减速器", "伺服")),
        ("semiconductor", "半导体", ("半导体", "芯片", "集成电路")),
    ]
    return [(tag_id, name) for tag_id, name, keywords in rules if any(keyword in text for keyword in keywords)]


class FundDataService:
    def __init__(
        self,
        providers: list[Any] | None = None,
        cache: FundCache | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self._now = now or (lambda: datetime.now(BEIJING))
        self.providers = sorted(
            providers or [EastmoneyDirectProvider(), TencentQuoteProvider(), AkshareEastmoneyProvider(), AkshareDanjuanProvider()],
            key=lambda provider: getattr(provider, "priority", 100),
        )
        self.cache = cache or FundCache(Path(_default_data_dir()) / "fund-cache" / "v1", now=self._now)

    def _section_from_cache(self, hit, stale: bool) -> dict[str, Any]:
        record = hit.payload
        stored = record["provider_result"]
        original_status = stored["status"]
        meta = DataMeta(
            source_name=stored["source_name"], source_reference=stored["source_reference"],
            data_type=stored["data_type"], as_of_date=stored.get("as_of_date"), fetched_at=hit.fetched_at,
            status="stale" if stale else original_status, is_cached=True, is_stale=stale,
            provider=record["provider"], fallback_used=bool(record.get("fallback_used")),
            message=("正在使用上次成功数据；数据已过期。上次成功更新时间：" + hit.fetched_at) if stale else stored.get("message", ""),
            original_status=original_status if stale else None,
        )
        return {"data": stored["data"], "meta": meta.to_dict()}

    def _fetch(self, capability: str, *, cache_key: str, force_refresh: bool = False, **kwargs: Any) -> dict[str, Any]:
        key = f"{capability}:{cache_key}"
        if force_refresh:
            self.cache.invalidate(key)
        else:
            fresh = self.cache.get(key)
            if fresh:
                return self._section_from_cache(fresh, stale=False)
        stale = self.cache.get(key, allow_stale=True)
        errors: list[str] = []
        empty_result: ProviderResult | None = None
        capable = [provider for provider in self.providers if capability in getattr(provider, "capabilities", set())]
        for index, provider in enumerate(capable):
            try:
                result: ProviderResult = provider.fetch(capability, **kwargs)
                if result.data is None or result.data == [] or result.data == {}:
                    empty_result = result
                    continue
                fallback_used = index > 0
                hit = self.cache.set(key, {
                    "provider": provider.name,
                    "fallback_used": fallback_used,
                    "provider_result": asdict(result),
                }, TTL[capability])
                meta = DataMeta(
                    source_name=result.source_name, source_reference=result.source_reference,
                    data_type=result.data_type, as_of_date=result.as_of_date, fetched_at=hit.fetched_at,
                    status=result.status, provider=provider.name, fallback_used=fallback_used, message=result.message,
                )
                return {"data": result.data, "meta": meta.to_dict()}
            except ProviderUnavailable as error:
                errors.append(str(error))
            except Exception as error:  # provider boundary: a malformed response must not break sibling modules
                errors.append(f"{provider.name}: {type(error).__name__}: {error}")
        if stale:
            return self._section_from_cache(stale, stale=True)
        now = self._now().isoformat()
        if empty_result is not None and not errors:
            meta = DataMeta(
                source_name=empty_result.source_name, source_reference=empty_result.source_reference,
                data_type=empty_result.data_type, as_of_date=empty_result.as_of_date, fetched_at=now,
                status="unavailable", provider="", message="没有找到可核验数据",
            )
            return {"data": None, "meta": meta.to_dict()}
        meta = DataMeta(
            source_name="", source_reference="", data_type=capability, as_of_date=None, fetched_at=now,
            status="error" if errors else "unavailable", provider="",
            message="；".join(errors) if errors else "当前没有 Provider 支持此能力",
        )
        return {"data": None, "meta": meta.to_dict()}

    def _unavailable(self, data_type: str, message: str) -> dict[str, Any]:
        return {"data": None, "meta": DataMeta(
            source_name="", source_reference="", data_type=data_type, as_of_date=None,
            fetched_at=self._now().isoformat(), status="unavailable", message=message,
        ).to_dict()}

    def search_funds(self, query: str, force_refresh: bool = False) -> dict[str, Any]:
        query = query.strip()
        if not query:
            return self._unavailable("fund_search", "请输入基金代码或名称")
        return self._fetch("search", cache_key=query.lower(), force_refresh=force_refresh, query=query)

    def _industry_exposure(self, holdings: dict[str, Any], snapshots: dict[str, Any], allocation: dict[str, Any] | None) -> dict[str, Any]:
        secondary: dict[str, float] = {}
        broad: dict[str, float] = {}
        tags: dict[tuple[str, str], float] = {}
        identified = 0.0
        for holding in holdings.get("holdings") or []:
            snapshot = snapshots.get(holding.get("stock_code")) or {}
            industry = str(snapshot.get("industry") or "").strip()
            weight = float(holding.get("weight_pct") or 0)
            if not industry:
                continue
            identified += weight
            secondary[industry] = secondary.get(industry, 0) + weight
            broad_name = _broad_industry(industry)
            broad[broad_name] = broad.get(broad_name, 0) + weight
            for tag in _system_tags(industry, str(holding.get("stock_name") or "")):
                tags[tag] = tags.get(tag, 0) + weight
        allocation_rows = (allocation or {}).get("industries") or []
        using_allocation = identified <= 0 and bool(allocation_rows)
        if using_allocation:
            for item in allocation_rows:
                industry = str(item.get("name") or "").strip()
                weight = float(item.get("weight_pct") or 0)
                if not industry or weight <= 0:
                    continue
                identified += weight
                secondary[industry] = secondary.get(industry, 0) + weight
                broad_name = _broad_industry(industry)
                broad[broad_name] = broad.get(broad_name, 0) + weight
                for tag in _system_tags(industry, ""):
                    tags[tag] = tags.get(tag, 0) + weight
        top10 = float(holdings.get("top10_coverage_pct") or 0)
        stock_exposure = float((allocation or {}).get("stock_exposure_pct") or top10)
        non_stock = max(0.0, 100 - stock_exposure)
        undisclosed_stock = max(0.0, stock_exposure - identified) if using_allocation else max(0.0, stock_exposure - top10)
        unidentified = 0.0 if using_allocation else max(0.0, top10 - identified)
        rows = lambda values: [
            {"name": name, "weight_pct": round(weight, 4)}
            for name, weight in sorted(values.items(), key=lambda item: (-item[1], item[0]))
        ]
        return {
            "primary": rows(secondary),
            "secondary": rows(secondary),
            "broad": rows(broad),
            "system_tags": [
                {"id": tag_id, "name": name, "weight_pct": round(weight, 4)}
                for (tag_id, name), weight in sorted(tags.items(), key=lambda item: (-item[1], item[0][0]))
            ],
            "identified_coverage_pct": round(identified, 4),
            "unidentified_disclosed_pct": round(unidentified, 4),
            "undisclosed_stock_pct": round(undisclosed_stock, 4),
            "non_stock_pct": round(non_stock, 4),
            "calculation_basis": "东方财富公开行业配置（覆盖基金全部股票资产）" if using_allocation else "最新公开前十大持仓比例 × 东方财富证券行业分类",
            "industry_classification_source": "东方财富基金行业配置" if using_allocation else "东方财富证券行情 f100 行业字段",
        }

    def get_fund_analysis(self, code: str, force_refresh: bool = False) -> dict[str, Any]:
        profile = self._fetch("profile", cache_key=code, force_refresh=force_refresh, code=code)
        nav_history = self._fetch("nav_history", cache_key=code, force_refresh=force_refresh, code=code)
        fund_type = str((profile.get("data") or {}).get("fund_type") or "")

        if "货币" in fund_type:
            holdings = self._unavailable("disclosed_holdings", "货币基金不适用股票前十大持仓")
        elif "FOF" in fund_type.upper():
            holdings = self._unavailable("disclosed_holdings", "FOF 主要资产为其他基金，当前不进行股票穿透")
        else:
            holdings = self._fetch("holdings", cache_key=code, force_refresh=force_refresh, code=code)

        latest_data = (nav_history.get("data") or {}).get("latest")
        latest_nav = {
            "data": latest_data,
            "meta": {**nav_history["meta"], "data_type": "latest_official_nav"},
        } if latest_data else self._unavailable("latest_official_nav", nav_history["meta"].get("message") or "暂无官方净值")
        performance = calculate_performance((nav_history.get("data") or {}).get("points") or [])

        industry_supported = not any(label in fund_type.upper() for label in ("货币", "FOF", "QDII"))
        snapshots = self._unavailable("stock_snapshot", "没有可用于行业或盘中估算的公开股票持仓")
        allocation = self._unavailable("disclosed_industry_allocation", "当前基金类型不支持 A 股行业穿透")
        if holdings.get("data") and industry_supported:
            codes = [item.get("stock_code") for item in holdings["data"].get("holdings") or [] if item.get("stock_code")]
            snapshots = self._fetch("stock_snapshot", cache_key=",".join(codes), force_refresh=force_refresh, codes=codes)
            allocation = self._fetch("industry_allocation", cache_key=code, force_refresh=force_refresh, code=code)

        if holdings.get("data") and (snapshots.get("data") or allocation.get("data")):
            exposure_data = self._industry_exposure(holdings["data"], snapshots.get("data") or {}, allocation.get("data"))
            allocation_fallback = exposure_data["industry_classification_source"] == "东方财富基金行业配置"
            exposure_source = allocation["meta"] if allocation_fallback else snapshots["meta"]
            exposure_meta = DataMeta(
                source_name="东方财富公开行业配置" if allocation_fallback else "东方财富公开持仓与证券行业",
                source_reference=exposure_source.get("source_reference") or "",
                data_type="calculated_industry_exposure", as_of_date=(allocation.get("data") or {}).get("as_of_date") if allocation_fallback else holdings["data"].get("disclosure_date"),
                fetched_at=self._now().isoformat(), status="disclosed", is_cached=bool(exposure_source.get("is_cached")),
                is_stale=bool(exposure_source.get("is_stale")), provider=exposure_source.get("provider") or "",
                fallback_used=bool(exposure_source.get("fallback_used")) or allocation_fallback,
                message="行业暴露来自基金公开行业配置；未识别资产不做归一化" if allocation_fallback else "行业暴露按公开持仓计算；未知与非股票资产未归一化",
            )
            industry_exposure = {"data": exposure_data, "meta": exposure_meta.to_dict()}
        else:
            industry_exposure = self._unavailable(
                "calculated_industry_exposure",
                "公开持仓或股票行业分类不足，无法形成可靠行业暴露",
            )

        if holdings.get("data") and latest_data and snapshots.get("data"):
            disclosure = holdings["data"].get("disclosure_date")
            estimate_data = calculate_intraday_estimate(
                latest_nav=latest_data.get("unit_nav"), fund_type=fund_type,
                holdings=holdings["data"].get("holdings") or [], quotes=snapshots["data"],
                disclosure_date=date.fromisoformat(disclosure) if disclosure else None, estimate_time=self._now(),
            )
            estimate_status = "estimated" if estimate_data.get("status") == "estimated" else "unavailable"
            estimate_meta = DataMeta(
                source_name="东方财富官方净值、公开持仓与证券行情", source_reference=snapshots["meta"].get("source_reference") or "",
                data_type="intraday_estimate", as_of_date=latest_data.get("nav_date"), fetched_at=self._now().isoformat(),
                status=estimate_status, provider=snapshots["meta"].get("provider") or "", message=estimate_data.get("message") or "",
            )
            intraday = {"data": estimate_data, "meta": estimate_meta.to_dict()}
        else:
            intraday = self._unavailable("intraday_estimate", "盘中估算暂不可用：当前基金类型或公开持仓不足以形成可靠估算")

        return {
            "code": code,
            "profile": profile,
            "latest_nav": latest_nav,
            "nav_history": nav_history,
            "performance": performance,
            "holdings": holdings,
            "industry_exposure": industry_exposure,
            "intraday_estimate": intraday,
            "data_quality": {
                "profile": profile["meta"], "latest_nav": latest_nav["meta"],
                "nav_history": nav_history["meta"], "holdings": holdings["meta"],
                "industry_exposure": industry_exposure["meta"], "intraday_estimate": intraday["meta"],
                "industry_allocation": allocation["meta"],
            },
        }

    def get_portfolio_analysis(self, holdings: list[dict[str, Any]], force_refresh: bool = False) -> dict[str, Any]:
        enriched: list[dict[str, Any]] = []
        overlap_inputs: list[dict[str, Any]] = []
        industry_inputs: list[dict[str, Any]] = []
        nav_dates: set[str] = set()
        total_cost = 0.0
        total_market_value = 0.0
        total_profit_loss = 0.0
        incomplete_cost = False
        intraday_inputs: list[tuple[float, float]] = []
        risk_flags: list[str] = []

        for holding in holdings:
            code = str(holding.get("code") or "")
            analysis = self.get_fund_analysis(code, force_refresh=force_refresh)
            profile = analysis.get("profile", {}).get("data") or {}
            latest = analysis.get("latest_nav", {}).get("data") or {}
            position = calculate_position(
                shares=float(holding.get("shares") or 0),
                avg_cost=holding.get("avg_cost"),
                official_nav=latest.get("unit_nav"),
            )
            if position["total_cost"] is None:
                incomplete_cost = True
            else:
                total_cost += float(position["total_cost"])
            if position["market_value"] is not None:
                total_market_value += float(position["market_value"])
            if position["profit_loss"] is not None:
                total_profit_loss += float(position["profit_loss"])
            if latest.get("nav_date"):
                nav_dates.add(str(latest["nav_date"]))

            disclosed = analysis.get("holdings", {}).get("data") or {}
            market_value = position.get("market_value")
            name = profile.get("name") or holding.get("manual_name") or holding.get("legacy_name") or code
            if market_value is not None:
                overlap_inputs.append({
                    "code": code, "name": name, "market_value": market_value,
                    "holdings": disclosed.get("holdings") or [],
                })
                exposure = analysis.get("industry_exposure", {}).get("data") or {}
                broad_map = {item["name"]: item["weight_pct"] for item in exposure.get("broad") or []}
                unknown = sum(float(exposure.get(key) or 0) for key in (
                    "unidentified_disclosed_pct", "undisclosed_stock_pct", "non_stock_pct",
                ))
                industry_inputs.append({"market_value": market_value, "broad_exposure": broad_map, "unknown_pct": unknown})
                intraday = analysis.get("intraday_estimate", {}).get("data") or {}
                if intraday.get("status") == "estimated" and intraday.get("estimated_change_pct") is not None:
                    intraday_inputs.append((float(market_value), float(intraday["estimated_change_pct"])))

            top10 = disclosed.get("top10_coverage_pct")
            if isinstance(top10, (int, float)) and top10 < 30:
                risk_flags.append(f"{name} 的前十大持仓覆盖比例较低（{top10:.2f}%）")
            disclosure_date = disclosed.get("disclosure_date")
            if disclosure_date:
                try:
                    if (self._now().date() - date.fromisoformat(disclosure_date)).days > 100:
                        risk_flags.append(f"{name} 的公开持仓披露已超过一个季度")
                except ValueError:
                    pass
            enriched.append({
                "code": code,
                "name": name,
                "fund_type": profile.get("fund_type"),
                "user_holding": holding,
                "position": position,
                "analysis": analysis,
            })

        overlap = calculate_overlap(overlap_inputs)
        concentration = calculate_industry_concentration(industry_inputs)
        if overlap:
            risk_flags.append("多只基金公开持仓包含同一批股票；请查看重复持仓明细")
        technology = next((item["weight_pct"] for item in concentration["exposure"] if item["name"] == "科技"), 0)
        if technology >= 50:
            risk_flags.append("组合对科技行业的公开持仓估算暴露较高")
        inconsistent_dates = len(nav_dates) > 1
        if inconsistent_dates:
            risk_flags.append("组合净值数据日期不完全一致")
        if incomplete_cost:
            risk_flags.append("部分旧持仓成本尚未确认，组合盈亏只计算已确认部分")

        return_rate = total_profit_loss / total_cost * 100 if total_cost else None
        intraday_change = None
        intraday_message = "盘中估算暂不可用：当前基金类型或公开持仓不足以形成可靠估算"
        if enriched and len(intraday_inputs) == len([item for item in enriched if item["position"]["market_value"] is not None]):
            denominator = sum(value for value, _ in intraday_inputs)
            if denominator:
                intraday_change = sum(value * change for value, change in intraday_inputs) / denominator
                intraday_message = "这是估算，不是官方净值"
        return {
            "overview": {
                "fund_count": len(holdings),
                "total_cost": round(total_cost, 4),
                "market_value": round(total_market_value, 4),
                "profit_loss": round(total_profit_loss, 4),
                "return_rate": round(return_rate, 4) if return_rate is not None else None,
                "intraday_change_pct": round(intraday_change, 4) if intraday_change is not None else None,
                "intraday_message": intraday_message,
                "nav_dates": sorted(nav_dates),
                "inconsistent_nav_dates": inconsistent_dates,
                "cost_incomplete": incomplete_cost,
                "updated_at": self._now().isoformat(),
            },
            "holdings": enriched,
            "overlap": overlap,
            "industry_concentration": concentration,
            "risk_flags": list(dict.fromkeys(risk_flags)),
        }


_service: FundDataService | None = None


def get_service() -> FundDataService:
    global _service
    if _service is None:
        _service = FundDataService()
    return _service


def reset_service() -> None:
    global _service
    _service = None
