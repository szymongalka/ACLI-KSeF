from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from importlib.resources import files
from typing import Any
from xml.sax.saxutils import escape

from lxml import etree


NS = "http://crd.gov.pl/wzor/2025/06/25/13775/"
NSMAP = {None: NS}
CENT = Decimal("0.01")
RATE_FIELDS = {"23": ("P_13_1", "P_14_1"), "8": ("P_13_2", "P_14_2"), "5": ("P_13_3", "P_14_3")}
PAYMENT_METHOD_CODES = {
    "gotówka": "1", "karta": "2", "bon": "3", "czek": "4",
    "kredyt": "5", "przelew": "6", "mobilna": "7",
}
PAYMENT_METHOD_LABELS = {code: label.capitalize() for label, code in PAYMENT_METHOD_CODES.items()}


def q(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def tag(name: str) -> str:
    return f"{{{NS}}}{name}"


def sub(parent: etree._Element, name: str, value: Any) -> etree._Element:
    element = etree.SubElement(parent, tag(name))
    element.text = str(value)
    return element


def _party(root: etree._Element, element_name: str, party: dict[str, Any]) -> None:
    node = etree.SubElement(root, tag(element_name))
    identity = etree.SubElement(node, tag("DaneIdentyfikacyjne"))
    sub(identity, "NIP", party["nip"])
    sub(identity, "Nazwa", party["name"])
    address = etree.SubElement(node, tag("Adres"))
    sub(address, "KodKraju", party.get("country", "PL"))
    sub(address, "AdresL1", party["address1"])
    if party.get("address2"):
        sub(address, "AdresL2", party["address2"])
    if element_name == "Podmiot2":
        sub(node, "JST", "2")
        sub(node, "GV", "2")


def build_invoice(payload: dict[str, Any]) -> bytes:
    """Build a basic domestic FA(3) VAT invoice from a human-readable payload.

    Supported here: PLN, ordinary invoice, rates 23/8/5. Other FA(3) cases can be
    brought in as already prepared XML and still use the same preview/approval path.
    """
    if payload.get("kind", "invoice") != "invoice":
        raise ValueError("Generator JSON obsługuje obecnie fakturę VAT. Korektę zaimportuj jako FA(3) XML.")
    if payload.get("currency", "PLN") != "PLN":
        raise ValueError("Generator JSON obsługuje obecnie PLN. Inne waluty zaimportuj jako FA(3) XML.")
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("Faktura wymaga co najmniej jednej pozycji 'lines'.")
    for role in ("seller", "buyer"):
        party = payload.get(role, {})
        if not all(party.get(k) for k in ("nip", "name", "address1")):
            raise ValueError(f"{role}: wymagane nip, name i address1.")
    if not payload.get("number") or not payload.get("issue_date"):
        raise ValueError("Wymagane number i issue_date.")
    payment_method = str(payload.get("payment_method") or "").strip().casefold()
    if payment_method and payment_method not in PAYMENT_METHOD_CODES:
        raise ValueError("Forma płatności: gotówka, karta, bon, czek, kredyt, przelew lub mobilna.")

    totals: dict[str, tuple[Decimal, Decimal]] = {}
    checked_lines: list[tuple[dict[str, Any], Decimal, Decimal, Decimal, str]] = []
    for line in lines:
        rate = str(line.get("vat_rate", ""))
        if rate not in RATE_FIELDS:
            raise ValueError("Generator JSON obsługuje stawki VAT 23, 8 i 5. Nie zgaduj stawki.")
        try:
            qty = Decimal(str(line["quantity"]))
            unit_price = Decimal(str(line["unit_price_net"]))
        except InvalidOperation as exc:
            raise ValueError("Pozycja wymaga prawidłowej ilości i ceny netto.") from exc
        if not qty.is_finite() or not unit_price.is_finite() or qty <= 0 or unit_price < 0 or not line.get("description"):
            raise ValueError("Pozycja wymaga nazwy, dodatniej ilości i nieujemnej ceny netto.")
        if unit_price.as_tuple().exponent < -8:
            raise ValueError("Cena netto pozycji może mieć maksymalnie 8 miejsc po przecinku.")
        net = q(qty * unit_price)
        vat = q(net * Decimal(rate) / Decimal(100))
        old_net, old_vat = totals.get(rate, (Decimal(0), Decimal(0)))
        totals[rate] = old_net + net, old_vat + vat
        checked_lines.append((line, qty, unit_price, net, rate))

    root = etree.Element(tag("Faktura"), nsmap=NSMAP)
    header = etree.SubElement(root, tag("Naglowek"))
    form = sub(header, "KodFormularza", "FA")
    form.set("kodSystemowy", "FA (3)")
    form.set("wersjaSchemy", "1-0E")
    sub(header, "WariantFormularza", "3")
    sub(header, "DataWytworzeniaFa", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    sub(header, "SystemInfo", "ASEF 0.1")
    _party(root, "Podmiot1", payload["seller"])
    _party(root, "Podmiot2", payload["buyer"])
    fa = etree.SubElement(root, tag("Fa"))
    sub(fa, "KodWaluty", "PLN")
    sub(fa, "P_1", payload["issue_date"])
    if payload.get("issue_place"):
        sub(fa, "P_1M", payload["issue_place"])
    sub(fa, "P_2", payload["number"])
    if payload.get("sale_date"):
        sub(fa, "P_6", payload["sale_date"])
    for rate in ("23", "8", "5"):
        if rate in totals:
            net, vat = totals[rate]
            net_field, vat_field = RATE_FIELDS[rate]
            sub(fa, net_field, f"{net:.2f}")
            sub(fa, vat_field, f"{vat:.2f}")
    gross = sum((a + b for a, b in totals.values()), Decimal(0))
    sub(fa, "P_15", f"{gross:.2f}")
    annotations = etree.SubElement(fa, tag("Adnotacje"))
    for key in ("P_16", "P_17", "P_18", "P_18A"):
        sub(annotations, key, "2")
    exemption = etree.SubElement(annotations, tag("Zwolnienie"))
    sub(exemption, "P_19N", "1")
    vehicles = etree.SubElement(annotations, tag("NoweSrodkiTransportu"))
    sub(vehicles, "P_22N", "1")
    sub(annotations, "P_23", "2")
    margin = etree.SubElement(annotations, tag("PMarzy"))
    sub(margin, "P_PMarzyN", "1")
    sub(fa, "RodzajFaktury", "VAT")
    for index, (line, qty, unit_price, net, rate) in enumerate(checked_lines, 1):
        row = etree.SubElement(fa, tag("FaWiersz"))
        sub(row, "NrWierszaFa", index)
        sub(row, "P_7", line["description"])
        sub(row, "P_8A", line.get("unit", "szt."))
        sub(row, "P_8B", str(qty))
        sub(row, "P_9A", f"{unit_price:f}")
        sub(row, "P_11", f"{net:.2f}")
        sub(row, "P_12", rate)
    if payload.get("payment_due_date") or payment_method or payload.get("bank_account"):
        payment = etree.SubElement(fa, tag("Platnosc"))
        if payload.get("payment_due_date"):
            term = etree.SubElement(payment, tag("TerminPlatnosci"))
            sub(term, "Termin", payload["payment_due_date"])
        if payment_method:
            sub(payment, "FormaPlatnosci", PAYMENT_METHOD_CODES[payment_method])
        if payload.get("bank_account"):
            account_data = payload["bank_account"]
            if not isinstance(account_data, dict) or not account_data.get("number"):
                raise ValueError("Rachunek bankowy wymaga numeru.")
            account = etree.SubElement(payment, tag("RachunekBankowy"))
            sub(account, "NrRB", account_data["number"])
            for key, field in (("swift", "SWIFT"), ("bank", "NazwaBanku"), ("description", "OpisRachunku")):
                if account_data.get(key):
                    sub(account, field, account_data[key])
    xml = etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)
    validate_xml(xml)
    return xml


def validate_xml(xml: bytes) -> None:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(xml, parser)
    schema_path = files("asef").joinpath("xsd/schemat_FA(3)_v1-0E.xsd")
    schema = etree.XMLSchema(etree.parse(str(schema_path)))
    if not schema.validate(root):
        errors = "; ".join(str(e) for e in schema.error_log[:5])
        raise ValueError(f"XML niezgodny z FA(3): {errors}")


def _value(parent: etree._Element, name: str) -> str | None:
    node = parent.find(".//" + tag(name))
    return node.text if node is not None else None


def invoice_summary(xml: bytes) -> dict[str, Any]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(xml, parser)
    fa = root.find(tag("Fa"))
    if fa is None:
        raise ValueError("Brak elementu Fa.")
    seller = root.find(tag("Podmiot1"))
    buyer = root.find(tag("Podmiot2"))
    if seller is None or buyer is None:
        raise ValueError("Brak sprzedawcy lub nabywcy.")
    correction = _value(fa, "RodzajFaktury") in {"KOR", "KOR_ZAL", "KOR_ROZ"}
    net_fields = tuple(f"P_13_{index}" for index in range(1, 12)) + ("P_13_6_1", "P_13_6_2", "P_13_6_3")
    net = sum((Decimal(_value(fa, field) or "0") for field in net_fields), Decimal(0))
    vat = sum((Decimal(_value(fa, field) or "0") for field in
               ("P_14_1", "P_14_2", "P_14_3", "P_14_4", "P_14_5")), Decimal(0))
    return {
        "kind": "correction" if correction else "invoice",
        "number": _value(fa, "P_2") or "",
        "issue_date": _value(fa, "P_1"),
        "seller_nip": _value(seller, "NIP"),
        "seller_name": _value(seller, "Nazwa"),
        "buyer_nip": _value(buyer, "NIP"),
        "buyer_name": _value(buyer, "Nazwa"),
        "currency": _value(fa, "KodWaluty") or "PLN",
        "net_amount": f"{net:.2f}",
        "vat_amount": f"{vat:.2f}",
        "gross_amount": _value(fa, "P_15"),
        "original_ksef_number": _value(fa, "NrKSeFFaKorygowanej") if correction else None,
        "xml_sha256": hashlib.sha256(xml).hexdigest(),
    }


def safe_text(value: Any) -> str:
    return escape(str(value or ""))
