"""Atomic, non-secret data-source configuration storage."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from cache_io_lock import CACHE_IO_LOCK

from .catalog import DataSourceCatalog, build_catalog


class ConfigValidationError(ValueError):
    """Raised for malformed, unsafe, or corrupt non-secret configuration."""


_ADAPTER_FIELDS = {
    "enabled", "usage_mode", "daily_budget", "monthly_budget", "per_request_budget",
    "daily_request_limit", "monthly_request_limit", "last_validated_at",
}
_CREDENTIAL_TERMS = ("credential", "secret", "token", "password", "api_key", "apikey", "access_key", "private_key", "authorization", "bearer")
_SAFE_USAGE_MODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _contains_credential_marker(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(marker in lowered for marker in _CREDENTIAL_TERMS)


def _decimal_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigValidationError(f"{field} must be a non-negative decimal string")
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ConfigValidationError(f"{field} must be a non-negative decimal string") from None
    if not decimal.is_finite() or decimal < 0:
        raise ConfigValidationError(f"{field} must be a non-negative decimal string")
    return format(decimal, "f")


def _integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigValidationError(f"{field} must be a non-negative integer")
    return value


class DataSourceConfigStore:
    """Store only operational flags and usage metadata below ``VR_DATA_DIR``."""

    def __init__(self, root: str | os.PathLike[str] | None = None, *, catalog: DataSourceCatalog | None = None) -> None:
        if root is None:
            configured = os.environ.get("VR_DATA_DIR")
            root = Path(configured) if configured and configured.strip() else Path(os.environ.get("USERPROFILE") or Path.home()) / ".vibe-research"
        self.root = Path(root) / "data-sources" / "v1"
        self.path = self.root / "config.json"
        self._catalog = catalog or build_catalog({"sources": []})
        self._adapter_ids = frozenset(adapter.adapter_id for adapter in self._catalog.adapters)

    def _ensure_safe_path(self) -> None:
        # Never follow a symlink at the configuration boundary.  A directory
        # symlink could redirect an atomic replace outside the data root.
        if self.path.is_symlink():
            raise ConfigValidationError("configuration path is unsafe")
        current = self.path.parent
        while current != current.parent:
            if current.exists() and current.is_symlink():
                raise ConfigValidationError("configuration path is unsafe")
            current = current.parent

    def _validate(self, document: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(document, Mapping):
            raise ConfigValidationError("configuration must be an object")
        if any(not isinstance(key, str) or _contains_credential_marker(key) for key in document):
            raise ConfigValidationError("configuration must not contain credential material")
        unknown_top_level = set(document) - {"free_only", "adapters"}
        if unknown_top_level:
            raise ConfigValidationError("unknown configuration field")
        free_only = document.get("free_only", True)
        if not isinstance(free_only, bool):
            raise ConfigValidationError("free_only must be boolean")
        adapters = document.get("adapters", {})
        if not isinstance(adapters, Mapping):
            raise ConfigValidationError("adapters must be an object")
        result_adapters: dict[str, dict[str, Any]] = {}
        for adapter_id, raw_adapter in adapters.items():
            if not isinstance(adapter_id, str) or adapter_id not in self._adapter_ids:
                raise ConfigValidationError("unknown adapter identifier")
            if not isinstance(raw_adapter, Mapping):
                raise ConfigValidationError("adapter configuration must be an object")
            if any(not isinstance(key, str) or _contains_credential_marker(key) for key in raw_adapter):
                raise ConfigValidationError("configuration must not contain credential material")
            if set(raw_adapter) - _ADAPTER_FIELDS:
                raise ConfigValidationError("unknown adapter configuration field")
            entry: dict[str, Any] = {}
            if "enabled" in raw_adapter:
                if not isinstance(raw_adapter["enabled"], bool):
                    raise ConfigValidationError("enabled must be boolean")
                entry["enabled"] = raw_adapter["enabled"]
            if "usage_mode" in raw_adapter:
                usage_mode = raw_adapter["usage_mode"]
                if not isinstance(usage_mode, str) or not _SAFE_USAGE_MODE.fullmatch(usage_mode) or _contains_credential_marker(usage_mode):
                    raise ConfigValidationError("usage_mode is invalid")
                entry["usage_mode"] = usage_mode
            for field in ("daily_budget", "monthly_budget", "per_request_budget"):
                if field in raw_adapter:
                    entry[field] = _decimal_string(raw_adapter[field], field)
            for field in ("daily_request_limit", "monthly_request_limit"):
                if field in raw_adapter:
                    entry[field] = _integer(raw_adapter[field], field)
            if "last_validated_at" in raw_adapter:
                timestamp = raw_adapter["last_validated_at"]
                if timestamp is not None:
                    if not isinstance(timestamp, str):
                        raise ConfigValidationError("last_validated_at must be an ISO timestamp or null")
                    try:
                        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except ValueError:
                        raise ConfigValidationError("last_validated_at must be an ISO timestamp or null") from None
                entry["last_validated_at"] = timestamp
            if _contains_credential_marker(json.dumps(entry, ensure_ascii=False)):
                raise ConfigValidationError("configuration must not contain credential material")
            result_adapters[adapter_id] = entry
        return {"free_only": free_only, "adapters": result_adapters}

    def _atomic_write(self, document: Mapping[str, Any]) -> None:
        self._ensure_safe_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_safe_path()
        descriptor, raw_path = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=self.path.parent)
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary.unlink()
            except OSError:
                pass
            raise

    def load(self) -> dict[str, Any]:
        with CACHE_IO_LOCK:
            self._ensure_safe_path()
            if not self.path.exists():
                return {"free_only": True, "adapters": {}}
            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                raise ConfigValidationError("configuration is corrupt") from None
            return self._validate(document)

    def save(self, document: Mapping[str, Any]) -> dict[str, Any]:
        validated = self._validate(document)
        with CACHE_IO_LOCK:
            self._atomic_write(validated)
        return validated
