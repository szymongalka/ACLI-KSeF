import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from asef.bulk_xml import export_xml_bundle, import_xml_bundle
from asef.cli import app
from asef.db import Database
from asef.invoice import build_invoice
from asef.service import AsefService


SAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "faktura.json").read_text())
PASSWORD = "demo archive password"


def test_xml_bundle_roundtrip_preserves_roles_without_transferring_approval(tmp_path: Path) -> None:
    seller, buyer = SAMPLE["seller"]["nip"], SAMPLE["buyer"]["nip"]
    with Database(tmp_path / "source.sqlite3") as source:
        service = AsefService(source)
        service.profile_add(seller, "Seller", "test")
        service.profile_add(buyer, "Buyer", "demo")
        accepted = service.draft_from_payload(seller, SAMPLE, "test")
        source.update_document(accepted["id"], status="accepted", ksef_number="KSEF-1")
        pending_payload = {**SAMPLE, "number": "FV/09/2026/002"}
        pending = service.draft_from_payload(seller, pending_payload, "test")
        pending_xml = source.get_document(pending["id"])["xml"]
        source.update_document(pending["id"], status="sending")
        approved_payload = {**SAMPLE, "number": "FV/09/2026/003"}
        approved = service.draft_from_payload(seller, approved_payload, "test")
        service.approve(approved["id"], approved["xml_sha256"])
        received = service._new_document(source.profile(buyer, "demo"), build_invoice(SAMPLE),
                                         "received", "received", "ksef", None, "KSEF-2")
        payload, exported = export_xml_bundle(source)
        assert exported == {"profiles": 2, "documents": 4}
        assert b"KSEF-1" in payload  # The CLI encrypts this in the output file.

    with Database(tmp_path / "destination.sqlite3") as destination:
        imported = import_xml_bundle(destination, payload)
        assert imported == {"profiles_created": 2, "imported": 4, "existing": 0, "pending_review": 1}
        rows = {row["number"]: row for row in destination.connection.execute(
            "SELECT number,direction,status,ksef_number,approved_sha256,profile_nip,environment,xml "
            "FROM documents WHERE direction='issued'"
        )}
        assert rows["FV/09/2026/001"]["status"] == "accepted"
        assert rows["FV/09/2026/001"]["ksef_number"] == "KSEF-1"
        assert rows["FV/09/2026/002"]["status"] == "draft"
        assert rows["FV/09/2026/002"]["approved_sha256"] is None
        assert rows["FV/09/2026/002"]["xml"] == pending_xml
        assert rows["FV/09/2026/003"]["status"] == "draft"
        assert rows["FV/09/2026/003"]["approved_sha256"] is None
        receipt = destination.connection.execute(
            "SELECT direction,status,ksef_number,profile_nip,environment FROM documents "
            "WHERE direction='received'"
        ).fetchone()
        assert tuple(receipt) == ("received", "received", "KSEF-2", buyer, "demo")
        assert import_xml_bundle(destination, payload) == {
            "profiles_created": 0, "imported": 0, "existing": 4, "pending_review": 0,
        }

def test_xml_bundle_filters_and_rejects_invalid_data_before_writing(tmp_path: Path) -> None:
    seller = SAMPLE["seller"]["nip"]
    with Database(tmp_path / "source.sqlite3") as source:
        service = AsefService(source)
        service.profile_add(seller, "Seller", "test")
        service.profile_add(seller, "Seller PROD", "prod")
        service.draft_from_payload(seller, SAMPLE, "test")
        payload, counts = export_xml_bundle(source, seller, "test")
        assert counts == {"profiles": 1, "documents": 1}

    with Database(tmp_path / "destination.sqlite3") as destination:
        bad_hash = json.loads(payload)
        bad_hash["documents"][0]["sha256"] = "0" * 64
        with pytest.raises(ValueError, match="SHA-256"):
            import_xml_bundle(destination, json.dumps(bad_hash).encode())
        assert destination.profiles() == []

        wrong_role = json.loads(payload)
        wrong_role["profiles"][0]["nip"] = "9999999999"
        wrong_role["documents"][0]["profile_nip"] = "9999999999"
        with pytest.raises(ValueError, match="NIP profilu"):
            import_xml_bundle(destination, json.dumps(wrong_role).encode())
        assert destination.profiles() == []

        malformed = json.loads(payload)
        malformed["documents"][0]["xml_base64"] = "!"
        with pytest.raises(ValueError, match=r"FA\(3\)"):
            import_xml_bundle(destination, json.dumps(malformed).encode())
        assert destination.profiles() == []


def test_xml_bundle_conflict_rolls_back_whole_import(tmp_path: Path) -> None:
    seller = SAMPLE["seller"]["nip"]
    with Database(tmp_path / "source.sqlite3") as source:
        service = AsefService(source)
        service.profile_add(seller, "Seller")
        first = service.draft_from_payload(seller, SAMPLE)
        source.update_document(first["id"], status="accepted", ksef_number="KSEF-1")
        second_payload = {**SAMPLE, "number": "FV/09/2026/002"}
        second = service.draft_from_payload(seller, second_payload)
        source.update_document(second["id"], status="accepted", ksef_number="KSEF-2")
        payload, _ = export_xml_bundle(source)

    with Database(tmp_path / "destination.sqlite3") as destination:
        service = AsefService(destination)
        service.profile_add(seller, "Existing")
        collision = service.draft_from_payload(seller, {**SAMPLE, "number": "OTHER"})
        destination.update_document(collision["id"], status="accepted", ksef_number="KSEF-2")
        with pytest.raises(ValueError, match="Numer KSeF"):
            import_xml_bundle(destination, payload)
        assert destination.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_xml_bundle_cli_encrypts_and_requires_password_before_creating_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "source"))
    with Database() as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, "Seller")
        service.draft_from_payload(nip, SAMPLE)
    archive = tmp_path / "invoices.asefxml"
    runner = CliRunner()
    exported = runner.invoke(app, ["invoice", "export-bundle", str(archive)],
                             input=f"{PASSWORD}\n{PASSWORD}\n")
    assert exported.exit_code == 0, exported.output
    assert b"<Faktura" not in archive.read_bytes()

    destination = tmp_path / "target"
    monkeypatch.setenv("ASEF_DATA_DIR", str(destination))
    wrong = runner.invoke(app, ["invoice", "import-bundle", str(archive)], input="wrong password\n")
    assert wrong.exit_code == 1
    assert not (destination / "asef.sqlite3").exists()
    restored = runner.invoke(app, ["invoice", "import-bundle", str(archive)], input=f"{PASSWORD}\n")
    assert restored.exit_code == 0, restored.output
    with Database() as db:
        assert db.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
