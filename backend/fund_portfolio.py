"""PP03 基金持仓本地存储。

与既有股票持仓 `portfolio.py` 分离，默认写入用户目录
`~/.vibe-research/fund-portfolio.json`。不抓取、不推断基金净值或披露持仓。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

DATA_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".vibe-research")
FUND_FILE = os.path.join(DATA_DIR, "fund-portfolio.json")
BEIJING = timezone(timedelta(hours=8))
_LOCK = threading.Lock()


class FundPortfolioCorrupt(RuntimeError):
    """用户文件不是合法 JSON；写入必须停止，避免覆盖原始字节。"""


def _empty() -> dict:
    return {"holdings": [], "updated": None}


def _load() -> tuple[dict, bool]:
    try:
        with open(FUND_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get("holdings"), list):
            return _empty(), True
        return data, False
    except FileNotFoundError:
        return _empty(), False
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _empty(), True


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(FUND_FILE), exist_ok=True)
    tmp = FUND_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, FUND_FILE)


def _public(data: dict) -> dict:
    holdings = data.get("holdings", [])
    return {
        "holdings": holdings,
        "total_amount": round(sum(float(item.get("amount") or 0) for item in holdings), 2),
        "updated": data.get("updated"),
    }


def list_fund_holdings() -> dict:
    with _LOCK:
        data, _ = _load()
    return _public(data)


def upsert_fund_holding(record: dict) -> dict:
    clean = {
        "code": record["code"],
        "name": record["name"],
        "amount": record["amount"],
        "shares": record["shares"],
        "cost": record["cost"],
        "buy_date": record["buy_date"],
        "notes": record.get("notes", ""),
        "tag_ids": record.get("tag_ids", []),
        "official_nav": None,
        "official_nav_date": None,
        "intraday_estimate": None,
        "estimate_updated_at": None,
        "estimate_confidence": None,
        "holding_disclosure_date": None,
        "top10_coverage": None,
        "historical_nav": None,
    }
    with _LOCK:
        data, corrupt = _load()
        if corrupt:
            raise FundPortfolioCorrupt("基金持仓文件已损坏；为保护原始数据，本次写入已停止")
        holdings = [item for item in data.get("holdings", []) if item.get("code") != clean["code"]]
        holdings.append(clean)
        holdings.sort(key=lambda item: item["code"])
        data = {"holdings": holdings, "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")}
        _save(data)
    return _public(data)


def delete_fund_holding(code: str) -> dict:
    with _LOCK:
        data, corrupt = _load()
        if corrupt:
            raise FundPortfolioCorrupt("基金持仓文件已损坏；为保护原始数据，本次写入已停止")
        data["holdings"] = [item for item in data.get("holdings", []) if item.get("code") != code]
        data["updated"] = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")
        _save(data)
    return _public(data)
