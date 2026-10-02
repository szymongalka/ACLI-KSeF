import base64
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from asef.db import Database
from asef.ksef import KsefClient, KsefError
from asef.openclaw_bridge import MemoryTokenSecrets, dispatch, run
from asef.service import AsefService

NIP = "1234567890"
TOKEN = "fictional-ksef-credential"


def request(**changes):
    return {"action": "auth_check", "nip": NIP, "environment": "prod", "token": TOKEN, **changes}


def test_real_client_authenticates_using_pipe_credential_without_keyring(tmp_path, monkeypatch):
    monkeypatch.setattr("asef.ksef.keyring.get_password", lambda *_: pytest.fail("keyring read"))
    monkeypatch.setattr("asef.ksef.keyring.set_password", lambda *_: pytest.fail("keyring write"))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    calls = []

    def respond(req):
        calls.append(req.url.path)
        assert req.url.host == "api.ksef.mf.gov.pl"
        assert TOKEN.encode() not in req.content
        if req.url.path.endswith("/auth/challenge"):
            return httpx.Response(200, json={"challenge": "challenge", "timestampMs": 1234})
        if req.url.path.endswith("/security/public-key-certificates"):
            return httpx.Response(200, json=[{"usage": ["KsefTokenEncryption"], "validFrom": "2020-01-01T00:00:00Z",
                "validTo": future, "certificate": base64.b64encode(der).decode()}])
        if req.url.path.endswith("/auth/ksef-token"):
            body = json.loads(req.content)
            decoded = key.decrypt(base64.b64decode(body["encryptedToken"]), padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
            assert decoded == f"{TOKEN}|1234".encode()
            assert body["contextIdentifier"] == {"type": "Nip", "value": NIP}
            return httpx.Response(200, json={"authenticationToken": {"token": "private-temp"}, "referenceNumber": "ref"})
        if req.url.path.endswith("/auth/ref"):
            return httpx.Response(200, json={"status": {"code": 200}})
        if req.url.path.endswith("/auth/token/redeem"):
            return httpx.Response(200, json={"accessToken": {"token": "private-access", "validUntil": future},
                "refreshToken": {"token": "private-refresh", "validUntil": future}})
        pytest.fail("Unexpected KSeF operation")

    with httpx.Client(base_url="https://api.ksef.mf.gov.pl/v2", transport=httpx.MockTransport(respond)) as http:
        with Database(tmp_path / "asef.sqlite3") as db:
            db.add_profile(NIP, "Fictional company", "prod")
            factory = lambda env, nip, secrets: KsefClient(env, nip, http=http, secrets=secrets)
            result = dispatch(request(), db=db, client_type=factory)
            assert result == {"authenticated": True, "nip": NIP, "environment": "prod", "method": "token"}
            assert len(calls) == 5
            assert db.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    for file in tmp_path.iterdir():
        assert TOKEN.encode() not in file.read_bytes()
        assert b"private-refresh" not in file.read_bytes()


def test_memory_secrets_are_per_invocation_and_read_only():
    first = MemoryTokenSecrets(TOKEN)
    first.set("refresh_token", "dummy-refresh")
    assert MemoryTokenSecrets(TOKEN).get("refresh_token") is None
    with pytest.raises(ValueError):
        first.set("ksef_token", "other")
    first.clear()
    assert not first.get("ksef_token") and first.get("refresh_token") is None


@pytest.mark.parametrize("error,code", [(KsefError("private-access " + TOKEN, 401), "KSEF_ERROR"),
    (ValueError(TOKEN), "INVALID_REQUEST_OR_STATE"), (RuntimeError(TOKEN), "BRIDGE_ERROR"),
    (httpx.ConnectError(TOKEN), "NETWORK_ERROR")])
def test_exception_contents_never_leave_bridge(error, code):
    def fail(_):
        raise error
    output = io.StringIO()
    assert run(io.BytesIO(json.dumps(request()).encode()), output, dispatcher=fail) == 1
    assert json.loads(output.getvalue())["error"] == code
    assert TOKEN not in output.getvalue() and "private-access" not in output.getvalue()


def test_oversize_input_is_not_dispatched():
    output = io.StringIO()
    assert run(io.BytesIO(b"x" * 32769), output, dispatcher=lambda _: pytest.fail("dispatched")) == 1


def test_cross_profile_document_cannot_use_token(tmp_path):
    sample = json.loads((Path(__file__).parent.parent / "examples/faktura.json").read_text())
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        db.add_profile(NIP, "Other fictional", "prod")
        seller = sample["seller"]["nip"]
        db.add_profile(seller, "Fictional", "test")
        doc = service.draft_from_payload(seller, sample, "test")
        with pytest.raises(ValueError, match="different profile"):
            dispatch(request(action="invoice_status", document_id=doc["id"]), db=db,
                client_type=lambda *_a, **_k: pytest.fail("network"))


def test_send_still_requires_policy_sha256_and_existing_approval(tmp_path):
    sample = json.loads((Path(__file__).parent.parent / "examples/faktura.json").read_text())
    with Database(tmp_path / "asef.sqlite3") as db:
        seller = sample["seller"]["nip"]
        db.add_profile(seller, "Fictional", "prod")
        service = AsefService(db)
        doc = service.draft_from_payload(seller, sample, "prod")
        params = request(action="invoice_send", nip=seller, document_id=doc["id"], sha256=doc["xml_sha256"])
        fail = lambda *_a, **_k: pytest.fail("network")
        with pytest.raises(ValueError, match="enabled policy"):
            dispatch(params, db=db, client_type=fail)
        with pytest.raises(ValueError, match="zatwierdzon"):
            dispatch({**params, "allow_send": True}, db=db, client_type=fail)
        service.approve(doc["id"], doc["xml_sha256"])
        with pytest.raises(ValueError, match="XML has changed"):
            dispatch({**params, "allow_send": True, "sha256": "a" * 64}, db=db, client_type=fail)


def test_unknown_profile_does_not_authenticate(tmp_path):
    with Database(tmp_path / "asef.sqlite3") as db:
        with pytest.raises(ValueError):
            dispatch(request(), db=db, client_type=lambda *_a, **_k: pytest.fail("network"))
