import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from asef.ksef import KsefClient, KsefError, Secrets


def test_systemd_credential_uses_read_only_token_and_memory_refresh(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    credential = tmp_path / "asef_prod_1234567890"
    credential.write_text("sample-token\n")
    credential.chmod(0o600)
    secrets = Secrets("prod", "1234567890")
    assert secrets.get("ksef_token") == "sample-token"
    secrets.set("refresh_token", "short-lived-refresh")
    assert Secrets("prod", "1234567890").get("refresh_token") == "short-lived-refresh"
    assert credential.read_text() == "sample-token\n"
    with pytest.raises(KsefError, match="zarządzany przez systemd"):
        secrets.set("ksef_token", "new-token")

    credential.chmod(0o644)
    with pytest.raises(KsefError, match="tylko dla właściciela"):
        secrets.get("ksef_token")


def test_missing_systemd_credential_does_not_use_keyring(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setattr("asef.ksef.keyring.get_password", lambda *_: pytest.fail("keyring read"))
    monkeypatch.setattr("asef.ksef.keyring.set_password", lambda *_: pytest.fail("keyring write"))
    with pytest.raises(KsefError, match="Brak poświadczenia ASEF"):
        Secrets("prod", "1234567890")


def test_replacing_ksef_token_invalidates_old_refresh(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    stored = {
        "ksef_token": "old-token",
        "refresh_token": "old-refresh",
        "refresh_valid_until": "2999-01-01T00:00:00Z",
    }
    monkeypatch.setattr("asef.ksef.keyring.get_password", lambda _service, key: stored.get(key))
    monkeypatch.setattr("asef.ksef.keyring.set_password", lambda _service, key, value: stored.__setitem__(key, value))
    monkeypatch.setattr("asef.ksef.keyring.delete_password", lambda _service, key: stored.pop(key, None))

    secrets = Secrets("test", "1234567890")
    secrets.set("ksef_token", "new-token")

    assert secrets.get("ksef_token") == "new-token"
    assert secrets.get("refresh_token") is None
    assert secrets.get("refresh_valid_until") is None


def test_systemd_certificate_credential_works_without_token_or_keyring(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setattr("asef.ksef.keyring.get_password", lambda *_: pytest.fail("keyring read"))
    monkeypatch.setattr("asef.ksef.keyring.set_password", lambda *_: pytest.fail("keyring write"))
    config = {
        "fingerprint": "a" * 64,
        "password": "fictional key password",
        "subjectIdentifierType": "certificateSubject",
    }
    credential = tmp_path / "asef_cert_prod_1234567890"
    credential.write_text(json.dumps(config))
    credential.chmod(0o600)

    secrets = Secrets("prod", "1234567890")
    assert secrets.get("auth_method") == "certificate"
    assert secrets.get("ksef_token") is None
    assert secrets.certificate_config() == config
    with pytest.raises(KsefError, match="systemd"):
        secrets.select_token()
    with pytest.raises(KsefError, match="systemd"):
        secrets.select_certificate(config["fingerprint"], config["password"], "certificateSubject")

    credential.chmod(0o644)
    with pytest.raises(KsefError, match="tylko dla właściciela"):
        secrets.certificate_config()


def test_systemd_rejects_ambiguous_token_and_certificate_credentials(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    token = tmp_path / "asef_prod_1234567890"
    token.write_text("fictional-token")
    token.chmod(0o600)
    certificate = tmp_path / "asef_cert_prod_1234567890"
    certificate.write_text(json.dumps({
        "fingerprint": "a" * 64,
        "password": "fictional key password",
        "subjectIdentifierType": "certificateSubject",
    }))
    certificate.chmod(0o600)

    with pytest.raises(KsefError, match="dwie metody"):
        Secrets("prod", "1234567890")


def test_systemd_refresh_is_cleared_when_credential_changes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    token = tmp_path / "asef_prod_1234567890"
    token.write_text("first-fictional-token")
    token.chmod(0o600)
    secrets = Secrets("prod", "1234567890")
    secrets.set("refresh_token", "first-refresh")
    token.write_text("second-fictional-token")
    assert Secrets("prod", "1234567890").get("refresh_token") is None

    secrets.set("refresh_token", "second-refresh")
    token.unlink()
    certificate = tmp_path / "asef_cert_prod_1234567890"
    certificate.write_text(json.dumps({
        "fingerprint": "a" * 64,
        "password": "fictional key password",
        "subjectIdentifierType": "certificateSubject",
    }))
    certificate.chmod(0o600)
    assert secrets.get("auth_method") == "certificate"
    assert secrets.get("refresh_token") is None

    secrets.set("refresh_token", "certificate-refresh")
    certificate.write_text(certificate.read_text().replace("fictional key password", "different password"))
    assert secrets.get("refresh_token") is None


def test_long_lived_client_discards_access_after_systemd_rotation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    credential = tmp_path / "asef_prod_1234567890"
    credential.write_text("first-fictional-token")
    credential.chmod(0o600)
    with KsefClient("prod", "1234567890") as client:
        client._access_token = "old-access"
        client._access_valid_until = datetime(2999, 1, 1, tzinfo=timezone.utc)
        client.secrets.set("refresh_token", "old-refresh")
        client.secrets.set("refresh_valid_until", "2999-01-01T00:00:00Z")
        credential.write_text("second-fictional-token")

        def authenticate():
            assert client._access_token is None
            assert client.secrets.get("refresh_token") is None
            client._access_token = "new-access"
            return {}

        monkeypatch.setattr(client, "authenticate", authenticate)
        assert client.access_token() == "new-access"


def test_long_lived_client_discards_access_after_keyring_method_switch(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    stored: dict[str, str] = {}
    monkeypatch.setattr("asef.ksef.keyring.get_password", lambda _service, key: stored.get(key))
    monkeypatch.setattr("asef.ksef.keyring.set_password", lambda _service, key, value: stored.__setitem__(key, value))
    monkeypatch.setattr("asef.ksef.keyring.delete_password", lambda _service, key: stored.pop(key, None))
    secrets = Secrets("prod", "1234567890")
    secrets.set("ksef_token", "first-fictional-token")
    with KsefClient("prod", "1234567890", secrets=secrets) as client:
        methods: list[str] = []

        def authenticate():
            method = client.authentication_method()
            methods.append(method)
            client._access_token = f"{method}-access-{len(methods)}"
            client._access_valid_until = datetime(2999, 1, 1, tzinfo=timezone.utc)
            return {}

        monkeypatch.setattr(client, "authenticate", authenticate)
        assert client.access_token() == "token-access-1"
        secrets.select_certificate("a" * 64, "fictional password", "certificateSubject")
        assert client.access_token() == "certificate-access-2"
        secrets.select_token()
        assert client.access_token() == "token-access-3"
        secrets.set("ksef_token", "second-fictional-token")
        assert client.access_token() == "token-access-4"
        assert methods == ["token", "certificate", "token", "token"]


def test_systemd_rotation_during_auth_does_not_cache_old_refresh(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    credential = tmp_path / "asef_prod_1234567890"
    credential.write_text("first-fictional-token")
    credential.chmod(0o600)
    with KsefClient("prod", "1234567890") as client:
        monkeypatch.setattr(client, "_encrypt_rsa", lambda *_: ("fictional-ciphertext", None))

        def response(_method, path, **_kwargs):
            if path == "/auth/challenge":
                return {"challenge": "fictional-challenge", "timestampMs": 123}
            if path == "/auth/ksef-token":
                return {"authenticationToken": {"token": "temporary"}, "referenceNumber": "reference"}
            if path == "/auth/reference":
                return {"status": {"code": 200}}
            if path == "/auth/token/redeem":
                return {
                    "accessToken": {"token": "old-access", "validUntil": "2999-01-01T00:00:00Z"},
                    "refreshToken": {"token": "old-refresh", "validUntil": "2999-01-01T00:00:00Z"},
                }
            raise AssertionError(path)

        monkeypatch.setattr(client, "_json", response)
        original_set = client.secrets.set

        def rotate_on_save(key, value):
            if key == "refresh_token":
                credential.write_text("second-fictional-token")
            original_set(key, value)

        monkeypatch.setattr(client.secrets, "set", rotate_on_save)
        with pytest.raises(KsefError, match="zmieniło się"):
            client.authenticate()
        assert client._access_token is None
        assert client.secrets.get("refresh_token") is None
