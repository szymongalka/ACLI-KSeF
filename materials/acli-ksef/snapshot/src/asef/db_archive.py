"""Encrypted, portable backup of the ASEF database and KSeF credentials."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from .certificates import export_certificates, prepare_certificate_import, validate_certificate_entries
from .db import Database
from .ksef import Secrets
from .paths import db_path
from .secure_archive import read_encrypted_file, write_encrypted_file


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Eksport ASEF ma uszkodzoną bazę SQLite.")
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Eksport ASEF ma niespójne powiązania danych.")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"profiles", "profile_environments", "documents", "templates", "sync_cursors", "audit_events"} <= tables:
        raise ValueError("Plik nie jest bazą ASEF.")
    for xml, xml_sha256, approved_sha256 in connection.execute(
        "SELECT xml,xml_sha256,approved_sha256 FROM documents"
    ):
        actual = hashlib.sha256(xml).hexdigest() if isinstance(xml, bytes) else None
        if actual != xml_sha256 or (approved_sha256 and approved_sha256 != actual):
            raise ValueError("Eksport ASEF zawiera fakturę z niespójnym SHA-256 XML.")
    return {
        "profiles": connection.execute("SELECT COUNT(*) FROM profile_environments").fetchone()[0],
        "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "templates": connection.execute("SELECT COUNT(*) FROM templates").fetchone()[0],
    }


def _read_payload(source: Path, password: str) -> tuple[bytes, list[dict[str, str]], list[Any]]:
    plaintext = read_encrypted_file(source, password, "database")
    try:
        payload = json.loads(plaintext)
        if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
            raise ValueError
        encoded, tokens = payload["sqlite"], payload["tokens"]
        if not isinstance(encoded, str) or not isinstance(tokens, list):
            raise ValueError
        database_bytes = base64.b64decode(encoded, validate=True)
        seen: set[tuple[str, str]] = set()
        for entry in tokens:
            if not isinstance(entry, dict) or set(entry) != {"nip", "environment", "token"}:
                raise ValueError
            nip, environment, token = entry["nip"], entry["environment"], entry["token"]
            if not isinstance(nip, str) or not isinstance(environment, str) or not isinstance(token, str):
                raise ValueError
            if not nip or environment not in {"test", "demo", "prod"} or not token or len(token) > 16384:
                raise ValueError
            pair = (nip, environment)
            if pair in seen:
                raise ValueError
            seen.add(pair)
        entries = payload["certificates"] if payload["version"] == 2 else []
        return database_bytes, tokens, validate_certificate_entries(entries)
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise ValueError("Archiwum ASEF ma nieprawidłową zawartość.") from exc


def export_database(output: Path, password: str) -> dict[str, Any]:
    if os.path.lexists(output.expanduser().absolute()):
        raise ValueError("Archiwum już istnieje; wybierz nową nazwę pliku.")
    with Database() as db, closing(sqlite3.connect(":memory:")) as snapshot:
        db.connection.backup(snapshot)
        # A WAL source leaves WAL header bytes in the in-memory snapshot;
        # VACUUM makes the serialized image independently readable.
        snapshot.execute("VACUUM")
        counts = _counts(snapshot)
        database_bytes = snapshot.serialize()
        tokens: list[dict[str, str]] = []
        missing: list[dict[str, str]] = []
        credentials_dir = os.environ.get("CREDENTIALS_DIRECTORY")
        if credentials_dir == "":
            raise ValueError("CREDENTIALS_DIRECTORY jest pusty.")
        for nip, environment in snapshot.execute("SELECT nip,environment FROM profile_environments ORDER BY nip,environment"):
            if credentials_dir is not None and not (Path(credentials_dir) / f"asef_{environment}_{nip}").is_file():
                missing.append({"nip": nip, "environment": environment})
                continue
            token = Secrets(environment, nip).get("ksef_token")
            if token:
                tokens.append({"nip": nip, "environment": environment, "token": token})
            else:
                missing.append({"nip": nip, "environment": environment})
    certificates = export_certificates()
    payload = json.dumps({
        "version": 2,
        "sqlite": base64.b64encode(database_bytes).decode("ascii"),
        "tokens": tokens,
        "certificates": certificates,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path = write_encrypted_file(output, payload, password, "database")
    return {"path": str(path), "counts": counts,
            "tokens_exported": len(tokens), "profiles_without_token": missing,
            "certificates_exported": len(certificates)}


def import_database(source: Path, password: str) -> dict[str, Any]:
    target = db_path()
    if any(os.path.lexists(str(target) + suffix) for suffix in ("", "-wal", "-shm", "-journal")):
        raise ValueError("Baza ASEF lub jej pliki pomocnicze już istnieją. Import wymaga pustego ASEF_DATA_DIR.")
    database_bytes, tokens, certificates = _read_payload(source, password)
    try:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.deserialize(database_bytes)
            counts = _counts(connection)
            profiles = {(row[0], row[1]) for row in connection.execute(
                "SELECT nip,environment FROM profile_environments"
            )}
    except sqlite3.Error as exc:
        raise ValueError("Archiwum ASEF ma uszkodzoną bazę SQLite.") from exc
    if any((entry["nip"], entry["environment"]) not in profiles for entry in tokens):
        raise ValueError("Archiwum ASEF zawiera token bez profilu.")
    if os.environ.get("CREDENTIALS_DIRECTORY") == "":
        raise ValueError("CREDENTIALS_DIRECTORY jest pusty.")
    if tokens and os.environ.get("CREDENTIALS_DIRECTORY") is not None:
        raise ValueError("Import tokenów do poświadczeń systemd nie jest obsługiwany. Użyj keyringu na nowym hoście.")
    for nip, environment in profiles:
        if os.environ.get("CREDENTIALS_DIRECTORY") is None:
            secret = Secrets(environment, nip)
            if any(secret.get(key) for key in (
                "ksef_token", "refresh_token", "refresh_valid_until", "certificate_auth",
            )):
                raise ValueError("Magazyn haseł zawiera już poświadczenie KSeF dla importowanego profilu.")

    certificate_import = prepare_certificate_import(certificates) if certificates else None
    published_certificates: list[Path] = []
    stored: list[Secrets] = []
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(database_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        for entry in tokens:
            secret = Secrets(entry["environment"], entry["nip"])
            stored.append(secret)
            secret.set("ksef_token", entry["token"])
        if certificate_import is not None:
            published_certificates = certificate_import.publish()
        os.link(temporary, target)  # Publish only after the database and credentials are ready.
    except BaseException as exc:
        rollback_failed = False
        for path in reversed(published_certificates):
            try:
                path.unlink()
            except OSError:
                rollback_failed = True
        for secret in reversed(stored):
            try:
                secret.delete("ksef_token")
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise RuntimeError("Import przerwany; nie udało się usunąć części zapisanych poświadczeń KSeF.") from exc
        raise
    finally:
        if certificate_import is not None:
            certificate_import.cleanup()
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return {"path": str(target), "counts": counts, "tokens_imported": len(tokens),
            "certificates_imported": len(certificates)}
