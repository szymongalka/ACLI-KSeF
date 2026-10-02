import pytest
from lxml import etree

from asef.invoice import NS, build_invoice


def invoice_with_price(price: str, quantity: str = "3") -> dict:
    return {
        "number": "FV/1", "issue_date": "2026-09-27",
        "seller": {"nip": "1111111111", "name": "Sprzedawca", "address1": "Warszawa"},
        "buyer": {"nip": "2222222222", "name": "Nabywca", "address1": "Kraków"},
        "lines": [{"description": "Usługa", "quantity": quantity,
                   "unit_price_net": price, "vat_rate": 23}],
    }


@pytest.mark.parametrize("price", ["0.3333", "0.33333333"])
def test_unit_price_keeps_precision_until_line_total(price: str) -> None:
    root = etree.fromstring(build_invoice(invoice_with_price(price)))
    assert root.findtext(f".//{{{NS}}}P_9A") == price
    assert root.findtext(f".//{{{NS}}}P_11") == "1.00"
    assert root.findtext(f".//{{{NS}}}P_13_1") == "1.00"
    assert root.findtext(f".//{{{NS}}}P_15") == "1.23"


def test_unit_price_rejects_more_than_eight_decimal_places() -> None:
    with pytest.raises(ValueError, match="maksymalnie 8 miejsc"):
        build_invoice(invoice_with_price("0.333333333"))


@pytest.mark.parametrize("price,quantity", [("NaN", "3"), ("Infinity", "3"), ("0.33", "NaN")])
def test_nonfinite_price_or_quantity_is_rejected(price: str, quantity: str) -> None:
    with pytest.raises(ValueError, match="Pozycja wymaga"):
        build_invoice(invoice_with_price(price, quantity))
