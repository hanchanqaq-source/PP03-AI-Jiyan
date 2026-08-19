from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from fastapi.testclient import TestClient
import pytest

import app as app_module
from data_sources import api as api_module
from data_sources.catalog import build_catalog
from data_sources.config_store import DataSourceConfigStore
from data_sources.credentials import MemoryCredentialStore
from data_sources.service import DataSourceService
from data_sources.usage_store import UsageStore


NOW = datetime(2026, 8, 20, 4, 5, 6, tzinfo=timezone.utc)


class _EmptyHealthService:
    def list_sources(self):
        return []

    def start_run(self, scope: str, *, excluded_adapter_ids=()):
        return {"run_id": "d" * 20}


class _NoCallRegistry:
    def adapter(self, adapter_id: str):
        raise AssertionError("cost and usage reads must not resolve a provider")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    catalog = build_catalog({"sources": []})
    scope = {
        adapter.adapter_id: tuple(adapter.credential_env_names)
        for adapter in catalog.adapters
        if adapter.credential_env_names
    }
    credentials = MemoryCredentialStore(scope)
    config = DataSourceConfigStore(tmp_path / "config", catalog=catalog)
    usage = UsageStore(tmp_path / "usage")
    service = DataSourceService(
        catalog_builder=lambda: catalog,
        health_service_factory=_EmptyHealthService,
        config_store=config,
        credential_store=credentials,
        usage_store=usage,
        provider_registry=_NoCallRegistry(),
        now_factory=lambda: NOW,
    )
    monkeypatch.setattr(api_module, "_service", service)
    return TestClient(app_module.app), config, credentials, usage


def _seed_usage(usage: UsageStore) -> None:
    usage.reserve(
        "fmp", estimated_cost=Decimal("0.10"), daily_budget=Decimal("1.00"),
        monthly_budget=Decimal("5.00"), now=NOW - timedelta(seconds=2),
        reservation_id="completed-reservation",
    )
    usage.reconcile(
        "fmp", reservation_id="completed-reservation", actual_cost=Decimal("0.07"),
        request_count=1, status="succeeded", units=Decimal("3.5"),
        now=NOW - timedelta(seconds=1),
    )
    usage.reserve(
        "fmp", estimated_cost=Decimal("0.10"), daily_budget=Decimal("1.00"),
        monthly_budget=Decimal("5.00"), now=NOW, reservation_id="open-reservation",
    )


def test_usage_api_returns_utc_period_exact_decimals_status_counts_and_no_ids(harness):
    client, _config, _credentials, usage = harness
    _seed_usage(usage)

    response = client.get("/api/data-sources/usage", params={"adapter_id": "fmp"})

    assert response.status_code == 200
    assert response.json() == {
        "as_of": "2026-08-20T04:05:06Z",
        "timezone": "UTC",
        "adapters": [{
            "adapter_id": "fmp", "day": "2026-08-20", "month": "2026-08",
            "daily_cost": "0.17", "monthly_cost": "0.17",
            "daily_request_count": 1, "monthly_request_count": 1,
            "daily_units": "3.5", "monthly_units": "3.5",
            "status_counts": {"reserved": 1, "succeeded": 1},
            "open_reservations": 1,
        }],
    }
    serialized = response.text.lower()
    assert "completed-reservation" not in serialized
    assert "open-reservation" not in serialized
    for forbidden in ("secret", "token", "cookie", "holding", "account"):
        assert forbidden not in serialized


def test_cost_api_reports_budget_usage_and_remaining_as_decimal_strings(harness):
    client, config, credentials, usage = harness
    config.save({
        "free_only": False,
        "adapters": {"fmp": {
            "enabled": False, "daily_budget": "1.00", "monthly_budget": "5.00",
            "per_request_budget": "0.10", "usage_mode": "paid_api",
        }},
    })
    credentials.set("fmp", "FMP_API_KEY", "cost-api-secret")
    _seed_usage(usage)

    response = client.get("/api/data-sources/cost", params={"adapter_id": "fmp"})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "as_of": "2026-08-20T04:05:06Z", "timezone": "UTC", "free_only": False,
        "adapters": [{
            "adapter_id": "fmp", "billing_model": "paid_api", "enabled": False,
            "credential_configured": True, "status": "disabled",
            "day": "2026-08-20", "month": "2026-08",
            "daily_budget": "1.00", "monthly_budget": "5.00",
            "per_request_budget": "0.10", "daily_cost": "0.17",
            "monthly_cost": "0.17", "daily_remaining": "0.83",
            "monthly_remaining": "4.83", "open_reservations": 1,
        }],
    }
    assert "cost-api-secret" not in json.dumps(body)


def test_usage_and_cost_unknown_adapter_are_404(harness):
    client, _config, _credentials, _usage = harness
    assert client.get("/api/data-sources/usage", params={"adapter_id": "not-real"}).status_code == 404
    assert client.get("/api/data-sources/cost", params={"adapter_id": "not-real"}).status_code == 404


@pytest.mark.parametrize("endpoint", ["usage", "cost"])
def test_corrupt_usage_ledger_is_503_without_partial_zero_rows(harness, endpoint):
    client, _config, _credentials, usage = harness
    usage.root.mkdir(parents=True, exist_ok=True)
    usage.path.write_text('{"version":1,"records":"not-a-list"}', encoding="utf-8")

    response = client.get(f"/api/data-sources/{endpoint}", params={"adapter_id": "fmp"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "usage_store_unavailable"
    assert "daily_cost" not in response.text
    assert "monthly_cost" not in response.text
