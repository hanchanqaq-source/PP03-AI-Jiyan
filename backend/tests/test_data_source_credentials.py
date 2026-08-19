from __future__ import annotations

import json

import pytest

from data_sources.credentials import (
    CredentialWriteNotSupported,
    EnvironmentCredentialStore,
    KeyringCredentialStore,
    MemoryCredentialStore,
)


@pytest.fixture
def memory_credentials() -> MemoryCredentialStore:
    return MemoryCredentialStore()


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
    store = EnvironmentCredentialStore()

    assert store.get("FRED_API_KEY") == "secret-value"
    with pytest.raises(CredentialWriteNotSupported):
        store.set("FRED_API_KEY", "replacement")
    with pytest.raises(CredentialWriteNotSupported):
        store.delete("FRED_API_KEY")


def test_environment_store_uses_only_requested_names_and_normalizes_blank(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FRED_API_KEY", "   ")
    store = EnvironmentCredentialStore({"fred": ("FRED_API_KEY",)})

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
    store = KeyringCredentialStore(keyring_module=_UnavailableKeyring())

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

    KeyringCredentialStore(keyring_module=FakeKeyring()).set("fred", "FRED_API_KEY", "secret-value")

    assert calls == [("PP03:fred", "FRED_API_KEY", "secret-value")]
