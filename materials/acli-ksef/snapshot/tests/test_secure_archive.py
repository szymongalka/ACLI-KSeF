import stat
from pathlib import Path

import pytest

from asef.secure_archive import decrypt_bytes, encrypt_bytes, read_encrypted_file, write_encrypted_file


PASSWORD = "long demo password 2026"


def test_encrypted_file_is_private_and_rejects_wrong_password_or_tampering(tmp_path: Path) -> None:
    path = tmp_path / "export.asef"
    assert write_encrypted_file(path, b"invoice and fake token", PASSWORD, "database") == path
    data = path.read_bytes()
    assert b"invoice" not in data and b"token" not in data
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_encrypted_file(path, PASSWORD, "database") == b"invoice and fake token"
    with pytest.raises(ValueError, match="hasło"):
        decrypt_bytes(data, "different demo password", "database")
    with pytest.raises(ValueError, match="uszkodzone"):
        decrypt_bytes(data[:-1] + bytes([data[-1] ^ 1]), PASSWORD, "database")
    with pytest.raises(ValueError, match="inny rodzaj"):
        decrypt_bytes(data, PASSWORD, "xml-bundle")
    with pytest.raises(ValueError, match="już istnieje"):
        write_encrypted_file(path, b"replacement", PASSWORD, "database")
    assert path.read_bytes() == data
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".export.asef.")]


def test_archive_uses_fresh_salt_and_nonce_and_requires_a_long_password() -> None:
    with pytest.raises(ValueError, match="12"):
        encrypt_bytes(b"data", "short", "database")
    first = encrypt_bytes(b"data", PASSWORD, "database")
    second = encrypt_bytes(b"data", PASSWORD, "database")
    assert first != second
    assert decrypt_bytes(first, PASSWORD, "database") == b"data"
