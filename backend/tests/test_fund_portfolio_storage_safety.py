"""Storage-safety contract for legacy fund holdings.

All fixtures live under pytest's temporary directory.  No real user file is read.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import fund_portfolio as fp


@pytest.fixture()
def fund_file(tmp_path, monkeypatch):
    path = tmp_path / "fund-portfolio.json"
    monkeypatch.setattr(fp, "FUND_FILE", str(path))
    monkeypatch.setattr(fp, "_now", lambda: datetime(2026, 8, 16, 8, tzinfo=timezone.utc))
    return path


def _v1(*, two=False):
    holdings = [{
        "code": "000001", "name": "旧基金", "amount": 1000,
        "shares": 500, "cost": 2, "buy_date": "2026-01-01",
        "notes": "v1", "tag_ids": ["storage"],
    }]
    if two:
        holdings.append({
            "code": "000002", "name": "第二只", "amount": 800,
            "shares": 400, "cost": 2, "buy_date": "2026-01-02",
            "notes": "keep", "tag_ids": [],
        })
    return json.dumps({"holdings": holdings, "updated": "2026-08-01 10:00"}, ensure_ascii=False).encode()


def _v2():
    return json.dumps({
        "schema_version": 2,
        "holdings": [{
            "code": "000001", "shares": 500, "avg_cost": 2,
            "buy_date": "2026-01-01", "notes": "v2",
            "custom_tag_ids": ["storage"], "verification_status": "verified",
            "manual_name": None, "cost_confirmation_required": False,
            "created_at": "2026-08-01T10:00:00+08:00",
            "updated_at": "2026-08-01T10:00:00+08:00",
        }],
        "updated": None,
    }, ensure_ascii=False).encode()


def _fingerprint(path: Path):
    raw = path.read_bytes()
    stat = path.stat()
    return raw, stat.st_size, stat.st_mtime_ns, hashlib.sha256(raw).hexdigest()


def _record(code="000003", **changes):
    record = {
        "code": code,
        "input_mode": "amount_pnl",
        "amount_snapshot": 1200,
        "cumulative_pnl_snapshot": 100,
        "shares": None,
        "shares_source": None,
        "basis_nav": None,
        "basis_nav_date": None,
        "shares_inference_note": None,
        "avg_cost": None,
        "avg_unit_cost": None,
        "buy_date": "",
        "notes": "主动写入",
        "custom_tag_ids": [],
        "verification_status": "verified",
        "manual_name": None,
    }
    record.update(changes)
    return record


def _v3():
    holding = _record("000001")
    holding.update({
        "schema_version": 3,
        "snapshot_at": "2026-08-16T08:00:00+00:00",
        "cost_confirmation_required": False,
        "created_at": "2026-08-16T08:00:00+00:00",
        "updated_at": "2026-08-16T08:00:00+00:00",
    })
    return json.dumps({
        "schema_version": 3,
        "holdings": [holding],
        "updated": "2026-08-16T08:00:00+00:00",
        "migration": None,
        "data_status": "ok",
    }, ensure_ascii=False).encode()


@pytest.mark.parametrize(("legacy", "expected_from"), [(_v1(), 1), (_v2(), 2)], ids=["v1", "v2"])
def test_legacy_read_normalizes_in_memory_without_any_disk_change(fund_file, legacy, expected_from):
    fund_file.write_bytes(legacy)
    before = _fingerprint(fund_file)

    data = fp.list_fund_holdings()

    assert data["schema_version"] == 3
    assert data["migration"]["from_schema"] == expected_from
    assert data["migration"]["persisted"] is False
    assert data["data_status"] == "legacy_read_only"
    assert _fingerprint(fund_file) == before
    assert list(fund_file.parent.glob("fund-portfolio.v*-backup-*.json")) == []
    assert list(fund_file.parent.glob("*.tmp*")) == []


def test_repeated_legacy_refreshes_are_zero_write(fund_file):
    fund_file.write_bytes(_v1())
    before = _fingerprint(fund_file)

    for _ in range(5):
        assert fp.list_fund_holdings()["holdings"][0]["code"] == "000001"

    assert _fingerprint(fund_file) == before
    assert list(fund_file.parent.iterdir()) == [fund_file]


def test_schema_v3_repeated_reads_do_not_create_meaningless_writes(fund_file):
    fund_file.write_bytes(_v3())
    before = _fingerprint(fund_file)

    for _ in range(5):
        data = fp.list_fund_holdings()
        assert data["schema_version"] == 3
        assert data["data_status"] == "ok"

    assert _fingerprint(fund_file) == before
    assert list(fund_file.parent.iterdir()) == [fund_file]


def _assert_migrated_after_user_action(fund_file, original):
    stored = json.loads(fund_file.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 3
    assert stored["migration"]["persisted"] is True
    backups = [path for path in fund_file.parent.glob("fund-portfolio.v*-backup-*.json") if path.is_file()]
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert list(fund_file.parent.glob("*.tmp*")) == []


def test_first_active_add_backs_up_then_migrates_without_overwriting_existing_backup(fund_file):
    original = _v1()
    fund_file.write_bytes(original)
    sentinel = fund_file.parent / "fund-portfolio.v0.1-backup-20260816-080000.json"
    sentinel.write_bytes(b"existing-backup")

    data = fp.upsert_fund_holding(_record())

    assert {item["code"] for item in data["holdings"]} == {"000001", "000003"}
    assert sentinel.read_bytes() == b"existing-backup"
    valid = [path for path in fund_file.parent.glob("fund-portfolio.v0.1-backup-*.json") if path.read_bytes() == original]
    assert len(valid) == 1
    assert json.loads(fund_file.read_text(encoding="utf-8"))["schema_version"] == 3


def test_first_active_edit_backs_up_then_migrates(fund_file):
    original = _v2()
    fund_file.write_bytes(original)

    data = fp.upsert_fund_holding(_record("000001", notes="edited"), replace=True)

    assert data["holdings"][0]["notes"] == "edited"
    _assert_migrated_after_user_action(fund_file, original)


def test_first_active_delete_backs_up_then_migrates(fund_file):
    original = _v1(two=True)
    fund_file.write_bytes(original)

    data = fp.delete_fund_holding("000001")

    assert [item["code"] for item in data["holdings"]] == ["000002"]
    _assert_migrated_after_user_action(fund_file, original)


def test_backup_failure_leaves_original_unchanged_and_no_temp(fund_file, monkeypatch):
    original = _v1()
    fund_file.write_bytes(original)
    monkeypatch.setattr(fp, "_write_verified_backup", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("backup denied")), raising=False)

    with pytest.raises(fp.FundPortfolioCorrupt, match="备份失败"):
        fp.upsert_fund_holding(_record())

    assert fund_file.read_bytes() == original
    assert list(fund_file.parent.glob("*.tmp*")) == []


def test_atomic_replace_failure_leaves_original_unchanged_and_cleans_temp(fund_file, monkeypatch):
    original = _v2()
    fund_file.write_bytes(original)
    real_replace = fp.os.replace

    def fail_main_replace(source, destination):
        if Path(destination) == fund_file:
            raise OSError("replace denied")
        return real_replace(source, destination)

    monkeypatch.setattr(fp.os, "replace", fail_main_replace)

    with pytest.raises(fp.FundPortfolioCorrupt, match="原文件保持不变"):
        fp.upsert_fund_holding(_record())

    assert fund_file.read_bytes() == original
    assert list(fund_file.parent.glob("*.tmp*")) == []


def test_corrupt_legacy_file_fails_closed_and_never_writes(fund_file):
    original = b'{"schema_version":2,"holdings":[{"code":"bad"}]}'
    fund_file.write_bytes(original)

    data = fp.list_fund_holdings()
    with pytest.raises(fp.FundPortfolioCorrupt):
        fp.upsert_fund_holding(_record())

    assert data["data_status"] == "corrupt"
    assert fund_file.read_bytes() == original
    assert list(fund_file.parent.glob("fund-portfolio.v*-backup-*.json")) == []
    assert list(fund_file.parent.glob("*.tmp*")) == []


def _drop(field):
    return lambda item: item.pop(field)


def _set(field, value):
    return lambda item: item.__setitem__(field, value)


@pytest.mark.parametrize(("schema", "mutate"), [
    ("v1", _drop("name")),
    ("v1", _set("buy_date", "2026/01/01")),
    ("v1", _set("tag_ids", ["storage", 7])),
    ("v1", _set("amount", 0)),
    ("v2", _drop("shares")),
    ("v2", _set("verification_status", "trusted")),
    ("v2", _set("custom_tag_ids", ["storage", 7])),
    ("v2", _set("shares", 0)),
    ("v3", _drop("input_mode")),
    ("v3", _set("input_mode", "quick")),
    ("v3", _set("custom_tag_ids", ["storage", 7])),
    ("v3", _set("amount_snapshot", 0)),
    ("v3", _set("snapshot_at", "not-a-date")),
    ("v3", _drop("created_at")),
], ids=[
    "v1-missing-name", "v1-invalid-date", "v1-non-string-tag", "v1-zero-amount",
    "v2-missing-shares", "v2-invalid-verification", "v2-non-string-tag", "v2-zero-shares",
    "v3-missing-mode", "v3-invalid-mode", "v3-non-string-tag", "v3-zero-amount",
    "v3-invalid-snapshot-date", "v3-missing-created-at",
])
def test_malformed_v1_v2_v3_records_fail_closed_without_writes(fund_file, schema, mutate):
    documents = {
        "v1": json.loads(_v1()),
        "v2": json.loads(_v2()),
        "v3": json.loads(_v3()),
    }
    document = documents[schema]
    mutate(document["holdings"][0])
    original = json.dumps(document, ensure_ascii=False).encode("utf-8")
    fund_file.write_bytes(original)
    before = _fingerprint(fund_file)

    assert fp.list_fund_holdings()["data_status"] == "corrupt"
    with pytest.raises(fp.FundPortfolioCorrupt):
        fp.upsert_fund_holding(_record())

    assert _fingerprint(fund_file) == before
    assert list(fund_file.parent.glob("fund-portfolio.v*-backup-*.json")) == []
    assert list(fund_file.parent.glob("*.tmp*")) == []


def test_v2_numeric_cost_with_confirmation_required_fails_closed_without_writes(fund_file):
    document = json.loads(_v2())
    document["holdings"][0]["cost_confirmation_required"] = True
    original = json.dumps(document, ensure_ascii=False).encode("utf-8")
    fund_file.write_bytes(original)
    before = _fingerprint(fund_file)

    assert fp.list_fund_holdings()["data_status"] == "corrupt"
    with pytest.raises(fp.FundPortfolioCorrupt):
        fp.delete_fund_holding("000001")

    assert _fingerprint(fund_file) == before
    assert list(fund_file.parent.glob("fund-portfolio.v*-backup-*.json")) == []
    assert list(fund_file.parent.glob("*.tmp*")) == []


def test_persist_rejects_invalid_normalized_v3_before_backup_or_write(fund_file):
    original = _v2()
    fund_file.write_bytes(original)
    before = _fingerprint(fund_file)
    normalized = fp._normalize_legacy(json.loads(original), from_schema=2)
    normalized["holdings"][0]["cost_confirmation_required"] = True

    with pytest.raises(fp.FundPortfolioCorrupt, match="schema v3"):
        fp._persist_mutation(normalized, (original, 2))

    assert _fingerprint(fund_file) == before
    assert list(fund_file.parent.glob("fund-portfolio.v*-backup-*.json")) == []
    assert list(fund_file.parent.glob("*.tmp*")) == []


@pytest.mark.parametrize("target", ["document", "holding"])
def test_v3_schema_version_requires_an_integer_shape(fund_file, target):
    document = json.loads(_v3())
    if target == "document":
        document["schema_version"] = 3.0
    else:
        document["holdings"][0]["schema_version"] = 3.0
    original = json.dumps(document, ensure_ascii=False).encode("utf-8")
    fund_file.write_bytes(original)
    before = _fingerprint(fund_file)

    assert fp.list_fund_holdings()["data_status"] == "corrupt"
    with pytest.raises(fp.FundPortfolioCorrupt):
        fp.upsert_fund_holding(_record())

    assert _fingerprint(fund_file) == before
    assert list(fund_file.parent.glob("fund-portfolio.v*-backup-*.json")) == []


@pytest.mark.parametrize("schema", ["v1", "v2", "v3"])
def test_duplicate_fund_codes_fail_closed_before_edit_or_delete_can_collapse_rows(fund_file, schema):
    documents = {
        "v1": json.loads(_v1(two=True)),
        "v2": json.loads(_v2()),
        "v3": json.loads(_v3()),
    }
    document = documents[schema]
    duplicate = dict(document["holdings"][0])
    document["holdings"].append(duplicate)
    original = json.dumps(document, ensure_ascii=False).encode("utf-8")
    fund_file.write_bytes(original)
    before = _fingerprint(fund_file)

    assert fp.list_fund_holdings()["data_status"] == "corrupt"
    with pytest.raises(fp.FundPortfolioCorrupt):
        fp.delete_fund_holding("000001")

    assert _fingerprint(fund_file) == before
    assert list(fund_file.parent.glob("fund-portfolio.v*-backup-*.json")) == []


@pytest.mark.parametrize("cosmetic_change", [
    {"notes": "只改备注"},
    {"custom_tag_ids": ["custom:核心"]},
], ids=["note-only", "tag-only"])
def test_cosmetic_amount_pnl_edit_preserves_existing_inference_and_snapshot(fund_file, cosmetic_change):
    original = fp.upsert_fund_holding(_record(
        "000001", shares=800, shares_source="inferred", basis_nav=1.5,
        basis_nav_date="2026-08-14", shares_inference_note="原始推算依据",
    ))["holdings"][0]

    edited = fp.upsert_fund_holding(_record(
        "000001", shares=600, shares_source="inferred", basis_nav=2.0,
        basis_nav_date="2026-08-15", shares_inference_note="不应替换的最新推算",
        **cosmetic_change,
    ), replace=True)["holdings"][0]

    assert edited["snapshot_at"] == original["snapshot_at"]
    assert edited["shares"] == original["shares"] == 800
    assert edited["shares_source"] == "inferred"
    assert edited["basis_nav"] == 1.5
    assert edited["basis_nav_date"] == "2026-08-14"
    assert edited["shares_inference_note"] == "原始推算依据"


def test_amount_change_accepts_new_inference_and_refreshes_snapshot(fund_file, monkeypatch):
    original = fp.upsert_fund_holding(_record(
        "000001", shares=800, shares_source="inferred", basis_nav=1.5,
        basis_nav_date="2026-08-14", shares_inference_note="原始推算依据",
    ))["holdings"][0]
    monkeypatch.setattr(fp, "_now", lambda: datetime(2026, 8, 16, 9, tzinfo=timezone.utc))

    edited = fp.upsert_fund_holding(_record(
        "000001", amount_snapshot=1400, shares=700, shares_source="inferred",
        basis_nav=2.0, basis_nav_date="2026-08-15", shares_inference_note="新金额推算依据",
    ), replace=True)["holdings"][0]

    assert edited["snapshot_at"] != original["snapshot_at"]
    assert edited["shares"] == 700
    assert edited["basis_nav"] == 2.0
    assert edited["basis_nav_date"] == "2026-08-15"


def test_pnl_change_may_clear_inference_when_reliable_nav_is_unavailable(fund_file, monkeypatch):
    original = fp.upsert_fund_holding(_record(
        "000001", shares=800, shares_source="inferred", basis_nav=1.5,
        basis_nav_date="2026-08-14", shares_inference_note="原始推算依据",
    ))["holdings"][0]
    monkeypatch.setattr(fp, "_now", lambda: datetime(2026, 8, 16, 9, tzinfo=timezone.utc))

    edited = fp.upsert_fund_holding(_record(
        "000001", cumulative_pnl_snapshot=120, shares=None, shares_source=None,
        basis_nav=None, basis_nav_date=None, shares_inference_note=None,
    ), replace=True)["holdings"][0]

    assert edited["snapshot_at"] != original["snapshot_at"]
    assert edited["shares"] is None
    assert edited["shares_source"] is None
    assert edited["basis_nav"] is None
    assert edited["basis_nav_date"] is None


def test_editing_in_memory_v1_migration_preserves_unverified_identity(fund_file):
    original = _v1()
    fund_file.write_bytes(original)
    migrated = fp.list_fund_holdings()["holdings"][0]
    assert migrated["verification_status"] == "manual_unverified"

    edited = fp.upsert_fund_holding(_record(
        "000001", notes="编辑备注", verification_status="verified", manual_name=None,
    ), replace=True)["holdings"][0]

    assert edited["verification_status"] == "manual_unverified"
    assert edited["manual_name"] == "旧基金"
    _assert_migrated_after_user_action(fund_file, original)


def test_backup_verification_read_failure_removes_failed_backup(fund_file, monkeypatch):
    original = _v1()
    fund_file.write_bytes(original)
    real_read_bytes = Path.read_bytes

    def fail_backup_read(path):
        if path != fund_file:
            raise OSError("verification read denied")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_backup_read)

    with pytest.raises(fp.FundPortfolioCorrupt, match="备份失败"):
        fp.upsert_fund_holding(_record())

    assert real_read_bytes(fund_file) == original
    assert list(fund_file.parent.glob("fund-portfolio.v*-backup-*.json")) == []


def test_backup_hash_mismatch_removes_failed_backup(fund_file, monkeypatch):
    original = _v1()
    fund_file.write_bytes(original)
    real_read_bytes = Path.read_bytes

    def mismatch_backup_read(path):
        if path != fund_file:
            return b"mismatched-backup-view"
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", mismatch_backup_read)

    with pytest.raises(fp.FundPortfolioCorrupt, match="备份失败"):
        fp.upsert_fund_holding(_record())

    assert real_read_bytes(fund_file) == original
    assert list(fund_file.parent.glob("fund-portfolio.v*-backup-*.json")) == []


def test_fund_mutations_never_touch_stock_portfolio_file(fund_file):
    stock_file = fund_file.parent / "portfolio.json"
    stock_file.write_bytes(b'{"holdings":[{"code":"600519","shares":1,"cost":1}]}')
    before = _fingerprint(stock_file)

    fp.upsert_fund_holding(_record())
    fp.list_fund_holdings()
    fp.delete_fund_holding("000003")

    assert _fingerprint(stock_file) == before
