"""Portable, in-memory invoice XML bundle for password-protected transfer."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from typing import Any

from lxml import etree

from .db import Database, now
from .invoice import invoice_summary, validate_xml


FORMAT = "asef-invoice-xml"
VERSION = 1
PENDING = {"preparing", "sending", "submitted"}


def export_xml_bundle(db: Database, nip: str | None = None,
                      environment: str | None = None) -> tuple[bytes, dict[str, int]]:
    """Serialize selected invoice XML without writing plaintext to disk."""
    profiles = [profile for profile in db.profiles()
                if (nip is None or profile["nip"] == nip)
                and (environment is None or profile["environment"] == environment)]
    if not profiles:
        raise ValueError("Nie znaleziono profili dla wskazanego NIP i środowiska.")
    selected = {(profile["nip"], profile["environment"]) for profile in profiles}
    documents = []
    for row in db.connection.execute(
        "SELECT profile_nip,environment,direction,status,ksef_number,xml,xml_sha256 "
        "FROM documents ORDER BY created_at,id"
    ):
        profile_nip, env, direction, status, ksef_number, xml, expected_hash = row
        if (profile_nip, env) not in selected:
            continue
        if not isinstance(xml, bytes) or hashlib.sha256(xml).hexdigest() != expected_hash:
            raise ValueError("Faktura w bazie ma niespójny SHA-256 XML; eksport przerwany.")
        documents.append({
            "profile_nip": profile_nip,
            "environment": env,
            "direction": direction,
            "source_status": status,
            "ksef_number": ksef_number,
            "sha256": expected_hash,
            "xml_base64": base64.b64encode(xml).decode("ascii"),
        })
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "profiles": [{"nip": p["nip"], "environment": p["environment"], "name": p["name"]}
                     for p in profiles],
        "documents": documents,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), {
        "profiles": len(profiles), "documents": len(documents),
    }


def import_xml_bundle(db: Database, payload: bytes) -> dict[str, int]:
    """Validate the whole bundle, then add invoices in one SQLite transaction."""
    try:
        archive = json.loads(payload)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Pakiet faktur nie jest poprawnym JSON.") from exc
    if not isinstance(archive, dict) or archive.get("format") != FORMAT or archive.get("version") != VERSION:
        raise ValueError("Nieobsługiwany format pakietu faktur XML.")
    raw_profiles, raw_documents = archive.get("profiles"), archive.get("documents")
    if not isinstance(raw_profiles, list) or not isinstance(raw_documents, list):
        raise ValueError("Pakiet faktur ma nieprawidłową strukturę.")

    profiles: dict[tuple[str, str], str] = {}
    for entry in raw_profiles:
        if not isinstance(entry, dict):
            raise ValueError("Pakiet faktur ma nieprawidłowy profil.")
        nip, environment, name = entry.get("nip"), entry.get("environment"), entry.get("name")
        if (not isinstance(nip, str) or not re.fullmatch(r"[0-9]{10}", nip)
                or environment not in {"test", "demo", "prod"}
                or not isinstance(name, str) or not name.strip()):
            raise ValueError("Pakiet faktur ma nieprawidłowy profil.")
        key = (nip, environment)
        if key in profiles:
            raise ValueError("Pakiet faktur zawiera powtórzony profil.")
        profiles[key] = name

    records: list[dict[str, Any]] = []
    source_statuses: dict[tuple[str, str, str, str], str] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for entry in raw_documents:
        if not isinstance(entry, dict):
            raise ValueError("Pakiet faktur ma nieprawidłowy dokument.")
        nip, env, direction = entry.get("profile_nip"), entry.get("environment"), entry.get("direction")
        status, ksef_number = entry.get("source_status"), entry.get("ksef_number")
        digest, encoded = entry.get("sha256"), entry.get("xml_base64")
        if ((nip, env) not in profiles or direction not in {"issued", "received"}
                or not isinstance(status, str)
                or (ksef_number is not None and (not isinstance(ksef_number, str) or not ksef_number))
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(encoded, str)):
            raise ValueError("Pakiet faktur ma nieprawidłowy dokument.")
        try:
            xml = base64.b64decode(encoded, validate=True)
        except (binascii.Error, etree.LxmlError, ValueError) as exc:
            raise ValueError("Pakiet faktur zawiera niepoprawny FA(3) XML.") from exc
        if hashlib.sha256(xml).hexdigest() != digest:
            raise ValueError("Pakiet faktur zawiera XML z niespójnym SHA-256.")
        try:
            validate_xml(xml)
            summary = invoice_summary(xml)
        except (etree.LxmlError, ValueError) as exc:
            raise ValueError("Pakiet faktur zawiera niepoprawny FA(3) XML.") from exc
        if ((direction == "issued" and summary["seller_nip"] != nip)
                or (direction == "received" and summary["buyer_nip"] != nip)):
            raise ValueError("NIP profilu nie odpowiada roli na fakturze XML.")
        key = (nip, env, direction, digest)
        if key in seen:
            raise ValueError("Pakiet faktur zawiera powtórzony XML.")
        seen.add(key)
        source_statuses[key] = status
        if direction == "received":
            imported_status = "received"
        elif status == "accepted" and ksef_number:
            imported_status = "accepted"
        else:
            imported_status = "draft"
        records.append({
            "profile_nip": nip, "environment": env, "direction": direction,
            "kind": summary["kind"], "status": imported_status,
            "number": summary["number"], "issue_date": summary["issue_date"],
            "seller_nip": summary["seller_nip"], "seller_name": summary["seller_name"],
            "buyer_nip": summary["buyer_nip"], "buyer_name": summary["buyer_name"],
            "currency": summary["currency"], "net_amount": summary["net_amount"],
            "vat_amount": summary["vat_amount"], "gross_amount": summary["gross_amount"],
            "original_ksef_number": summary["original_ksef_number"],
            "ksef_number": ksef_number if imported_status in {"accepted", "received"} else None,
            "session_reference": None, "invoice_reference": None, "session_closed": 0,
            "xml": xml, "xml_sha256": digest, "payload_json": None,
            "approved_sha256": None, "upo_xml": None, "source": "xml_bundle",
        })

    counts = {"profiles_created": 0, "imported": 0, "existing": 0, "pending_review": 0}
    with db.connection:
        db.connection.execute("BEGIN IMMEDIATE")
        for (nip, env), name in profiles.items():
            found = db.connection.execute(
                "SELECT 1 FROM profile_environments WHERE nip=? AND environment=?", (nip, env)
            ).fetchone()
            if found:
                continue
            timestamp = now()
            db.connection.execute(
                "INSERT OR IGNORE INTO profiles(nip,name,environment,created_at) VALUES(?,?,?,?)",
                (nip, name, env, timestamp),
            )
            db.connection.execute(
                "INSERT INTO profile_environments(nip,environment,name,created_at) VALUES(?,?,?,?)",
                (nip, env, name, timestamp),
            )
            counts["profiles_created"] += 1
        for record in records:
            match = db.connection.execute(
                "SELECT xml FROM documents WHERE profile_nip=? AND environment=? "
                "AND direction=? AND xml_sha256=? LIMIT 1",
                (record["profile_nip"], record["environment"], record["direction"], record["xml_sha256"]),
            ).fetchone()
            if match:
                if match[0] != record["xml"]:
                    raise ValueError("Kolizja SHA-256 z inną treścią XML.")
                counts["existing"] += 1
                continue
            if record["ksef_number"]:
                conflict = db.document_by_ksef(record["profile_nip"], record["environment"], record["ksef_number"])
                if conflict:
                    raise ValueError("Numer KSeF z pakietu przypisano już do innego XML.")
            record["id"] = str(uuid.uuid4())
            record["created_at"] = record["updated_at"] = now()
            fields = list(record)
            db.connection.execute(
                f"INSERT INTO documents({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                tuple(record.values()),
            )
            db._refresh_summary(record["id"])
            key = (record["profile_nip"], record["environment"], record["direction"], record["xml_sha256"])
            db._insert_audit("xml_bundle_imported", record["id"], {
                "source_status": source_statuses[key],
                "imported_status": record["status"],
            })
            counts["imported"] += 1
            if source_statuses[key] in PENDING:
                counts["pending_review"] += 1
    return counts
