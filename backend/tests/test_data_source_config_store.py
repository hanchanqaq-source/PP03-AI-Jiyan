from __future__ import annotations

import json
import math

import pytest

from data_sources.config_store import ConfigValidationError, DataSourceConfigStore
from data_sources.catalog import build_catalog


@pytest.fixture
def store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> DataSourceConfigStore:
    monkeypatch.setenv("VR_DATA_DIR", str(tmp_path / "data"))
    return DataSourceConfigStore(catalog=build_catalog({"sources": []}))


def test_config_round_trips_only_non_secret_state_and_uses_atomic_replace(store: DataSourceConfigStore, monkeypatch: pytest.MonkeyPatch):
    replacements: list[tuple[object, object]] = []
    from data_sources import config_store

    original_replace = config_store.os.replace

    def record_replace(source, destination):
        replacements.append((source, destination))
        original_replace(source, destination)

    monkeypatch.setattr("data_sources.config_store.os.replace", record_replace)

    document = store.save({
        "free_only": True,
        "adapters": {
            "sec-edgar": {
                "enabled": False,
                "usage_mode": "personal_research",
                "daily_budget": "0",
                "monthly_budget": "0",
                "per_request_budget": "0",
                "daily_request_limit": 0,
                "monthly_request_limit": 0,
                "last_validated_at": None,
            },
        },
    })

    assert document["free_only"] is True
    assert document["adapters"]["sec-edgar"]["usage_mode"] == "personal_research"
    assert replacements and replacements[0][1] == store.path
    serialized = store.path.read_text(encoding="utf-8") if store.path.exists() else json.dumps(document)
    assert "secret" not in serialized.lower()
    assert store.load() == document


@pytest.mark.parametrize("adapter_id", ["unknown", "../../escape", "sec-edgar/../secret"])
def test_config_rejects_unknown_or_path_like_adapter_ids(store: DataSourceConfigStore, adapter_id: str):
    with pytest.raises(ConfigValidationError):
        store.save({"free_only": True, "adapters": {adapter_id: {"enabled": False}}})


@pytest.mark.parametrize("invalid", [True, 1.5, -1, math.inf, math.nan])
def test_config_rejects_invalid_integer_counters(store: DataSourceConfigStore, invalid: object):
    with pytest.raises(ConfigValidationError):
        store.save({"free_only": True, "adapters": {"sec-edgar": {"daily_request_limit": invalid}}})


@pytest.mark.parametrize("invalid", ["-1", "NaN", "Infinity", float("nan"), float("inf"), -1])
def test_config_rejects_invalid_budgets(store: DataSourceConfigStore, invalid: object):
    with pytest.raises(ConfigValidationError):
        store.save({"free_only": True, "adapters": {"sec-edgar": {"daily_budget": invalid}}})


def test_config_rejects_credential_keys_and_values(store: DataSourceConfigStore):
    with pytest.raises(ConfigValidationError):
        store.save({"free_only": True, "api_key": "secret-value", "adapters": {}})
    with pytest.raises(ConfigValidationError):
        store.save({"free_only": True, "adapters": {"sec-edgar": {"usage_mode": "Bearer secret-value"}}})


def test_config_corruption_and_symlink_escape_fail_closed(store: DataSourceConfigStore):
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigValidationError):
        store.load()

    external = store.path.parent.parent / "outside.json"
    external.write_text("{}", encoding="utf-8")
    store.path.unlink()
    try:
        store.path.symlink_to(external)
    except OSError:
        pytest.skip("symlinks are unavailable for this test user")
    with pytest.raises(ConfigValidationError):
        store.load()
