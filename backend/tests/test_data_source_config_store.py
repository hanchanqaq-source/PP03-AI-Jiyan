from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from data_sources.config_store import ConfigValidationError, DataSourceConfigStore
from data_sources.catalog import build_catalog


@pytest.fixture
def store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> DataSourceConfigStore:
    monkeypatch.setenv("VR_DATA_DIR", str(tmp_path / "data"))
    return DataSourceConfigStore(catalog=build_catalog({"sources": []}), lock_timeout_seconds=0.01)


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


def test_config_rejects_oversized_valid_disk_document_before_json_materialization(store: DataSourceConfigStore):
    store.save({"free_only": True, "adapters": {}})
    store.path.write_bytes(b" " * (1024 * 1024 + 1) + b"{}")

    with pytest.raises(ConfigValidationError, match="configuration is corrupt"):
        store.load()


@pytest.mark.parametrize("value", ["1e10000", "-0e-10000", "0." + "0" * 10_000])
def test_config_rejects_extreme_or_overlong_decimal_scalars_before_decimal_format(
    store: DataSourceConfigStore, value: str
):
    with pytest.raises(ConfigValidationError):
        store._validate({
            "free_only": True,
            "adapters": {"sec-edgar": {"daily_budget": value}},
        })


def test_config_rejects_huge_exact_integer_before_serialization(store: DataSourceConfigStore):
    with pytest.raises(ConfigValidationError):
        store._validate({
            "free_only": True,
            "adapters": {"sec-edgar": {"daily_request_limit": 1 << 20_000}},
        })


def test_config_normalizes_huge_json_integer_to_bounded_corrupt_state(store: DataSourceConfigStore):
    store.save({"free_only": True, "adapters": {}})
    store.path.write_text(
        '{"free_only":true,"adapters":{"sec-edgar":{"daily_request_limit":'
        + "9" * 5_000
        + "}}}",
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError, match="configuration is corrupt"):
        store.load()


def test_config_rejects_custom_scalar_without_executing_string_methods(store: DataSourceConfigStore):
    class ExplodingString(str):
        called = False

        def explode(self, *_args, **_kwargs):
            self.called = True
            raise AssertionError("custom string method executed")

        strip = lower = __str__ = explode

    value = ExplodingString("1.00")

    with pytest.raises(ConfigValidationError):
        store._validate({
            "free_only": True,
            "adapters": {"sec-edgar": {"daily_budget": value}},
        })
    assert value.called is False


def test_config_rejects_custom_mapping_without_iterating_it(store: DataSourceConfigStore):
    class ExplodingMapping(dict):
        called = False

        def explode(self, *_args, **_kwargs):
            self.called = True
            raise AssertionError("custom mapping method executed")

        __iter__ = keys = items = values = get = explode

    document = ExplodingMapping({"free_only": True, "adapters": {}})

    with pytest.raises(ConfigValidationError):
        store._validate(document)
    assert document.called is False


def test_config_rejects_naive_or_overlong_validation_timestamp(store: DataSourceConfigStore):
    for value in ("2026-08-20T00:00:00", "2026-08-20T00:00:00Z" + "0" * 1_000):
        with pytest.raises(ConfigValidationError):
            store._validate({
                "free_only": True,
                "adapters": {"sec-edgar": {"last_validated_at": value}},
            })


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


@pytest.mark.parametrize("mode, attributes", [
    (stat.S_IFLNK, 0),
    (stat.S_IFDIR, 0x400),  # FILE_ATTRIBUTE_REPARSE_POINT: Windows junction/reparse point.
])
def test_config_rejects_dangling_link_or_windows_reparse_ancestor_before_mkdir(
    store: DataSourceConfigStore, monkeypatch: pytest.MonkeyPatch, mode: int, attributes: int,
):
    unsafe_ancestor = store.root.parent
    store._data_root.mkdir(parents=True)
    original_lstat = __import__("os").lstat
    original_mkdir = Path.mkdir
    calls: list[Path] = []
    mkdir_calls: list[Path] = []

    def fake_lstat(path):
        candidate = Path(path)
        calls.append(candidate)
        if candidate == unsafe_ancestor:
            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
        return original_lstat(path)

    def record_mkdir(path, *args, **kwargs):
        mkdir_calls.append(Path(path))
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr("data_sources.config_store.os.lstat", fake_lstat)
    monkeypatch.setattr("data_sources.config_store.Path.mkdir", record_mkdir)

    with pytest.raises(ConfigValidationError):
        store.save({"free_only": True, "adapters": {}})

    assert unsafe_ancestor in calls
    assert mkdir_calls == []


def test_update_adapter_merges_independent_process_writes(tmp_path):
    root = tmp_path / "data"
    backend_root = Path(__file__).parents[1]
    worker = """
from data_sources.catalog import build_catalog
from data_sources.config_store import DataSourceConfigStore
import sys
store = DataSourceConfigStore(sys.argv[1], catalog=build_catalog({'sources': []}), lock_timeout_seconds=5)
for _ in range(25):
    store.update_adapter(sys.argv[2], {'enabled': True, 'daily_request_limit': 1})
"""
    processes = [
        subprocess.Popen([sys.executable, "-c", worker, str(root), adapter], cwd=backend_root)
        for adapter in ("sec-edgar", "baostock")
    ]
    assert [process.wait(timeout=20) for process in processes] == [0, 0]

    document = DataSourceConfigStore(root, catalog=build_catalog({"sources": []})).load()
    assert set(document["adapters"]) == {"sec-edgar", "baostock"}


def test_config_lock_failures_are_closed_before_writing(store: DataSourceConfigStore, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(store, "_try_lock_file", lambda _handle: False)

    with pytest.raises(ConfigValidationError):
        store.update_adapter("sec-edgar", {"enabled": True})

    assert not store.path.exists()


def test_corrupt_lock_path_fails_closed_before_writing(store: DataSourceConfigStore):
    store.root.mkdir(parents=True)
    store._lock_path.mkdir()

    with pytest.raises(ConfigValidationError):
        store.update_adapter("sec-edgar", {"enabled": True})

    assert not store.path.exists()


def test_relative_data_root_first_component_reparse_is_rejected_before_config_or_lock_open(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VR_DATA_DIR", "relative-data")
    store = DataSourceConfigStore(catalog=build_catalog({"sources": []}))
    original_lstat = os.lstat
    opened: list[Path] = []

    def fake_lstat(path):
        if Path(path) == Path("relative-data"):
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original_lstat(path)

    def record_open(path, *args, **kwargs):
        opened.append(Path(path))
        return original_open(path, *args, **kwargs)

    original_open = Path.open
    monkeypatch.setattr("data_sources.config_store.os.lstat", fake_lstat)
    monkeypatch.setattr("data_sources.config_store.Path.open", record_open)

    with pytest.raises(ConfigValidationError):
        store.save({"free_only": True, "adapters": {}})

    assert opened == []
    assert not (tmp_path / "relative-data").exists()


def test_relative_data_root_reparse_lock_path_is_rejected_before_open(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    store = DataSourceConfigStore("relative-data", catalog=build_catalog({"sources": []}))
    store.root.mkdir(parents=True)
    original_lstat = os.lstat
    opened: list[Path] = []

    def fake_lstat(path):
        if Path(path) == store._lock_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original_lstat(path)

    original_open = Path.open

    def record_open(path, *args, **kwargs):
        opened.append(Path(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("data_sources.config_store.os.lstat", fake_lstat)
    monkeypatch.setattr("data_sources.config_store.Path.open", record_open)

    with pytest.raises(ConfigValidationError):
        store.load()

    assert opened == []


def test_relative_data_root_real_symlink_is_rejected_when_windows_allows_it(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    relative_root = tmp_path / "relative-data"
    try:
        relative_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("relative directory symlinks are unavailable for this Windows user")
    monkeypatch.setenv("VR_DATA_DIR", "relative-data")
    store = DataSourceConfigStore(catalog=build_catalog({"sources": []}))

    with pytest.raises(ConfigValidationError):
        store.save({"free_only": True, "adapters": {}})

    assert not (outside / "data-sources" / "v1" / "config.json").exists()


@pytest.mark.parametrize("timeout", [True, False, "1", None, 0, -1, 0.0, -0.1, math.nan, math.inf, -math.inf, 10**1000])
def test_config_rejects_nonfinite_or_nonpositive_lock_timeout(tmp_path, timeout: object):
    with pytest.raises(ConfigValidationError):
        DataSourceConfigStore(tmp_path / "data", catalog=build_catalog({"sources": []}), lock_timeout_seconds=timeout)
