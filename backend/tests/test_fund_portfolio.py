"""PP03 基金持仓：只使用测试临时目录，不接触用户真实数据。"""
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
    return path


def _payload(**changes):
    payload = {
        "code": "000001",
        "name": "用户录入基金",
        "amount": 1000,
        "shares": 500,
        "cost": 2,
        "buy_date": "2026-08-16",
        "notes": "长期观察",
        "tag_ids": ["storage"],
    }
    payload.update(changes)
    return payload


def test_fund_portfolio_roundtrip_keeps_truth_fields_separate(isolated_fund_file):
    response = client.post("/api/fund-portfolio/holding", json=_payload())
    assert response.status_code == 200
    holding = response.json()["data"]["holdings"][0]
    assert holding["tag_ids"] == ["storage"]
    assert holding["official_nav"] is None
    assert holding["intraday_estimate"] is None
    assert holding["historical_nav"] is None
    assert isolated_fund_file.exists()
    assert not Path(str(isolated_fund_file) + ".tmp").exists()

    read_back = client.get("/api/fund-portfolio").json()["data"]
    assert read_back["total_amount"] == 1000
    assert read_back["holdings"][0]["name"] == "用户录入基金"


def test_upsert_replaces_same_fund_code_instead_of_duplicating(isolated_fund_file):
    client.post("/api/fund-portfolio/holding", json=_payload())
    result = client.post("/api/fund-portfolio/holding", json=_payload(amount=1800, notes="更新记录")).json()["data"]
    assert len(result["holdings"]) == 1
    assert result["holdings"][0]["amount"] == 1800
    assert result["holdings"][0]["notes"] == "更新记录"


def test_delete_fund_holding(isolated_fund_file):
    client.post("/api/fund-portfolio/holding", json=_payload())
    response = client.delete("/api/fund-portfolio/holding?code=000001")
    assert response.status_code == 200
    assert response.json()["data"]["holdings"] == []


@pytest.mark.parametrize("changes", [
    {"code": "abc"},
    {"name": ""},
    {"amount": -1},
    {"shares": -1},
    {"buy_date": "16/08/2026"},
    {"tag_ids": ["storage", ""]},
])
def test_invalid_fund_holding_is_rejected(isolated_fund_file, changes):
    response = client.post("/api/fund-portfolio/holding", json=_payload(**changes))
    assert response.status_code in (400, 422)
    assert not isolated_fund_file.exists()


def test_corrupt_fund_file_degrades_to_empty_without_fabricating_values(isolated_fund_file):
    isolated_fund_file.write_text("{broken", encoding="utf-8")
    data = client.get("/api/fund-portfolio").json()["data"]
    assert data == {"holdings": [], "total_amount": 0, "updated": None}


def test_corrupt_fund_file_is_not_overwritten_by_a_new_record(isolated_fund_file):
    original = b"{broken-user-data"
    isolated_fund_file.write_bytes(original)

    response = client.post("/api/fund-portfolio/holding", json=_payload())

    assert response.status_code == 409
    assert isolated_fund_file.read_bytes() == original
