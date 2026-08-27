"""PP03 fund-portfolio user truth, schema migrations and protected local storage."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
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


def _atomic_write(path: Path, payload: bytes) -> None:
    """Replace *path* atomically and remove an uncommitted temporary file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _save(data: dict[str, Any]) -> None:
    path = Path(FUND_FILE)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write(path, payload)


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


def _normalize_legacy(data: dict[str, Any], *, from_schema: int) -> dict[str, Any]:
    """Return the schema-v3 runtime model without touching the source file."""

    timestamp = _now().isoformat()
    holdings = [
        _migrated_holding(item, from_schema=from_schema, timestamp=timestamp)
        for item in data.get("holdings") or []
        if isinstance(item, dict) and item.get("code")
    ]
    migration = {
        "from_schema": from_schema,
        "migrated_at": None,
        "backup_file": None,
        "cost_confirmation_required_count": sum(bool(item.get("cost_confirmation_required")) for item in holdings),
        "persisted": False,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "holdings": sorted(holdings, key=lambda item: item["code"]),
        "updated": data.get("updated"),
        "migration": migration,
        "data_status": "legacy_read_only",
    }


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _valid_date(value: Any, *, allow_empty: bool = False) -> bool:
    if value == "":
        return allow_empty
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _valid_datetime(value: Any, *, allow_none: bool = False) -> bool:
    if value is None:
        return allow_none
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_text(value: Any, *, allow_none: bool = False, allow_empty: bool = True) -> bool:
    if value is None:
        return allow_none
    return isinstance(value, str) and (allow_empty or bool(value.strip()))


def _valid_tags(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= 50
        and all(isinstance(tag, str) and bool(tag.strip()) for tag in value)
    )


def _valid_identity(item: dict[str, Any]) -> bool:
    status = item.get("verification_status")
    manual_name = item.get("manual_name")
    if status not in {"verified", "manual_unverified"}:
        return False
    if not _valid_text(manual_name, allow_none=True, allow_empty=False):
        return False
    return status != "manual_unverified" or bool(manual_name and manual_name.strip())


def _valid_v1_holding(item: dict[str, Any]) -> bool:
    required = {"code", "name", "amount", "shares", "cost", "buy_date", "notes", "tag_ids"}
    return (
        required.issubset(item)
        and _valid_text(item["name"], allow_empty=False)
        and _finite_number(item["amount"]) and float(item["amount"]) > 0
        and _finite_number(item["shares"]) and float(item["shares"]) > 0
        and _finite_number(item["cost"]) and float(item["cost"]) >= 0
        and _valid_date(item["buy_date"])
        and _valid_text(item["notes"])
        and _valid_tags(item["tag_ids"])
    )


def _valid_v2_holding(item: dict[str, Any]) -> bool:
    required = {
        "code", "shares", "avg_cost", "buy_date", "notes", "custom_tag_ids",
        "verification_status", "manual_name", "cost_confirmation_required",
        "created_at", "updated_at",
    }
    if not required.issubset(item) or not isinstance(item["cost_confirmation_required"], bool):
        return False
    avg_cost = item["avg_cost"]
    cost_valid = (
        avg_cost is None and item["cost_confirmation_required"]
    ) or (
        not item["cost_confirmation_required"]
        and _finite_number(avg_cost)
        and float(avg_cost) >= 0
    )
    return (
        _finite_number(item["shares"]) and float(item["shares"]) > 0
        and cost_valid
        and _valid_date(item["buy_date"])
        and _valid_text(item["notes"])
        and _valid_tags(item["custom_tag_ids"])
        and _valid_identity(item)
        and _valid_datetime(item["created_at"])
        and _valid_datetime(item["updated_at"])
    )


def _valid_v3_holding(item: dict[str, Any]) -> bool:
    required = {
        "schema_version", "code", "input_mode", "amount_snapshot",
        "cumulative_pnl_snapshot", "snapshot_at", "shares", "shares_source",
        "basis_nav", "basis_nav_date", "shares_inference_note", "avg_unit_cost",
        "avg_cost", "buy_date", "notes", "custom_tag_ids", "verification_status",
        "manual_name", "cost_confirmation_required", "created_at", "updated_at",
    }
    item_schema = item.get("schema_version")
    if (
        not required.issubset(item)
        or not isinstance(item_schema, int)
        or isinstance(item_schema, bool)
        or item_schema != SCHEMA_VERSION
    ):
        return False
    if item["input_mode"] not in {"amount_pnl", "shares_cost"}:
        return False
    if not isinstance(item["cost_confirmation_required"], bool):
        return False
    if not (
        _valid_date(item["buy_date"], allow_empty=True)
        and _valid_text(item["notes"])
        and _valid_tags(item["custom_tag_ids"])
        and _valid_identity(item)
        and _valid_datetime(item["created_at"])
        and _valid_datetime(item["updated_at"])
    ):
        return False

    amount = item["amount_snapshot"]
    pnl = item["cumulative_pnl_snapshot"]
    snapshot_at = item["snapshot_at"]
    if amount is None:
        if pnl is not None or snapshot_at is not None:
            return False
    elif not (
        _finite_number(amount) and float(amount) > 0
        and (pnl is None or _finite_number(pnl))
        and _valid_datetime(snapshot_at)
    ):
        return False

    shares = item["shares"]
    if shares is not None and not (_finite_number(shares) and float(shares) > 0):
        return False
    if item["shares_source"] not in {None, "user", "inferred"}:
        return False
    basis_nav = item["basis_nav"]
    if basis_nav is not None and not (_finite_number(basis_nav) and float(basis_nav) > 0):
        return False
    if item["basis_nav_date"] is not None and not _valid_date(item["basis_nav_date"]):
        return False
    if not _valid_text(item["shares_inference_note"], allow_none=True, allow_empty=False):
        return False

    if item["input_mode"] == "amount_pnl":
        if amount is None or item["avg_unit_cost"] is not None or item["avg_cost"] is not None:
            return False
        if shares is None:
            return all(item[field] is None for field in (
                "shares_source", "basis_nav", "basis_nav_date", "shares_inference_note",
            ))
        return (
            item["shares_source"] == "inferred"
            and basis_nav is not None
            and item["basis_nav_date"] is not None
            and item["shares_inference_note"] is not None
        )

    if not item["buy_date"] or shares is None or item["shares_source"] != "user":
        return False
    if any(item[field] is not None for field in ("basis_nav", "basis_nav_date", "shares_inference_note")):
        return False
    avg_unit_cost = item["avg_unit_cost"]
    avg_cost = item["avg_cost"]
    if item["cost_confirmation_required"]:
        return avg_unit_cost is None and avg_cost is None
    return (
        _finite_number(avg_unit_cost) and float(avg_unit_cost) >= 0
        and _finite_number(avg_cost) and float(avg_cost) >= 0
        and float(avg_unit_cost) == float(avg_cost)
    )


def _valid_holdings(data: dict[str, Any], *, schema_version: int) -> bool:
    holdings = data.get("holdings")
    if not isinstance(holdings, list):
        return False
    validators = {1: _valid_v1_holding, 2: _valid_v2_holding, 3: _valid_v3_holding}
    validator = validators[schema_version]
    codes: set[str] = set()
    for item in holdings:
        if not isinstance(item, dict):
            return False
        code = item.get("code")
        if not isinstance(code, str) or re.fullmatch(r"\d{6}", code) is None or code in codes:
            return False
        if not validator(item):
            return False
        codes.add(code)
    updated = data.get("updated")
    if updated is not None and not _valid_datetime(updated):
        return False
    if schema_version == SCHEMA_VERSION:
        if data.get("migration") is not None and not isinstance(data.get("migration"), dict):
            return False
        if not isinstance(data.get("data_status", "ok"), str):
            return False
    return True


def _load() -> tuple[dict[str, Any], bool, tuple[bytes, int] | None]:
    path = Path(FUND_FILE)
    try:
        original = path.read_bytes()
        data = json.loads(original.decode("utf-8"))
    except FileNotFoundError:
        return _empty(), False, None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _empty("corrupt"), True, None
    if not isinstance(data, dict):
        return _empty("corrupt"), True, None
    raw_version = data.get("schema_version")
    if raw_version is not None and (not isinstance(raw_version, int) or isinstance(raw_version, bool)):
        return _empty("corrupt"), True, None
    if raw_version == SCHEMA_VERSION:
        if not _valid_holdings(data, schema_version=SCHEMA_VERSION):
            return _empty("corrupt"), True, None
        data.setdefault("migration", None)
        data.setdefault("data_status", "ok")
        return data, False, None

    if raw_version not in (None, 1, 2):
        return _empty("corrupt"), True, None
    from_schema = 2 if raw_version == 2 else 1
    if not _valid_holdings(data, schema_version=from_schema):
        return _empty("corrupt"), True, None
    return _normalize_legacy(data, from_schema=from_schema), False, (original, from_schema)


def _write_verified_backup(path: Path, original: bytes, *, from_schema: int) -> Path:
    """Create a non-overwriting verified backup beside the user file."""

    backup = _backup_path(path, from_schema)
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("xb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass
        raise OSError(f"V{from_schema} 持仓备份失败：{error}") from error

    try:
        copied = backup.read_bytes()
        verified = (
            len(copied) == len(original)
            and hashlib.sha256(copied).digest() == hashlib.sha256(original).digest()
        )
    except OSError as error:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass
        raise OSError(f"V{from_schema} 持仓备份校验失败：{error}") from error
    if not verified:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass
        raise OSError(f"V{from_schema} 持仓备份校验失败")
    return backup


def _persist_mutation(data: dict[str, Any], legacy: tuple[bytes, int] | None) -> None:
    schema_version = data.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
        or not _valid_holdings(data, schema_version=SCHEMA_VERSION)
    ):
        raise FundPortfolioCorrupt("规范化后的 schema v3 持仓无效；写入已停止，原文件保持不变")
    path = Path(FUND_FILE)
    if legacy is not None:
        original, from_schema = legacy
        try:
            backup = _write_verified_backup(path, original, from_schema=from_schema)
        except OSError as error:
            raise FundPortfolioCorrupt(f"基金持仓备份失败：{error}；迁移已停止，原文件保持不变") from error
        data["migration"] = {
            **(data.get("migration") or {}),
            "from_schema": from_schema,
            "migrated_at": _now().isoformat(),
            "backup_file": backup.name,
            "persisted": True,
        }
    try:
        _save(data)
    except OSError as error:
        raise FundPortfolioCorrupt(f"基金持仓原子写入失败；原文件保持不变：{error}") from error


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
        data, _, _ = _load()
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
        data, corrupt, legacy = _load()
        if corrupt:
            raise FundPortfolioCorrupt("基金持仓文件已损坏；为保护原始数据，本次写入已停止")
        existing = next((item for item in data.get("holdings", []) if item.get("code") == record["code"]), None)
        if existing and not replace:
            raise FundAlreadyExists("该基金已在持仓中；请使用编辑操作更新，不会静默覆盖")

        timestamp = _now().isoformat()
        previous = existing or {}
        input_mode = record.get("input_mode") or "shares_cost"
        preserve_inference = bool(
            existing
            and previous.get("input_mode") == "amount_pnl"
            and input_mode == "amount_pnl"
            and previous.get("amount_snapshot") == record.get("amount_snapshot")
            and previous.get("cumulative_pnl_snapshot") == record.get("cumulative_pnl_snapshot")
        )
        clean = dict(previous)
        clean.update({
            "schema_version": SCHEMA_VERSION,
            "code": record["code"],
            "input_mode": input_mode,
            "buy_date": record.get("buy_date") or "",
            "notes": record.get("notes", ""),
            "custom_tag_ids": list(record.get("custom_tag_ids") or []),
            "verification_status": previous.get("verification_status", record.get("verification_status", "verified")),
            "manual_name": previous.get("manual_name", record.get("manual_name")),
            "cost_confirmation_required": False,
            "created_at": previous.get("created_at") or timestamp,
            "updated_at": timestamp,
        })
        clean.update(_snapshot_fields(record, previous, timestamp))

        if input_mode == "amount_pnl":
            clean.update({
                "shares": previous.get("shares") if preserve_inference else record.get("shares"),
                "shares_source": (
                    previous.get("shares_source")
                    if preserve_inference
                    else record.get("shares_source") if record.get("shares") is not None else None
                ),
                "basis_nav": previous.get("basis_nav") if preserve_inference else record.get("basis_nav"),
                "basis_nav_date": previous.get("basis_nav_date") if preserve_inference else record.get("basis_nav_date"),
                "shares_inference_note": previous.get("shares_inference_note") if preserve_inference else record.get("shares_inference_note"),
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
        _persist_mutation(data, legacy)
    return _public(data)


def delete_fund_holding(code: str) -> dict[str, Any]:
    with _LOCK:
        data, corrupt, legacy = _load()
        if corrupt:
            raise FundPortfolioCorrupt("基金持仓文件已损坏；为保护原始数据，本次写入已停止")
        data["holdings"] = [item for item in data.get("holdings", []) if item.get("code") != code]
        data["updated"] = _now().isoformat()
        data["data_status"] = "ok"
        _persist_mutation(data, legacy)
    return _public(data)
