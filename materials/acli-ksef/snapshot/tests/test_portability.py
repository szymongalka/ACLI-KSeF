import base64
import json
import sqlite3
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from asef.cli import app
from asef.db import Database, now
from asef.secure_archive import decrypt_bytes, encrypt_bytes
from asef.service import AsefService


SAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "faktura.json").read_text())
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    """Portability tests must never read or change real credentials."""
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr("asef.ksef.keyring.get_password", lambda service, key: stored.get((service, key)))
    monkeypatch.setattr("asef.ksef.keyring.set_password", lambda service, key, value: stored.__setitem__((service, key), value))
    monkeypatch.setattr("asef.ksef.keyring.delete_password", lambda service, key: stored.pop((service, key), None))
    return stored


def _run(*args: str, password: str = PASSWORD):
    typed = password + "\n" + (password + "\n" if args[0] == "export" else "")
    return CliRunner().invoke(app, ["--json", "db", *args], input=typed)


def _result(result):
    return json.loads(result.stdout)


def _make_source(tmp_path: Path, monkeypatch, *, prod: bool = False) -> Path:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "source"))
    with Database() as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, SAMPLE["seller"]["name"], "test")
        service.draft_from_payload(nip, SAMPLE, "test")
        if prod:
            service.profile_add(nip, SAMPLE["seller"]["name"], "prod")
    exported = tmp_path / "asef-export.asef"
    result = _run("export", str(exported))
    assert result.exit_code == 0, result.output
    return exported


def test_database_export_import_roundtrip_with_tokens(tmp_path: Path, monkeypatch, fake_keyring) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "source"))
    with Database() as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, SAMPLE["seller"]["name"], "test")
        document = service.draft_from_payload(nip, SAMPLE, "test")
        service.approve(document["id"], document["xml_sha256"])
        original_xml = db.get_document(document["id"])["xml"]
        service.profile_add(nip, SAMPLE["seller"]["name"], "prod")
        db.set_cursor(nip, "test", "Subject1", "2026-09-01T00:00:00+00:00")
        with db.connection:
            db.connection.execute(
                "INSERT INTO templates(name,payload_json,updated_at) VALUES(?,?,?)",
                ("standard", json.dumps(SAMPLE), now()),
            )
    fake_keyring[(f"asef:test:{nip}", "ksef_token")] = "fake-test-token"
    fake_keyring[(f"asef:prod:{nip}", "ksef_token")] = "fake-prod-token"
    fake_keyring[(f"asef:test:{nip}", "refresh_token")] = "transient-token"

    exported = tmp_path / "asef-export.asef"
    result = _run("export", str(exported))
    assert result.exit_code == 0, result.output
    exported_result = _result(result)["result"]
    assert exported_result["path"] == str(exported)
    assert exported_result["tokens_exported"] == 2
    assert exported_result["profiles_without_token"] == []
    assert stat.S_IMODE(exported.stat().st_mode) == 0o600
    archive = exported.read_bytes()
    assert archive.startswith(b"ASEFENC1")
    assert b"SQLite format 3" not in archive
    assert b"fake-test-token" not in archive
    assert b"fake-prod-token" not in archive
    assert b"transient-token" not in archive
    assert "fake-test-token" not in result.output
    assert PASSWORD not in result.output

    fake_keyring.clear()
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(exported))
    assert result.exit_code == 0, result.output
    imported_result = _result(result)["result"]
    assert imported_result["counts"] == {"profiles": 2, "documents": 1, "templates": 1}
    assert imported_result["tokens_imported"] == 2
    assert fake_keyring == {
        (f"asef:test:{nip}", "ksef_token"): "fake-test-token",
        (f"asef:prod:{nip}", "ksef_token"): "fake-prod-token",
    }
    with Database() as imported:
        assert stat.S_IMODE(imported.path.stat().st_mode) == 0o600
        assert {(p["nip"], p["environment"]) for p in imported.profiles()} == {(nip, "test"), (nip, "prod")}
        restored = imported.get_document(document["id"])
        assert restored["xml"] == original_xml
        assert restored["approved_sha256"] == document["xml_sha256"]
        assert imported.cursor(nip, "test", "Subject1") == "2026-09-01T00:00:00+00:00"
        assert imported.connection.execute("SELECT name FROM templates").fetchone()[0] == "standard"
        assert imported.connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] >= 2


def test_database_export_reports_profiles_without_token(tmp_path: Path, monkeypatch) -> None:
    source = _make_source(tmp_path, monkeypatch)
    payload = json.loads(decrypt_bytes(source.read_bytes(), PASSWORD, "database"))
    assert payload["tokens"] == []
    assert payload["version"] == 2
    assert payload["certificates"] == []
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(source))
    assert result.exit_code == 0, result.output
    assert _result(result)["result"]["tokens_imported"] == 0


def test_database_export_reads_systemd_token_without_keyring(tmp_path: Path, monkeypatch) -> None:
    nip = SAMPLE["seller"]["nip"]
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "source"))
    with Database() as db:
        AsefService(db).profile_add(nip, "Firma", "prod")
    credentials_dir = tmp_path / "credentials"
    credentials_dir.mkdir()
    credential = credentials_dir / f"asef_prod_{nip}"
    credential.write_text("fake-systemd-token\n")
    credential.chmod(0o600)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials_dir))
    monkeypatch.setattr("asef.ksef.keyring.get_password", lambda *_: pytest.fail("keyring read"))
    source = tmp_path / "systemd.asef"
    result = _run("export", str(source))
    assert result.exit_code == 0, result.output
    assert _result(result)["result"]["tokens_exported"] == 1
    payload = json.loads(decrypt_bytes(source.read_bytes(), PASSWORD, "database"))
    assert payload["tokens"] == [{"nip": nip, "environment": "prod", "token": "fake-systemd-token"}]
    assert b"fake-systemd-token" not in source.read_bytes()


def test_database_export_and_import_refuse_to_replace_existing_data(tmp_path: Path, monkeypatch) -> None:
    source = _make_source(tmp_path, monkeypatch)
    before_archive = source.read_bytes()
    result = _run("export", str(source))
    assert result.exit_code == 1
    assert "już istnieje" in _result(result)["error"]
    assert source.read_bytes() == before_archive

    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    with Database() as db:
        AsefService(db).profile_add("2222222222", "Target")
        target = db.path
    before_database = target.read_bytes()
    result = _run("import", str(source))
    assert result.exit_code == 1
    assert "Import wymaga pustego" in _result(result)["error"]
    assert target.read_bytes() == before_database
    with Database() as db:
        assert db.profiles()[0]["nip"] == "2222222222"


def test_database_import_rejects_wrong_password_and_tampering(tmp_path: Path, monkeypatch) -> None:
    source = _make_source(tmp_path, monkeypatch)
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(source), password="incorrect-password-123")
    assert result.exit_code == 1
    assert "hasło lub uszkodzone" in _result(result)["error"]
    assert not (tmp_path / "target" / "asef.sqlite3").exists()

    tampered = tmp_path / "tampered.asef"
    data = bytearray(source.read_bytes())
    data[-1] ^= 1
    tampered.write_bytes(data)
    result = _run("import", str(tampered))
    assert result.exit_code == 1
    assert "hasło lub uszkodzone" in _result(result)["error"]
    assert not (tmp_path / "target" / "asef.sqlite3").exists()
    assert not list((tmp_path / "target").glob(".asef.sqlite3.*"))


def test_database_import_rejects_foreign_sqlite_file(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "other.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(source))
    assert result.exit_code == 1
    assert "nie jest zaszyfrowanym archiwum" in _result(result)["error"]
    assert not (tmp_path / "target" / "asef.sqlite3").exists()


def test_database_import_rejects_encrypted_foreign_database(tmp_path: Path, monkeypatch) -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        raw = connection.serialize()
    source = tmp_path / "foreign.asef"
    payload = {"version": 1, "sqlite": base64.b64encode(raw).decode("ascii"), "tokens": []}
    source.write_bytes(encrypt_bytes(json.dumps(payload).encode(), PASSWORD, "database"))
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(source))
    assert result.exit_code == 1
    assert "nie jest bazą ASEF" in _result(result)["error"]
    assert not (tmp_path / "target" / "asef.sqlite3").exists()


def test_database_import_refuses_stale_sqlite_sidecar(tmp_path: Path, monkeypatch) -> None:
    source = _make_source(tmp_path, monkeypatch)
    target_dir = tmp_path / "target"
    target_dir.mkdir(mode=0o700)
    sidecar = target_dir / "asef.sqlite3-wal"
    sidecar.write_bytes(b"stale")
    monkeypatch.setenv("ASEF_DATA_DIR", str(target_dir))
    result = _run("import", str(source))
    assert result.exit_code == 1
    assert "pliki pomocnicze" in _result(result)["error"]
    assert sidecar.read_bytes() == b"stale"
    assert not (target_dir / "asef.sqlite3").exists()


def test_database_import_rejects_xml_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    source = _make_source(tmp_path, monkeypatch)
    payload = json.loads(decrypt_bytes(source.read_bytes(), PASSWORD, "database"))
    with sqlite3.connect(":memory:") as connection:
        connection.deserialize(base64.b64decode(payload["sqlite"]))
        connection.execute("UPDATE documents SET xml_sha256='bad'")
        connection.commit()
        payload["sqlite"] = base64.b64encode(connection.serialize()).decode("ascii")
    corrupt = tmp_path / "bad-hash.asef"
    corrupt.write_bytes(encrypt_bytes(json.dumps(payload).encode(), PASSWORD, "database"))
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(corrupt))
    assert result.exit_code == 1
    assert "SHA-256" in _result(result)["error"]
    assert not (tmp_path / "target" / "asef.sqlite3").exists()


def test_database_import_rolls_back_tokens_after_keyring_failure(tmp_path: Path, monkeypatch, fake_keyring) -> None:
    nip = SAMPLE["seller"]["nip"]
    fake_keyring[(f"asef:test:{nip}", "ksef_token")] = "fake-test-token"
    fake_keyring[(f"asef:prod:{nip}", "ksef_token")] = "fake-prod-token"
    source = _make_source(tmp_path, monkeypatch, prod=True)
    fake_keyring.clear()

    def fail_after_second_write(service: str, key: str, value: str) -> None:
        fake_keyring[(service, key)] = value
        if service == f"asef:test:{nip}" and key == "ksef_token":
            raise RuntimeError("fake keyring write failure")

    monkeypatch.setattr("asef.ksef.keyring.set_password", fail_after_second_write)
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(source))
    assert result.exit_code == 1
    assert "fake keyring write failure" in _result(result)["error"]
    assert fake_keyring == {}
    assert not (tmp_path / "target" / "asef.sqlite3").exists()
    assert not list((tmp_path / "target").glob(".asef.sqlite3.*"))


def test_database_import_refuses_read_only_systemd_credentials(tmp_path: Path, monkeypatch, fake_keyring) -> None:
    nip = SAMPLE["seller"]["nip"]
    fake_keyring[(f"asef:test:{nip}", "ksef_token")] = "fake-test-token"
    source = _make_source(tmp_path, monkeypatch)
    credentials_dir = tmp_path / "credentials"
    credentials_dir.mkdir()
    credential = credentials_dir / f"asef_test_{nip}"
    credential.write_text("existing-systemd-token\n")
    credential.chmod(0o600)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials_dir))
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(source))
    assert result.exit_code == 1
    assert "systemd" in _result(result)["error"]
    assert credential.read_text() == "existing-systemd-token\n"
    assert not (tmp_path / "target" / "asef.sqlite3").exists()


def test_database_import_keeps_existing_keyring_token(tmp_path: Path, monkeypatch, fake_keyring) -> None:
    nip = SAMPLE["seller"]["nip"]
    fake_keyring[(f"asef:test:{nip}", "ksef_token")] = "archived-token"
    source = _make_source(tmp_path, monkeypatch)
    fake_keyring[(f"asef:test:{nip}", "ksef_token")] = "existing-token"
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(source))
    assert result.exit_code == 1
    assert "już poświadczenie" in _result(result)["error"]
    assert fake_keyring[(f"asef:test:{nip}", "ksef_token")] == "existing-token"
    assert not (tmp_path / "target" / "asef.sqlite3").exists()


def test_database_import_refuses_existing_certificate_selection(tmp_path: Path, monkeypatch, fake_keyring) -> None:
    nip = SAMPLE["seller"]["nip"]
    fake_keyring[(f"asef:test:{nip}", "ksef_token")] = "archived-token"
    source = _make_source(tmp_path, monkeypatch)
    fake_keyring.clear()
    fake_keyring[(f"asef:test:{nip}", "certificate_auth")] = "existing-certificate-selection"
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "target"))
    result = _run("import", str(source))
    assert result.exit_code == 1
    assert "już poświadczenie" in _result(result)["error"]
    assert fake_keyring[(f"asef:test:{nip}", "certificate_auth")] == "existing-certificate-selection"
    assert not (tmp_path / "target" / "asef.sqlite3").exists()
