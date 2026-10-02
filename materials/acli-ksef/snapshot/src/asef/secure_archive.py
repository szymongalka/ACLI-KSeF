"""Password-protected ASEF archives shared by database and XML transfers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


_MAGIC = b"ASEFENC1"
_KINDS = {"database": 1, "xml-bundle": 2}
_HEADER_SIZE = len(_MAGIC) + 1 + 16 + 12


def _key(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode("utf-8"))


def encrypt_bytes(data: bytes, password: str, kind: str) -> bytes:
    if kind not in _KINDS:
        raise ValueError("Nieznany rodzaj archiwum ASEF.")
    if len(password) < 12:
        raise ValueError("Hasło eksportu musi mieć co najmniej 12 znaków.")
    salt, nonce = os.urandom(16), os.urandom(12)
    header = _MAGIC + bytes([_KINDS[kind]]) + salt + nonce
    return header + AESGCM(_key(password, salt)).encrypt(nonce, data, header)


def decrypt_bytes(envelope: bytes, password: str, kind: str) -> bytes:
    if kind not in _KINDS:
        raise ValueError("Nieznany rodzaj archiwum ASEF.")
    if len(envelope) < _HEADER_SIZE + 16 or not envelope.startswith(_MAGIC):
        raise ValueError("Plik nie jest zaszyfrowanym archiwum ASEF.")
    if envelope[len(_MAGIC)] != _KINDS[kind]:
        raise ValueError("Archiwum ASEF ma inny rodzaj danych.")
    if not password:
        raise ValueError("Podaj hasło archiwum ASEF.")
    header = envelope[:_HEADER_SIZE]
    salt = header[len(_MAGIC) + 1:len(_MAGIC) + 17]
    nonce = header[-12:]
    try:
        return AESGCM(_key(password, salt)).decrypt(nonce, envelope[_HEADER_SIZE:], header)
    except InvalidTag as exc:
        raise ValueError("Nieprawidłowe hasło lub uszkodzone archiwum ASEF.") from exc


def write_encrypted_file(path: Path, data: bytes, password: str, kind: str) -> Path:
    encrypted = encrypt_bytes(data, password, kind)
    target = path.expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(target):
        raise ValueError("Archiwum już istnieje; wybierz nową nazwę pliku.")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)  # An existing archive is never replaced.
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def read_encrypted_file(path: Path, password: str, kind: str) -> bytes:
    source = path.expanduser().absolute()
    if not source.is_file():
        raise ValueError("Nie znaleziono zaszyfrowanego archiwum ASEF.")
    return decrypt_bytes(source.read_bytes(), password, kind)
