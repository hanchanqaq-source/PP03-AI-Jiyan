"""Stage A RED for the restored, storage-isolated fund portfolio API."""

from fastapi.testclient import TestClient

import app as app_module


client = TestClient(app_module.app)


def test_fund_portfolio_read_api_exists_without_touching_stock_portfolio():
    response = client.get("/api/fund-portfolio")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "schema_version": 3,
        "holdings": [],
        "total_cost": 0.0,
        "updated": None,
        "migration": None,
        "data_status": "ok",
    }
