"""Private stdio bridge for the trusted OpenClaw SecretRef plugin.

This is not a secret retrieval command. Only the native plugin supplies the
resolved token over an anonymous pipe; the model receives sanitized results.
No token, access token, or refresh token is persisted by this module.
"""
from __future__ import annotations

import hashlib
import json
import re
import resource
import sys
from typing import Any

import httpx

from .db import Database
from .ksef import BASE_URLS, KsefClient, KsefError
from .ksef_export import KsefExportClient
from .service import AsefService

MAX_INPUT = 32768
ACTIONS = {"auth_check", "sync", "invoice_status", "invoice_send"}


class MemoryTokenSecrets:
    """One profile, one invocation. Never falls back to keyring/systemd."""
    _runtime_only = True

    def __init__(self, token: str):
        if not isinstance(token, str) or not token.strip() or len(token) > 16384:
            raise ValueError("Invalid credential")
        self._token = token.strip()
        self._runtime: dict[str, str] = {}

    def credential_identity(self) -> str:
        return hashlib.sha256(self._token.encode()).hexdigest()

    def get(self, key: str) -> str | None:
        if key == "auth_method":
            return "token"
        if key == "ksef_token":
            return self._token
        return self._runtime.get(key)

    def set(self, key: str, value: str) -> None:
        if key not in {"refresh_token", "refresh_valid_until"}:
            raise ValueError("Credential is managed by OpenClaw Secret Store")
        self._runtime[key] = value

    def delete(self, key: str) -> None:
        self._runtime.pop(key, None)

    def clear(self) -> None:
        self._token = ""
        self._runtime.clear()


def _validate(request: Any) -> None:
    if not isinstance(request, dict) or set(request) - {
        "action", "nip", "environment", "token", "start_from", "document_id", "sha256", "allow_send"
    }:
        raise ValueError("Invalid request")
    if (request.get("action") not in ACTIONS or request.get("environment") not in BASE_URLS
            or not isinstance(request.get("nip"), str)
            or not re.fullmatch(r"[0-9]{10}", request["nip"])):
        raise ValueError("Invalid profile/action")
    action = request["action"]
    if action.startswith("invoice_"):
        if not isinstance(request.get("document_id"), str) or not re.fullmatch(
            r"[0-9a-f-]{36}", request["document_id"]
        ):
            raise ValueError("Invalid document")
    if action == "invoice_send" and (
        request.get("allow_send") is not True or not isinstance(request.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", request["sha256"])
    ):
        raise ValueError("Sending requires enabled policy and reviewed SHA-256")
    if request.get("start_from") is not None and not isinstance(request["start_from"], str):
        raise ValueError("Invalid start date")


def _document_summary(document: dict[str, Any]) -> dict[str, Any]:
    # Do not return raw API descriptions, XML, arbitrary fields, or credentials.
    return {key: document.get(key) for key in (
        "id", "profile_nip", "environment", "status", "xml_sha256", "ksef_number"
    )}


def dispatch(request: dict[str, Any], *, db: Database | None = None,
             client_type: type[KsefClient] = KsefClient) -> dict[str, Any]:
    _validate(request)
    nip, environment, action = request["nip"], request["environment"], request["action"]
    secrets = MemoryTokenSecrets(request["token"])

    def client_factory(env: str, context_nip: str) -> KsefClient:
        if (env, context_nip) != (environment, nip):
            raise ValueError("Cross-profile credential use is forbidden")
        # The native plugin is a trusted SecretRef consumer, not an egress
        # sentinel consumer. Only fixed official endpoints, TLS, no redirects.
        selected_client = KsefExportClient if action == "sync" and client_type is KsefClient else client_type
        return selected_client(env, context_nip, secrets=secrets)

    own_db = db is None
    try:
        db = db or Database()
        db.profile(nip, environment)  # Existing explicit profile only.
        if action == "auth_check":
            with client_factory(environment, nip) as client:
                client.access_token()
                return {"authenticated": True, "nip": nip, "environment": environment, "method": "token"}
        service = AsefService(db, client_factory=client_factory)
        if action == "sync":
            return service.sync(nip, request.get("start_from"), environment)
        document = db.get_document(request["document_id"])
        if (document["environment"], document["profile_nip"]) != (environment, nip):
            raise ValueError("Document belongs to a different profile")
        if action == "invoice_send":
            if request["sha256"] != document["xml_sha256"]:
                raise ValueError("Reviewed XML has changed")
            return {"document": _document_summary(service.send(document["id"], request["sha256"]))}
        result = service.refresh_status(document["id"])
        return {"document": _document_summary(result["document"]),
                "ksef_status_code": result["ksef_status"].get("code"),
                "session_close_failed": bool(result.get("session_close_error")),
                "preflight_recovered": bool(result.get("preflight_recovered"))}
    finally:
        secrets.clear()
        if own_db and db is not None:
            db.close()


def run(stream: Any, output: Any, *, dispatcher: Any = dispatch) -> int:
    try:
        data = stream.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise ValueError("Input too large")
        request = json.loads(data)
        result = dispatcher(request)
        response = {"ok": True, "result": result}
    except KsefError as exc:
        response = {"ok": False, "error": "KSEF_ERROR", "status_code": exc.status_code}
    except httpx.HTTPError:
        response = {"ok": False, "error": "NETWORK_ERROR"}
    except (ValueError, KeyError, TypeError):
        response = {"ok": False, "error": "INVALID_REQUEST_OR_STATE"}
    except Exception:
        # Never expose an exception's message/traceback: it may contain HTTP
        # response data, headers, bearer tokens, or the source credential.
        response = {"ok": False, "error": "BRIDGE_ERROR"}
    output.write(json.dumps(response, ensure_ascii=False) + "\n")
    return 0 if response["ok"] else 1


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    raise SystemExit(run(sys.stdin.buffer, sys.stdout))


if __name__ == "__main__":
    main()
