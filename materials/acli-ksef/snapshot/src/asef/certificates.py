"""Private storage for user-supplied KSeF certificates and their original keys."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from .paths import data_dir


PURPOSES = {"Authentication", "Offline"}
MAX_FILE_SIZE = 1024 * 1024


class EncryptedKeyPasswordRequired(ValueError):
    """The key is encrypted and must be checked with its own password."""


@dataclass(frozen=True)
class CertificateMaterial:
    fingerprint: str
    purpose: str
    certificate: bytes
    private_key: bytes


def _directory(*, create: bool) -> Path:
    directory = data_dir() / "certificates"
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    if directory.exists() or directory.is_symlink():
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError("Katalog certyfikatów musi być prywatnym katalogiem ASEF.")
    return directory


def _read_file(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError("Nie można odczytać pliku certyfikatu lub klucza.") from exc
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_SIZE:
            raise ValueError("Plik certyfikatu lub klucza ma nieprawidłowy format albo rozmiar.")
        return stream.read(MAX_FILE_SIZE + 1)


def _certificate(data: bytes) -> x509.Certificate:
    try:
        if data.startswith(b"-----BEGIN CERTIFICATE-----"):
            return x509.load_pem_x509_certificate(data)
        return x509.load_der_x509_certificate(data)
    except ValueError as exc:
        raise ValueError("Nieprawidłowy certyfikat X.509.") from exc


def _key(data: bytes, password: str | None) -> object:
    if not data.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"):
        raise ValueError("Klucz musi być zaszyfrowanym PEM PKCS#8; zaszyfruj go przed dodaniem do ASEF.")
    try:
        return serialization.load_pem_private_key(
            data, password=password.encode("utf-8") if password is not None else None
        )
    except TypeError as exc:
        if password is None:
            raise EncryptedKeyPasswordRequired("Klucz jest zaszyfrowany; podaj hasło klucza.") from exc
        raise ValueError("Nie można odczytać klucza prywatnego z podanym hasłem.") from exc
    except (ValueError, OverflowError) as exc:
        raise ValueError("Nieprawidłowy klucz prywatny albo hasło klucza.") from exc


def _public_bytes(key: object) -> bytes:
    try:
        return key.public_key().public_bytes(  # type: ignore[attr-defined]
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    except (AttributeError, TypeError) as exc:
        raise ValueError("Nieobsługiwany typ klucza certyfikatu.") from exc


def _material(cert_bytes: bytes, key_bytes: bytes, purpose: str, *, key_password: str | None = None,
              require_match: bool = False) -> CertificateMaterial:
    if purpose not in PURPOSES:
        raise ValueError("Przeznaczenie certyfikatu: Authentication albo Offline.")
    if not cert_bytes or not key_bytes or len(cert_bytes) > MAX_FILE_SIZE or len(key_bytes) > MAX_FILE_SIZE:
        raise ValueError("Certyfikat lub klucz jest pusty albo za duży.")
    cert = _certificate(cert_bytes)
    try:
        key_usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound as exc:
        raise ValueError("Certyfikat nie zawiera rozszerzenia KeyUsage.") from exc
    required_usage = key_usage.digital_signature if purpose == "Authentication" else key_usage.content_commitment
    if not required_usage:
        raise ValueError("KeyUsage certyfikatu nie pasuje do podanego przeznaczenia.")
    try:
        private_key = _key(key_bytes, key_password)
    except EncryptedKeyPasswordRequired:
        if require_match:
            raise
        private_key = None
    if private_key is not None and _public_bytes(private_key) != cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise ValueError("Klucz prywatny nie pasuje do certyfikatu.")
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    return CertificateMaterial(fingerprint, purpose, cert_bytes, key_bytes)


def _paths(directory: Path, fingerprint: str) -> tuple[Path, Path, Path]:
    return tuple(directory / f"{fingerprint}{suffix}" for suffix in (".crt", ".key", ".json"))


def _metadata(material: CertificateMaterial) -> dict[str, str]:
    cert = _certificate(material.certificate)
    return {
        "fingerprint": material.fingerprint,
        "purpose": material.purpose,
        "valid_from": cert.not_valid_before_utc.isoformat(),
        "valid_until": cert.not_valid_after_utc.isoformat(),
    }


def _check_available(materials: list[CertificateMaterial], directory: Path) -> None:
    for material in materials:
        if any(os.path.lexists(path) for path in _paths(directory, material.fingerprint)):
            raise ValueError("Certyfikat jest już zapisany lub ma niekompletny wpis w ASEF.")


def validate_certificate_entries(entries: list[dict]) -> list[CertificateMaterial]:
    """Validate every archive entry before touching the destination."""
    if not isinstance(entries, list):
        raise ValueError("Lista certyfikatów w archiwum jest nieprawidłowa.")
    materials: list[CertificateMaterial] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "fingerprint", "purpose", "certificate_b64", "private_key_b64"
        } or not all(isinstance(value, str) for value in entry.values()):
            raise ValueError("Wpis certyfikatu w archiwum jest nieprawidłowy.")
        if len(entry["certificate_b64"]) > MAX_FILE_SIZE * 2 or len(entry["private_key_b64"]) > MAX_FILE_SIZE * 2:
            raise ValueError("Wpis certyfikatu w archiwum jest za duży.")
        try:
            cert_bytes = base64.b64decode(entry["certificate_b64"], validate=True)
            key_bytes = base64.b64decode(entry["private_key_b64"], validate=True)
        except binascii.Error as exc:
            raise ValueError("Wpis certyfikatu w archiwum ma nieprawidłowe kodowanie.") from exc
        material = _material(cert_bytes, key_bytes, entry["purpose"])
        if material.fingerprint != entry["fingerprint"] or material.fingerprint in seen:
            raise ValueError("Odcisk certyfikatu w archiwum jest nieprawidłowy lub powtórzony.")
        seen.add(material.fingerprint)
        materials.append(material)
    return materials


class CertificateImport:
    def __init__(self, materials: list[CertificateMaterial]):
        self.directory = _directory(create=True)
        _check_available(materials, self.directory)
        self.staged: list[tuple[Path, Path]] = []
        try:
            for material in materials:
                payloads = (
                    material.certificate,
                    material.private_key,
                    json.dumps({
                        "version": 1,
                        "purpose": material.purpose,
                        "certificate_sha256": hashlib.sha256(material.certificate).hexdigest(),
                        "private_key_sha256": hashlib.sha256(material.private_key).hexdigest(),
                    }, separators=(",", ":")).encode(),
                )
                for target, payload in zip(_paths(self.directory, material.fingerprint), payloads):
                    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.directory)
                    self.staged.append((Path(temporary), target))
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
        except BaseException:
            self.cleanup()
            raise

    def publish(self) -> list[Path]:
        created: list[Path] = []
        try:
            if any(os.path.lexists(target) for _, target in self.staged):
                raise ValueError("Certyfikat pojawił się podczas importu; niczego nie nadpisano.")
            for temporary, target in self.staged:
                os.link(temporary, target)
                created.append(target)
            return created
        except BaseException as exc:
            rollback_failed = False
            for path in reversed(created):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    rollback_failed = True
            if rollback_failed:
                raise RuntimeError("Import certyfikatów przerwany; nie udało się usunąć części plików.") from exc
            raise

    def cleanup(self) -> None:
        for temporary, _ in self.staged:
            temporary.unlink(missing_ok=True)


def prepare_certificate_import(materials: list[CertificateMaterial]) -> CertificateImport:
    return CertificateImport(materials)


def add_certificate(cert_path: Path, key_path: Path, key_password: str | None = None,
                    purpose: str = "Authentication") -> dict[str, str | bool]:
    material = _material(_read_file(cert_path), _read_file(key_path), purpose,
                         key_password=key_password, require_match=True)
    prepared = prepare_certificate_import([material])
    try:
        prepared.publish()
    finally:
        prepared.cleanup()
    return {**_metadata(material), "key_pair_verified": True}


def _stored_materials() -> list[CertificateMaterial]:
    directory = _directory(create=False)
    if not directory.exists():
        return []
    fingerprints = {path.stem for suffix in (".crt", ".key", ".json") for path in directory.glob(f"*{suffix}")}
    return [_stored_material(directory, fingerprint) for fingerprint in sorted(fingerprints)]


def _stored_material(directory: Path, fingerprint: str) -> CertificateMaterial:
    cert_path, key_path, metadata_path = _paths(directory, fingerprint)
    if not all(path.exists() for path in (cert_path, key_path, metadata_path)):
        raise ValueError("Magazyn certyfikatów ASEF zawiera niekompletny wpis.")
    try:
        metadata = json.loads(_read_file(metadata_path))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Metadane certyfikatu ASEF są uszkodzone.") from exc
    if not isinstance(metadata, dict) or set(metadata) != {
        "version", "purpose", "certificate_sha256", "private_key_sha256"
    } or metadata["version"] != 1 or metadata["purpose"] not in PURPOSES:
        raise ValueError("Metadane certyfikatu ASEF są uszkodzone.")
    cert_bytes, key_bytes = _read_file(cert_path), _read_file(key_path)
    if (hashlib.sha256(cert_bytes).hexdigest() != metadata["certificate_sha256"]
            or hashlib.sha256(key_bytes).hexdigest() != metadata["private_key_sha256"]):
        raise ValueError("Pliki certyfikatu ASEF nie zgadzają się z zapisanymi sumami SHA-256.")
    material = _material(cert_bytes, key_bytes, metadata["purpose"])
    if material.fingerprint != fingerprint:
        raise ValueError("Odcisk certyfikatu ASEF nie zgadza się z nazwą pliku.")
    return material


def load_authentication_certificate(fingerprint: str, key_password: str) -> tuple[bytes, object]:
    """Load one verified Authentication signing pair by its SHA-256 fingerprint."""
    if (not isinstance(fingerprint, str) or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)):
        raise ValueError("Odcisk certyfikatu SHA-256 jest nieprawidłowy.")
    directory = _directory(create=False)
    if not any(os.path.lexists(path) for path in _paths(directory, fingerprint)):
        raise ValueError("Nie znaleziono certyfikatu o podanym odcisku.")
    material = _stored_material(directory, fingerprint)
    if material.purpose != "Authentication":
        raise ValueError("Certyfikat Offline nie służy do uwierzytelniania.")
    cert = _certificate(material.certificate)
    current_time = datetime.now(timezone.utc)
    if current_time < cert.not_valid_before_utc:
        raise ValueError("Certyfikat uwierzytelniający jeszcze nie obowiązuje.")
    if current_time > cert.not_valid_after_utc:
        raise ValueError("Certyfikat uwierzytelniający wygasł.")
    private_key = _key(material.private_key, key_password)
    if _public_bytes(private_key) != cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise ValueError("Klucz prywatny nie pasuje do certyfikatu.")
    return cert.public_bytes(serialization.Encoding.PEM), private_key


def list_certificates() -> list[dict[str, str]]:
    return [_metadata(material) for material in _stored_materials()]


def export_certificates() -> list[dict[str, str]]:
    return [
        {
            "fingerprint": material.fingerprint,
            "purpose": material.purpose,
            "certificate_b64": base64.b64encode(material.certificate).decode("ascii"),
            "private_key_b64": base64.b64encode(material.private_key).decode("ascii"),
        }
        for material in _stored_materials()
    ]
