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
    def get(self, adapter_id: str, env_name: str | None = None) -> str | None: ...

    def set(self, adapter_id: str, env_name: str | None = None, value: str | None = None) -> None: ...

    def delete(self, adapter_id: str, env_name: str | None = None) -> None: ...

    def state(self, adapter_id: str) -> CredentialState: ...


def _name_pair(adapter_id: str, env_name: str | None) -> tuple[str, str]:
    """Allow one-argument environment access without weakening scoped stores."""
    if env_name is None:
        env_name = adapter_id
        adapter_id = ""
    if not isinstance(adapter_id, str) or (adapter_id and not _IDENTIFIER.fullmatch(adapter_id)):
        raise ValueError("invalid credential adapter identifier")
    if not isinstance(env_name, str) or not _IDENTIFIER.fullmatch(env_name):
        raise ValueError("invalid credential environment identifier")
    return adapter_id, env_name


def _nonblank(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


class MemoryCredentialStore:
    """Test-only in-memory credential store; never select this in production."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def get(self, adapter_id: str, env_name: str | None = None) -> str | None:
        adapter_id, env_name = _name_pair(adapter_id, env_name)
        return self._values.get((adapter_id, env_name))

    def set(self, adapter_id: str, env_name: str | None = None, value: str | None = None) -> None:
        adapter_id, env_name = _name_pair(adapter_id, env_name)
        normalized = _nonblank(value)
        if normalized is None:
            raise ValueError("credential value must not be blank")
        self._values[(adapter_id, env_name)] = normalized

    def delete(self, adapter_id: str, env_name: str | None = None) -> None:
        adapter_id, env_name = _name_pair(adapter_id, env_name)
        self._values.pop((adapter_id, env_name), None)

    def state(self, adapter_id: str) -> CredentialState:
        if not isinstance(adapter_id, str) or not _IDENTIFIER.fullmatch(adapter_id):
            raise ValueError("invalid credential adapter identifier")
        configured = any(key_adapter == adapter_id for key_adapter, _ in self._values)
        return CredentialState(configured, "stored" if configured else "unconfigured", None, "memory")


class EnvironmentCredentialStore:
    """Read-only access to explicitly requested environment variable names."""

    def __init__(self, adapter_env_names: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self._adapter_env_names = {
            adapter_id: tuple(names)
            for adapter_id, names in (adapter_env_names or {}).items()
        }
        self._requested_names: dict[str, set[str]] = {}

    def get(self, adapter_id: str, env_name: str | None = None) -> str | None:
        adapter_id, env_name = _name_pair(adapter_id, env_name)
        self._requested_names.setdefault(adapter_id, set()).add(env_name)
        # Do not enumerate os.environ: callers may request only their declared key.
        return _nonblank(os.environ.get(env_name))

    def set(self, adapter_id: str, env_name: str | None = None, value: str | None = None) -> None:
        del adapter_id, env_name, value
        raise CredentialWriteNotSupported("environment credentials are read-only")

    def delete(self, adapter_id: str, env_name: str | None = None) -> None:
        del adapter_id, env_name
        raise CredentialWriteNotSupported("environment credentials are read-only")

    def state(self, adapter_id: str) -> CredentialState:
        if not isinstance(adapter_id, str) or (adapter_id and not _IDENTIFIER.fullmatch(adapter_id)):
            raise ValueError("invalid credential adapter identifier")
        names = self._adapter_env_names.get(adapter_id, ()) or tuple(self._requested_names.get(adapter_id, ()))
        configured = any(_nonblank(os.environ.get(env_name)) is not None for env_name in names)
        return CredentialState(configured, "stored" if configured else "unconfigured", None, "environment")


class KeyringCredentialStore:
    """System keyring store with a PP03/adapter namespace and no plaintext fallback."""

    def __init__(self, adapter_env_names: Mapping[str, tuple[str, ...]] | None = None, *, keyring_module: object | None = None) -> None:
        self._adapter_env_names = {adapter_id: tuple(names) for adapter_id, names in (adapter_env_names or {}).items()}
        self._requested_names: dict[str, set[str]] = {}
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
        if not _IDENTIFIER.fullmatch(adapter_id):
            raise ValueError("invalid credential adapter identifier")
        return f"PP03:{adapter_id}"

    def _backend(self, adapter_id: str) -> object:
        if self._keyring is None:
            self._unavailable_adapters.add(adapter_id)
            raise CredentialStoreUnavailable("credential store unavailable")
        return self._keyring

    def get(self, adapter_id: str, env_name: str | None = None) -> str | None:
        adapter_id, env_name = _name_pair(adapter_id, env_name)
        if not adapter_id:
            raise ValueError("keyring credentials require an adapter identifier")
        self._requested_names.setdefault(adapter_id, set()).add(env_name)
        try:
            return _nonblank(self._backend(adapter_id).get_password(self._service_name(adapter_id), env_name))
        except CredentialStoreUnavailable:
            return None
        except Exception:
            self._unavailable_adapters.add(adapter_id)
            return None

    def set(self, adapter_id: str, env_name: str | None = None, value: str | None = None) -> None:
        adapter_id, env_name = _name_pair(adapter_id, env_name)
        if not adapter_id:
            raise ValueError("keyring credentials require an adapter identifier")
        normalized = _nonblank(value)
        if normalized is None:
            raise ValueError("credential value must not be blank")
        self._requested_names.setdefault(adapter_id, set()).add(env_name)
        try:
            self._backend(adapter_id).set_password(self._service_name(adapter_id), env_name, normalized)
        except CredentialStoreUnavailable:
            raise
        except Exception as error:
            del error
            self._unavailable_adapters.add(adapter_id)
            raise CredentialStoreUnavailable("credential store unavailable") from None

    def delete(self, adapter_id: str, env_name: str | None = None) -> None:
        adapter_id, env_name = _name_pair(adapter_id, env_name)
        if not adapter_id:
            raise ValueError("keyring credentials require an adapter identifier")
        self._requested_names.setdefault(adapter_id, set()).add(env_name)
        try:
            self._backend(adapter_id).delete_password(self._service_name(adapter_id), env_name)
        except CredentialStoreUnavailable:
            raise
        except Exception as error:
            del error
            self._unavailable_adapters.add(adapter_id)
            raise CredentialStoreUnavailable("credential store unavailable") from None

    def state(self, adapter_id: str) -> CredentialState:
        if not isinstance(adapter_id, str) or not _IDENTIFIER.fullmatch(adapter_id):
            raise ValueError("invalid credential adapter identifier")
        if adapter_id in self._unavailable_adapters or self._keyring is None:
            return CredentialState(False, "credential_store_unavailable", None, "keyring")
        names = self._adapter_env_names.get(adapter_id, ()) or tuple(self._requested_names.get(adapter_id, ()))
        configured = any(self.get(adapter_id, env_name) is not None for env_name in names)
        if adapter_id in self._unavailable_adapters:
            return CredentialState(False, "credential_store_unavailable", None, "keyring")
        return CredentialState(configured, "stored" if configured else "unconfigured", None, "keyring")
