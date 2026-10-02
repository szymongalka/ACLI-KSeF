import httpx
import pytest

from asef.db import Database
from asef import registries


def test_keyless_vat_lookup_and_krs_from_vat(tmp_path, monkeypatch):
    nip = "1234567890"
    calls = []

    def reply(request):
        calls.append(str(request.url))
        if request.url.host == "wl-api.mf.gov.pl":
            return httpx.Response(200, json={"result": {
                "requestId": "test-request",
                "subject": {"nip": nip, "name": "Firma Testowa", "regon": "123456789",
                            "krs": "12345", "statusVat": "Czynny"},
            }})
        return httpx.Response(200, json={"odpis": {"dane": {"nip": nip}}})

    client = httpx.Client(transport=httpx.MockTransport(reply))
    check_vat = registries.check_vat
    check_krs = registries.check_krs
    monkeypatch.setattr(registries, "check_vat", lambda number: check_vat(number, client, "2026-09-26"))
    monkeypatch.setattr(registries, "check_krs", lambda number, wanted: check_krs(number, wanted, client))
    with Database(tmp_path / "asef.sqlite3") as db:
        result = registries.check_company(nip, db)
        assert db.connection.execute("SELECT COUNT(*) FROM company_checks").fetchone()[0] == 2
    assert result["sources"]["vat"]["name"] == "Firma Testowa"
    assert result["sources"]["vat"]["request_id"] == "test-request"
    assert result["sources"]["krs"]["krs"] == "0000012345"
    assert result["sources"]["krs"]["found"] is True
    assert "ceidg" in result["manual_lookup"] and "gus_regon" in result["manual_lookup"]
    assert "/api/search/nip/1234567890?date=2026-09-26" in calls[0]


def test_vat_lookup_rejects_wrong_nip():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"result": {"subject": {"nip": "9999999999"}}})
    ))
    with pytest.raises(ValueError, match="nie odpowiada"):
        registries.check_vat("1234567890", client, "2026-09-26")
