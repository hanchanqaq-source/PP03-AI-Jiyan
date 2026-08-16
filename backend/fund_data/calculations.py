from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def calculate_position(shares: float, avg_cost: float | None, official_nav: float | None) -> dict[str, float | None]:
    market_value = None if official_nav is None else float(shares) * float(official_nav)
    total_cost = None if avg_cost is None else float(shares) * float(avg_cost)
    profit_loss = None if market_value is None or total_cost is None else market_value - total_cost
    return_rate = None if profit_loss is None or not total_cost else profit_loss / total_cost * 100
    return {
        "total_cost": _round(total_cost),
        "market_value": _round(market_value),
        "profit_loss": _round(profit_loss),
        "return_rate": _round(return_rate),
    }


def _period_return(points: list[tuple[date, float]], days: int | None) -> float | None:
    if len(points) < 2:
        return None
    latest_date, latest_nav = points[-1]
    candidates = points if days is None else [point for point in points if point[0] >= latest_date - timedelta(days=days)]
    if len(candidates) < 2 or not candidates[0][1]:
        return None
    return (latest_nav / candidates[0][1] - 1) * 100


def calculate_performance(history: list[dict[str, Any]]) -> dict[str, Any]:
    points: list[tuple[date, float]] = []
    for item in history:
        try:
            nav_date = date.fromisoformat(str(item["date"])[:10])
            nav = float(item["unit_nav"])
        except (KeyError, TypeError, ValueError):
            continue
        if nav > 0:
            points.append((nav_date, nav))
    points.sort(key=lambda point: point[0])
    if len(points) < 2:
        return {
            "returns": {"1m": None, "3m": None, "6m": None, "1y": None, "3y": None},
            "since_inception": None,
            "max_drawdown": None,
            "annualized_volatility": None,
        }

    peak = points[0][1]
    max_drawdown = 0.0
    changes: list[float] = []
    for index, (_, nav) in enumerate(points):
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1)
        if index and points[index - 1][1]:
            changes.append(nav / points[index - 1][1] - 1)
    volatility = statistics.stdev(changes) * math.sqrt(252) * 100 if len(changes) >= 2 else None
    returns = {
        "1m": _round(_period_return(points, 31)),
        "3m": _round(_period_return(points, 93)),
        "6m": _round(_period_return(points, 186)),
        "1y": _round(_period_return(points, 366)),
        "3y": _round(_period_return(points, 1096)),
    }
    return {
        "returns": returns,
        "since_inception": _round(_period_return(points, None)),
        "max_drawdown": _round(max_drawdown * 100),
        "annualized_volatility": _round(volatility, 6),
    }


def calculate_overlap(funds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total_value = sum(float(fund.get("market_value") or 0) for fund in funds)
    if total_value <= 0:
        return []
    grouped: dict[str, dict[str, Any]] = {}
    for fund in funds:
        fund_value = float(fund.get("market_value") or 0)
        for holding in fund.get("holdings") or []:
            stock_code = str(holding.get("stock_code") or "")
            if not stock_code:
                continue
            weight = float(holding.get("weight_pct") or 0)
            contribution = round(fund_value / total_value * weight, 4)
            entry = grouped.setdefault(stock_code, {
                "stock_code": stock_code,
                "stock_name": holding.get("stock_name") or stock_code,
                "funds": [],
            })
            entry["funds"].append({
                "fund_code": fund.get("code"),
                "fund_name": fund.get("name") or fund.get("code"),
                "weight_pct": weight,
                "portfolio_exposure_pct": contribution,
            })
    output: list[dict[str, Any]] = []
    for entry in grouped.values():
        if len(entry["funds"]) < 2:
            continue
        entry["portfolio_exposure_pct"] = round(sum(item["portfolio_exposure_pct"] for item in entry["funds"]), 4)
        entry["calculation_basis"] = "基金官方净值对应市值权重 × 最新公开持仓比例"
        output.append(entry)
    return sorted(output, key=lambda item: (-item["portfolio_exposure_pct"], item["stock_code"]))


def calculate_industry_concentration(funds: list[dict[str, Any]]) -> dict[str, Any]:
    total_value = sum(float(fund.get("market_value") or 0) for fund in funds)
    exposure: dict[str, float] = defaultdict(float)
    unknown = 0.0
    if total_value > 0:
        for fund in funds:
            portfolio_weight = float(fund.get("market_value") or 0) / total_value
            for name, weight in (fund.get("broad_exposure") or {}).items():
                exposure[str(name)] += portfolio_weight * float(weight or 0)
            unknown += portfolio_weight * float(fund.get("unknown_pct") or 0)
    rows = [
        {"name": name, "weight_pct": round(weight, 4)}
        for name, weight in sorted(exposure.items(), key=lambda item: (-item[1], item[0]))
        if weight > 0
    ]
    identified = round(sum(row["weight_pct"] for row in rows), 4)
    return {
        "exposure": rows,
        "identified_coverage_pct": identified,
        "unknown_pct": round(unknown, 4),
        "calculation_basis": "基金官方净值对应市值权重 × 最新公开行业暴露",
    }


def calculate_intraday_estimate(
    *,
    latest_nav: float | None,
    fund_type: str,
    holdings: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    disclosure_date: date | None,
    estimate_time: datetime,
) -> dict[str, Any]:
    message = "盘中估算暂不可用：当前基金类型或公开持仓不足以形成可靠估算"
    disallowed = any(label in fund_type.upper() for label in ("QDII", "FOF", "货币", "债券"))
    allowed = any(label in fund_type for label in ("股票", "混合", "指数", "ETF")) and not disallowed
    coverage = sum(float(item.get("weight_pct") or 0) for item in holdings)
    disclosed_recently = disclosure_date is not None and (estimate_time.date() - disclosure_date).days <= 120
    if latest_nav is None or not allowed or coverage < 50 or not disclosed_recently:
        return {"status": "unavailable", "message": message, "holdings_coverage_pct": round(coverage, 4)}

    quoted_weight = 0.0
    weighted_change = 0.0
    for holding in holdings:
        quote = quotes.get(str(holding.get("stock_code") or ""))
        change = quote.get("change_pct") if quote else None
        if not isinstance(change, (int, float)):
            continue
        weight = float(holding.get("weight_pct") or 0)
        quoted_weight += weight
        weighted_change += weight / 100 * float(change)
    quote_coverage = quoted_weight / coverage * 100 if coverage else 0.0
    if quote_coverage < 90:
        return {
            "status": "unavailable",
            "message": message,
            "holdings_coverage_pct": round(coverage, 4),
            "quote_coverage_pct": round(quote_coverage, 4),
        }
    return {
        "status": "estimated",
        "estimated_nav": round(float(latest_nav) * (1 + weighted_change / 100), 6),
        "estimated_change_pct": round(weighted_change, 4),
        "estimated_at": estimate_time.isoformat(),
        "disclosure_date": disclosure_date.isoformat(),
        "holdings_coverage_pct": round(coverage, 4),
        "quote_coverage_pct": round(quote_coverage, 4),
        "confidence": "中" if coverage < 70 else "高",
        "formula": "最新官方净值 × (1 + Σ公开持仓比例×对应股票涨跌幅)",
        "message": "这是估算，不是官方净值",
    }

