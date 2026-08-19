from __future__ import annotations

import sys

import pytest

from data_sources.catalog import build_catalog
from data_sources.provider_registry import ProviderRegistry


def test_registry_lists_unique_catalog_aligned_adapter_ids_without_loading_optional_clients():
    sys.modules.pop("baostock", None)
    sys.modules.pop("yfinance", None)

    registry = ProviderRegistry(build_catalog({"sources": []}))

    identifiers = registry.available_adapter_ids()
    assert identifiers == tuple(sorted(set(identifiers)))
    assert {"baostock", "yahoo-finance", "sec-edgar", "gdelt", "imf"} <= set(identifiers)
    assert set(identifiers) <= {adapter.adapter_id for adapter in build_catalog({"sources": []}).adapters}
    assert "baostock" not in sys.modules
    assert "yfinance" not in sys.modules


def test_registry_constructs_only_requested_adapter_and_keeps_optional_dependency_failure_local(monkeypatch):
    registry = ProviderRegistry(build_catalog({"sources": []}))

    baostock = registry.adapter("baostock")
    assert baostock.descriptor.adapter_id == "baostock"
    calls: list[str] = []

    def unavailable():
        calls.append("baostock")
        raise ModuleNotFoundError("baostock")

    monkeypatch.setattr(baostock, "_client_loader", unavailable)
    assert baostock.probe("stock_history") == {
        "status": "optional_dependency_unavailable",
        "connected": False,
    }
    assert calls == ["baostock"]
    assert registry.adapter("gdelt").descriptor.adapter_id == "gdelt"


def test_registry_rejects_unknown_or_catalog_misaligned_adapter_ids():
    registry = ProviderRegistry(build_catalog({"sources": []}))

    with pytest.raises(KeyError, match="unknown provider adapter"):
        registry.adapter("not-a-provider")
