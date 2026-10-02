from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from lxml import html


VAT_SOURCE = "https://www.podatki.gov.pl/podatki-firmowe/vat/stawki-i-limity"
WIS_SOURCE = "https://www.podatki.gov.pl/wiazace-informacje-podatkowe-i-celne/wiazaca-informacja-stawkowa-wis"


def vat_rates(http: httpx.Client | None = None) -> dict[str, Any]:
    """Verify the general VAT rate catalog on the current MF page.

    This does not classify a particular supply and cannot replace a WIS decision.
    """
    own = http is None
    http = http or httpx.Client(timeout=20, follow_redirects=True)
    try:
        response = http.get(VAT_SOURCE)
        response.raise_for_status()
        text = " ".join(html.fromstring(response.text).text_content().split())
        vat_section = text.split("Stawki podatku VAT", 1)[-1].split("Podstawa prawna", 1)[0]
        expected = ("23%", "8%", "5%", "0%")
        missing = [rate for rate in expected if rate not in vat_section]
        if missing:
            raise RuntimeError("Nie można potwierdzić pełnego katalogu stawek na stronie MF; sprawdź źródło ręcznie.")
        return {
            "rates": [
                {"code": "23", "percent": 23, "label": "stawka podstawowa"},
                {"code": "8", "percent": 8, "label": "stawka obniżona"},
                {"code": "5", "percent": 5, "label": "stawka obniżona"},
                {"code": "0", "percent": 0, "label": "stawka 0% - zależna od warunków"},
            ],
            "other_treatments": ["zwolnienie", "nie podlega opodatkowaniu"],
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "source": VAT_SOURCE,
            "classification_source": WIS_SOURCE,
            "note": "Wybór stawki dla konkretnego towaru lub usługi wymaga sprawdzenia właściwych przepisów lub WIS.",
        }
    finally:
        if own:
            http.close()
