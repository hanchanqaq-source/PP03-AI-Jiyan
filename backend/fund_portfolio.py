"""PP03 fund-portfolio user truth, schema migrations and protected local storage."""

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
SCHEMA_VERSION = 3
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


def _backup_path(path: Path, from_schema: int) -> Path:
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    version_label = "0.1" if from_schema == 1 else str(from_schema)
    candidate = path.with_name(f"{path.stem}.v{version_label}-backup-{stamp}{path.suffix}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.v{version_label}-backup-{stamp}-{counter}{path.suffix}")
        counter += 1
    return candidate


def _migrated_holding(item: dict[str, Any], *, from_schema: int, timestamp: str) -> dict[str, Any]:
    migrated = dict(item)
    shares = item.get("shares")
    if from_schema == 1:
        has_legacy_cost = item.get("cost") is not None
        migrated.update({
            "avg_cost": None,
            "avg_unit_cost": None,
            "custom_tag_ids": list(item.get("tag_ids") or []),
            "verification_status": "manual_unverified",
            "manual_name": item.get("name") or None,
            "cost_confirmation_required": has_legacy_cost,
            "legacy_name": item.get("name"),
            "legacy_amount": item.get("amount"),
            "legacy_cost": item.get("cost"),
        })
    else:
        migrated.setdefault("custom_tag_ids", [])
        migrated["avg_unit_cost"] = item.get("avg_cost")
        migrated.setdefault("cost_confirmation_required", False)

    migrated.update({
        "schema_version": SCHEMA_VERSION,
        "code": str(item.get("code")),
        "input_mode": "shares_cost",
        "shares": shares,
        "shares_source": "user" if isinstance(shares, (int, float)) and shares > 0 else None,
        "amount_snapshot": None,
        "cumulative_pnl_snapshot": None,
        "snapshot_at": None,
        "basis_nav": None,
        "basis_nav_date": None,
        "shares_inference_note": None,
        "buy_date": item.get("buy_date") or "",
        "notes": item.get("notes") or "",
        "created_at": item.get("created_at") or timestamp,
        "updated_at": item.get("updated_at") or timestamp,
    })
    return migrated


def _migrate(data: dict[str, Any], original_bytes: bytes, *, from_schema: int) -> dict[str, Any]:
    path = Path(FUND_FILE)
    backup = _backup_path(path, from_schema)
    backup.write_bytes(original_bytes)
    if backup.read_bytes() != original_bytes:
        raise FundPortfolioCorrupt(f"V{from_schema} 持仓备份校验失败；迁移已停止，原文件未改动")

    timestamp = _now().isoformat()
    holdings = [
        _migrated_holding(item, from_schema=from_schema, timestamp=timestamp)
        for item in data.get("holdings") or []
        if isinstance(item, dict) and item.get("code")
    ]
    migration = {
        "from_schema": from_schema,
        "migrated_at": timestamp,
        "backup_file": backup.name,
        "cost_confirmation_required_count": sum(bool(item.get("cost_confirmation_required")) for item in holdings),
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

    raw_version = data.get("schema_version")
    from_schema = 2 if raw_version == 2 else 1
    try:
        return _migrate(data, original, from_schema=from_schema), False
    except OSError as error:
        raise FundPortfolioCorrupt(f"V{from_schema} 持仓备份失败；迁移已停止：{error}") from error


def _holding_reference_cost(item: dict[str, Any]) -> float | None:
    if item.get("input_mode") == "amount_pnl":
        amount = item.get("amount_snapshot")
        pnl = item.get("cumulative_pnl_snapshot")
        if amount is None or pnl is None:
            return None
        reference_cost = float(amount) - float(pnl)
        return reference_cost if reference_cost > 0 else None
    unit_cost = item.get("avg_unit_cost") if item.get("avg_unit_cost") is not None else item.get("avg_cost")
    if unit_cost is None or item.get("shares") is None:
        return None
    return float(item["shares"]) * float(unit_cost)


def _public(data: dict[str, Any]) -> dict[str, Any]:
    holdings = data.get("holdings", [])
    costs = [_holding_reference_cost(item) for item in holdings]
    total_cost = sum(cost for cost in costs if cost is not None)
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


def _snapshot_fields(record: dict[str, Any], existing: dict[str, Any], timestamp: str) -> dict[str, Any]:
    if record.get("input_mode") == "amount_pnl":
        amount = record.get("amount_snapshot")
        pnl = record.get("cumulative_pnl_snapshot")
        changed = (
            not existing
            or existing.get("amount_snapshot") != amount
            or existing.get("cumulative_pnl_snapshot") != pnl
        )
        return {
            "amount_snapshot": amount,
            "cumulative_pnl_snapshot": pnl,
            "snapshot_at": timestamp if changed else existing.get("snapshot_at"),
        }
    return {
        "amount_snapshot": existing.get("amount_snapshot"),
        "cumulative_pnl_snapshot": existing.get("cumulative_pnl_snapshot"),
        "snapshot_at": existing.get("snapshot_at"),
    }


def upsert_fund_holding(record: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    with _LOCK:
        data, corrupt = _load()
        if corrupt:
            raise FundPortfolioCorrupt("基金持仓文件已损坏；为保护原始数据，本次写入已停止")
        existing = next((item for item in data.get("holdings", []) if item.get("code") == record["code"]), None)
        if existing and not replace:
            raise FundAlreadyExists("该基金已在持仓中；请使用编辑操作更新，不会静默覆盖")

        timestamp = _now().isoformat()
        previous = existing or {}
        input_mode = record.get("input_mode") or "shares_cost"
        clean = dict(previous)
        clean.update({
            "schema_version": SCHEMA_VERSION,
            "code": record["code"],
            "input_mode": input_mode,
            "buy_date": record.get("buy_date") or "",
            "notes": record.get("notes", ""),
            "custom_tag_ids": list(record.get("custom_tag_ids") or []),
            "verification_status": record.get("verification_status", "verified"),
            "manual_name": record.get("manual_name"),
            "cost_confirmation_required": False,
            "created_at": previous.get("created_at") or timestamp,
            "updated_at": timestamp,
        })
        clean.update(_snapshot_fields(record, previous, timestamp))

        if input_mode == "amount_pnl":
            clean.update({
                "shares": record.get("shares"),
                "shares_source": record.get("shares_source") if record.get("shares") is not None else None,
                "basis_nav": record.get("basis_nav"),
                "basis_nav_date": record.get("basis_nav_date"),
                "shares_inference_note": record.get("shares_inference_note"),
                "avg_unit_cost": None,
                "avg_cost": None,
            })
        else:
            avg_unit_cost = record.get("avg_unit_cost")
            if avg_unit_cost is None:
                avg_unit_cost = record.get("avg_cost")
            clean.update({
                "shares": record.get("shares"),
                "shares_source": "user" if record.get("shares") is not None else None,
                "avg_unit_cost": avg_unit_cost,
                "avg_cost": avg_unit_cost,
                "basis_nav": None,
                "basis_nav_date": None,
                "shares_inference_note": None,
            })

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
