import base64
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from typer.testing import CliRunner

from asef.cli import app
from asef.db import Database
from asef.secure_archive import decrypt_bytes, encrypt_bytes
from asef.service import AsefService


ARCHIVE_PASSWORD = "fictional archive password"
KEY_PASSWORD = "fictional key password"


def _fake_pair(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fictional certificate")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2025, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=timezone.utc))
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "fictional.crt", directory / "fictional.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(KEY_PASSWORD.encode()),
    ))
    return cert_path, key_path


@pytest.fixture
def fake_keyring(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr("asef.ksef.keyring.get_password", lambda service, key: stored.get((service, key)))
    monkeypatch.setattr("asef.ksef.keyring.set_password", lambda service, key, value: stored.__setitem__((service, key), value))
    monkeypatch.setattr("asef.ksef.keyring.delete_password", lambda service, key: stored.pop((service, key), None))
    return stored


def _prepare_archive(tmp_path: Path, monkeypatch, fake_keyring) -> tuple[Path, Path, Path, str]:
    source = tmp_path / "source"
    monkeypatch.setenv("ASEF_DATA_DIR", str(source))
    with Database() as db:
        AsefService(db).profile_add("1111111111", "Fictional", "test")
    fake_keyring[("asef:test:1111111111", "ksef_token")] = "fictional-ksef-token"
    cert_path, key_path = _fake_pair(tmp_path)
    runner = CliRunner()
    added = runner.invoke(app, ["--json", "auth", "add-certificate", str(cert_path), str(key_path)],
                          input=f"{KEY_PASSWORD}\n")
    assert added.exit_code == 0, added.output
    assert KEY_PASSWORD not in added.output
    fingerprint = json.loads(added.stdout)["result"]["fingerprint"]
    archive = tmp_path / "full.asef"
    exported = runner.invoke(app, ["--json", "db", "export", str(archive)],
                             input=f"{ARCHIVE_PASSWORD}\n{ARCHIVE_PASSWORD}\n")
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.stdout)["result"]["certificates_exported"] == 1
    return archive, cert_path, key_path, fingerprint


def test_certificate_and_token_roundtrip_in_encrypted_database_archive(tmp_path: Path, monkeypatch,
                                                                       fake_keyring) -> None:
    archive, cert_path, key_path, fingerprint = _prepare_archive(tmp_path, monkeypatch, fake_keyring)
    encrypted = archive.read_bytes()
    assert b"fictional-ksef-token" not in encrypted
    assert b"BEGIN CERTIFICATE" not in encrypted
    assert b"ENCRYPTED PRIVATE KEY" not in encrypted
    assert ARCHIVE_PASSWORD.encode() not in encrypted
    payload = json.loads(decrypt_bytes(encrypted, ARCHIVE_PASSWORD, "database"))
    assert payload["version"] == 2
    assert base64.b64decode(payload["certificates"][0]["certificate_b64"]) == cert_path.read_bytes()
    assert base64.b64decode(payload["certificates"][0]["private_key_b64"]) == key_path.read_bytes()

    fake_keyring.clear()
    target = tmp_path / "target"
    monkeypatch.setenv("ASEF_DATA_DIR", str(target))
    restored = CliRunner().invoke(app, ["--json", "db", "import", str(archive)],
                                  input=f"{ARCHIVE_PASSWORD}\n")
    assert restored.exit_code == 0, restored.output
    assert json.loads(restored.stdout)["result"]["certificates_imported"] == 1
    assert fake_keyring[("asef:test:1111111111", "ksef_token")] == "fictional-ksef-token"
    directory = target / "certificates"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert (directory / f"{fingerprint}.crt").read_bytes() == cert_path.read_bytes()
    assert (directory / f"{fingerprint}.key").read_bytes() == key_path.read_bytes()
    assert all(stat.S_IMODE((directory / f"{fingerprint}{suffix}").stat().st_mode) == 0o600
               for suffix in (".crt", ".key", ".json"))
    listed = CliRunner().invoke(app, ["--json", "auth", "certificates"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["result"][0]["fingerprint"] == fingerprint


def test_invalid_certificate_archive_and_collision_leave_database_and_token_untouched(
    tmp_path: Path, monkeypatch, fake_keyring,
) -> None:
    archive, cert_path, key_path, _ = _prepare_archive(tmp_path, monkeypatch, fake_keyring)
    payload = json.loads(decrypt_bytes(archive.read_bytes(), ARCHIVE_PASSWORD, "database"))
    payload["certificates"][0]["fingerprint"] = "0" * 64
    broken = tmp_path / "broken.asef"
    broken.write_bytes(encrypt_bytes(json.dumps(payload).encode(), ARCHIVE_PASSWORD, "database"))
    fake_keyring.clear()
    target = tmp_path / "target"
    monkeypatch.setenv("ASEF_DATA_DIR", str(target))
    runner = CliRunner()
    invalid = runner.invoke(app, ["--json", "db", "import", str(broken)], input=f"{ARCHIVE_PASSWORD}\n")
    assert invalid.exit_code == 1
    assert not (target / "asef.sqlite3").exists()
    assert fake_keyring == {}
    assert not list((target / "certificates").glob("*.key"))

    from asef.certificates import add_certificate
    add_certificate(cert_path, key_path, KEY_PASSWORD)
    collision = runner.invoke(app, ["--json", "db", "import", str(archive)], input=f"{ARCHIVE_PASSWORD}\n")
    assert collision.exit_code == 1
    assert not (target / "asef.sqlite3").exists()
    assert fake_keyring == {}


def test_database_publication_failure_rolls_back_new_certificate_files_and_token(
    tmp_path: Path, monkeypatch, fake_keyring,
) -> None:
    archive, _, _, _ = _prepare_archive(tmp_path, monkeypatch, fake_keyring)
    fake_keyring.clear()
    target = tmp_path / "target"
    monkeypatch.setenv("ASEF_DATA_DIR", str(target))
    real_link = os.link

    def fail_database_link(source, destination):
        if Path(destination).name == "asef.sqlite3":
            raise OSError("simulated database publication failure")
        return real_link(source, destination)

    monkeypatch.setattr("asef.db_archive.os.link", fail_database_link)
    result = CliRunner().invoke(app, ["--json", "db", "import", str(archive)],
                                input=f"{ARCHIVE_PASSWORD}\n")
    assert result.exit_code == 1
    assert "simulated database publication failure" in json.loads(result.stdout)["error"]
    assert fake_keyring == {}
    assert not (target / "asef.sqlite3").exists()
    assert not list((target / "certificates").iterdir())
