from __future__ import annotations

from importlib.resources import files
import base64
import hashlib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from jinja2 import Environment, select_autoescape
from lxml import etree
from reportlab.graphics.barcode.qr import QrCodeWidget

from .invoice import NS, PAYMENT_METHOD_LABELS, invoice_summary
from .pdf_layout import render_mobile_pdf


def _text(node: etree._Element | None, name: str) -> str:
    if node is None:
        return ""
    found = node.find(f".//{{{NS}}}{name}")
    return found.text or "" if found is not None else ""


def _direct(node: etree._Element | None, name: str) -> str:
    return node.findtext(f"{{{NS}}}{name}", default="") or "" if node is not None else ""


def _amount(value: str) -> Decimal | None:
    try:
        return Decimal(value) if value else None
    except InvalidOperation:
        return None


def _line_amounts(row: etree._Element) -> tuple[str, str, str]:
    net = _direct(row, "P_11")
    gross = _direct(row, "P_11A")
    vat = _direct(row, "P_11Vat")
    if not vat and net and gross:
        vat = f"{Decimal(gross) - Decimal(net):.2f}"
    if not vat and net and _direct(row, "P_12").isdigit():
        vat = f"{(Decimal(net) * Decimal(_direct(row, 'P_12')) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"
    if not gross and net and vat:
        gross = f"{Decimal(net) + Decimal(vat):.2f}"
    return net, vat, gross


def _verification(xml: bytes, nip: str, issue_date: str, environment: str | None,
                  status: str, ksef_number: str | None) -> dict[str, str] | None:
    if status not in {"accepted", "received"} or not ksef_number or environment not in {"test", "demo", "prod"}:
        return None
    if not nip or not issue_date:
        return None
    hosts = {"test": "qr-test.ksef.mf.gov.pl", "demo": "qr-demo.ksef.mf.gov.pl", "prod": "qr.ksef.mf.gov.pl"}
    digest = base64.urlsafe_b64encode(hashlib.sha256(xml).digest()).rstrip(b"=").decode("ascii")
    date = "-".join(reversed(issue_date.split("-")))
    url = f"https://{hosts[environment]}/invoice/{nip}/{date}/{digest}"
    qr = QrCodeWidget(url)
    qr.qr.make()
    modules = qr.qr.modules
    size = len(modules) + 8
    squares = "".join(f'<rect x="{x + 4}" y="{y + 4}" width="1" height="1"/>'
                      for y, cells in enumerate(modules) for x, active in enumerate(cells) if active)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" role="img" aria-label="Kod QR weryfikacji KSeF"><rect width="{size}" height="{size}" fill="white"/><g fill="black">{squares}</g></svg>'
    return {"url": url, "svg": svg}


def _gross_unit_price(row: etree._Element) -> str:
    """Prefer the FA(3) gross unit price; derive a display value when possible."""
    explicit = _text(row, "P_9B")
    if explicit:
        return explicit
    net, rate = _text(row, "P_9A"), _text(row, "P_12")
    if not net or not rate:
        return ""
    try:
        gross = Decimal(net) * (1 + Decimal(rate) / 100)
    except InvalidOperation:
        return ""
    return f"{gross.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _tax_breakdown(fa: etree._Element | None, rows: list[dict[str, str]]) -> dict[str, Any]:
    """Read FA(3) aggregate tax fields without deriving tax from item names."""
    rates = {row["vat_rate"] for row in rows}
    basic_rate = "23%" if rates & {"23"} and "22" not in rates else "22%" if "22" in rates and "23" not in rates else "Stawka podstawowa"
    reduced_rate = "8%" if rates & {"8"} and "7" not in rates else "7%" if "7" in rates and "8" not in rates else "Stawka obniżona I"
    fields = (
        (basic_rate, "P_13_1", "P_14_1"),
        (reduced_rate, "P_13_2", "P_14_2"),
        ("5%", "P_13_3", "P_14_3"),
        ("Ryczałt TAXI", "P_13_4", "P_14_4"),
        ("Procedura szczególna", "P_13_5", "P_14_5"),
        ("0%", "P_13_6_1", None),
        ("0% - WDT", "P_13_6_2", None),
        ("0% - eksport", "P_13_6_3", None),
        ("Zwolniona", "P_13_7", None),
        ("Poza terytorium kraju", "P_13_8", None),
        ("Usługi UE", "P_13_9", None),
        ("Odwrotne obciążenie", "P_13_10", None),
        ("Procedura marży", "P_13_11", None),
    )
    breakdown = []
    total_net = total_vat = Decimal(0)
    for label, net_field, vat_field in fields:
        net_text = _text(fa, net_field)
        if not net_text:
            continue
        net = Decimal(net_text)
        vat = Decimal(_text(fa, vat_field) or "0") if vat_field else Decimal(0)
        total_net += net
        total_vat += vat
        breakdown.append({
            "label": label, "field": net_field, "net": f"{net:.2f}",
            "vat": f"{vat:.2f}", "gross": f"{net + vat:.2f}",
        })
    return {
        "rows": breakdown,
        "net": f"{total_net:.2f}",
        "vat": f"{total_vat:.2f}",
        "gross": f"{total_net + total_vat:.2f}",
    }


def preview_data(xml: bytes, status: str = "draft", ksef_number: str | None = None,
                 environment: str | None = None) -> dict[str, Any]:
    root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    summary = invoice_summary(xml)
    fa = root.find(f"{{{NS}}}Fa")
    if fa is not None:
        period = fa.find(f"{{{NS}}}OkresFa")
        summary["sale_date"] = _text(fa, "P_6") or _text(period, "P_6_Do")
        summary["issue_place"] = _direct(fa, "P_1M")
    else:
        summary["sale_date"] = ""
        summary["issue_place"] = ""
    seller_node = root.find(f"{{{NS}}}Podmiot1")
    buyer_node = root.find(f"{{{NS}}}Podmiot2")
    payment_node = fa.find(f"{{{NS}}}Platnosc") if fa is not None else None
    method_code = _direct(payment_node, "FormaPlatnosci")
    payment_method = PAYMENT_METHOD_LABELS.get(method_code or "", "")
    if not payment_method:
        payment_method = _direct(payment_node, "OpisPlatnosci")
    payment_terms = payment_node.findall(f"{{{NS}}}TerminPlatnosci/{{{NS}}}Termin") if payment_node is not None else []
    payment_due_date = ", ".join(term.text for term in payment_terms if term.text)
    accounts = [{"number": _direct(account, "NrRB"), "swift": _direct(account, "SWIFT"),
                 "bank": _direct(account, "NazwaBanku"), "description": _direct(account, "OpisRachunku"),
                 "own_bank_account": {"1": "Rozliczenie nabywanych wierzytelności", "2": "Pobranie należności od nabywcy", "3": "Gospodarka własna banku"}.get(_direct(account, "RachunekWlasnyBanku"), "")}
                for account in payment_node.findall(f"{{{NS}}}RachunekBankowy")] if payment_node is not None else []
    partial = _direct(payment_node, "ZnacznikZaplatyCzesciowej")
    payment_status = "Zapłacono" if _direct(payment_node, "Zaplacono") == "1" or partial == "2" else "Zapłacono częściowo" if partial == "1" else ""
    partial_payments = [{"amount": _direct(node, "KwotaZaplatyCzesciowej"), "date": _direct(node, "DataZaplatyCzesciowej"),
                         "method": PAYMENT_METHOD_LABELS.get(_direct(node, "FormaPlatnosci"), "") or _direct(node, "OpisPlatnosci")}
                        for node in payment_node.findall(f"{{{NS}}}ZaplataCzesciowa")] if payment_node is not None else []
    descriptions = [{"key": _direct(node, "Klucz"), "value": _direct(node, "Wartosc"),
                     "row": _direct(node, "NrWiersza")}
                    for node in fa.findall(f"{{{NS}}}DodatkowyOpis")] if fa is not None else []
    contracts = [{"number": _direct(node, "NrUmowy"), "date": _direct(node, "DataUmowy")}
                 for node in fa.findall(f"{{{NS}}}WarunkiTransakcji/{{{NS}}}Umowy")] if fa is not None else []

    def party(node: etree._Element | None) -> dict[str, str]:
        return {k: _text(node, v) for k, v in {
            "name": "Nazwa", "nip": "NIP", "address1": "AdresL1", "address2": "AdresL2", "country": "KodKraju"
        }.items()}

    rows = []
    if fa is not None:
        for row in fa.findall(f"{{{NS}}}FaWiersz"):
            net, vat, gross = _line_amounts(row)
            rows.append({
                "number": _direct(row, "NrWierszaFa"), "before": _direct(row, "StanPrzed") == "1",
                "description": _text(row, "P_7"), "quantity": _text(row, "P_8B"),
                "unit": _text(row, "P_8A"), "unit_price": _text(row, "P_9A"),
                "gross_unit_price": _gross_unit_price(row), "vat_rate": _text(row, "P_12"),
                "net_amount": net, "vat_amount": vat, "gross_amount": gross,
                "sale_date": _direct(row, "P_6A"),
            })
    tax = _tax_breakdown(fa, rows)
    due_difference = Decimal(summary["gross_amount"] or "0") - Decimal(tax["gross"])
    summary["amount_label"] = "Korekta kwoty należności" if summary["kind"] == "correction" else "Kwota należności ogółem"
    settlement_node = fa.find(f"{{{NS}}}Rozliczenie") if fa is not None else None
    corrections = [{"number": _direct(node, "NrFaKorygowanej"),
                    "issue_date": _direct(node, "DataWystFaKorygowanej"),
                    "ksef_number": _direct(node, "NrKSeFFaKorygowanej")}
                   for node in fa.findall(f"{{{NS}}}DaneFaKorygowanej")] if fa is not None else []
    return {
        "invoice": summary, "seller": party(seller_node), "buyer": party(buyer_node),
        "rows": rows, "totals": {"net": summary["net_amount"], "vat": summary["vat_amount"]},
        "descriptions": descriptions, "contracts": contracts,
        "tax": tax, "due_difference": f"{due_difference:.2f}" if due_difference else None,
        "settlement": {"due": _direct(settlement_node, "DoZaplaty"),
                       "credit": _direct(settlement_node, "DoRozliczenia")},
        "payment": {"due_date": payment_due_date, "method": payment_method,
                    "status": payment_status, "paid_date": _direct(payment_node, "DataZaplaty"),
                    "accounts": accounts, "partial": partial_payments},
        "buyer_flags": {"jst": {"1": "Tak", "2": "Nie"}.get(_direct(buyer_node, "JST"), ""),
                        "gv": {"1": "Tak", "2": "Nie"}.get(_direct(buyer_node, "GV"), "")},
        "correction": {"reason": _direct(fa, "PrzyczynaKorekty"),
                       "type": {"1": "W dacie ujęcia faktury pierwotnej", "2": "W dacie wystawienia korekty", "3": "W innej dacie"}.get(_direct(fa, "TypKorekty"), ""),
                       "invoices": corrections, "period": _direct(fa, "OkresFaKorygowanej")},
        "status": status, "ksef_number": ksef_number,
        "verification": _verification(xml, summary["seller_nip"] or "", summary["issue_date"] or "", environment, status, ksef_number),
    }


def render_html(xml: bytes, status: str = "draft", ksef_number: str | None = None,
                environment: str | None = None) -> str:
    env = Environment(autoescape=select_autoescape(default=True))
    template = files("asef").joinpath("templates/invoice.html.j2").read_text(encoding="utf-8")
    rendered = env.from_string(template).render(**preview_data(xml, status, ksef_number, environment))
    return "\n".join(line.rstrip() for line in rendered.split("\n"))


def render_pdf(
    xml: bytes, status: str = "draft", ksef_number: str | None = None,
    theme: str = "light", environment: str | None = None,
) -> bytes:
    return render_mobile_pdf(preview_data(xml, status, ksef_number, environment), theme)
