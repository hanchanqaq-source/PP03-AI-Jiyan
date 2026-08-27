from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def calculate_position(
    shares: float | None,
    avg_cost: float | None,
    official_nav: float | None,
    *,
    input_mode: str | None = None,
    avg_unit_cost: float | None = None,
    amount_snapshot: float | None = None,
    cumulative_pnl_snapshot: float | None = None,
    snapshot_at: str | None = None,
    shares_source: str | None = None,
    intraday_change_pct: float | None = None,
) -> dict[str, Any]:
    """Calculate explicit user-snapshot, official and intraday position values.

    Calls without ``input_mode`` retain the schema-v2 response shape for existing
    consumers. Schema-v3 callers receive provenance fields and compatibility aliases.
    """
    if input_mode is None:
        market_value = None if official_nav is None else float(shares or 0) * float(official_nav)
        total_cost = None if avg_cost is None else float(shares or 0) * float(avg_cost)
        profit_loss = None if market_value is None or total_cost is None else market_value - total_cost
        return_rate = None if profit_loss is None or not total_cost else profit_loss / total_cost * 100
        return {
            "total_cost": _round(total_cost),
            "market_value": _round(market_value),
            "profit_loss": _round(profit_loss),
            "return_rate": _round(return_rate),
        }

    official_market_value: float | None = None
    position_value: float | None = None
    position_value_basis = "unavailable"
    reference_total_cost: float | None = None
    profit_loss: float | None = None
    return_rate: float | None = None

    if input_mode == "amount_pnl":
        if shares_source == "inferred" and shares is not None and official_nav is not None:
            official_market_value = float(shares) * float(official_nav)
            position_value = official_market_value
            position_value_basis = "official_nav_from_inferred_shares"
        elif amount_snapshot is not None:
            position_value = float(amount_snapshot)
            position_value_basis = "user_amount_snapshot"

        if amount_snapshot is not None and cumulative_pnl_snapshot is not None:
            candidate_cost = float(amount_snapshot) - float(cumulative_pnl_snapshot)
            reference_total_cost = candidate_cost if candidate_cost > 0 else None
            profit_loss = float(cumulative_pnl_snapshot)
            if reference_total_cost is not None:
                return_rate = profit_loss / reference_total_cost * 100
    else:
        unit_cost = avg_unit_cost if avg_unit_cost is not None else avg_cost
        if shares is not None and official_nav is not None:
            official_market_value = float(shares) * float(official_nav)
            position_value = official_market_value
            position_value_basis = "official_nav_from_user_shares"
        if shares is not None and unit_cost is not None:
            reference_total_cost = float(shares) * float(unit_cost)
        if official_market_value is not None and reference_total_cost is not None:
            profit_loss = official_market_value - reference_total_cost
            if reference_total_cost > 0:
                return_rate = profit_loss / reference_total_cost * 100

    today_estimated_profit_loss = None
    intraday_market_value = None
    if position_value is not None and intraday_change_pct is not None:
        today_estimated_profit_loss = position_value * float(intraday_change_pct) / 100
        intraday_market_value = position_value + today_estimated_profit_loss

    return {
        "user_amount_snapshot": _round(amount_snapshot),
        "user_cumulative_pnl_snapshot": _round(cumulative_pnl_snapshot),
        "snapshot_at": snapshot_at,
        "official_market_value": _round(official_market_value),
        "intraday_market_value": _round(intraday_market_value),
        "position_value": _round(position_value),
        "position_value_basis": position_value_basis,
        "reference_total_cost": _round(reference_total_cost),
        "today_estimated_profit_loss": _round(today_estimated_profit_loss),
        "intraday_change_pct": _round(intraday_change_pct),
        "profit_loss": _round(profit_loss),
        "return_rate": _round(return_rate),
        "total_cost": _round(reference_total_cost),
        "market_value": _round(position_value),
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
    def weighted_rows(layer: str) -> list[dict[str, Any]]:
        totals: defaultdict[str, float] = defaultdict(float)
        if total_value > 0:
            for fund in funds:
                portfolio_weight = float(fund.get("market_value") or 0) / total_value
                for row in (fund.get("lookthrough") or {}).get(layer) or []:
                    name = str(row.get("name") or "").strip()
                    if name:
                        totals[name] += portfolio_weight * float(row.get("weight_pct") or 0)
        return [
            {"name": name, "weight_pct": round(weight, 4)}
            for name, weight in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
            if weight > 0
        ]

    scalars = {
        "identified_coverage_pct": 0.0,
        "other_pct": 0.0,
        "unknown_pct": 0.0,
        "undisclosed_stock_pct": 0.0,
        "non_stock_pct": 0.0,
    }
    tags: dict[tuple[str, str], float] = defaultdict(float)
    if total_value > 0:
        for fund in funds:
            portfolio_weight = float(fund.get("market_value") or 0) / total_value
            lookthrough = fund.get("lookthrough") or {}
            for key in scalars:
                scalars[key] += portfolio_weight * float(lookthrough.get(key) or 0)
            for tag in fund.get("industry_chain_tags") or []:
                tag_id = str(tag.get("id") or "").strip()
                name = str(tag.get("name") or "").strip()
                if tag_id and name:
                    tags[(tag_id, name)] += portfolio_weight * float(tag.get("weight_pct") or 0)

    primary = weighted_rows("primary")
    secondary = weighted_rows("secondary")
    detail = weighted_rows("detail")
    tag_rows = [
        {"id": tag_id, "name": name, "weight_pct": round(weight, 4)}
        for (tag_id, name), weight in sorted(tags.items(), key=lambda item: (-item[1], item[0][0]))
        if weight > 0
    ]
    return {
        "primary": primary,
        "secondary": secondary,
        "detail": detail,
        "exposure": primary,
        "industry_chain_tags": tag_rows,
        **{key: round(value, 4) for key, value in scalars.items()},
        "calculation_basis": "当前参考市值权重 × 最新公开前十大持仓穿透行业；未披露部分未归一化",
    }


def calculate_intraday_estimate(
    *,
    latest_nav: float | None,
    fund_type: str,
    holdings: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    holdings_as_of_date: date | None,
    estimate_time: datetime,
) -> dict[str, Any]:
    message = "盘中估算暂不可用：当前基金类型或公开持仓不足以形成可靠估算"
    disallowed = any(label in fund_type.upper() for label in ("QDII", "FOF", "货币", "债券"))
    allowed = any(label in fund_type for label in ("股票", "混合", "指数", "ETF")) and not disallowed
    coverage = sum(float(item.get("weight_pct") or 0) for item in holdings)
    disclosed_recently = holdings_as_of_date is not None and (estimate_time.date() - holdings_as_of_date).days <= 120
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
        "holdings_as_of_date": holdings_as_of_date.isoformat(),
        "holdings_coverage_pct": round(coverage, 4),
        "quote_coverage_pct": round(quote_coverage, 4),
        "confidence": "中" if coverage < 70 else "高",
        "formula": "最新官方净值 × (1 + Σ公开持仓比例×对应股票涨跌幅)",
        "message": "这是估算，不是官方净值",
    }
