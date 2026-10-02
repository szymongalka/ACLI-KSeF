from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import db_path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
  nip TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  environment TEXT NOT NULL CHECK (environment IN ('test','demo','prod')),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_environments (
  nip TEXT NOT NULL REFERENCES profiles(nip),
  environment TEXT NOT NULL CHECK (environment IN ('test','demo','prod')),
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(nip, environment)
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  profile_nip TEXT NOT NULL REFERENCES profiles(nip),
  environment TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('issued','received')),
  kind TEXT NOT NULL CHECK (kind IN ('invoice','correction')),
  status TEXT NOT NULL,
  number TEXT NOT NULL,
  issue_date TEXT,
  seller_nip TEXT,
  seller_name TEXT,
  buyer_nip TEXT,
  buyer_name TEXT,
  currency TEXT NOT NULL DEFAULT 'PLN',
  net_amount TEXT,
  vat_amount TEXT,
  gross_amount TEXT,
  original_ksef_number TEXT,
  ksef_number TEXT,
  session_reference TEXT,
  invoice_reference TEXT,
  session_closed INTEGER NOT NULL DEFAULT 0,
  xml BLOB NOT NULL,
  xml_sha256 TEXT NOT NULL,
  payload_json TEXT,
  approved_sha256 TEXT,
  upo_xml BLOB,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(environment, profile_nip, ksef_number)
);
CREATE INDEX IF NOT EXISTS idx_documents_lookup ON documents(profile_nip, direction, kind, issue_date);
CREATE TABLE IF NOT EXISTS wystawione_faktury (
  document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  numer TEXT NOT NULL,
  data_wystawienia TEXT,
  nabywca_nip TEXT,
  nabywca_nazwa TEXT,
  netto TEXT,
  vat TEXT,
  brutto TEXT,
  waluta TEXT,
  status TEXT,
  numer_ksef TEXT
);
CREATE TABLE IF NOT EXISTS odebrane_faktury (
  document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  numer TEXT NOT NULL,
  data_wystawienia TEXT,
  sprzedawca_nip TEXT,
  sprzedawca_nazwa TEXT,
  netto TEXT,
  vat TEXT,
  brutto TEXT,
  waluta TEXT,
  numer_ksef TEXT
);
CREATE TABLE IF NOT EXISTS korekty (
  document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  kierunek TEXT NOT NULL,
  numer TEXT NOT NULL,
  data_wystawienia TEXT,
  sprzedawca_nip TEXT,
  nabywca_nip TEXT,
  netto TEXT,
  vat TEXT,
  brutto TEXT,
  numer_ksef TEXT,
  numer_ksef_faktury_pierwotnej TEXT,
  status TEXT
);
CREATE TABLE IF NOT EXISTS templates (
  name TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_cursors (
  profile_nip TEXT NOT NULL,
  environment TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  next_from TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_nip, environment, subject_type)
);
CREATE TABLE IF NOT EXISTS company_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nip TEXT NOT NULL,
  source TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id TEXT,
  action TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | None = None):
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.stat().st_mode & 0o077:
            raise PermissionError("Katalog bazy ASEF musi być dostępny tylko dla właściciela (0700).")
        if self.path.is_symlink():
            raise ValueError("Baza ASEF nie może być dowiązaniem symbolicznym.")
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        if not self.path.is_file():
            raise ValueError("Baza ASEF musi być zwykłym plikiem.")
        self.path.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists():
                if not stat.S_ISREG(sidecar.lstat().st_mode):
                    raise ValueError("Plik pomocniczy SQLite musi być zwykłym plikiem.")
                sidecar.chmod(0o600)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA)
        if "session_closed" not in {row[1] for row in self.connection.execute("PRAGMA table_info(documents)")}:
            self.connection.execute("ALTER TABLE documents ADD COLUMN session_closed INTEGER NOT NULL DEFAULT 0")
            self.connection.execute("UPDATE documents SET session_closed=1 WHERE upo_xml IS NOT NULL")
        # Keep the legacy NIP parent table for existing document foreign keys.
        self.connection.execute(
            "INSERT OR IGNORE INTO profile_environments(nip,environment,name,created_at) "
            "SELECT nip,environment,name,created_at FROM profiles"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add_profile(self, nip: str, name: str, environment: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO profiles(nip,name,environment,created_at) VALUES(?,?,?,?)",
                (nip, name, environment, now()),
            )
            self.connection.execute(
                "INSERT INTO profile_environments(nip,environment,name,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(nip,environment) DO UPDATE SET name=excluded.name",
                (nip, environment, name, now()),
            )

    def profile(self, nip: str | None = None, environment: str | None = None) -> dict[str, Any]:
        clauses, params = [], []
        if nip:
            clauses.append("nip=?")
            params.append(nip)
        if environment:
            clauses.append("environment=?")
            params.append(environment)
        rows = self.connection.execute(
            "SELECT * FROM profile_environments" + (" WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY created_at LIMIT 2", params
        ).fetchall()
        if not rows:
            raise ValueError("Nie znaleziono profilu NIP. Użyj asef profile add.")
        if len(rows) != 1:
            raise ValueError("Wskaż profil przez --nip i --env (więcej niż jeden profil pasuje).")
        return dict(rows[0])

    def profiles(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.connection.execute("SELECT * FROM profile_environments ORDER BY nip,environment")]

    def save_document(self, document: dict[str, Any],
                      audit_event: tuple[str, dict[str, Any]] | list[tuple[str, dict[str, Any]]] | None = None) -> None:
        fields = list(document)
        updates = ",".join(f"{f}=excluded.{f}" for f in fields if f != "id")
        with self.connection:
            self.connection.execute(
                f"INSERT INTO documents({','.join(fields)}) VALUES({','.join('?' for _ in fields)}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                tuple(document.values()),
            )
            self._refresh_summary(document["id"])
            events = audit_event if isinstance(audit_event, list) else [audit_event] if audit_event else []
            for action, details in events:
                self._insert_audit(action, document["id"], details)

    def update_document(self, document_id: str, *, expected: dict[str, Any] | None = None,
                        audit_event: tuple[str, dict[str, Any]] | list[tuple[str, dict[str, Any]]] | None = None,
                        **changes: Any) -> bool:
        if not changes:
            return False
        changes["updated_at"] = now()
        conditions = ["id=?"]
        parameters: list[Any] = [document_id]
        for key, value in (expected or {}).items():
            if value is None:
                conditions.append(f"{key} IS NULL")
            else:
                conditions.append(f"{key}=?")
                parameters.append(value)
        with self.connection:
            changed = self.connection.execute(
                "UPDATE documents SET " + ",".join(f"{k}=?" for k in changes)
                + " WHERE " + " AND ".join(conditions),
                (*changes.values(), *parameters),
            )
            if changed.rowcount != 1:
                return False
            self._refresh_summary(document_id)
            events = audit_event if isinstance(audit_event, list) else [audit_event] if audit_event else []
            for action, details in events:
                self._insert_audit(action, document_id, details)
        return True

    def _refresh_summary(self, document_id: str) -> None:
        d = self.get_document(document_id)
        for table in ("wystawione_faktury", "odebrane_faktury", "korekty"):
            self.connection.execute(f"DELETE FROM {table} WHERE document_id=?", (document_id,))
        if d["kind"] == "correction":
            self.connection.execute(
                "INSERT INTO korekty VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (document_id, d["direction"], d["number"], d["issue_date"], d["seller_nip"],
                 d["buyer_nip"], d["net_amount"], d["vat_amount"], d["gross_amount"],
                 d["ksef_number"], d["original_ksef_number"], d["status"]),
            )
        elif d["direction"] == "issued":
            self.connection.execute(
                "INSERT INTO wystawione_faktury VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (document_id, d["number"], d["issue_date"], d["buyer_nip"], d["buyer_name"],
                 d["net_amount"], d["vat_amount"], d["gross_amount"], d["currency"],
                 d["status"], d["ksef_number"]),
            )
        else:
            self.connection.execute(
                "INSERT INTO odebrane_faktury VALUES(?,?,?,?,?,?,?,?,?,?)",
                (document_id, d["number"], d["issue_date"], d["seller_nip"], d["seller_name"],
                 d["net_amount"], d["vat_amount"], d["gross_amount"], d["currency"], d["ksef_number"]),
            )

    def get_document(self, document_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise ValueError(f"Nie znaleziono dokumentu {document_id}.")
        return dict(row)

    def document_by_ksef(self, profile_nip: str, environment: str, ksef_number: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE profile_nip=? AND environment=? AND ksef_number=?",
            (profile_nip, environment, ksef_number),
        ).fetchone()
        return dict(row) if row else None

    def sent_document_by_hash(self, profile_nip: str, environment: str, xml_sha256: str) -> dict[str, Any] | None:
        rows = self.connection.execute(
            "SELECT * FROM documents WHERE profile_nip=? AND environment=? AND direction='issued' "
            "AND xml_sha256=? AND status IN ('sending','submitted','accepted')",
            (profile_nip, environment, xml_sha256),
        ).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None

    def list_documents(self, profile_nip: str, category: str | None = None, limit: int = 100,
                       environment: str | None = None) -> list[dict[str, Any]]:
        profile = self.profile(profile_nip, environment)
        clauses = ["profile_nip=?", "environment=?"]
        params: list[Any] = [profile_nip, profile["environment"]]
        if category == "issued":
            clauses += ["direction='issued'", "kind='invoice'"]
        elif category == "received":
            clauses += ["direction='received'", "kind='invoice'"]
        elif category == "corrections":
            clauses += ["kind='correction'"]
        elif category is not None:
            raise ValueError("Kategoria: issued, received albo corrections.")
        params.append(limit)
        rows = self.connection.execute(
            "SELECT * FROM documents WHERE " + " AND ".join(clauses) + " ORDER BY issue_date DESC, created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [{k: v for k, v in dict(r).items() if k not in {"xml", "upo_xml", "payload_json"}} for r in rows]

    def _insert_audit(self, action: str, document_id: str | None,
                      details: dict[str, Any] | None) -> None:
        self.connection.execute(
            "INSERT INTO audit_events(document_id,action,details_json,created_at) VALUES(?,?,?,?)",
            (document_id, action, json.dumps(details or {}, ensure_ascii=False), now()),
        )

    def audit(self, action: str, document_id: str | None = None, details: dict[str, Any] | None = None) -> None:
        with self.connection:
            self._insert_audit(action, document_id, details)

    def set_cursor(self, nip: str, environment: str, subject_type: str, next_from: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO sync_cursors VALUES(?,?,?,?,?) ON CONFLICT(profile_nip,environment,subject_type) "
                "DO UPDATE SET next_from=excluded.next_from,updated_at=excluded.updated_at",
                (nip, environment, subject_type, next_from, now()),
            )

    def cursor(self, nip: str, environment: str, subject_type: str) -> str | None:
        row = self.connection.execute(
            "SELECT next_from FROM sync_cursors WHERE profile_nip=? AND environment=? AND subject_type=?",
            (nip, environment, subject_type),
        ).fetchone()
        return row[0] if row else None
