from __future__ import annotations

import json
import math
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
from fund_data.providers import (
    AkshareDanjuanProvider,
    AkshareEastmoneyProvider,
    CninfoIndustryProvider,
    EastmoneyDirectProvider,
    TencentQuoteProvider,
)
from fund_data.providers.base import ProviderUnavailable

BEIJING = timezone(timedelta(hours=8))
TTL = {
    "search": 24 * 3600,
    "profile": 24 * 3600,
    "nav_history": 12 * 3600,
    "holdings": 24 * 3600,
    "industry_allocation": 24 * 3600,
    "stock_industry_classification": 24 * 3600,
    "stock_snapshot": 60,
}


def _default_data_dir() -> str:
    return os.environ.get("VR_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".vibe-research")


CHAIN_TAG_RULES = [
    ("storage", "存储", ("存储", "DRAM", "NAND", "HBM")),
    ("semiconductor-equipment", "半导体设备", ("半导体设备",)),
    ("chip-design", "芯片设计", ("数字芯片设计", "集成电路设计")),
    ("software", "软件", ("软件开发", "IT服务", "IT 服务")),
    ("consumer-electronics", "消费电子", ("消费电子",)),
    ("robotics", "机器人", ("机器人", "自动化设备")),
    ("semiconductor", "半导体", ("半导体", "数字芯片设计", "集成电路设计")),
]


def _industry_chain_tags(classification: dict[str, Any]) -> list[tuple[str, str]]:
    text = " ".join(str(classification.get(key) or "") for key in (
        "primary_industry", "secondary_industry", "detail_industry", "fine_industry",
    ))
    return [
        (tag_id, name)
        for tag_id, name, terms in CHAIN_TAG_RULES
        if any(term in text for term in terms)
    ]


def _official_requires_lookthrough(name: str) -> bool:
    broad_categories = (
        "制造业", "采矿业", "建筑业", "金融业", "房地产业", "批发和零售业",
        "信息传输、软件和信息技术服务业", "交通运输、仓储和邮政业",
    )
    return any(category in name for category in broad_categories)


class FundDataService:
    def __init__(
        self,
        providers: list[Any] | None = None,
        cache: FundCache | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self._now = now or (lambda: datetime.now(BEIJING))
        self.providers = sorted(
            providers or [
                CninfoIndustryProvider(), EastmoneyDirectProvider(), TencentQuoteProvider(),
                AkshareEastmoneyProvider(), AkshareDanjuanProvider(),
            ],
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

    def _industry_exposure(
        self,
        holdings: dict[str, Any],
        classifications_section: dict[str, Any],
        allocation_section: dict[str, Any],
    ) -> dict[str, Any]:
        classification_data = classifications_section.get("data") or {}
        classifications = classification_data.get("classifications") or {}
        classification_meta = classifications_section.get("meta") or {}
        allocation = allocation_section.get("data") or {}
        allocation_meta = allocation_section.get("meta") or {}

        layer_totals: dict[str, dict[str, float]] = {
            "primary": {}, "secondary": {}, "detail": {},
        }
        tags: dict[tuple[str, str], float] = {}
        tag_sources: dict[tuple[str, str], str] = {}
        identified = 0.0
        unknown = 0.0
        other_primary = 0.0
        other_constituents: list[dict[str, Any]] = []
        unknown_constituents: list[dict[str, Any]] = []
        used_standards: set[str] = set()

        for holding in holdings.get("holdings") or []:
            code = str(holding.get("stock_code") or "").zfill(6)
            name = str(holding.get("stock_name") or code)
            weight = float(holding.get("weight_pct") or 0)
            classification = classifications.get(code)
            if not classification:
                unknown += weight
                unknown_constituents.append({
                    "stock_code": code,
                    "stock_name": name,
                    "weight_pct": round(weight, 4),
                    "reason": "股票行业分类缺失或请求失败",
                })
                continue
            identified += weight
            standard = str(classification.get("classification_standard") or "").strip()
            if standard:
                used_standards.add(standard)
            for layer, field in (
                ("primary", "primary_industry"),
                ("secondary", "secondary_industry"),
                ("detail", "detail_industry"),
            ):
                industry = str(classification.get(field) or "").strip()
                if industry:
                    values = layer_totals[layer]
                    values[industry] = values.get(industry, 0.0) + weight
            if not str(classification.get("primary_industry") or "").strip():
                other_primary += weight
                other_constituents.append({
                    "stock_code": code,
                    "stock_name": name,
                    "weight_pct": round(weight, 4),
                    "reason": "已取得行业记录，但缺少一级行业名称",
                })
            for tag in _industry_chain_tags(classification):
                tags[tag] = tags.get(tag, 0.0) + weight
                tag_sources.setdefault(tag, str(classification.get("source_name") or classification_meta.get("source_name") or ""))

        def rows(values: dict[str, float]) -> list[dict[str, Any]]:
            return [
                {"name": name, "weight_pct": round(weight, 4)}
                for name, weight in sorted(values.items(), key=lambda item: (-item[1], item[0]))
                if weight > 0
            ]

        top10 = float(holdings.get("top10_coverage_pct") or 0)
        stock_exposure = float(allocation.get("stock_exposure_pct") or top10)
        undisclosed_stock = max(0.0, stock_exposure - top10)
        non_stock = max(0.0, 100.0 - stock_exposure)
        if undisclosed_stock > 0:
            unknown_constituents.append({
                "stock_code": "",
                "stock_name": "未披露股票资产",
                "weight_pct": round(undisclosed_stock, 4),
                "reason": "基金股票资产超过公开前十大持仓覆盖，具体证券未披露",
            })

        official_rows = []
        for item in allocation.get("industries") or []:
            name = str(item.get("name") or "").strip()
            weight = float(item.get("weight_pct") or 0)
            if not name or weight <= 0:
                continue
            requires_lookthrough = _official_requires_lookthrough(name)
            official_rows.append({
                "name": name,
                "display_name": f"{name}（待穿透）" if requires_lookthrough else name,
                "weight_pct": round(weight, 4),
                "requires_lookthrough": requires_lookthrough,
            })

        tag_rows = [
            {
                "id": tag_id,
                "name": name,
                "weight_pct": round(weight, 4),
                "evidence_level": "disclosed_stock_classification",
                "source_name": tag_sources.get((tag_id, name)) or "巨潮资讯上市公司行业归属",
            }
            for (tag_id, name), weight in sorted(tags.items(), key=lambda item: -item[1])
        ]
        primary = rows(layer_totals["primary"])
        secondary = rows(layer_totals["secondary"])
        detail = rows(layer_totals["detail"])
        calculation_basis = "最新公开前十大持仓原始占基金净值比例 × 上市公司公开行业分类；未披露部分未归一化"
        lookthrough = {
            "status": "disclosed" if classifications else "unavailable",
            "message": (
                "行业暴露仅基于公开持仓估算，未披露部分未归一化"
                if classifications
                else "股票行业穿透暂不可用；官方行业配置不代替穿透结果"
            ),
            "primary": primary,
            "secondary": secondary,
            "detail": detail,
            "identified_coverage_pct": round(identified, 4),
            "other_pct": round(other_primary, 4),
            "unknown_pct": round(unknown, 4),
            "undisclosed_stock_pct": round(undisclosed_stock, 4),
            "non_stock_pct": round(non_stock, 4),
            "disclosure_date": holdings.get("disclosure_date"),
            "source_name": classification_meta.get("source_name") or "",
            "source_reference": classification_meta.get("source_reference") or "",
            "classification_standard": "、".join(sorted(used_standards)),
            "calculation_basis": calculation_basis,
        }
        official_allocation = {
            "exposure": official_rows,
            "stock_exposure_pct": round(stock_exposure, 4),
            "as_of_date": allocation.get("as_of_date") or allocation_meta.get("as_of_date"),
            "source_name": allocation_meta.get("source_name") or "",
            "source_reference": allocation_meta.get("source_reference") or "",
        }
        return {
            "official_allocation": official_allocation,
            "lookthrough": lookthrough,
            "industry_chain_tags": tag_rows,
            "other_constituents": other_constituents,
            "unknown_constituents": unknown_constituents,
            # Compatibility fields for existing clients during the V0.2-W1 transition.
            "primary": primary,
            "secondary": secondary,
            "broad": primary,
            "system_tags": [
                {"id": item["id"], "name": item["name"], "weight_pct": item["weight_pct"]}
                for item in tag_rows
            ],
            "identified_coverage_pct": lookthrough["identified_coverage_pct"],
            "unidentified_disclosed_pct": lookthrough["unknown_pct"],
            "undisclosed_stock_pct": lookthrough["undisclosed_stock_pct"],
            "non_stock_pct": lookthrough["non_stock_pct"],
            "calculation_basis": calculation_basis,
            "industry_classification_source": lookthrough["source_name"],
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
        snapshots = self._unavailable("stock_snapshot", "没有可用于盘中估算的公开股票持仓")
        classifications = self._unavailable("stock_industry_classification", "没有可用于行业穿透的公开股票持仓")
        allocation = self._unavailable("disclosed_industry_allocation", "当前基金类型不支持 A 股行业穿透")
        if holdings.get("data") and industry_supported:
            codes = sorted({
                str(item.get("stock_code"))
                for item in holdings["data"].get("holdings") or []
                if item.get("stock_code")
            })
            snapshots = self._fetch("stock_snapshot", cache_key=",".join(codes), force_refresh=force_refresh, codes=codes)
            classifications = self._fetch(
                "stock_industry_classification",
                cache_key=",".join(codes),
                force_refresh=force_refresh,
                codes=codes,
            )
            allocation = self._fetch("industry_allocation", cache_key=code, force_refresh=force_refresh, code=code)

        if holdings.get("data"):
            exposure_data = self._industry_exposure(holdings["data"], classifications, allocation)
            classification_meta = classifications["meta"]
            allocation_meta = allocation["meta"]
            has_classification = bool(classifications.get("data"))
            has_official = bool(allocation.get("data"))
            exposure_source = classification_meta if has_classification else allocation_meta
            exposure_status = "disclosed" if has_classification or has_official else "unavailable"
            exposure_meta = DataMeta(
                source_name=exposure_source.get("source_name") or "",
                source_reference=exposure_source.get("source_reference") or "",
                data_type="calculated_industry_exposure",
                as_of_date=holdings["data"].get("disclosure_date"),
                fetched_at=self._now().isoformat(), status=exposure_status, is_cached=bool(exposure_source.get("is_cached")),
                is_stale=bool(exposure_source.get("is_stale")), provider=exposure_source.get("provider") or "",
                fallback_used=bool(exposure_source.get("fallback_used")),
                message=(
                    "重仓股穿透按公开持仓与股票行业分类计算；官方行业配置独立展示；未披露部分未归一化"
                    if has_classification
                    else "股票行业穿透暂不可用；官方行业配置仅作为独立证据展示"
                ),
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
                "stock_industry_classification": classifications["meta"],
                "industry_allocation": allocation["meta"],
            },
        }

    def get_portfolio_analysis(self, holdings: list[dict[str, Any]], force_refresh: bool = False) -> dict[str, Any]:
        enriched: list[dict[str, Any]] = []
        overlap_inputs: list[dict[str, Any]] = []
        industry_inputs: list[dict[str, Any]] = []
        nav_dates: set[str] = set()
        total_cost = 0.0
        total_position_value = 0.0
        total_profit_loss = 0.0
        incomplete_cost = False
        incomplete_pnl = False
        intraday_inputs: list[tuple[float, float]] = []
        intraday_estimated_profit_loss = 0.0
        estimable_count = 0
        official_only_count = 0
        risk_flags: list[str] = []

        for holding in holdings:
            code = str(holding.get("code") or "")
            analysis = self.get_fund_analysis(code, force_refresh=force_refresh)
            profile = analysis.get("profile", {}).get("data") or {}
            latest = analysis.get("latest_nav", {}).get("data") or {}
            latest_meta = analysis.get("latest_nav", {}).get("meta") or {}
            nav_value = latest.get("unit_nav")
            official_nav = nav_value if (
                latest_meta.get("status") == "official"
                and not latest_meta.get("is_stale", False)
                and isinstance(nav_value, (int, float))
                and math.isfinite(nav_value)
                and nav_value > 0
            ) else None
            intraday = analysis.get("intraday_estimate", {}).get("data") or {}
            intraday_change_pct = (
                float(intraday["estimated_change_pct"])
                if intraday.get("status") == "estimated" and isinstance(intraday.get("estimated_change_pct"), (int, float))
                else None
            )
            position = calculate_position(
                shares=holding.get("shares"),
                avg_cost=holding.get("avg_cost"),
                avg_unit_cost=holding.get("avg_unit_cost"),
                official_nav=official_nav,
                input_mode=holding.get("input_mode") or "shares_cost",
                amount_snapshot=holding.get("amount_snapshot"),
                cumulative_pnl_snapshot=holding.get("cumulative_pnl_snapshot"),
                snapshot_at=holding.get("snapshot_at"),
                shares_source=holding.get("shares_source") or ("user" if holding.get("shares") is not None else None),
                intraday_change_pct=intraday_change_pct,
            )
            if position["reference_total_cost"] is None:
                incomplete_cost = True
            else:
                total_cost += float(position["reference_total_cost"])
            if position["position_value"] is not None:
                total_position_value += float(position["position_value"])
            if position["profit_loss"] is not None:
                total_profit_loss += float(position["profit_loss"])
            else:
                incomplete_pnl = True
            if official_nav is not None and latest.get("nav_date"):
                nav_dates.add(str(latest["nav_date"]))
            if position["today_estimated_profit_loss"] is not None:
                intraday_estimated_profit_loss += float(position["today_estimated_profit_loss"])
                estimable_count += 1
            elif position["official_market_value"] is not None:
                official_only_count += 1

            disclosed = analysis.get("holdings", {}).get("data") or {}
            market_value = position.get("position_value")
            name = profile.get("name") or holding.get("manual_name") or holding.get("legacy_name") or code
            if market_value is not None:
                overlap_inputs.append({
                    "code": code, "name": name, "market_value": market_value,
                    "holdings": disclosed.get("holdings") or [],
                })
                exposure = analysis.get("industry_exposure", {}).get("data") or {}
                industry_inputs.append({
                    "market_value": market_value,
                    "lookthrough": exposure.get("lookthrough") or {},
                    "industry_chain_tags": exposure.get("industry_chain_tags") or [],
                })
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

        for item in enriched:
            value = item["position"].get("position_value")
            item["weight_pct"] = round(float(value) / total_position_value * 100, 4) if value is not None and total_position_value else None

        overlap = calculate_overlap(overlap_inputs)
        concentration = calculate_industry_concentration(industry_inputs)
        if overlap:
            risk_flags.append("多只基金公开持仓包含同一批股票；请查看重复持仓明细")
        inconsistent_dates = len(nav_dates) > 1
        if inconsistent_dates:
            risk_flags.append("组合净值数据日期不完全一致")
        if incomplete_cost:
            risk_flags.append("部分旧持仓成本尚未确认，组合盈亏只计算已确认部分")

        overview_profit_loss = None if incomplete_pnl else total_profit_loss
        return_rate = overview_profit_loss / total_cost * 100 if overview_profit_loss is not None and total_cost > 0 else None
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
                "market_value": round(total_position_value, 4),
                "total_holding_value": round(total_position_value, 4),
                "profit_loss": round(overview_profit_loss, 4) if overview_profit_loss is not None else None,
                "return_rate": round(return_rate, 4) if return_rate is not None else None,
                "intraday_change_pct": round(intraday_change, 4) if intraday_change is not None else None,
                "intraday_estimated_profit_loss": round(intraday_estimated_profit_loss, 4) if estimable_count else None,
                "intraday_message": intraday_message,
                "nav_dates": sorted(nav_dates),
                "latest_nav_date": max(nav_dates) if nav_dates else None,
                "inconsistent_nav_dates": inconsistent_dates,
                "cost_incomplete": incomplete_cost,
                "pnl_complete": not incomplete_pnl,
                "estimable_count": estimable_count,
                "official_only_count": official_only_count,
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
