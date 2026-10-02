import base64
import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from asef.certificates import (
    EncryptedKeyPasswordRequired,
    add_certificate,
    export_certificates,
    list_certificates,
    load_authentication_certificate,
    prepare_certificate_import,
    validate_certificate_entries,
)


KEY_PASSWORD = "fictional key password"


def _pair(tmp_path: Path, *, offline: bool = False, key_for_cert=None,
          valid_from: datetime | None = None, valid_until: datetime | None = None) -> tuple[Path, Path]:
    key = key_for_cert or ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fictional KSeF test certificate")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(valid_from or datetime(2025, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(valid_until or datetime(2030, 1, 1, tzinfo=timezone.utc))
        .add_extension(
            x509.KeyUsage(
                digital_signature=not offline,
                content_commitment=offline,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "test.crt", tmp_path / "test.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(KEY_PASSWORD.encode()),
    ))
    return cert_path, key_path


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_add_and_export_exact_encrypted_key_without_identity_leak(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    cert_path, key_path = _pair(tmp_path)
    cert_bytes, key_bytes = cert_path.read_bytes(), key_path.read_bytes()

    added = add_certificate(cert_path, key_path, KEY_PASSWORD)
    fingerprint = added["fingerprint"]
    directory = tmp_path / "data" / "certificates"

    assert added["key_pair_verified"] is True
    assert "subject" not in added
    assert _mode(directory) == 0o700
    assert all(_mode(directory / f"{fingerprint}{suffix}") == 0o600 for suffix in (".crt", ".key", ".json"))
    assert (directory / f"{fingerprint}.crt").read_bytes() == cert_bytes
    assert (directory / f"{fingerprint}.key").read_bytes() == key_bytes
    assert list_certificates() == [{key: value for key, value in added.items() if key != "key_pair_verified"}]
    archive = export_certificates()
    assert archive[0]["fingerprint"] == fingerprint
    assert base64.b64decode(archive[0]["certificate_b64"]) == cert_bytes
    assert base64.b64decode(archive[0]["private_key_b64"]) == key_bytes
    with pytest.raises(ValueError, match="już zapisany"):
        add_certificate(cert_path, key_path, KEY_PASSWORD)


def test_load_authentication_certificate_by_exact_fingerprint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    cert_path, key_path = _pair(tmp_path)
    original_cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    cert_path.write_bytes(original_cert.public_bytes(serialization.Encoding.DER))
    fingerprint = add_certificate(cert_path, key_path, KEY_PASSWORD)["fingerprint"]

    cert_pem, private_key = load_authentication_certificate(fingerprint, KEY_PASSWORD)
    loaded_cert = x509.load_pem_x509_certificate(cert_pem)
    assert loaded_cert.fingerprint(hashes.SHA256()).hex() == fingerprint
    assert private_key.public_key().public_numbers() == loaded_cert.public_key().public_numbers()

    with pytest.raises(ValueError, match="Odcisk"):
        load_authentication_certificate("../" + fingerprint, KEY_PASSWORD)
    with pytest.raises(ValueError, match="Nie znaleziono"):
        load_authentication_certificate("0" * 64, KEY_PASSWORD)
    with pytest.raises(ValueError, match="hasło"):
        load_authentication_certificate(fingerprint, "wrong password")


def test_load_authentication_certificate_checks_only_selected_entry_and_key_pair(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    cert_path, key_path = _pair(tmp_path)
    selected = add_certificate(cert_path, key_path, KEY_PASSWORD)["fingerprint"]
    cert_path, key_path = _pair(tmp_path)
    other = add_certificate(cert_path, key_path, KEY_PASSWORD)["fingerprint"]
    directory = tmp_path / "data" / "certificates"
    (directory / f"{other}.key").write_bytes(b"damaged")
    assert (x509.load_pem_x509_certificate(load_authentication_certificate(selected, KEY_PASSWORD)[0])
            .fingerprint(hashes.SHA256()).hex() == selected)

    different_key = ec.generate_private_key(ec.SECP256R1())
    key_bytes = different_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(KEY_PASSWORD.encode()),
    )
    (directory / f"{selected}.key").write_bytes(key_bytes)
    metadata_path = directory / f"{selected}.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["private_key_sha256"] = hashlib.sha256(key_bytes).hexdigest()
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="nie pasuje"):
        load_authentication_certificate(selected, KEY_PASSWORD)


def test_load_authentication_certificate_requires_current_validity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    current_time = datetime.now(timezone.utc)
    for valid_from, valid_until, expected_error in (
        (current_time + timedelta(days=1), current_time + timedelta(days=30), "jeszcze nie obowiązuje"),
        (current_time - timedelta(days=30), current_time - timedelta(days=1), "wygasł"),
    ):
        cert_path, key_path = _pair(tmp_path, valid_from=valid_from, valid_until=valid_until)
        fingerprint = add_certificate(cert_path, key_path, KEY_PASSWORD)["fingerprint"]
        with pytest.raises(ValueError, match=expected_error):
            load_authentication_certificate(fingerprint, KEY_PASSWORD)
    assert len(export_certificates()) == 2


def test_requires_encrypted_matching_key_and_correct_purpose(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    cert_path, key_path = _pair(tmp_path)
    with pytest.raises(EncryptedKeyPasswordRequired):
        add_certificate(cert_path, key_path)
    with pytest.raises(ValueError, match="hasło"):
        add_certificate(cert_path, key_path, "wrong password")
    with pytest.raises(ValueError, match="KeyUsage"):
        add_certificate(cert_path, key_path, KEY_PASSWORD, "Offline")

    other_key = ec.generate_private_key(ec.SECP256R1())
    key_path.write_bytes(other_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(KEY_PASSWORD.encode()),
    ))
    with pytest.raises(ValueError, match="nie pasuje"):
        add_certificate(cert_path, key_path, KEY_PASSWORD)

    key_path.write_bytes(other_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    with pytest.raises(ValueError, match="zaszyfrowanym PEM PKCS#8"):
        add_certificate(cert_path, key_path, KEY_PASSWORD)
    assert not (tmp_path / "data" / "certificates").exists()


def test_offline_purpose_and_import_preflight_rollback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "source"))
    cert_path, key_path = _pair(tmp_path, offline=True)
    added = add_certificate(cert_path, key_path, KEY_PASSWORD, "Offline")
    with pytest.raises(ValueError, match="Offline"):
        load_authentication_certificate(added["fingerprint"], KEY_PASSWORD)
    archive = export_certificates()
    assert archive[0]["purpose"] == "Offline"

    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "destination"))
    materials = validate_certificate_entries(archive)
    prepared = prepare_certificate_import(materials)
    assert all(_mode(source) == 0o600 for source, _ in prepared.staged)

    real_link = os.link
    calls = 0

    def fail_second_link(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publication failure")
        return real_link(source, target)

    monkeypatch.setattr("asef.certificates.os.link", fail_second_link)
    with pytest.raises(OSError, match="simulated"):
        prepared.publish()
    assert not list((tmp_path / "destination" / "certificates").glob("*.crt"))
    prepared.cleanup()
    assert not list((tmp_path / "destination" / "certificates").glob(".*"))

    monkeypatch.setattr("asef.certificates.os.link", real_link)
    prepared = prepare_certificate_import(materials)
    try:
        created = prepared.publish()
        assert len(created) == 3
    finally:
        prepared.cleanup()
    assert list_certificates()[0]["fingerprint"] == added["fingerprint"]
    assert export_certificates() == archive
    # Content validation is independent of the destination; staging enforces no overwrite.
    assert validate_certificate_entries(archive) == materials
    with pytest.raises(ValueError, match="już zapisany"):
        prepare_certificate_import(materials)


def test_archive_fingerprint_and_stored_checksums_are_verified(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    cert_path, key_path = _pair(tmp_path)
    fingerprint = add_certificate(cert_path, key_path, KEY_PASSWORD)["fingerprint"]
    entry = export_certificates()[0]
    bad = dict(entry, fingerprint="0" * 64)
    with pytest.raises(ValueError, match="Odcisk"):
        validate_certificate_entries([bad])
    with pytest.raises(ValueError, match="powtórzony"):
        validate_certificate_entries([entry, entry])

    stored_key = tmp_path / "data" / "certificates" / f"{fingerprint}.key"
    stored_key.write_bytes(stored_key.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA-256"):
        export_certificates()
    with pytest.raises(ValueError, match="SHA-256"):
        load_authentication_certificate(fingerprint, KEY_PASSWORD)
