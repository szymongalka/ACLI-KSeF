import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import httpx
from lxml import etree
from typer.testing import CliRunner

from asef.cli import app
from asef.db import Database
from asef.invoice import NS, build_invoice, validate_xml
from asef.ksef import KsefClient, KsefError
from asef.preview import preview_data, render_html, render_pdf
from asef.service import AsefService


SAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "faktura.json").read_text())


def test_invoice_preview_and_revision(tmp_path: Path) -> None:
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        service.profile_add(SAMPLE["seller"]["nip"], SAMPLE["seller"]["name"])
        doc = service.draft_from_payload(SAMPLE["seller"]["nip"], SAMPLE)
        xml = db.get_document(doc["id"])["xml"]
        validate_xml(xml)
        root = etree.fromstring(xml)
        assert root.findtext(f".//{{{NS}}}P_6") == SAMPLE["sale_date"]
        assert root.findtext(f".//{{{NS}}}TerminPlatnosci/{{{NS}}}Termin") == SAMPLE["payment_due_date"]
        assert root.findtext(f".//{{{NS}}}Platnosc/{{{NS}}}FormaPlatnosci") == "6"
        html = render_html(xml)
        assert "<meta name=\"viewport\"" in html
        assert "2946.00 PLN" in html
        assert "Łąka Innowacje" in html
        assert '<table class="items">' in html
        assert '<table class="tax">' in html
        assert "Zestawienie VAT" in html
        assert "Wartość netto" not in html and "Wartość brutto" not in html
        assert "Kwota VAT" in html and "Cena brutto" in html
        assert html.index("Ilość</th>") < html.index("Jm</th>") < html.index("Cena netto</th>") < html.index("VAT</th>") < html.index("Kwota VAT</th>") < html.index("Cena brutto</th>")
        assert "Kwota należności ogółem" in html and "Do zapłaty" not in html
        assert "Data zakończenia dostawy / usługi" in html
        assert "Termin płatności" in html and "Forma płatności" in html
        assert "2026-10-09" in html and "Przelew" in html
        assert "Miejsce wystawienia" in html and "Bank demonstracyjny" in html
        assert "Agencyjny System Elektronicznych Faktur" in html
        assert "Autor podglądu: Szymon Gałka" in html
        assert preview_data(xml)["payment"]["due_date"] == "2026-10-09"
        assert preview_data(xml)["payment"]["method"] == "Przelew"
        assert preview_data(xml)["payment"]["status"] == ""
        assert "Brak zapłaty" not in html
        assert preview_data(xml)["payment"]["accounts"][0]["number"] == SAMPLE["bank_account"]["number"]
        assert [row["net_amount"] for row in preview_data(xml)["rows"]] == ["2000.00", "450.00"]
        assert [row["gross_unit_price"] for row in preview_data(xml)["rows"]] == ["1230.00", "486.00"]
        tax_rows = preview_data(xml)["tax"]["rows"]
        assert [(row["label"], row["net"], row["vat"], row["gross"]) for row in tax_rows] == [
            ("23%", "2000.00", "460.00", "2460.00"),
            ("8%", "450.00", "36.00", "486.00"),
        ]
        light = render_pdf(xml, theme="light")
        dark = render_pdf(xml, theme="crt")
        assert light.startswith(b"%PDF") and dark.startswith(b"%PDF") and light != dark
        with pytest.raises(ValueError, match="tylko dla PDF"):
            service.preview(doc["id"], tmp_path / "bad.html", "html", "crt")
        assert db.connection.execute("SELECT COUNT(*) FROM wystawione_faktury").fetchone()[0] == 1

        service.approve(doc["id"], doc["xml_sha256"])
        revised = dict(SAMPLE)
        revised["number"] = "FV/09/2026/002"
        service.revise_from_xml(doc["id"], build_invoice(revised))
        changed = db.get_document(doc["id"])
        assert changed["status"] == "draft"
        assert changed["approved_sha256"] is None
        assert changed["number"] == revised["number"]
        with pytest.raises(ValueError, match="zmienił się"):
            service.approve(doc["id"], doc["xml_sha256"])


def test_gross_unit_price_uses_explicit_xml_value_and_avoids_unknown_rate() -> None:
    root = etree.fromstring(build_invoice(SAMPLE))
    rows = root.findall(f".//{{{NS}}}FaWiersz")
    net_price = rows[0].find(f"{{{NS}}}P_9A")
    assert net_price is not None
    gross_price = etree.Element(f"{{{NS}}}P_9B")
    gross_price.text = "1229.99"
    net_price.addnext(gross_price)
    rate = rows[1].find(f"{{{NS}}}P_12")
    assert rate is not None
    rate.text = "zw"
    xml = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    validate_xml(xml)
    assert [row["gross_unit_price"] for row in preview_data(xml)["rows"]] == ["1229.99", ""]


def test_payment_fields_are_optional_but_unknown_method_is_rejected() -> None:
    payload = {key: value for key, value in SAMPLE.items()
               if key not in {"sale_date", "payment_due_date", "payment_method"}}
    data = preview_data(build_invoice(payload))
    assert data["invoice"]["sale_date"] == ""
    assert data["payment"]["due_date"] == "" and data["payment"]["method"] == ""
    with pytest.raises(ValueError, match="Forma płatności"):
        build_invoice({**payload, "payment_method": "nieznana"})


def test_tax_breakdown_includes_zero_and_exempt() -> None:
    root = etree.fromstring(build_invoice(SAMPLE))
    fa = root.find(f"{{{NS}}}Fa")
    assert fa is not None
    payable = fa.find(f"{{{NS}}}P_15")
    assert payable is not None
    for field, amount in (("P_13_6_1", "100.00"), ("P_13_7", "50.00")):
        node = etree.Element(f"{{{NS}}}{field}")
        node.text = amount
        payable.addprevious(node)
    payable.text = "3096.00"
    xml = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    validate_xml(xml)
    data = preview_data(xml)
    assert [(row["label"], row["vat"], row["gross"]) for row in data["tax"]["rows"][-2:]] == [
        ("0%", "0.00", "100.00"),
        ("Zwolniona", "0.00", "50.00"),
    ]
    assert data["tax"]["gross"] == "3096.00"
    assert data["due_difference"] is None


def test_settlement_due_is_distinct_from_invoice_total() -> None:
    root = etree.fromstring(build_invoice(SAMPLE))
    fa = root.find(f"{{{NS}}}Fa")
    assert fa is not None
    payment = fa.find(f"{{{NS}}}Platnosc")
    assert payment is not None
    settlement = etree.Element(f"{{{NS}}}Rozliczenie")
    etree.SubElement(settlement, f"{{{NS}}}DoZaplaty").text = "2900.00"
    payment.addprevious(settlement)
    xml = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    validate_xml(xml)
    data = preview_data(xml)
    assert data["invoice"]["gross_amount"] == "2946.00"
    assert data["settlement"]["due"] == "2900.00"
    html = render_html(xml)
    assert "Kwota należności ogółem" in html
    assert "Do zapłaty" in html and "2900.00 PLN" in html


def test_imported_correction_fields_are_conditional_and_verification_is_local() -> None:
    root = etree.fromstring(build_invoice(SAMPLE))
    fa = root.find(f"{{{NS}}}Fa")
    assert fa is not None
    kind = fa.find(f"{{{NS}}}RodzajFaktury")
    assert kind is not None
    kind.text = "KOR"
    reason = etree.Element(f"{{{NS}}}PrzyczynaKorekty")
    reason.text = "Zmiana liczby sztuk"
    kind.addnext(reason)
    effect = etree.Element(f"{{{NS}}}TypKorekty")
    effect.text = "2"
    reason.addnext(effect)
    original = etree.Element(f"{{{NS}}}DaneFaKorygowanej")
    for field, value in (("DataWystFaKorygowanej", "2026-09-01"),
                         ("NrFaKorygowanej", "FV/09/2026/000"), ("NrKSeFN", "1")):
        etree.SubElement(original, f"{{{NS}}}{field}").text = value
    effect.addnext(original)
    row = fa.find(f"{{{NS}}}FaWiersz")
    assert row is not None
    etree.SubElement(row, f"{{{NS}}}StanPrzed").text = "1"
    payment = fa.find(f"{{{NS}}}Platnosc")
    assert payment is not None
    paid = etree.Element(f"{{{NS}}}Zaplacono")
    paid.text = "1"
    payment.insert(0, paid)
    paid_date = etree.Element(f"{{{NS}}}DataZaplaty")
    paid_date.text = "2026-09-25"
    paid.addnext(paid_date)
    xml = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    validate_xml(xml)
    data = preview_data(xml, "accepted", "1111111111-20260925-ABC123", "test")
    assert data["correction"]["reason"] == "Zmiana liczby sztuk"
    assert data["invoice"]["amount_label"] == "Korekta kwoty należności"
    assert data["correction"]["invoices"][0]["number"] == "FV/09/2026/000"
    assert data["rows"][0]["before"] is True
    assert data["payment"]["status"] == "Zapłacono"
    assert data["verification"]["url"].startswith("https://qr-test.ksef.mf.gov.pl/invoice/1111111111/25-09-2026/")
    html = render_html(xml, "accepted", "1111111111-20260925-ABC123", "test")
    assert "Zmiana liczby sztuk" in html and "Stan przed korektą" in html
    assert "Kod QR weryfikacji KSeF" in html
    assert render_pdf(xml, "accepted", "1111111111-20260925-ABC123", "crt", "test").startswith(b"%PDF")
    assert preview_data(xml)["verification"] is None


def test_cli_creates_both_pdf_themes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    profile = runner.invoke(app, ["--json", "profile", "add", SAMPLE["seller"]["nip"], "Firma demonstracyjna"])
    assert profile.exit_code == 0, profile.output
    created = runner.invoke(app, ["--json", "invoice", "create", "--file", str(Path(__file__).parent.parent / "examples" / "faktura.json")])
    assert created.exit_code == 0, created.output
    document_id = json.loads(created.output)["result"]["id"]
    for theme in ("light", "crt"):
        output = tmp_path / f"{theme}.pdf"
        result = runner.invoke(app, ["--json", "invoice", "preview", document_id,
                                     "--format", "pdf", "--theme", theme, "--out", str(output)])
        assert result.exit_code == 0, result.output
        assert output.read_bytes().startswith(b"%PDF")


def test_cli_approves_exact_xml_noninteractively_only_with_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    runner.invoke(app, ["profile", "add", SAMPLE["seller"]["nip"], SAMPLE["seller"]["name"]])
    created = runner.invoke(app, ["--json", "invoice", "create", "--file", str(Path(__file__).parent.parent / "examples" / "faktura.json")])
    doc = json.loads(created.output)["result"]
    base = ["--json", "invoice", "approve", doc["id"], "--sha256"]
    rejected = runner.invoke(app, base + [doc["xml_sha256"]])
    assert rejected.exit_code != 0
    assert json.loads(rejected.output)["ok"] is False
    wrong = runner.invoke(app, base + ["0" * 64, "--yes"])
    assert wrong.exit_code != 0
    assert json.loads(wrong.output)["ok"] is False
    approved = runner.invoke(app, base + [doc["xml_sha256"], "--yes"])
    assert approved.exit_code == 0, approved.output
    result = json.loads(approved.output)["result"]
    assert result["status"] == "approved"
    assert result["approved_sha256"] == doc["xml_sha256"]


def test_cli_missing_file_keeps_json_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    runner.invoke(app, ["profile", "add", SAMPLE["seller"]["nip"], SAMPLE["seller"]["name"]])
    result = runner.invoke(app, ["--json", "invoice", "create", "--file", str(tmp_path / "missing.json")])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_uncertain_send_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeKsef:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self) -> "FakeKsef":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def send_invoice(self, _xml: bytes, *, on_session, on_invoice) -> None:
            on_session("session-123")
            raise RuntimeError("niepewny wynik POST")

    monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        service.profile_add(SAMPLE["seller"]["nip"], SAMPLE["seller"]["name"])
        doc = service.draft_from_payload(SAMPLE["seller"]["nip"], SAMPLE)
        service.approve(doc["id"], doc["xml_sha256"])
        with pytest.raises(RuntimeError, match="niepewny"):
            service.send(doc["id"])
        pending = db.get_document(doc["id"])
        assert pending["status"] == "sending"
        assert pending["session_reference"] == "session-123"
        with pytest.raises(ValueError, match="zatwierdzoną"):
            service.send(doc["id"])


def test_http_400_invoice_rejection_preserves_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeKsef:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self) -> "FakeKsef":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def send_invoice(self, _xml: bytes, *, on_session, on_invoice) -> None:
            on_session("session-rejected")
            raise KsefError("KSeF HTTP 400", 400)

    monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, "Test")
        doc = service.draft_from_payload(nip, SAMPLE)
        service.approve(doc["id"], doc["xml_sha256"])
        with pytest.raises(KsefError) as error:
            service.send(doc["id"])
        assert error.value.status_code == 400
        rejected = db.get_document(doc["id"])
        assert rejected["status"] == "approved"
        assert rejected["approved_sha256"] == doc["xml_sha256"]
        assert rejected["session_reference"] is None
        assert db.connection.execute("SELECT action FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()[0] == "send_rejected"


def test_profiles_are_separate_and_prod_needs_second_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nip = SAMPLE["seller"]["nip"]
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        service.profile_add(nip, "Demo", "demo")
        demo = service.draft_from_payload(nip, SAMPLE, "demo")
        service.profile_add(nip, "Prod", "prod")
        prod = service.draft_from_payload(nip, SAMPLE, "prod")
        assert [d["id"] for d in db.list_documents(nip, environment="demo")] == [demo["id"]]
        assert [d["id"] for d in db.list_documents(nip, environment="prod")] == [prod["id"]]
        with pytest.raises(ValueError, match="--env"):
            service.profile_add(nip, "Omitted environment")
        with pytest.raises(ValueError, match="--env"):
            db.profile(nip)
        service.approve(prod["id"], prod["xml_sha256"])
        with pytest.raises(ValueError, match="osobnego potwierdzenia"):
            service.send(prod["id"])
        assert db.get_document(prod["id"])["status"] == "approved"

        class PreflightFailure:
            def __init__(self, *_: object) -> None:
                raise KsefError("brak autoryzacji")

        monkeypatch.setattr("asef.service.KsefClient", PreflightFailure)
        with pytest.raises(KsefError, match="brak autoryzacji"):
            service.send(prod["id"], prod["xml_sha256"])
        assert db.get_document(prod["id"])["status"] == "approved"
        assert db.get_document(prod["id"])["session_reference"] is None


def test_cli_requires_environment_for_shared_nip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    nip = SAMPLE["seller"]["nip"]
    for environment in ("demo", "prod"):
        result = runner.invoke(app, ["profile", "add", nip, "Firma", "--env", environment])
        assert result.exit_code == 0, result.output
    ambiguous = runner.invoke(app, ["--json", "invoice", "list", "--nip", nip])
    assert ambiguous.exit_code != 0
    assert "--env" in ambiguous.output
    selected = runner.invoke(app, ["--json", "invoice", "list", "--nip", nip, "--env", "demo"])
    assert selected.exit_code == 0, selected.output


def test_failed_close_is_retried_when_refreshing_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeKsef:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self) -> "FakeKsef":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def send_invoice(self, _xml: bytes, *, on_session, on_invoice) -> dict:
            on_session("session-123")
            on_invoice("invoice-123")
            return {"session_reference": "session-123", "invoice_reference": "invoice-123", "session_closed": "false"}

        def close_session(self, _session: str) -> None:
            pass

        def invoice_status(self, _session: str, _invoice: str) -> dict:
            return {"status": {"code": 200, "description": "OK"}, "ksefNumber": "123"}

        def invoice_upo(self, _session: str, _invoice: str) -> bytes:
            return b"<upo/>"

    monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, "Test")
        doc = service.draft_from_payload(nip, SAMPLE)
        service.approve(doc["id"], doc["xml_sha256"])
        sent = service.send(doc["id"])
        assert sent["session_closed"] == 0
        checked = service.refresh_status(doc["id"])
        assert checked["document"]["session_closed"] == 1
        assert db.get_document(doc["id"])["upo_xml"] == b"<upo/>"


def test_read_only_request_respects_429_without_retrying_invoice_post(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1 or request.method == "POST":
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={})

    monkeypatch.setattr("asef.ksef.time.sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.invalid") as http:
        client = KsefClient("test", "1111111111", http=http)
        client._request("GET", "/security/public-key-certificates")
        assert len(calls) == 2
        with pytest.raises(KsefError, match="429"):
            client._request("POST", "/sessions/online/abc/invoices")
        assert len(calls) == 3


def test_invoice_post_preserves_http_400_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions/online":
            return httpx.Response(200, json={"referenceNumber": "session-rejected"})
        return httpx.Response(400, json={"detail": "Sesja tymczasowo niedostępna"})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.invalid") as http:
        client = KsefClient("test", "1111111111", http=http)
        monkeypatch.setattr(client, "_encrypt_rsa", lambda *_: ("encrypted-key", None))
        monkeypatch.setattr(client, "access_token", lambda: "fake-access-token")
        sessions = []
        with pytest.raises(KsefError) as error:
            client.send_invoice(b"<Faktura/>", on_session=sessions.append)
        assert error.value.status_code == 400
        assert sessions == ["session-rejected"]


def test_cli_database_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    with Database() as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, "Test")
        doc = service.draft_from_payload(nip, SAMPLE)
    destination = tmp_path / "backup.sqlite3"
    result = CliRunner().invoke(app, ["db", "backup", str(destination)])
    assert result.exit_code == 0, result.output
    assert destination.exists()
    with Database(destination) as db:
        assert db.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.get_document(doc["id"])["xml_sha256"] == doc["xml_sha256"]


def test_sync_splits_long_range_and_deduplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xml = build_invoice(SAMPLE)
    calls = []

    class FakeKsef:
        def __init__(self, *_: object) -> None:
            self.seen = set()

        def __enter__(self) -> "FakeKsef":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def query_metadata(self, subject: str, date_from: str, page: int, date_to: str) -> dict:
            assert page == 0
            assert datetime.fromisoformat(date_to) - datetime.fromisoformat(date_from) <= timedelta(days=99)
            calls.append((subject, date_from, date_to))
            items = []
            if subject not in self.seen:
                self.seen.add(subject)
                items = [{"ksefNumber": "1111111111-20260925-ABCDEF123456-AB"}]
            return {"invoices": items, "hasMore": False, "isTruncated": False,
                    "permanentStorageHwmDate": date_to}

        def get_invoice(self, _number: str) -> bytes:
            return xml

    monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
    start = (datetime.now(timezone.utc) - timedelta(days=210)).isoformat()
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        service.profile_add(SAMPLE["seller"]["nip"], SAMPLE["seller"]["name"])
        result = service.sync(SAMPLE["seller"]["nip"], start)
        assert result["imported"] == 1
        assert result["existing"] == 3
        assert {subject for subject, *_ in calls} == {"Subject1", "Subject2", "Subject3", "SubjectAuthorized"}
        assert len(calls) >= 12
        assert db.connection.execute("SELECT COUNT(*) FROM wystawione_faktury").fetchone()[0] == 1


def test_sync_splits_truncated_metadata_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakeKsef:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self) -> "FakeKsef":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def query_metadata(self, subject: str, date_from: str, page: int, date_to: str) -> dict:
            duration = datetime.fromisoformat(date_to) - datetime.fromisoformat(date_from)
            calls.append((subject, duration))
            if duration > timedelta(days=10):
                return {"isTruncated": True}
            return {"invoices": [], "hasMore": False, "isTruncated": False,
                    "permanentStorageHwmDate": date_to}

    monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, "Test")
        result = service.sync(nip, (datetime.now(timezone.utc) - timedelta(days=21)).isoformat())
        assert len(result["subjects"]) == 4
        assert any(duration > timedelta(days=10) for _, duration in calls)
        assert all(db.cursor(nip, "test", subject) for subject in result["subjects"])


def test_third_party_invoice_is_not_recorded_as_purchase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = {**SAMPLE, "seller": {**SAMPLE["seller"], "nip": "2222222222"},
             "buyer": {**SAMPLE["buyer"], "nip": "3333333333"}}
    xml = build_invoice(other)

    class FakeKsef:
        def __init__(self, *_: object) -> None:
            self.returned = False

        def __enter__(self) -> "FakeKsef":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def query_metadata(self, subject: str, _from: str, _page: int, date_to: str) -> dict:
            items = []
            if subject == "Subject3" and not self.returned:
                self.returned = True
                items = [{"ksefNumber": "2222222222-20260925-ABCDEF123456-AB"}]
            return {"invoices": items, "hasMore": False, "isTruncated": False,
                    "permanentStorageHwmDate": date_to}

        def get_invoice(self, _number: str) -> bytes:
            return xml

    monkeypatch.setattr("asef.service.KsefClient", FakeKsef)
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, "Test")
        result = service.sync(nip, (datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        assert result["related_skipped"] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM odebrane_faktury").fetchone()[0] == 0
