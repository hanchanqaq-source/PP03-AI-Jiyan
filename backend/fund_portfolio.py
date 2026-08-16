"""PP03 fund-portfolio user truth, V0.1 migration and protected local storage."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATA_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".vibe-research")
FUND_FILE = os.path.join(DATA_DIR, "fund-portfolio.json")
BEIJING = timezone(timedelta(hours=8))
SCHEMA_VERSION = 2
_LOCK = threading.Lock()


class FundPortfolioCorrupt(RuntimeError):
    """The user file cannot be safely read or migrated; writes must stop."""


class FundAlreadyExists(RuntimeError):
    """Creating a duplicate holding would silently overwrite user truth."""


def _now() -> datetime:
    return datetime.now(BEIJING)


def _empty(data_status: str = "ok") -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "holdings": [], "updated": None, "migration": None, "data_status": data_status}


def _save(data: dict[str, Any]) -> None:
    path = Path(FUND_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _backup_path(path: Path) -> Path:
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.stem}.v0.1-backup-{stamp}{path.suffix}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.v0.1-backup-{stamp}-{counter}{path.suffix}")
        counter += 1
    return candidate


def _migrate_v01(legacy: dict[str, Any], original_bytes: bytes) -> dict[str, Any]:
    path = Path(FUND_FILE)
    backup = _backup_path(path)
    backup.write_bytes(original_bytes)
    if backup.read_bytes() != original_bytes:
        raise FundPortfolioCorrupt("V0.1 持仓备份校验失败；迁移已停止，原文件未改动")
    timestamp = _now().isoformat()
    holdings: list[dict[str, Any]] = []
    for item in legacy.get("holdings") or []:
        if not isinstance(item, dict) or not item.get("code"):
            continue
        has_legacy_cost = item.get("cost") is not None
        holdings.append({
            "code": str(item.get("code")),
            "shares": item.get("shares"),
            "avg_cost": None,
            "buy_date": item.get("buy_date") or "",
            "notes": item.get("notes") or "",
            "custom_tag_ids": list(item.get("tag_ids") or []),
            "verification_status": "manual_unverified",
            "manual_name": item.get("name") or None,
            "cost_confirmation_required": has_legacy_cost,
            "legacy_name": item.get("name"),
            "legacy_amount": item.get("amount"),
            "legacy_cost": item.get("cost"),
            "created_at": timestamp,
            "updated_at": timestamp,
        })
    migration = {
        "from_schema": 1,
        "migrated_at": timestamp,
        "backup_file": backup.name,
        "cost_confirmation_required_count": sum(bool(item["cost_confirmation_required"]) for item in holdings),
    }
    migrated = {
        "schema_version": SCHEMA_VERSION,
        "holdings": sorted(holdings, key=lambda item: item["code"]),
        "updated": timestamp,
        "migration": migration,
        "data_status": "migrated",
    }
    _save(migrated)
    return migrated


def _load() -> tuple[dict[str, Any], bool]:
    path = Path(FUND_FILE)
    try:
        original = path.read_bytes()
        data = json.loads(original.decode("utf-8"))
    except FileNotFoundError:
        return _empty(), False
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _empty("corrupt"), True
    if not isinstance(data, dict) or not isinstance(data.get("holdings"), list):
        return _empty("corrupt"), True
    if data.get("schema_version") == SCHEMA_VERSION:
        data.setdefault("migration", None)
        data.setdefault("data_status", "ok")
        return data, False
    try:
        return _migrate_v01(data, original), False
    except OSError as error:
        raise FundPortfolioCorrupt(f"V0.1 持仓备份失败；迁移已停止：{error}") from error


def _public(data: dict[str, Any]) -> dict[str, Any]:
    holdings = data.get("holdings", [])
    total_cost = sum(
        float(item.get("shares") or 0) * float(item.get("avg_cost") or 0)
        for item in holdings if item.get("avg_cost") is not None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "holdings": holdings,
        "total_cost": round(total_cost, 4),
        "updated": data.get("updated"),
        "migration": data.get("migration"),
        "data_status": data.get("data_status", "ok"),
    }


def list_fund_holdings() -> dict[str, Any]:
    with _LOCK:
        data, _ = _load()
    return _public(data)


def upsert_fund_holding(record: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    with _LOCK:
        data, corrupt = _load()
        if corrupt:
            raise FundPortfolioCorrupt("基金持仓文件已损坏；为保护原始数据，本次写入已停止")
        existing = next((item for item in data.get("holdings", []) if item.get("code") == record["code"]), None)
        if existing and not replace:
            raise FundAlreadyExists("该基金已在持仓中；请使用编辑操作更新，不会静默覆盖")
        timestamp = _now().isoformat()
        clean = {
            "code": record["code"],
            "shares": record["shares"],
            "avg_cost": record["avg_cost"],
            "buy_date": record["buy_date"],
            "notes": record.get("notes", ""),
            "custom_tag_ids": list(record.get("custom_tag_ids") or []),
            "verification_status": record.get("verification_status", "verified"),
            "manual_name": record.get("manual_name"),
            "cost_confirmation_required": False,
            "created_at": (existing or {}).get("created_at") or timestamp,
            "updated_at": timestamp,
        }
        holdings = [item for item in data.get("holdings", []) if item.get("code") != clean["code"]]
        holdings.append(clean)
        data = {
            "schema_version": SCHEMA_VERSION,
            "holdings": sorted(holdings, key=lambda item: item["code"]),
            "updated": timestamp,
            "migration": data.get("migration"),
            "data_status": "ok",
        }
        _save(data)
    return _public(data)


def delete_fund_holding(code: str) -> dict[str, Any]:
    with _LOCK:
        data, corrupt = _load()
        if corrupt:
            raise FundPortfolioCorrupt("基金持仓文件已损坏；为保护原始数据，本次写入已停止")
        data["holdings"] = [item for item in data.get("holdings", []) if item.get("code") != code]
        data["updated"] = _now().isoformat()
        data["data_status"] = "ok"
        _save(data)
    return _public(data)

