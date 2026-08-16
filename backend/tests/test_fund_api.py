from __future__ import annotations

from fastapi.testclient import TestClient

import app as app_module
import fund_portfolio as fp

client = TestClient(app_module.app)


class FakeFundService:
    def __init__(self):
        self.calls = []

    def search_funds(self, query, force_refresh=False):
        self.calls.append(("search", query, force_refresh))
        return {"data": [{"code": "000001", "name": "测试基金"}], "meta": {"status": "disclosed"}}

    def get_fund_analysis(self, code, force_refresh=False):
        self.calls.append(("analysis", code, force_refresh))
        return {
            "code": code,
            "profile": {"data": {"name": "测试基金"}, "meta": {"status": "disclosed"}},
            "latest_nav": {"data": {"unit_nav": 1.2, "nav_date": "2026-08-14"}, "meta": {"status": "official"}},
            "holdings": {"data": None, "meta": {"status": "error", "message": "持仓源失败"}},
        }

    def get_portfolio_analysis(self, holdings, force_refresh=False):
        self.calls.append(("portfolio", holdings, force_refresh))
        return {"overview": {"fund_count": len(holdings)}, "holdings": holdings, "overlap": []}


def test_fund_search_and_analysis_are_read_only_for_user_portfolio(tmp_path, monkeypatch):
    fake = FakeFundService()
    user_file = tmp_path / "fund-portfolio.json"
    monkeypatch.setattr(fp, "FUND_FILE", str(user_file))
    monkeypatch.setattr(app_module.fund_service, "get_service", lambda: fake)
    search = client.get("/api/funds/search?q=测试")
    analysis = client.get("/api/funds/000001/analysis")
    assert search.status_code == 200
    assert search.json()["data"]["data"][0]["name"] == "测试基金"
    assert analysis.status_code == 200
    assert analysis.json()["data"]["holdings"]["meta"]["status"] == "error"
    assert not user_file.exists()


def test_refresh_forces_all_fund_capabilities(monkeypatch):
    fake = FakeFundService()
    monkeypatch.setattr(app_module.fund_service, "get_service", lambda: fake)
    response = client.post("/api/funds/000001/refresh")
    assert response.status_code == 200
    assert ("analysis", "000001", True) in fake.calls


def test_portfolio_analysis_uses_migrated_user_truth_without_returning_500(tmp_path, monkeypatch):
    fake = FakeFundService()
    user_file = tmp_path / "fund-portfolio.json"
    monkeypatch.setattr(fp, "FUND_FILE", str(user_file))
    monkeypatch.setattr(app_module.fund_service, "get_service", lambda: fake)
    client.post("/api/fund-portfolio/holding", json={
        "code": "000001", "shares": 100, "avg_cost": 1.0, "buy_date": "2026-08-01",
        "notes": "", "custom_tag_ids": [], "verification_status": "verified", "manual_name": None,
        "replace": False,
    })
    response = client.get("/api/fund-portfolio/analysis")
    assert response.status_code == 200
    assert response.json()["data"]["overview"]["fund_count"] == 1
    assert fake.calls[-1][0] == "portfolio"


def test_fund_api_rejects_invalid_or_empty_queries():
    assert client.get("/api/funds/search?q=").status_code == 422
    assert client.get("/api/funds/not-a-code/analysis").status_code == 422

