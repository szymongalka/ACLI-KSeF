"""Local certificate-login contract tests with fictional credentials and KSeF responses."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from lxml import etree
from typer.testing import CliRunner

from asef.certificates import add_certificate
from asef.cli import app
from asef.db import Database
from asef.ksef import KsefClient, Secrets
from asef.xades_auth import AUTH_NS


NIP = "1111111111"
KEY_PASSWORD = "fictional key password"
SERVICE = f"asef:test:{NIP}"


@pytest.fixture
def fake_keyring(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr("asef.ksef.keyring.get_password", lambda service, key: stored.get((service, key)))
    monkeypatch.setattr("asef.ksef.keyring.set_password", lambda service, key, value: stored.__setitem__((service, key), value))
    monkeypatch.setattr("asef.ksef.keyring.delete_password", lambda service, key: stored.pop((service, key), None))
    return stored


def _certificate_files(folder: Path, *, offline: bool = False,
                       key: ec.EllipticCurvePrivateKey | None = None) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    key = key or ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fictional KSeF signer")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2025, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=timezone.utc))
        .add_extension(x509.KeyUsage(
            digital_signature=not offline, content_commitment=offline,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "fictional.crt", folder / "fictional.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(KEY_PASSWORD.encode()),
    ))
    return cert_path, key_path


def _profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    with Database() as db:
        db.add_profile(NIP, "Fictional company", "test")


def test_cli_selected_certificate_authenticates_and_refreshes_without_ksef_token(
    tmp_path: Path, monkeypatch, fake_keyring,
) -> None:
    _profile(tmp_path, monkeypatch)
    cert_path, key_path = _certificate_files(tmp_path / "pair")
    fingerprint = add_certificate(cert_path, key_path, KEY_PASSWORD)["fingerprint"]

    chosen = CliRunner().invoke(app, [
        "--json", "auth", "use-certificate", fingerprint, "--nip", NIP, "--env", "test",
        "--subject-identifier", "certificateFingerprint",
    ], input=f"{KEY_PASSWORD}\n")
    assert chosen.exit_code == 0, chosen.output
    assert json.loads(chosen.stdout)["result"]["method"] == "certificate"
    assert KEY_PASSWORD not in chosen.output
    assert (SERVICE, "ksef_token") not in fake_keyring

    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/auth/challenge":
            assert request.method == "POST"
            assert "Authorization" not in request.headers
            return httpx.Response(200, json={"challenge": "fictional-challenge", "timestampMs": 123})
        if request.url.path == "/auth/xades-signature":
            assert request.method == "POST"
            assert request.headers["Content-Type"] == "application/xml"
            assert request.url.params["verifyCertificateChain"] == "false"
            assert KEY_PASSWORD.encode() not in request.content
            root = etree.fromstring(request.content)
            ns = {"auth": AUTH_NS, "ds": "http://www.w3.org/2000/09/xmldsig#"}
            assert root.xpath("string(auth:Challenge)", namespaces=ns) == "fictional-challenge"
            assert root.xpath("string(auth:ContextIdentifier/auth:Nip)", namespaces=ns) == NIP
            assert root.xpath("string(auth:SubjectIdentifierType)", namespaces=ns) == "certificateFingerprint"
            assert len(root.xpath(".//ds:SignatureValue", namespaces=ns)) == 1
            return httpx.Response(202, json={
                "authenticationToken": {"token": "temporary"}, "referenceNumber": "reference",
            })
        if request.url.path == "/auth/reference":
            assert request.headers["Authorization"] == "Bearer temporary"
            return httpx.Response(200, json={"status": {"code": 200}})
        if request.url.path == "/auth/token/redeem":
            assert request.headers["Authorization"] == "Bearer temporary"
            return httpx.Response(200, json={
                "accessToken": {"token": "certificate-access", "validUntil": "2030-01-01T00:00:00Z"},
                "refreshToken": {"token": "certificate-refresh", "validUntil": "2030-01-01T00:00:00Z"},
            })
        if request.url.path == "/auth/token/refresh":
            assert request.headers["Authorization"] == "Bearer certificate-refresh"
            return httpx.Response(200, json={
                "accessToken": {"token": "refreshed-access", "validUntil": "2030-01-01T00:00:00Z"},
            })
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    with httpx.Client(transport=httpx.MockTransport(respond), base_url="https://example.invalid") as http:
        with KsefClient("test", NIP, http=http) as client:
            assert client.access_token() == "certificate-access"
            assert client.authentication_method() == "certificate"
        assert fake_keyring[(SERVICE, "refresh_token")] == "certificate-refresh"
        with KsefClient("test", NIP, http=http) as client:
            assert client.access_token() == "refreshed-access"
    assert paths == [
        "/auth/challenge", "/auth/xades-signature", "/auth/reference",
        "/auth/token/redeem", "/auth/token/refresh",
    ]


def test_switching_certificate_and_token_invalidates_refresh_without_losing_token(
    tmp_path: Path, monkeypatch, fake_keyring,
) -> None:
    _profile(tmp_path, monkeypatch)
    secrets = Secrets("test", NIP)
    secrets.set("ksef_token", "fictional-ksef-token")
    secrets.set("refresh_token", "old-refresh")
    secrets.set("refresh_valid_until", "2030-01-01T00:00:00Z")
    secrets.select_certificate("a" * 64, KEY_PASSWORD, "certificateSubject")
    assert secrets.get("auth_method") == "certificate"
    assert secrets.get("refresh_token") is None
    assert secrets.get("ksef_token") == "fictional-ksef-token"

    secrets.set("refresh_token", "certificate-refresh")
    secrets.set("refresh_valid_until", "2030-01-01T00:00:00Z")
    switched = CliRunner().invoke(app, ["--json", "auth", "use-token", "--nip", NIP, "--env", "test"])
    assert switched.exit_code == 0, switched.output
    assert json.loads(switched.stdout)["result"]["method"] == "token"
    assert secrets.get("auth_method") == "token"
    assert secrets.get("refresh_token") is None
    assert secrets.get("refresh_valid_until") is None
    assert (SERVICE, "certificate_auth") not in fake_keyring
    assert secrets.get("ksef_token") == "fictional-ksef-token"


@pytest.mark.parametrize("offline,password,error", [
    (True, KEY_PASSWORD, "Offline"),
    (False, "wrong password", "hasło"),
])
def test_certificate_selection_rejects_offline_or_wrong_key_password(
    tmp_path: Path, monkeypatch, fake_keyring, offline: bool, password: str, error: str,
) -> None:
    _profile(tmp_path, monkeypatch)
    cert_path, key_path = _certificate_files(tmp_path / "pair", offline=offline)
    fingerprint = add_certificate(
        cert_path, key_path, KEY_PASSWORD, "Offline" if offline else "Authentication"
    )["fingerprint"]
    selected = CliRunner().invoke(app, [
        "--json", "auth", "use-certificate", fingerprint, "--nip", NIP, "--env", "test",
    ], input=f"{password}\n")
    assert selected.exit_code == 1
    assert error in selected.output
    assert password not in selected.output
    assert (SERVICE, "certificate_auth") not in fake_keyring


def test_certificate_selection_rejects_understrength_ec_key(tmp_path: Path, monkeypatch, fake_keyring) -> None:
    _profile(tmp_path, monkeypatch)
    weak_key = ec.generate_private_key(ec.SECP224R1())
    cert_path, key_path = _certificate_files(tmp_path / "pair", key=weak_key)
    fingerprint = add_certificate(cert_path, key_path, KEY_PASSWORD)["fingerprint"]
    selected = CliRunner().invoke(app, [
        "--json", "auth", "use-certificate", fingerprint, "--nip", NIP, "--env", "test",
    ], input=f"{KEY_PASSWORD}\n")
    assert selected.exit_code == 1
    assert "256 bitów" in selected.output
    assert KEY_PASSWORD not in selected.output
    assert (SERVICE, "certificate_auth") not in fake_keyring
