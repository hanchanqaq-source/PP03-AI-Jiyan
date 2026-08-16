"""PP03 V0.2 fund holdings: every test uses an isolated temporary user file."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
import fund_portfolio as fp

client = TestClient(app_module.app)


@pytest.fixture()
def isolated_fund_file(tmp_path, monkeypatch):
    path = tmp_path / "fund-portfolio.json"
    monkeypatch.setattr(fp, "FUND_FILE", str(path))
    monkeypatch.setattr(fp, "_now", lambda: datetime(2026, 8, 16, 8, tzinfo=timezone.utc))
    return path


def _payload(**changes):
    payload = {
        "code": "000001",
        "shares": 500,
        "avg_cost": 2,
        "buy_date": "2026-08-16",
        "notes": "长期观察",
        "custom_tag_ids": ["storage"],
        "verification_status": "verified",
        "manual_name": None,
        "replace": False,
    }
    payload.update(changes)
    return payload


def test_v3_exact_roundtrip_saves_only_user_truth_fields(isolated_fund_file):
    response = client.post("/api/fund-portfolio/holding", json=_payload())
    assert response.status_code == 200
    holding = response.json()["data"]["holdings"][0]
    assert holding["code"] == "000001"
    assert holding["shares"] == 500
    assert holding["avg_cost"] == 2
    assert holding["custom_tag_ids"] == ["storage"]
    assert holding["input_mode"] == "shares_cost"
    assert holding["shares_source"] == "user"
    assert holding["avg_unit_cost"] == 2
    assert holding["cost_confirmation_required"] is False
    assert "official_nav" not in holding
    assert "fund_name" not in holding
    assert response.json()["data"]["total_cost"] == 1000
    assert not Path(str(isolated_fund_file) + ".tmp").exists()


def test_duplicate_create_is_rejected_and_explicit_replace_edits_only_that_fund(isolated_fund_file):
    assert client.post("/api/fund-portfolio/holding", json=_payload()).status_code == 200
    duplicate = client.post("/api/fund-portfolio/holding", json=_payload(notes="不应覆盖"))
    assert duplicate.status_code == 409
    edited = client.post("/api/fund-portfolio/holding", json=_payload(notes="已确认更新", replace=True))
    assert edited.status_code == 200
    assert len(edited.json()["data"]["holdings"]) == 1
    assert edited.json()["data"]["holdings"][0]["notes"] == "已确认更新"


def test_delete_fund_holding(isolated_fund_file):
    client.post("/api/fund-portfolio/holding", json=_payload())
    response = client.delete("/api/fund-portfolio/holding?code=000001")
    assert response.status_code == 200
    assert response.json()["data"]["holdings"] == []


@pytest.mark.parametrize("changes", [
    {"code": "abc"},
    {"shares": -1},
    {"avg_cost": -1},
    {"buy_date": "16/08/2026"},
    {"custom_tag_ids": ["storage", ""]},
    {"verification_status": "manual_unverified", "manual_name": ""},
])
def test_invalid_v2_holding_is_rejected(isolated_fund_file, changes):
    response = client.post("/api/fund-portfolio/holding", json=_payload(**changes))
    assert response.status_code in (400, 422)
    assert not isolated_fund_file.exists()


def test_v01_migration_backs_up_original_bytes_and_requires_cost_confirmation(isolated_fund_file):
    original = (
        '{"holdings":[{"code":"000001","name":"旧名称","amount":1000,'
        '"shares":500,"cost":2,"buy_date":"2026-01-01","notes":"不能丢",'
        '"tag_ids":["storage"]}],"updated":"2026-08-01 10:00"}'
    ).encode("utf-8")
    isolated_fund_file.write_bytes(original)
    data = fp.list_fund_holdings()
    backups = list(isolated_fund_file.parent.glob("fund-portfolio.v0.1-backup-*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    holding = data["holdings"][0]
    assert holding["code"] == "000001"
    assert holding["shares"] == 500
    assert holding["avg_cost"] is None
    assert holding["legacy_cost"] == 2
    assert holding["legacy_amount"] == 1000
    assert holding["legacy_name"] == "旧名称"
    assert holding["notes"] == "不能丢"
    assert holding["custom_tag_ids"] == ["storage"]
    assert holding["input_mode"] == "shares_cost"
    assert holding["shares_source"] == "user"
    assert holding["amount_snapshot"] is None
    assert holding["avg_unit_cost"] is None
    assert holding["cost_confirmation_required"] is True
    assert data["migration"]["cost_confirmation_required_count"] == 1
    stored = isolated_fund_file.read_text(encoding="utf-8")
    assert '"schema_version": 3' in stored


def test_v2_migration_backs_up_original_bytes_and_marks_exact_mode(isolated_fund_file):
    original = (
        '{"schema_version":2,"holdings":[{"code":"000001","shares":500,'
        '"avg_cost":2,"buy_date":"2026-01-01","notes":"保留备注",'
        '"custom_tag_ids":["storage"],"verification_status":"verified",'
        '"manual_name":null,"cost_confirmation_required":false,'
        '"created_at":"2026-08-01T10:00:00+08:00",'
        '"updated_at":"2026-08-01T10:00:00+08:00"}],"updated":null}'
    ).encode("utf-8")
    isolated_fund_file.write_bytes(original)

    data = fp.list_fund_holdings()

    backups = list(isolated_fund_file.parent.glob("fund-portfolio.v2-backup-*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    holding = data["holdings"][0]
    assert data["schema_version"] == 3
    assert holding["input_mode"] == "shares_cost"
    assert holding["shares_source"] == "user"
    assert holding["shares"] == 500
    assert holding["avg_unit_cost"] == 2
    assert holding["avg_cost"] == 2
    assert holding["amount_snapshot"] is None
    assert holding["notes"] == "保留备注"
    assert holding["custom_tag_ids"] == ["storage"]


def test_quick_mode_persists_user_snapshot_and_inferred_share_provenance(isolated_fund_file):
    data = fp.upsert_fund_holding({
        "code": "000001",
        "input_mode": "amount_pnl",
        "amount_snapshot": 1000,
        "cumulative_pnl_snapshot": 88.8,
        "shares": 800,
        "shares_source": "inferred",
        "basis_nav": 1.25,
        "basis_nav_date": "2026-08-14",
        "shares_inference_note": "按用户金额快照与正式净值推算，非用户确认份额",
        "avg_cost": None,
        "avg_unit_cost": None,
        "buy_date": "",
        "notes": "快速录入",
        "custom_tag_ids": ["storage"],
    })

    holding = data["holdings"][0]
    assert holding["input_mode"] == "amount_pnl"
    assert holding["amount_snapshot"] == 1000
    assert holding["cumulative_pnl_snapshot"] == 88.8
    assert holding["snapshot_at"] == "2026-08-16T08:00:00+00:00"
    assert holding["shares"] == 800
    assert holding["shares_source"] == "inferred"
    assert holding["basis_nav"] == 1.25
    assert holding["basis_nav_date"] == "2026-08-14"


def test_real_shares_switch_to_exact_mode_without_erasing_user_snapshot(isolated_fund_file):
    fp.upsert_fund_holding({
        "code": "000001",
        "input_mode": "amount_pnl",
        "amount_snapshot": 1000,
        "cumulative_pnl_snapshot": None,
        "shares": 800,
        "shares_source": "inferred",
        "basis_nav": 1.25,
        "basis_nav_date": "2026-08-14",
        "shares_inference_note": "推算份额",
        "avg_cost": None,
        "avg_unit_cost": None,
        "buy_date": "",
        "notes": "先快速录入",
        "custom_tag_ids": [],
    })

    data = fp.upsert_fund_holding({
        "code": "000001",
        "input_mode": "shares_cost",
        "shares": 810,
        "shares_source": "user",
        "avg_cost": 1.1,
        "avg_unit_cost": 1.1,
        "buy_date": "2026-08-01",
        "notes": "已补真实份额",
        "custom_tag_ids": [],
    }, replace=True)

    holding = data["holdings"][0]
    assert holding["input_mode"] == "shares_cost"
    assert holding["shares"] == 810
    assert holding["shares_source"] == "user"
    assert holding["amount_snapshot"] == 1000
    assert holding["cumulative_pnl_snapshot"] is None
    assert holding["snapshot_at"] == "2026-08-16T08:00:00+00:00"


def test_corrupt_fund_file_degrades_to_empty_and_is_never_overwritten(isolated_fund_file):
    original = b"{broken-user-data"
    isolated_fund_file.write_bytes(original)
    data = fp.list_fund_holdings()
    response = client.post("/api/fund-portfolio/holding", json=_payload())
    assert data["holdings"] == []
    assert data["data_status"] == "corrupt"
    assert response.status_code == 409
    assert isolated_fund_file.read_bytes() == original
