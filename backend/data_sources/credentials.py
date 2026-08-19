"""Credential storage boundaries for server-side provider adapters.

Only the store's ``get`` method returns a raw credential, and it is intended
for a provider immediately constructing an authenticated request.  State and
errors deliberately contain no credential material.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
import re
from typing import Mapping, Protocol


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")


class CredentialWriteNotSupported(RuntimeError):
    """Raised when a read-only credential source is asked to mutate state."""


class CredentialStoreUnavailable(RuntimeError):
    """Raised without backend details when the system keyring is unavailable."""


class CredentialNotAllowed(ValueError):
    """Raised before any credential backend access outside the declared scope."""


@dataclass(frozen=True, slots=True)
class CredentialState:
    configured: bool
    status: str
    last_validated_at: str | None
    credential_source: str

    def to_dict(self) -> dict[str, bool | str | None]:
        return {
            "configured": self.configured,
            "status": self.status,
            "last_validated_at": self.last_validated_at,
            "credential_source": self.credential_source,
        }


class CredentialStore(Protocol):
    def get(self, adapter_id: str, env_name: str) -> str | None: ...

    def set(self, adapter_id: str, env_name: str, value: str) -> None: ...

    def delete(self, adapter_id: str, env_name: str) -> None: ...

    def state(self, adapter_id: str) -> CredentialState: ...


class _CredentialScope:
    """A non-empty adapter/environment allowlist derived from Catalog metadata."""

    def __init__(self, adapter_env_names: Mapping[str, tuple[str, ...]]) -> None:
        if not isinstance(adapter_env_names, Mapping):
            raise CredentialNotAllowed("credential scope is required")
        normalized: dict[str, frozenset[str]] = {}
        for adapter_id, names in adapter_env_names.items():
            if not isinstance(adapter_id, str) or not _IDENTIFIER.fullmatch(adapter_id):
                raise CredentialNotAllowed("invalid credential adapter identifier")
            if not isinstance(names, tuple) or not names:
                raise CredentialNotAllowed("adapter credentials must be declared")
            allowed = frozenset(names)
            if any(not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) for name in allowed):
                raise CredentialNotAllowed("invalid credential environment identifier")
            normalized[adapter_id] = allowed
        self._names = normalized

    def names(self, adapter_id: str) -> frozenset[str]:
        if not isinstance(adapter_id, str) or not _IDENTIFIER.fullmatch(adapter_id) or adapter_id not in self._names:
            raise CredentialNotAllowed("credential adapter is not declared")
        return self._names[adapter_id]

    def pair(self, adapter_id: str, env_name: str) -> tuple[str, str]:
        names = self.names(adapter_id)
        if not isinstance(env_name, str) or env_name not in names:
            raise CredentialNotAllowed("credential environment name is not declared")
        return adapter_id, env_name


def _nonblank(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


class MemoryCredentialStore:
    """Test-only in-memory credential store; never select this in production."""

    def __init__(self, adapter_env_names: Mapping[str, tuple[str, ...]]) -> None:
        self._scope = _CredentialScope(adapter_env_names)
        self._values: dict[tuple[str, str], str] = {}

    def get(self, adapter_id: str, env_name: str) -> str | None:
        adapter_id, env_name = self._scope.pair(adapter_id, env_name)
        return self._values.get((adapter_id, env_name))

    def set(self, adapter_id: str, env_name: str, value: str) -> None:
        adapter_id, env_name = self._scope.pair(adapter_id, env_name)
        normalized = _nonblank(value)
        if normalized is None:
            raise ValueError("credential value must not be blank")
        self._values[(adapter_id, env_name)] = normalized

    def delete(self, adapter_id: str, env_name: str) -> None:
        adapter_id, env_name = self._scope.pair(adapter_id, env_name)
        self._values.pop((adapter_id, env_name), None)

    def state(self, adapter_id: str) -> CredentialState:
        self._scope.names(adapter_id)
        configured = any(key_adapter == adapter_id for key_adapter, _ in self._values)
        return CredentialState(configured, "stored" if configured else "unconfigured", None, "memory")


class EnvironmentCredentialStore:
    """Read-only access to explicitly requested environment variable names."""

    def __init__(self, adapter_env_names: Mapping[str, tuple[str, ...]]) -> None:
        self._scope = _CredentialScope(adapter_env_names)

    def get(self, adapter_id: str, env_name: str) -> str | None:
        adapter_id, env_name = self._scope.pair(adapter_id, env_name)
        # Do not enumerate os.environ: callers may request only their declared key.
        return _nonblank(os.environ.get(env_name))

    def set(self, adapter_id: str, env_name: str, value: str) -> None:
        del adapter_id, env_name, value
        raise CredentialWriteNotSupported("environment credentials are read-only")

    def delete(self, adapter_id: str, env_name: str) -> None:
        del adapter_id, env_name
        raise CredentialWriteNotSupported("environment credentials are read-only")

    def state(self, adapter_id: str) -> CredentialState:
        names = self._scope.names(adapter_id)
        configured = any(_nonblank(os.environ.get(env_name)) is not None for env_name in names)
        return CredentialState(configured, "stored" if configured else "unconfigured", None, "environment")


class KeyringCredentialStore:
    """System keyring store with a PP03/adapter namespace and no plaintext fallback."""

    def __init__(self, adapter_env_names: Mapping[str, tuple[str, ...]], *, keyring_module: object | None = None) -> None:
        self._scope = _CredentialScope(adapter_env_names)
        self._unavailable_adapters: set[str] = set()
        if keyring_module is not None:
            self._keyring = keyring_module
        else:
            try:
                self._keyring = importlib.import_module("keyring")
            except Exception:
                self._keyring = None

    @staticmethod
    def _service_name(adapter_id: str) -> str:
        return f"PP03:{adapter_id}"

    def _backend(self, adapter_id: str) -> object:
        if self._keyring is None:
            self._unavailable_adapters.add(adapter_id)
            raise CredentialStoreUnavailable("credential store unavailable")
        return self._keyring

    def get(self, adapter_id: str, env_name: str) -> str | None:
        adapter_id, env_name = self._scope.pair(adapter_id, env_name)
        try:
            return _nonblank(self._backend(adapter_id).get_password(self._service_name(adapter_id), env_name))
        except CredentialStoreUnavailable:
            return None
        except Exception:
            self._unavailable_adapters.add(adapter_id)
            return None

    def set(self, adapter_id: str, env_name: str, value: str) -> None:
        adapter_id, env_name = self._scope.pair(adapter_id, env_name)
        normalized = _nonblank(value)
        if normalized is None:
            raise ValueError("credential value must not be blank")
        try:
            self._backend(adapter_id).set_password(self._service_name(adapter_id), env_name, normalized)
        except CredentialStoreUnavailable:
            raise
        except Exception as error:
            del error
            self._unavailable_adapters.add(adapter_id)
            raise CredentialStoreUnavailable("credential store unavailable") from None

    def delete(self, adapter_id: str, env_name: str) -> None:
        adapter_id, env_name = self._scope.pair(adapter_id, env_name)
        try:
            self._backend(adapter_id).delete_password(self._service_name(adapter_id), env_name)
        except CredentialStoreUnavailable:
            raise
        except Exception as error:
            if type(error).__name__ == "PasswordDeleteError":
                return
            del error
            self._unavailable_adapters.add(adapter_id)
            raise CredentialStoreUnavailable("credential store unavailable") from None

    def state(self, adapter_id: str) -> CredentialState:
        names = self._scope.names(adapter_id)
        if adapter_id in self._unavailable_adapters or self._keyring is None:
            return CredentialState(False, "credential_store_unavailable", None, "keyring")
        configured = any(self.get(adapter_id, env_name) is not None for env_name in names)
        if adapter_id in self._unavailable_adapters:
            return CredentialState(False, "credential_store_unavailable", None, "keyring")
        return CredentialState(configured, "stored" if configured else "unconfigured", None, "keyring")
