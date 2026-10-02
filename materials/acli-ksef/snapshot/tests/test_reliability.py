import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest

from asef.db import Database
from asef.invoice import build_invoice, invoice_summary
from asef.service import AsefService


SAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "faktura.json").read_text())
NIP = SAMPLE["seller"]["nip"]


def approved_invoice(db: Database) -> dict:
    service = AsefService(db)
    service.profile_add(NIP, "Test")
    document = service.draft_from_payload(NIP, SAMPLE)
    service.approve(document["id"], document["xml_sha256"])
    return document


def test_two_connections_cannot_send_the_same_invoice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = Event(), Event()
    posts: list[bytes] = []

    class FakeKsef:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self) -> "FakeKsef":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def send_invoice(self, xml: bytes, *, on_session, on_invoice) -> dict:
            posts.append(xml)
            entered.set()  # The first sender has not yet recorded a KSeF session.
            assert release.wait(5)
            on_session("session-1")
            on_invoice("invoice-1")
            return {"session_closed": "true"}

    monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
    path = tmp_path / "asef.sqlite3"
    with Database(path) as db:
        document = approved_invoice(db)
        original_xml = db.get_document(document["id"])["xml"]

        def first_send() -> dict:
            with Database(path) as other_db:
                return AsefService(other_db).send(document["id"])

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(first_send)
            try:
                assert entered.wait(5)
                with pytest.raises(ValueError, match="zatwierdzoną"):
                    AsefService(db).send(document["id"])
            finally:
                release.set()
            assert future.result(timeout=5)["status"] == "submitted"
        assert posts == [original_xml]


def test_revise_is_rejected_while_send_is_preparing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "asef.sqlite3"
    revised_payload = {**SAMPLE, "number": "FV/revised"}
    revised_xml = build_invoice(revised_payload)
    with Database(path) as db:
        document = approved_invoice(db)
        original_xml = db.get_document(document["id"])["xml"]

        class FakeKsef:
            def __init__(self, *_: object) -> None:
                pass

            def __enter__(self) -> "FakeKsef":
                return self

            def __exit__(self, *_: object) -> None:
                pass

            def send_invoice(self, xml: bytes, *, on_session, on_invoice) -> dict:
                with Database(path) as other_db:
                    with pytest.raises(ValueError, match="szkic"):
                        AsefService(other_db).revise_from_xml(document["id"], revised_xml)
                assert xml == original_xml
                on_session("session-1")
                on_invoice("invoice-1")
                return {"session_closed": "true"}

        monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
        AsefService(db).send(document["id"])
        assert db.get_document(document["id"])["xml"] == original_xml


def test_send_checks_stored_xml_bytes_against_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with Database(tmp_path / "asef.sqlite3") as db:
        document = approved_invoice(db)
        xml = db.get_document(document["id"])["xml"]
        with db.connection:
            db.connection.execute("UPDATE documents SET xml=? WHERE id=?", (xml + b"\n", document["id"]))
        monkeypatch.setattr("asef.service.KsefClient", lambda *_: pytest.fail("KSeF client opened"))
        with pytest.raises(ValueError, match="Zatwierdzenie"):
            AsefService(db).send(document["id"])
        assert db.get_document(document["id"])["status"] == "approved"


def test_uncertain_post_recovers_reference_from_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"send": 0, "session_invoices": 0}
    with Database(tmp_path / "asef.sqlite3") as db:
        document = approved_invoice(db)
        xml = db.get_document(document["id"])["xml"]
        expected_hash = base64.b64encode(hashlib.sha256(xml).digest()).decode("ascii")

        class FakeKsef:
            def __init__(self, *_: object) -> None:
                pass

            def __enter__(self) -> "FakeKsef":
                return self

            def __exit__(self, *_: object) -> None:
                pass

            def send_invoice(self, sent_xml: bytes, *, on_session, on_invoice) -> None:
                assert sent_xml == xml
                calls["send"] += 1
                on_session("session-1")
                raise RuntimeError("POST outcome unknown")

            def session_invoices(self, session: str) -> list[dict]:
                assert session == "session-1"
                calls["session_invoices"] += 1
                return [{"invoiceHash": "other", "referenceNumber": "wrong"},
                        {"invoiceHash": expected_hash, "referenceNumber": "invoice-1"}]

            def close_session(self, session: str) -> None:
                assert session == "session-1"

            def invoice_status(self, session: str, reference: str) -> dict:
                assert (session, reference) == ("session-1", "invoice-1")
                return {"status": {"code": 100}}

        monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
        service = AsefService(db)
        with pytest.raises(RuntimeError, match="unknown"):
            service.send(document["id"])
        assert db.get_document(document["id"])["status"] == "sending"
        recovered = service.refresh_status(document["id"])
        assert recovered["document"]["status"] == "submitted"
        assert recovered["document"]["invoice_reference"] == "invoice-1"
        assert recovered["document"]["session_closed"] == 1
        assert calls == {"send": 1, "session_invoices": 1}


def test_interrupted_sync_replays_page_without_advancing_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xml = build_invoice(SAMPLE)
    number = "1111111111-20260927-ABCDEF123456-AB"
    state = {"fail": True, "downloads": 0}

    class FakeKsef:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self) -> "FakeKsef":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def query_metadata(self, subject: str, _from: str, page: int, date_to: str) -> dict:
            if subject == "Subject1" and page == 1 and state["fail"]:
                raise RuntimeError("metadata page interrupted")
            return {"invoices": [{"ksefNumber": number}] if subject == "Subject1" and page == 0 else [],
                    "hasMore": subject == "Subject1" and page == 0,
                    "isTruncated": False, "permanentStorageHwmDate": date_to}

        def get_invoice(self, requested: str) -> bytes:
            assert requested == number
            state["downloads"] += 1
            return xml

    monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
    start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        service.profile_add(NIP, "Test")
        with pytest.raises(RuntimeError, match="interrupted"):
            service.sync(NIP, start)
        assert db.cursor(NIP, "test", "Subject1") is None
        assert db.document_by_ksef(NIP, "test", number) is not None
        state["fail"] = False
        result = service.sync(NIP, start)
        assert result["existing"] == 1
        assert db.cursor(NIP, "test", "Subject1") is not None
        assert state["downloads"] == 1


def test_sync_keeps_xml_amounts_when_metadata_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xml = build_invoice(SAMPLE)
    summary = invoice_summary(xml)
    number = "1111111111-20260927-ABCDEF123456-AB"

    class FakeKsef:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self) -> "FakeKsef":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def query_metadata(self, subject: str, _from: str, page: int, date_to: str) -> dict:
            assert page == 0
            return {"invoices": [{"ksefNumber": number, "netAmount": "999.00", "vatAmount": "99.00",
                                  "grossAmount": "1098.00"}] if subject == "Subject1" else [],
                    "hasMore": False, "isTruncated": False, "permanentStorageHwmDate": date_to}

        def get_invoice(self, requested: str) -> bytes:
            assert requested == number
            return xml

    monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
    start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        service.profile_add(NIP, "Test")
        result = service.sync(NIP, start)
        assert result["metadata_mismatches"] == 1
        document = db.document_by_ksef(NIP, "test", number)
        assert document is not None
        for field in ("net_amount", "vat_amount", "gross_amount"):
            assert document[field] == summary[field]
        visible = db.connection.execute("SELECT netto,vat,brutto FROM wystawione_faktury WHERE document_id=?",
                                        (document["id"],)).fetchone()
        assert tuple(visible) == (summary["net_amount"], summary["vat_amount"], summary["gross_amount"])
        audit = db.connection.execute("SELECT details_json FROM audit_events WHERE document_id=? AND action=?",
                                      (document["id"], "metadata_amount_mismatch")).fetchone()
        assert audit is not None
        assert json.loads(audit[0])["net_amount"] == {"xml": summary["net_amount"], "metadata": "999.00"}


def test_document_and_audit_roll_back_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        service.profile_add(NIP, "Test")
        document = service.draft_from_payload(NIP, SAMPLE)
        original = db.get_document(document["id"])
        audit_count = db.connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

        def fail_audit(*_: object) -> None:
            raise RuntimeError("audit failed")

        monkeypatch.setattr(db, "_insert_audit", fail_audit)
        with pytest.raises(RuntimeError, match="audit failed"):
            db.update_document(document["id"], status="approved", audit_event=("approved", {}))
        assert db.get_document(document["id"])["status"] == "draft"
        assert db.connection.execute("SELECT status FROM wystawione_faktury WHERE document_id=?",
                                     (document["id"],)).fetchone()[0] == "draft"

        copy = {**original, "id": "copy", "number": "FV/copy"}
        with pytest.raises(RuntimeError, match="audit failed"):
            db.save_document(copy, audit_event=("created", {}))
        assert db.connection.execute("SELECT COUNT(*) FROM documents WHERE id='copy'").fetchone()[0] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM wystawione_faktury WHERE document_id='copy'").fetchone()[0] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == audit_count


def test_sync_acceptance_during_send_keeps_invoice_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with Database(tmp_path / "asef.sqlite3") as db:
        document = approved_invoice(db)

        class FakeKsef:
            def __init__(self, *_: object) -> None:
                pass

            def __enter__(self) -> "FakeKsef":
                return self

            def __exit__(self, *_: object) -> None:
                pass

            def send_invoice(self, xml: bytes, *, on_session, on_invoice) -> dict:
                on_session("session-1")
                db.update_document(document["id"], status="accepted", ksef_number="ksef-1")
                on_invoice("invoice-1")
                return {"session_closed": "false"}

        monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
        sent = AsefService(db).send(document["id"])
        assert sent["status"] == "accepted"
        assert sent["invoice_reference"] == "invoice-1"
        assert sent["ksef_number"] == "ksef-1"


def test_reference_recovery_preserves_accepted_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with Database(tmp_path / "asef.sqlite3") as db:
        document = approved_invoice(db)
        xml = db.get_document(document["id"])["xml"]
        invoice_hash = base64.b64encode(hashlib.sha256(xml).digest()).decode("ascii")
        db.update_document(document["id"], status="accepted", session_reference="session-1", ksef_number="ksef-1")

        class FakeKsef:
            def __init__(self, *_: object) -> None:
                pass

            def __enter__(self) -> "FakeKsef":
                return self

            def __exit__(self, *_: object) -> None:
                pass

            def session_invoices(self, _session: str) -> list[dict]:
                return [{"invoiceHash": invoice_hash, "referenceNumber": "invoice-1"}]

            def close_session(self, _session: str) -> None:
                pass

            def invoice_status(self, _session: str, _reference: str) -> dict:
                return {"status": {"code": 100}}

        monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
        refreshed = AsefService(db).refresh_status(document["id"])
        assert refreshed["document"]["status"] == "accepted"
        assert refreshed["document"]["invoice_reference"] == "invoice-1"


def test_send_does_not_clear_concurrently_closed_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with Database(tmp_path / "asef.sqlite3") as db:
        document = approved_invoice(db)

        class FakeKsef:
            def __init__(self, *_: object) -> None:
                pass

            def __enter__(self) -> "FakeKsef":
                return self

            def __exit__(self, *_: object) -> None:
                pass

            def send_invoice(self, xml: bytes, *, on_session, on_invoice) -> dict:
                on_session("session-1")
                on_invoice("invoice-1")
                db.update_document(document["id"], session_closed=1)
                return {"session_closed": "false"}

        monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
        assert AsefService(db).send(document["id"])["session_closed"] == 1


def test_metadata_mismatch_audit_failure_rolls_back_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        service.profile_add(NIP, "Test")
        profile = db.profile(NIP)
        original_insert_audit = db._insert_audit

        def fail_mismatch(action: str, document_id: str | None, details: dict) -> None:
            if action == "metadata_amount_mismatch":
                raise RuntimeError("mismatch audit failed")
            original_insert_audit(action, document_id, details)

        monkeypatch.setattr(db, "_insert_audit", fail_mismatch)
        with pytest.raises(RuntimeError, match="mismatch audit failed"):
            service._new_document(
                profile, build_invoice(SAMPLE), "issued", "accepted", "ksef", None, "ksef-1",
                metadata_mismatch={"net_amount": {"xml": "1.00", "metadata": "2.00"}},
            )
        assert db.document_by_ksef(NIP, "test", "ksef-1") is None
        assert db.connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='document_created'").fetchone()[0] == 0
