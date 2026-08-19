from __future__ import annotations

import json

import pytest

from data_sources.credentials import (
    CredentialNotAllowed,
    CredentialWriteNotSupported,
    EnvironmentCredentialStore,
    KeyringCredentialStore,
    MemoryCredentialStore,
)


_FRED_CREDENTIAL = {"fred": ("FRED_API_KEY",)}


@pytest.fixture
def memory_credentials() -> MemoryCredentialStore:
    return MemoryCredentialStore(_FRED_CREDENTIAL)


def test_credential_state_never_serializes_secret(memory_credentials: MemoryCredentialStore):
    memory_credentials.set("fred", "FRED_API_KEY", "secret-value")

    document = memory_credentials.state("fred").to_dict()

    assert document == {
        "configured": True,
        "status": "stored",
        "last_validated_at": None,
        "credential_source": "memory",
    }
    assert "secret-value" not in json.dumps(document)
    assert "secret-value" not in repr(memory_credentials)


def test_environment_store_is_read_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FRED_API_KEY", "secret-value")
    store = EnvironmentCredentialStore(_FRED_CREDENTIAL)

    assert store.get("fred", "FRED_API_KEY") == "secret-value"
    with pytest.raises(CredentialWriteNotSupported):
        store.set("fred", "FRED_API_KEY", "replacement")
    with pytest.raises(CredentialWriteNotSupported):
        store.delete("fred", "FRED_API_KEY")


def test_environment_store_uses_only_requested_names_and_normalizes_blank(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FRED_API_KEY", "   ")
    store = EnvironmentCredentialStore(_FRED_CREDENTIAL)

    assert store.get("fred", "FRED_API_KEY") is None
    assert store.state("fred").to_dict() == {
        "configured": False,
        "status": "unconfigured",
        "last_validated_at": None,
        "credential_source": "environment",
    }


class _UnavailableKeyring:
    def get_password(self, service_name: str, username: str) -> str | None:
        raise RuntimeError("private keyring failure")

    def set_password(self, service_name: str, username: str, value: str) -> None:
        raise RuntimeError("private keyring failure")

    def delete_password(self, service_name: str, username: str) -> None:
        raise RuntimeError("private keyring failure")


def test_keyring_unavailable_never_falls_back_to_plaintext_or_error_detail():
    store = KeyringCredentialStore(_FRED_CREDENTIAL, keyring_module=_UnavailableKeyring())

    assert store.get("fred", "FRED_API_KEY") is None
    state = store.state("fred")

    assert state.to_dict() == {
        "configured": False,
        "status": "credential_store_unavailable",
        "last_validated_at": None,
        "credential_source": "keyring",
    }
    assert "private" not in repr(store)


def test_keyring_service_is_pp03_and_adapter_scoped():
    calls: list[tuple[str, str, str]] = []

    class FakeKeyring:
        def set_password(self, service_name: str, username: str, value: str) -> None:
            calls.append((service_name, username, value))

        def get_password(self, service_name: str, username: str) -> str | None:
            return None

        def delete_password(self, service_name: str, username: str) -> None:
            return None

    KeyringCredentialStore(_FRED_CREDENTIAL, keyring_module=FakeKeyring()).set("fred", "FRED_API_KEY", "secret-value")

    assert calls == [("PP03:fred", "FRED_API_KEY", "secret-value")]


def test_credential_stores_require_declared_adapter_and_environment_names(monkeypatch: pytest.MonkeyPatch):
    environment_lookups: list[str] = []

    class ExplicitEnvironment(dict):
        def get(self, key, default=None):
            environment_lookups.append(key)
            return default

    keyring_calls: list[tuple[str, str]] = []

    class CountingKeyring:
        def get_password(self, service_name: str, username: str):
            keyring_calls.append((service_name, username))
            return None

        def set_password(self, service_name: str, username: str, value: str):
            keyring_calls.append((service_name, username))

        def delete_password(self, service_name: str, username: str):
            keyring_calls.append((service_name, username))

    monkeypatch.setattr("data_sources.credentials.os.environ", ExplicitEnvironment())
    environment = EnvironmentCredentialStore(_FRED_CREDENTIAL)
    keyring = KeyringCredentialStore(_FRED_CREDENTIAL, keyring_module=CountingKeyring())
    memory = MemoryCredentialStore(_FRED_CREDENTIAL)

    for store in (environment, keyring, memory):
        with pytest.raises(CredentialNotAllowed):
            store.get("fred", "UNDECLARED_KEY")
        with pytest.raises(CredentialNotAllowed):
            store.get("", "FRED_API_KEY")
        with pytest.raises(CredentialNotAllowed):
            store.state("unknown")
        with pytest.raises(TypeError):
            store.get("FRED_API_KEY")

    assert environment_lookups == []
    assert keyring_calls == []


def test_keyring_delete_of_missing_item_is_idempotent_not_unavailable():
    class PasswordDeleteError(Exception):
        pass

    class MissingItemKeyring:
        def delete_password(self, service_name: str, username: str) -> None:
            raise PasswordDeleteError()

        def get_password(self, service_name: str, username: str):
            return None

    store = KeyringCredentialStore(_FRED_CREDENTIAL, keyring_module=MissingItemKeyring())

    store.delete("fred", "FRED_API_KEY")

    assert store.state("fred").to_dict()["status"] == "unconfigured"
