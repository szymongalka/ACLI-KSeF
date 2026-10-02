from __future__ import annotations

import json
import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from .db import Database, now
from .invoice import build_invoice, invoice_summary, validate_xml
from .ksef import KsefClient, KsefError
from .paths import write_private_file
from .preview import render_html, render_pdf


def _public_document(document: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in document.items() if k not in {"xml", "upo_xml", "payload_json"}}


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Data synchronizacji musi być w formacie ISO 8601 UTC.") from exc
    if parsed.tzinfo is None:
        raise ValueError("Data synchronizacji musi zawierać strefę czasową UTC.")
    return parsed.astimezone(timezone.utc)


class AsefService:
    def __init__(self, db: Database, *, client_factory: Callable[..., KsefClient] | None = None):
        self.db = db
        self.client_factory = client_factory

    def profile_add(self, nip: str, name: str, environment: str | None = None) -> dict[str, Any]:
        if not nip.isdigit() or len(nip) != 10:
            raise ValueError("NIP musi składać się z 10 cyfr.")
        if environment is None:
            existing = [p for p in self.db.profiles() if p["nip"] == nip]
            if existing and any(p["environment"] != "test" for p in existing):
                raise ValueError("Ten NIP ma już profil poza TEST; podaj jawnie --env.")
            environment = "test"
        if environment not in {"test", "demo", "prod"}:
            raise ValueError("Środowisko: test, demo albo prod.")
        self.db.add_profile(nip, name, environment)
        return self.db.profile(nip, environment)

    def draft_from_payload(self, profile_nip: str, payload: dict[str, Any],
                           environment: str | None = None) -> dict[str, Any]:
        profile = self.db.profile(profile_nip, environment)
        if payload.get("seller", {}).get("nip") != profile_nip:
            raise ValueError("NIP sprzedawcy musi odpowiadać profilowi ASEF.")
        xml = build_invoice(payload)
        return self._new_document(profile, xml, "issued", "draft", "draft", payload)

    def draft_from_xml(self, profile_nip: str, xml: bytes,
                       environment: str | None = None) -> dict[str, Any]:
        profile = self.db.profile(profile_nip, environment)
        validate_xml(xml)
        summary = invoice_summary(xml)
        if summary["seller_nip"] != profile_nip:
            raise ValueError("NIP sprzedawcy w XML nie odpowiada profilowi ASEF.")
        return self._new_document(profile, xml, "issued", "draft", "draft", None)

    def _new_document(
        self, profile: dict[str, Any], xml: bytes, direction: str, status: str,
        source: str, payload: dict[str, Any] | None, ksef_number: str | None = None,
        metadata_mismatch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        summary = invoice_summary(xml)
        document = {
            "id": str(uuid.uuid4()), "profile_nip": profile["nip"],
            "environment": profile["environment"], "direction": direction,
            "kind": summary["kind"], "status": status, "number": summary["number"],
            "issue_date": summary["issue_date"], "seller_nip": summary["seller_nip"],
            "seller_name": summary["seller_name"], "buyer_nip": summary["buyer_nip"],
            "buyer_name": summary["buyer_name"], "currency": summary["currency"],
            "net_amount": summary["net_amount"], "vat_amount": summary["vat_amount"],
            "gross_amount": summary["gross_amount"],
            "original_ksef_number": summary["original_ksef_number"],
            "ksef_number": ksef_number, "session_reference": None, "invoice_reference": None,
            "session_closed": 0,
            "xml": xml, "xml_sha256": summary["xml_sha256"],
            "payload_json": json.dumps(payload, ensure_ascii=False) if payload else None,
            "approved_sha256": None, "upo_xml": None, "source": source,
            "created_at": now(), "updated_at": now(),
        }
        events = [("document_created", {"source": source, "direction": direction})]
        if metadata_mismatch:
            events.append(("metadata_amount_mismatch", metadata_mismatch))
        self.db.save_document(document, audit_event=events)
        return _public_document(document)

    def revise_from_xml(self, document_id: str, xml: bytes) -> dict[str, Any]:
        document = self.db.get_document(document_id)
        if document["status"] not in {"draft", "approved"}:
            raise ValueError("Można zmienić tylko szkic przed wysyłką.")
        validate_xml(xml)
        summary = invoice_summary(xml)
        if summary["seller_nip"] != document["profile_nip"]:
            raise ValueError("NIP sprzedawcy w XML nie odpowiada profilowi ASEF.")
        changed = self.db.update_document(
            document_id, expected={"status": document["status"], "xml_sha256": document["xml_sha256"]},
            audit_event=("document_revised", {"sha256": summary["xml_sha256"]}),
            **summary, xml=xml, status="draft", approved_sha256=None, payload_json=None,
        )
        if not changed:
            raise ValueError("Dokument zmienił się w trakcie edycji; odczytaj go ponownie.")
        return _public_document(self.db.get_document(document_id))

    def preview(self, document_id: str, output: Path, format: str, theme: str = "light") -> dict[str, str]:
        document = self.db.get_document(document_id)
        if hashlib.sha256(document["xml"]).hexdigest() != document["xml_sha256"]:
            raise ValueError("XML dokumentu nie odpowiada zapisanemu SHA-256.")
        if format not in {"html", "pdf"}:
            raise ValueError("Format podglądu: html albo pdf.")
        if theme not in {"light", "crt"}:
            raise ValueError("Motyw PDF: light albo crt.")
        if format == "html" and theme != "light":
            raise ValueError("Motyw crt jest dostępny tylko dla PDF; użyj --format pdf --theme crt.")
        content = (
            render_html(document["xml"], document["status"], document["ksef_number"], document["environment"]).encode("utf-8")
            if format == "html" else render_pdf(document["xml"], document["status"], document["ksef_number"], theme, document["environment"])
        )
        output = write_private_file(output, content)
        self.db.audit("preview_created", document_id, {"format": format, "theme": theme, "path": str(output)})
        return {"path": str(output), "format": format, "theme": theme, "xml_sha256": document["xml_sha256"]}

    def approve(self, document_id: str, expected_sha256: str) -> dict[str, Any]:
        document = self.db.get_document(document_id)
        if document["status"] not in {"draft", "approved"}:
            raise ValueError("Zatwierdzić można tylko szkic.")
        if document["xml_sha256"] != expected_sha256 or hashlib.sha256(document["xml"]).hexdigest() != expected_sha256:
            raise ValueError("XML zmienił się od czasu podglądu; przygotuj nowy podgląd.")
        changed = self.db.update_document(
            document_id, expected={"status": document["status"], "xml_sha256": expected_sha256},
            audit_event=("document_approved", {"sha256": expected_sha256}),
            status="approved", approved_sha256=expected_sha256,
        )
        if not changed:
            raise ValueError("Dokument zmienił się w trakcie zatwierdzania; odczytaj go ponownie.")
        return _public_document(self.db.get_document(document_id))

    def send(self, document_id: str, prod_confirmation: str | None = None) -> dict[str, Any]:
        document = self.db.get_document(document_id)
        if document["direction"] != "issued" or document["status"] != "approved":
            raise ValueError("Wysłać można tylko zatwierdzoną fakturę wystawioną.")
        xml_sha256 = hashlib.sha256(document["xml"]).hexdigest()
        if document["approved_sha256"] != xml_sha256 or document["xml_sha256"] != xml_sha256:
            raise ValueError("Zatwierdzenie nie odpowiada aktualnemu XML.")
        if document["environment"] == "prod" and prod_confirmation != xml_sha256:
            raise ValueError("Wysyłka PROD wymaga osobnego potwierdzenia SHA-256 zatwierdzonego XML.")
        validate_xml(document["xml"])
        claimed = self.db.update_document(
            document_id,
            expected={"status": "approved", "xml_sha256": xml_sha256, "approved_sha256": xml_sha256},
            audit_event=("send_preparing", {"sha256": xml_sha256}), status="preparing",
        )
        if not claimed:
            raise ValueError("Dokument zmienił się lub jest już wysyłany; sprawdź jego status.")

        def on_session(reference: str) -> None:
            # The session reference is durable before the first ambiguous invoice POST.
            recorded = self.db.update_document(
                document_id, expected={"status": "preparing", "xml_sha256": xml_sha256},
                audit_event=("send_started", {"sha256": xml_sha256, "session_reference": reference}),
                session_reference=reference, status="sending",
            )
            if not recorded:
                raise RuntimeError("Nie udało się zapisać sesji KSeF przed wysyłką faktury.")

        def on_invoice(reference: str) -> None:
            self._record_invoice_reference(document_id, reference)

        try:
            with (self.client_factory or KsefClient)(document["environment"], document["profile_nip"]) as client:
                result = client.send_invoice(document["xml"], on_session=on_session, on_invoice=on_invoice)
        except Exception as exc:
            current = self.db.get_document(document_id)
            if current["status"] == "preparing" and not current["session_reference"]:
                self.db.update_document(
                    document_id, expected={"status": "preparing", "session_reference": None},
                    audit_event=("send_preflight_failed", {"sha256": xml_sha256}), status="approved",
                )
            elif isinstance(exc, KsefError) and exc.status_code == 400 and current["status"] == "sending":
                self.db.update_document(
                    document_id, expected={"status": "sending", "session_reference": current["session_reference"]},
                    audit_event=("send_rejected", {"session_reference": current["session_reference"], "status_code": 400}),
                    status="approved", session_reference=None,
                )
            raise
        if result["session_closed"] == "true":
            self.db.update_document(document_id, audit_event=("document_submitted", result), session_closed=1)
        else:
            self.db.audit("document_submitted", document_id, result)
        return _public_document(self.db.get_document(document_id))

    def _record_invoice_reference(self, document_id: str, reference: str,
                                  audit_event: tuple[str, dict[str, Any]] | None = None) -> None:
        for _ in range(3):
            current = self.db.get_document(document_id)
            if current["invoice_reference"]:
                if current["invoice_reference"] == reference:
                    return
                raise RuntimeError("Dokument ma już inny numer referencyjny wysyłki.")
            if current["status"] not in {"sending", "submitted", "accepted"}:
                raise RuntimeError("Stan dokumentu zmienił się podczas zapisu wyniku wysyłki.")
            changes = {"invoice_reference": reference}
            if current["status"] == "sending":
                changes["status"] = "submitted"
            if self.db.update_document(
                document_id, expected={"status": current["status"], "invoice_reference": None},
                audit_event=audit_event, **changes,
            ):
                return
        raise RuntimeError("Stan dokumentu zmienił się podczas zapisu wyniku wysyłki.")

    def refresh_status(self, document_id: str) -> dict[str, Any]:
        document = self.db.get_document(document_id)
        if document["status"] == "preparing" and not document["session_reference"]:
            # on_session commits before the invoice POST; no reference means no invoice POST.
            recovered = self.db.update_document(
                document_id, expected={"status": "preparing", "session_reference": None},
                audit_event=("send_preflight_recovered", {"sha256": document["xml_sha256"]}),
                status="approved",
            )
            if not recovered:
                raise ValueError("Stan dokumentu zmienił się; ponów sprawdzenie.")
            return {"document": _public_document(self.db.get_document(document_id)),
                    "ksef_status": {}, "session_close_error": None, "preflight_recovered": True}
        if not document["session_reference"]:
            raise ValueError("Brak numeru sesji. Sprawdź w KSeF po numerze dokumentu i skrócie XML przed ponowną wysyłką.")
        with (self.client_factory or KsefClient)(document["environment"], document["profile_nip"]) as client:
            close_error = None
            if not document["invoice_reference"]:
                expected_hash = base64.b64encode(hashlib.sha256(document["xml"]).digest()).decode("ascii")
                matching = [item for item in client.session_invoices(document["session_reference"])
                            if item.get("invoiceHash") == expected_hash]
                if len(matching) == 1:
                    self._record_invoice_reference(
                        document_id, matching[0]["referenceNumber"],
                        audit_event=("invoice_reference_recovered", {"session_reference": document["session_reference"]}),
                    )
                    document = self.db.get_document(document_id)
                elif not matching:
                    raise ValueError("Faktury nie ma jeszcze na liście sesji. Nie wysyłaj ponownie przed wyjaśnieniem statusu.")
                else:
                    raise ValueError("W sesji znaleziono więcej niż jedną fakturę o tym samym skrócie XML; wymagana ręczna weryfikacja.")
            if not document["session_closed"]:
                try:
                    client.close_session(document["session_reference"])
                    self.db.update_document(
                        document_id, session_closed=1,
                        audit_event=("session_closed", {"session_reference": document["session_reference"]}),
                    )
                except KsefError as exc:
                    close_error = str(exc)
            result = client.invoice_status(document["session_reference"], document["invoice_reference"])
            code = result.get("status", {}).get("code")
            changes: dict[str, Any] = {}
            if code == 200:
                changes["status"] = "accepted"
                if result.get("ksefNumber"):
                    changes["ksef_number"] = result["ksefNumber"]
                try:
                    changes["upo_xml"] = client.invoice_upo(document["session_reference"], document["invoice_reference"])
                    changes["session_closed"] = 1
                except KsefError:
                    pass
            elif isinstance(code, int) and code >= 400:
                changes["status"] = "rejected"
            if changes:
                self.db.update_document(
                    document_id, audit_event=("status_checked", {"code": code, "description": result.get("status", {}).get("description")}),
                    **changes,
                )
            else:
                self.db.audit("status_checked", document_id, {"code": code, "description": result.get("status", {}).get("description")})
            return {"document": _public_document(self.db.get_document(document_id)),
                    "ksef_status": result.get("status", {}), "session_close_error": close_error}

    def export_xml(self, document_id: str, output: Path) -> str:
        document = self.db.get_document(document_id)
        output = write_private_file(output, document["xml"])
        return str(output)

    def export_upo(self, document_id: str, output: Path) -> str:
        document = self.db.get_document(document_id)
        if not document["upo_xml"]:
            raise ValueError("UPO nie jest jeszcze dostępne; sprawdź status wysyłki.")
        output = write_private_file(output, document["upo_xml"])
        return str(output)

    def sync(self, profile_nip: str, start_from: str | None = None,
             environment: str | None = None) -> dict[str, Any]:
        profile = self.db.profile(profile_nip, environment)
        counts = {"imported": 0, "existing": 0, "related_skipped": 0, "metadata_mismatches": 0,
                  "unsupported_skipped": 0, "subjects": {}}
        with (self.client_factory or KsefClient)(profile["environment"], profile_nip) as client:
            for subject in ("Subject1", "Subject2", "Subject3", "SubjectAuthorized"):
                cursor = self.db.cursor(profile_nip, profile["environment"], subject) or start_from
                if not cursor:
                    raise ValueError("Pierwsza synchronizacja wymaga --from w formacie ISO 8601 UTC.")
                current = _utc(cursor)
                end = datetime.now(timezone.utc)
                if current > end:
                    raise ValueError("Początek synchronizacji nie może być w przyszłości.")
                subject_count = 0
                while current < end:
                    # KSeF limits a metadata date range to 100 UTC days.
                    window_end = min(current + timedelta(days=99), end)
                    while True:
                        page = 0
                        high_water_mark: datetime | None = None
                        truncated = False
                        while True:
                            result = client.query_metadata(subject, current.isoformat(), page, window_end.isoformat())
                            if result.get("isTruncated"):
                                truncated = True
                                break
                            if result.get("permanentStorageHwmDate"):
                                high_water_mark = _utc(result["permanentStorageHwmDate"])
                            for item in result.get("invoices", []):
                                ksef_number = item["ksefNumber"]
                                if self.db.document_by_ksef(profile_nip, profile["environment"], ksef_number):
                                    counts["existing"] += 1
                                    continue
                                if (item.get("formCode") or {}).get("systemCode", "FA (3)") != "FA (3)":
                                    counts["unsupported_skipped"] += 1
                                    continue
                                xml = client.get_invoice(ksef_number)
                                summary = invoice_summary(xml)
                                if summary["seller_nip"] == profile_nip:
                                    direction = "issued"
                                elif summary["buyer_nip"] == profile_nip:
                                    direction = "received"
                                else:
                                    # Third-party/authorized access does not make this our purchase.
                                    counts["related_skipped"] += 1
                                    continue
                                mismatch = {}
                                for field, metadata_field in (("net_amount", "netAmount"),
                                                              ("vat_amount", "vatAmount"),
                                                              ("gross_amount", "grossAmount")):
                                    remote = item.get(metadata_field)
                                    if remote is None:
                                        continue
                                    try:
                                        differs = Decimal(str(remote)) != Decimal(str(summary[field]))
                                    except InvalidOperation:
                                        differs = True
                                    if differs:
                                        mismatch[field] = {"xml": summary[field], "metadata": str(remote)}
                                if mismatch:
                                    counts["metadata_mismatches"] += 1
                                if direction == "issued":
                                    local = self.db.sent_document_by_hash(profile_nip, profile["environment"], summary["xml_sha256"])
                                    if local:
                                        events = [("invoice_synced", {"ksef_number": ksef_number})]
                                        if mismatch:
                                            events.append(("metadata_amount_mismatch", mismatch))
                                        self.db.update_document(
                                            local["id"], ksef_number=ksef_number, status="accepted",
                                            audit_event=events,
                                        )
                                        subject_count += 1
                                        counts["imported"] += 1
                                        continue
                                self._new_document(
                                    profile, xml, direction, "accepted" if direction == "issued" else "received",
                                    "ksef", None, ksef_number, metadata_mismatch=mismatch or None,
                                )
                                subject_count += 1
                                counts["imported"] += 1
                            if not result.get("hasMore"):
                                break
                            page += 1
                        if not truncated:
                            break
                        if window_end - current <= timedelta(seconds=1):
                            # ponytail: metadata cannot split this window further; use package export for this volume.
                            raise KsefError("Ponad limit metadanych KSeF w jednej sekundzie; użyj eksportu paczki faktur.")
                        window_end = current + (window_end - current) / 2
                    if high_water_mark is None:
                        raise KsefError("KSeF nie podał punktu synchronizacji HWM; lokalny kursor nie został przesunięty.")
                    next_cursor = min(window_end, high_water_mark)
                    if next_cursor <= current:
                        break
                    current = next_cursor
                    self.db.set_cursor(profile_nip, profile["environment"], subject, current.isoformat())
                    if current < window_end:
                        break
                counts["subjects"][subject] = {"imported": subject_count, "cursor": current.isoformat()}
        self.db.audit("sync_completed", details={"profile_nip": profile_nip, **counts})
        return counts
