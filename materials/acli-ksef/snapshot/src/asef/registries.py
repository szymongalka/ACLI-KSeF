from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .db import Database


VAT_URL = "https://wl-api.mf.gov.pl/api/search/nip/"
KRS_URL = "https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/"
CEIDG_SEARCH_URL = "https://biznes.gov.pl/pl/wyszukiwarka-firm/?searchType=advanced"
GUS_SEARCH_URL = "https://wyszukiwarkaregon.stat.gov.pl/appBIR/index.aspx"


def check_vat(nip: str, http: httpx.Client | None = None, day: str | None = None) -> dict[str, Any]:
    day = day or datetime.now(ZoneInfo("Europe/Warsaw")).date().isoformat()
    own = http is None
    http = http or httpx.Client(timeout=20)
    try:
        response = http.get(VAT_URL + nip, params={"date": day})
        response.raise_for_status()
        result = response.json()["result"]
        subject = result.get("subject")
        if not subject:
            return {"source": "Wykaz podatników VAT MF", "found": False, "as_of": day,
                    "request_id": result.get("requestId")}
        if str(subject.get("nip")) != nip:
            raise ValueError("NIP w odpowiedzi wykazu VAT nie odpowiada szukanemu NIP.")
        return {
            "source": "Wykaz podatników VAT MF", "found": True, "as_of": day,
            "request_id": result.get("requestId"), "name": subject.get("name"),
            "regon": subject.get("regon"), "krs": subject.get("krs"),
            "status_vat": subject.get("statusVat"),
            "working_address": subject.get("workingAddress"),
            "residence_address": subject.get("residenceAddress"),
        }
    finally:
        if own:
            http.close()


def _has_nip(value: Any, nip: str) -> bool:
    if isinstance(value, dict):
        return any(
            (key.casefold() == "nip" and str(item) == nip) or _has_nip(item, nip)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_nip(item, nip) for item in value)
    return False


def check_krs(krs: str, nip: str, http: httpx.Client | None = None) -> dict[str, Any]:
    if not krs.isdigit() or len(krs) != 10:
        raise ValueError("Numer KRS musi mieć 10 cyfr.")
    own = http is None
    http = http or httpx.Client(timeout=20)
    try:
        response = http.get(KRS_URL + krs, params={"rejestr": "P", "format": "json"})
        if response.status_code == 404:
            return {"source": "KRS", "found": False, "krs": krs}
        response.raise_for_status()
        data = response.json()
        if not _has_nip(data, nip):
            raise RuntimeError("NIP w odpisie KRS nie odpowiada szukanemu NIP.")
        return {"source": "KRS", "found": True, "krs": krs, "record": data}
    finally:
        if own:
            http.close()


def check_company(nip: str, db: Database, krs: str | None = None) -> dict[str, Any]:
    if not nip.isdigit() or len(nip) != 10:
        raise ValueError("NIP musi mieć 10 cyfr.")
    result: dict[str, Any] = {
        "nip": nip, "checked_at": datetime.now(timezone.utc).isoformat(), "sources": {},
        "manual_lookup": {
            "ceidg": CEIDG_SEARCH_URL,
            "gus_regon": GUS_SEARCH_URL,
            "note": "Wpisów CEIDG i REGON nie sprawdzono automatycznie; ich oficjalne API wymagają poświadczeń.",
        },
    }
    try:
        vat = check_vat(nip)
        result["sources"]["vat"] = vat
        if not krs:
            krs = vat.get("krs")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        result["sources"]["vat"] = {"source": "Wykaz podatników VAT MF", "error": str(exc)}
    if krs:
        try:
            result["sources"]["krs"] = check_krs(str(krs).zfill(10), nip)
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            result["sources"]["krs"] = {"source": "KRS", "error": str(exc)}
    else:
        result["sources"]["krs"] = {"source": "KRS", "checked": False, "reason": "Brak numeru KRS; podaj --krs, jeśli go znasz."}
    with db.connection:
        for source, data in result["sources"].items():
            db.connection.execute(
                "INSERT INTO company_checks(nip,source,checked_at,result_json) VALUES(?,?,?,?)",
                (nip, source, result["checked_at"], json.dumps(data, ensure_ascii=False)),
            )
    return result
