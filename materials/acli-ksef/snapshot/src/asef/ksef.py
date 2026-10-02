from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

import httpx
import keyring
from cryptography import x509
from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .certificates import load_authentication_certificate
from .xades_auth import SUBJECT_TYPES, signed_auth_request


BASE_URLS = {
    "test": "https://api-test.ksef.mf.gov.pl/v2",
    "demo": "https://api-demo.ksef.mf.gov.pl/v2",
    "prod": "https://api.ksef.mf.gov.pl/v2",
}


class KsefError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _digest(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


class Secrets:
    _runtime_tokens: dict[str, dict[str, str]] = {}
    _runtime_fingerprints: dict[str, str] = {}

    def __init__(self, environment: str, nip: str):
        self.service = f"asef:{environment}:{nip}"
        credentials_dir = os.environ.get("CREDENTIALS_DIRECTORY")
        if credentials_dir == "":
            raise KsefError("CREDENTIALS_DIRECTORY jest pusty.")
        self.credential_file = (
            Path(credentials_dir) / f"asef_{environment}_{nip}" if credentials_dir is not None else None
        )
        self.certificate_credential_file = (
            Path(credentials_dir) / f"asef_cert_{environment}_{nip}" if credentials_dir is not None else None
        )
        self._runtime_only = self.credential_file is not None
        if self._runtime_only:
            has_token = self.credential_file.is_file()
            has_certificate = self.certificate_credential_file.is_file()
            if has_token == has_certificate:
                if has_token:
                    raise KsefError(f"Skonfigurowano dwie metody uwierzytelniania dla {environment}/{nip}.")
                raise KsefError(f"Brak poświadczenia ASEF dla {environment}/{nip}.")

    @staticmethod
    def _read_credential(path: Path) -> str:
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise KsefError("Nie można odczytać poświadczenia ASEF.") from exc
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                    raise KsefError("Poświadczenie ASEF musi być zwykłym plikiem dostępnym tylko dla właściciela.")
                value = stream.read(16385)
        except UnicodeError as exc:
            raise KsefError("Poświadczenie ASEF ma nieprawidłowe kodowanie.") from exc
        if not value or len(value) > 16384:
            raise KsefError("Poświadczenie ASEF jest puste albo za duże.")
        return value

    def _runtime_credential(self) -> tuple[str, str, str]:
        assert self.credential_file is not None and self.certificate_credential_file is not None
        has_token = self.credential_file.is_file()
        has_certificate = self.certificate_credential_file.is_file()
        if has_token == has_certificate:
            if has_token:
                raise KsefError(f"Skonfigurowano dwie metody uwierzytelniania dla {self.service}.")
            raise KsefError(f"Brak poświadczenia ASEF dla {self.service}.")
        method = "certificate" if has_certificate else "token"
        path = self.certificate_credential_file if has_certificate else self.credential_file
        value = self._read_credential(path)
        fingerprint = hashlib.sha256(method.encode() + b"\0" + os.fsencode(path) + b"\0" + value.encode()).hexdigest()
        if self._runtime_fingerprints.get(self.service) != fingerprint:
            self._runtime_tokens.pop(self.service, None)
            self._runtime_fingerprints[self.service] = fingerprint
        return method, value, fingerprint

    def credential_identity(self) -> str | None:
        if self._runtime_only:
            return self._runtime_credential()[2]
        certificate = keyring.get_password(self.service, "certificate_auth")
        method = "certificate" if certificate else "token"
        credential = certificate or keyring.get_password(self.service, "ksef_token") or ""
        return hashlib.sha256(method.encode() + b"\0" + credential.encode()).hexdigest()

    def _certificate_credential(self) -> dict[str, str]:
        if self._runtime_only:
            method, raw, _ = self._runtime_credential()
            if method != "certificate":
                raise KsefError("Brak konfiguracji certyfikatu KSeF dla profilu.")
        else:
            raw = keyring.get_password(self.service, "certificate_auth")
        if not raw:
            raise KsefError("Brak konfiguracji certyfikatu KSeF dla profilu.")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise KsefError("Poświadczenie certyfikatu ASEF ma nieprawidłowy format.") from exc
        if (not isinstance(data, dict) or set(data) != {"fingerprint", "password", "subjectIdentifierType"}
                or not all(isinstance(value, str) for value in data.values())
                or not re.fullmatch(r"[0-9a-f]{64}", data["fingerprint"])
                or not data["password"] or data["subjectIdentifierType"] not in SUBJECT_TYPES):
            raise KsefError("Poświadczenie certyfikatu ASEF ma nieprawidłowy format.")
        return data

    def get(self, key: str) -> str | None:
        if self._runtime_only:
            method, credential, _ = self._runtime_credential()
            if key == "auth_method":
                return method
            if key == "ksef_token":
                if method == "certificate":
                    return None
                token = credential.strip()
                if not token or len(token) > 16384:
                    raise KsefError("Poświadczenie ASEF jest puste albo za duże.")
                return token
            return self._runtime_tokens.get(self.service, {}).get(key)
        if key == "auth_method":
            return "certificate" if keyring.get_password(self.service, "certificate_auth") else "token"
        return keyring.get_password(self.service, key)

    def certificate_config(self) -> dict[str, str]:
        return self._certificate_credential()

    def set(self, key: str, value: str) -> None:
        if self._runtime_only:
            if key == "ksef_token":
                raise KsefError("Token KSeF jest zarządzany przez systemd; zmień poświadczenie usługi.")
            if key == "certificate_auth":
                raise KsefError("Poświadczenie KSeF jest zarządzane przez systemd; zmień poświadczenie usługi.")
            self._runtime_credential()
            self._runtime_tokens.setdefault(self.service, {})[key] = value
            return
        if key in {"ksef_token", "certificate_auth"}:
            self.delete("refresh_token")
            self.delete("refresh_valid_until")
        keyring.set_password(self.service, key, value)
        if key == "ksef_token":
            self.delete("certificate_auth")

    def select_certificate(self, fingerprint: str, password: str, subject_type: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or not password or subject_type not in SUBJECT_TYPES:
            raise ValueError("Nieprawidłowa konfiguracja certyfikatu KSeF.")
        self.set("certificate_auth", json.dumps({
            "fingerprint": fingerprint, "password": password, "subjectIdentifierType": subject_type,
        }, separators=(",", ":")))

    def select_token(self) -> None:
        if self._runtime_only:
            raise KsefError("Poświadczenie KSeF jest zarządzane przez systemd; zmień poświadczenie usługi.")
        if not self.get("ksef_token"):
            raise KsefError("Brak tokena KSeF dla profilu. Użyj asef auth set-token.")
        self.delete("refresh_token")
        self.delete("refresh_valid_until")
        self.delete("certificate_auth")

    def delete(self, key: str) -> None:
        if self._runtime_only:
            self._runtime_tokens.get(self.service, {}).pop(key, None)
            return
        try:
            keyring.delete_password(self.service, key)
        except keyring.errors.PasswordDeleteError:
            pass


class KsefClient:
    def __init__(
        self,
        environment: str,
        nip: str,
        *,
        http: httpx.Client | None = None,
        secrets: Secrets | None = None,
    ):
        if environment not in BASE_URLS:
            raise ValueError("Środowisko: test, demo albo prod.")
        self.environment = environment
        self.nip = nip
        self.http = http or httpx.Client(base_url=BASE_URLS[environment], timeout=30)
        self._owns_http = http is None
        self.secrets = secrets or Secrets(environment, nip)
        self._access_token: str | None = None
        self._access_valid_until = datetime.min.replace(tzinfo=timezone.utc)
        self._credential_identity = self.secrets.credential_identity() if self.secrets._runtime_only else None

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> "KsefClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        auth: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if auth:
            token = self.access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        retryable = method == "GET" or (method == "POST" and path == "/invoices/query/metadata")
        for attempt in range(3):
            try:
                response = self.http.request(method, path, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise KsefError(f"Błąd połączenia z KSeF: {exc.__class__.__name__}") from exc
            if response.status_code != 429 or not retryable or attempt == 2:
                break
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError):
                    delay = 2 ** attempt
            delay = max(1.0, delay)
            if delay > 30:
                break
            time.sleep(delay)
        if response.status_code >= 400:
            # Never expose request headers, credentials, or a potentially sensitive body.
            try:
                problem = response.json()
                detail = problem.get("detail") or problem.get("title") or problem.get("exception", {}).get("description")
            except (ValueError, AttributeError):
                detail = None
            retry = response.headers.get("Retry-After")
            suffix = f"; Retry-After={retry}" if retry else ""
            raise KsefError(f"KSeF HTTP {response.status_code}: {detail or 'żądanie odrzucone'}{suffix}", response.status_code)
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._request(method, path, **kwargs)
        return response.json() if response.content else {}

    def _public_key(self, usage: str) -> tuple[Any, str | None]:
        entries = self._request("GET", "/security/public-key-certificates").json()
        now = datetime.now(timezone.utc)
        for item in entries:
            if usage not in item.get("usage", []):
                continue
            if not (_parse_time(item.get("validFrom")) <= now <= _parse_time(item.get("validTo"))):
                continue
            der = base64.b64decode(item["certificate"])
            try:
                key = x509.load_der_x509_certificate(der).public_key()
            except ValueError:
                key = serialization.load_der_public_key(der)
            return key, item.get("publicKeyId")
        raise KsefError(f"Brak aktualnego klucza publicznego KSeF dla {usage}.")

    def _encrypt_rsa(self, data: bytes, usage: str) -> tuple[str, str | None]:
        key, key_id = self._public_key(usage)
        encrypted = key.encrypt(
            data,
            rsa_padding.OAEP(mgf=rsa_padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        return base64.b64encode(encrypted).decode("ascii"), key_id

    def authentication_method(self) -> str:
        method = self.secrets.get("auth_method") or "token"
        if method not in {"token", "certificate"}:
            raise KsefError("Nieznana metoda uwierzytelniania KSeF.")
        return method

    def authenticate(self, timeout_seconds: int = 60) -> dict[str, Any]:
        credential_identity = self.secrets.credential_identity()
        if self.authentication_method() == "certificate":
            config = self.secrets.certificate_config()
            certificate_pem, private_key = load_authentication_certificate(
                config["fingerprint"], config["password"]
            )
            challenge = self._json("POST", "/auth/challenge")
            signed_xml = signed_auth_request(
                challenge["challenge"], self.nip, config["subjectIdentifierType"], certificate_pem, private_key
            )
            init = self._json(
                "POST", "/auth/xades-signature", content=signed_xml,
                headers={"Content-Type": "application/xml"}, params={"verifyCertificateChain": "false"},
            )
        else:
            ksef_token = self.secrets.get("ksef_token")
            if not ksef_token:
                raise KsefError("Brak tokena KSeF w systemowym magazynie haseł. Użyj asef auth set-token.")
            challenge = self._json("POST", "/auth/challenge")
            plaintext = f"{ksef_token}|{challenge['timestampMs']}".encode("utf-8")
            encrypted, key_id = self._encrypt_rsa(plaintext, "KsefTokenEncryption")
            body: dict[str, Any] = {
                "challenge": challenge["challenge"],
                "contextIdentifier": {"type": "Nip", "value": self.nip},
                "encryptedToken": encrypted,
            }
            if key_id:
                body["publicKeyId"] = key_id
            init = self._json("POST", "/auth/ksef-token", json=body)
        temporary_token = init["authenticationToken"]["token"]
        reference = init["referenceNumber"]
        deadline = time.monotonic() + timeout_seconds
        while True:
            status = self._json("GET", f"/auth/{reference}", token=temporary_token)
            code = status.get("status", {}).get("code")
            if code == 200:
                break
            if code != 100:
                raise KsefError(f"Uwierzytelnienie KSeF odrzucone: {status.get('status', {}).get('description', code)}")
            if time.monotonic() >= deadline:
                raise KsefError("Przekroczono czas oczekiwania na uwierzytelnienie KSeF.")
            time.sleep(2)
        result = self._json("POST", "/auth/token/redeem", token=temporary_token)
        if credential_identity != self.secrets.credential_identity():
            raise KsefError("Poświadczenie ASEF zmieniło się podczas uwierzytelniania; spróbuj ponownie.")
        access = result["accessToken"]
        refresh = result["refreshToken"]
        self._access_token = access["token"]
        self._access_valid_until = _parse_time(access["validUntil"])
        self.secrets.set("refresh_token", refresh["token"])
        self.secrets.set("refresh_valid_until", refresh["validUntil"])
        if credential_identity != self.secrets.credential_identity():
            self._access_token = None
            self.secrets.delete("refresh_token")
            self.secrets.delete("refresh_valid_until")
            raise KsefError("Poświadczenie ASEF zmieniło się podczas uwierzytelniania; spróbuj ponownie.")
        self._credential_identity = credential_identity
        return {"valid_until": access["validUntil"], "reference_number": reference}

    def access_token(self) -> str:
        credential_identity = self.secrets.credential_identity()
        if credential_identity != self._credential_identity:
            self._access_token = None
            self._credential_identity = credential_identity
        now = datetime.now(timezone.utc)
        if self._access_token and self._access_valid_until > now + timedelta(seconds=30):
            return self._access_token
        refresh = self.secrets.get("refresh_token")
        if refresh and _parse_time(self.secrets.get("refresh_valid_until")) > now + timedelta(seconds=30):
            try:
                result = self._json("POST", "/auth/token/refresh", token=refresh)
                access = result["accessToken"]
                self._access_token = access["token"]
                self._access_valid_until = _parse_time(access["validUntil"])
                return self._access_token
            except KsefError as exc:
                if exc.status_code not in (400, 401, 403):
                    raise
        self.authenticate()
        assert self._access_token
        return self._access_token

    def query_metadata(
        self, subject_type: str, date_from: str, page_offset: int = 0,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "subjectType": subject_type,
            "formType": "FA",
            "dateRange": {
                "dateType": "PermanentStorage",
                "from": date_from,
                "restrictToPermanentStorageHwmDate": True,
            },
        }
        if date_to:
            body["dateRange"]["to"] = date_to
        return self._json(
            "POST", "/invoices/query/metadata", auth=True, json=body,
            params={"pageOffset": page_offset, "pageSize": 100, "sortOrder": "Asc"},
        )

    def get_invoice(self, ksef_number: str) -> bytes:
        return self._request("GET", f"/invoices/ksef/{ksef_number}", auth=True).content

    def send_invoice(
        self, xml: bytes, *, on_session: Callable[[str], None] | None = None,
        on_invoice: Callable[[str], None] | None = None,
    ) -> dict[str, str]:
        key = os.urandom(32)
        iv = os.urandom(16)
        encrypted_key, key_id = self._encrypt_rsa(key, "SymmetricKeyEncryption")
        encryption: dict[str, Any] = {
            "encryptedSymmetricKey": encrypted_key,
            "initializationVector": base64.b64encode(iv).decode("ascii"),
        }
        if key_id:
            encryption["publicKeyId"] = key_id
        session = self._json(
            "POST", "/sessions/online", auth=True,
            json={"formCode": {"systemCode": "FA (3)", "schemaVersion": "1-0E", "value": "FA"}, "encryption": encryption},
        )
        session_ref = session["referenceNumber"]
        padder = padding.PKCS7(128).padder()
        padded = padder.update(xml) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        encrypted_xml = cipher.update(padded) + cipher.finalize()
        request = {
            "invoiceHash": _digest(xml),
            "invoiceSize": len(xml),
            "encryptedInvoiceHash": _digest(encrypted_xml),
            "encryptedInvoiceSize": len(encrypted_xml),
            "encryptedInvoiceContent": base64.b64encode(encrypted_xml).decode("ascii"),
            "offlineMode": False,
        }
        if on_session:
            on_session(session_ref)
        try:
            sent = self._json("POST", f"/sessions/online/{session_ref}/invoices", auth=True, json=request)
        except KsefError as exc:
            if exc.status_code == 400:
                raise KsefError(f"KSeF odrzucił wysyłkę w sesji {session_ref}: {exc}", 400) from exc
            raise KsefError(
                f"Sesja {session_ref} została otwarta, lecz wysyłka nie została potwierdzona: {exc}",
                exc.status_code,
            ) from exc
        except Exception as exc:
            raise KsefError(f"Sesja {session_ref} została otwarta, lecz wysyłka nie została potwierdzona: {exc}") from exc
        invoice_ref = sent["referenceNumber"]
        if on_invoice:
            on_invoice(invoice_ref)
        # Closing can be retried using the recorded session reference.
        try:
            self._request("POST", f"/sessions/online/{session_ref}/close", auth=True)
            closed = "true"
        except KsefError:
            closed = "false"
        return {"session_reference": session_ref, "invoice_reference": invoice_ref, "session_closed": closed}

    def close_session(self, session_reference: str) -> None:
        self._request("POST", f"/sessions/online/{session_reference}/close", auth=True)

    def session_invoices(self, session_reference: str) -> list[dict[str, Any]]:
        invoices: list[dict[str, Any]] = []
        continuation = None
        while True:
            headers = {"x-continuation-token": continuation} if continuation else {}
            result = self._json(
                "GET", f"/sessions/{session_reference}/invoices", auth=True,
                headers=headers, params={"pageSize": 100},
            )
            invoices.extend(result.get("invoices", []))
            continuation = result.get("continuationToken")
            if not continuation:
                return invoices

    def invoice_status(self, session_reference: str, invoice_reference: str) -> dict[str, Any]:
        return self._json("GET", f"/sessions/{session_reference}/invoices/{invoice_reference}", auth=True)

    def invoice_upo(self, session_reference: str, invoice_reference: str) -> bytes:
        return self._request(
            "GET", f"/sessions/{session_reference}/invoices/{invoice_reference}/upo", auth=True
        ).content
