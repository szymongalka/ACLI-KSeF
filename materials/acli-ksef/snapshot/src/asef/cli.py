from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import httpx
import keyring
import typer

from .bulk_xml import export_xml_bundle, import_xml_bundle
from .certificates import (
    EncryptedKeyPasswordRequired, add_certificate, list_certificates, load_authentication_certificate,
)
from .db import Database
from .db_archive import export_database, import_database
from .ksef import KsefClient, KsefError, Secrets
from .paths import db_path
from .openclaw_cli import request_ksef
from .registries import check_company
from .service import AsefService
from .secure_archive import read_encrypted_file, write_encrypted_file
from .tax import vat_rates
from .xades_auth import SUBJECT_TYPES, signature_algorithm


app = typer.Typer(help="ASEF - Agencyjny System Elektronicznych Faktur", no_args_is_help=True)
profiles = typer.Typer(help="Profile firm")
auth = typer.Typer(help="Uwierzytelnianie KSeF")
invoices = typer.Typer(help="Faktury i korekty")
templates = typer.Typer(help="Szablony szkiców")
registry = typer.Typer(help="Wykaz VAT MF, KRS oraz linki do CEIDG i GUS")
vat = typer.Typer(help="Stawki VAT i źródła")
database = typer.Typer(help="Lokalna baza danych")
app.add_typer(profiles, name="profile")
app.add_typer(auth, name="auth")
app.add_typer(invoices, name="invoice")
app.add_typer(templates, name="template")
app.add_typer(registry, name="registry")
app.add_typer(vat, name="vat")
app.add_typer(database, name="db")


class CredentialProvider(str, Enum):
    system = "system"
    openclaw = "openclaw"


@app.callback()
def main(ctx: typer.Context, json_output: bool = typer.Option(False, "--json", help="Wynik maszynowy JSON"),
         credentials: CredentialProvider = typer.Option(CredentialProvider.system, "--credentials",
             help="system: keyring/systemd; openclaw: istniejący Secret Store")) -> None:
    ctx.obj = {"json": json_output, "credentials": credentials}


def _openclaw(ctx: typer.Context) -> bool:
    return ctx.obj.get("credentials") == CredentialProvider.openclaw


def _emit(ctx: typer.Context, result: Any) -> None:
    if ctx.obj.get("json"):
        typer.echo(json.dumps({"ok": True, "result": result}, ensure_ascii=False, default=str))
    else:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _run(ctx: typer.Context, fn: Callable[[], Any]) -> None:
    try:
        _emit(ctx, fn())
    except (ValueError, RuntimeError, OSError, httpx.HTTPError, sqlite3.Error, keyring.errors.KeyringError) as exc:
        message = str(exc)
        if ctx.obj.get("json"):
            typer.echo(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        else:
            typer.echo(f"Błąd: {message}", err=True)
        raise typer.Exit(1) from exc


def _profile(db: Database, nip: str | None, environment: str | None = None) -> dict[str, Any]:
    return db.profile(nip, environment)


@profiles.command("add")
def profile_add(ctx: typer.Context, nip: str, name: str, environment: str = typer.Option(None, "--env")) -> None:
    def task() -> Any:
        with Database() as db:
            return AsefService(db).profile_add(nip, name, environment)
    _run(ctx, task)


@profiles.command("list")
def profile_list(ctx: typer.Context) -> None:
    def task() -> Any:
        with Database() as db:
            return db.profiles()
    _run(ctx, task)


@auth.command("set-token")
def auth_set_token(ctx: typer.Context, nip: str = typer.Option(None, "--nip"),
                   environment: str = typer.Option(None, "--env")) -> None:
    def task() -> Any:
        with Database() as db:
            profile = _profile(db, nip, environment)
        if _openclaw(ctx):
            raise ValueError("Poświadczeniem zarządza OpenClaw Secret Store; nie przenoś tokenu do keyring.")
        token = typer.prompt("Token KSeF", hide_input=True)
        if not token.strip():
            raise ValueError("Pusty token.")
        Secrets(profile["environment"], profile["nip"]).set("ksef_token", token.strip())
        return {"saved": True, "nip": profile["nip"], "environment": profile["environment"]}
    _run(ctx, task)


@auth.command("check")
def auth_check(ctx: typer.Context, nip: str = typer.Option(None, "--nip"),
               environment: str = typer.Option(None, "--env")) -> None:
    def task() -> Any:
        with Database() as db:
            profile = _profile(db, nip, environment)
        if _openclaw(ctx):
            return request_ksef({"action": "auth_check", "nip": profile["nip"], "environment": profile["environment"]})
        with KsefClient(profile["environment"], profile["nip"]) as client:
            client.access_token()
            return {"authenticated": True, "nip": profile["nip"], "environment": profile["environment"],
                    "method": client.authentication_method()}
    _run(ctx, task)


@auth.command("add-certificate")
def auth_add_certificate(ctx: typer.Context, certificate: Path, private_key: Path,
                         purpose: str = typer.Option("Authentication", "--purpose")) -> None:
    """Zarejestruj parę certyfikat KSeF / klucz do zaszyfrowanego backupu."""
    def task() -> Any:
        try:
            return add_certificate(certificate, private_key, purpose=purpose)
        except EncryptedKeyPasswordRequired:
            password = typer.prompt("Hasło klucza prywatnego certyfikatu KSeF", hide_input=True, err=True)
            return add_certificate(certificate, private_key, key_password=password, purpose=purpose)
    _run(ctx, task)


@auth.command("certificates")
def auth_certificates(ctx: typer.Context) -> None:
    _run(ctx, list_certificates)


@auth.command("use-certificate")
def auth_use_certificate(ctx: typer.Context, fingerprint: str, nip: str = typer.Option(None, "--nip"),
                         environment: str = typer.Option(None, "--env"),
                         subject_identifier: str = typer.Option("certificateSubject", "--subject-identifier")) -> None:
    """Wybierz certyfikat Authentication do logowania dla profilu."""
    def task() -> Any:
        if _openclaw(ctx):
            raise ValueError("Poświadczeniem zarządza OpenClaw Secret Store; wybór certyfikatu wymaga osobnej konfiguracji.")
        if os.environ.get("CREDENTIALS_DIRECTORY") is not None:
            raise ValueError("Poświadczenia systemd są zarządzane przez usługę; skonfiguruj asef_cert_ENV_NIP.")
        if subject_identifier not in SUBJECT_TYPES:
            raise ValueError("Typ identyfikatora: certificateSubject albo certificateFingerprint.")
        with Database() as db:
            profile = _profile(db, nip, environment)
        password = typer.prompt("Hasło klucza prywatnego certyfikatu KSeF", hide_input=True, err=True)
        _, private_key = load_authentication_certificate(fingerprint, password)
        signature_algorithm(private_key)
        Secrets(profile["environment"], profile["nip"]).select_certificate(
            fingerprint, password, subject_identifier
        )
        return {"method": "certificate", "fingerprint": fingerprint,
                "nip": profile["nip"], "environment": profile["environment"],
                "subject_identifier": subject_identifier}
    _run(ctx, task)


@auth.command("use-token")
def auth_use_token(ctx: typer.Context, nip: str = typer.Option(None, "--nip"),
                   environment: str = typer.Option(None, "--env")) -> None:
    """Wróć do uwierzytelniania zapisanym tokenem KSeF."""
    def task() -> Any:
        if _openclaw(ctx):
            raise ValueError("Poświadczeniem zarządza OpenClaw Secret Store; użyj auth check.")
        with Database() as db:
            profile = _profile(db, nip, environment)
        Secrets(profile["environment"], profile["nip"]).select_token()
        return {"method": "token", "nip": profile["nip"], "environment": profile["environment"]}
    _run(ctx, task)


@invoices.command("create")
def invoice_create(
    ctx: typer.Context,
    file: Path = typer.Option(None, "--file", help="JSON z danymi faktury"),
    template: str = typer.Option(None, "--template", help="Nazwa zapisanego szablonu"),
    nip: str = typer.Option(None, "--nip"),
    environment: str = typer.Option(None, "--env"),
) -> None:
    def task() -> Any:
        with Database() as db:
            profile = _profile(db, nip, environment)
            payload: dict[str, Any] = {}
            if template:
                row = db.connection.execute("SELECT payload_json FROM templates WHERE name=?", (template,)).fetchone()
                if not row:
                    raise ValueError("Nie znaleziono szablonu.")
                payload = json.loads(row[0])
            if file:
                override = json.loads(file.read_text(encoding="utf-8"))
                payload.update(override)
            if not payload:
                raise ValueError("Podaj --file lub --template.")
            return AsefService(db).draft_from_payload(profile["nip"], payload, profile["environment"])
    _run(ctx, task)


@invoices.command("import-xml")
def invoice_import_xml(ctx: typer.Context, file: Path, nip: str = typer.Option(None, "--nip"),
                       environment: str = typer.Option(None, "--env")) -> None:
    def task() -> Any:
        with Database() as db:
            profile = _profile(db, nip, environment)
            return AsefService(db).draft_from_xml(profile["nip"], file.read_bytes(), profile["environment"])
    _run(ctx, task)


@invoices.command("revise")
def invoice_revise(ctx: typer.Context, document_id: str, file: Path) -> None:
    def task() -> Any:
        with Database() as db:
            return AsefService(db).revise_from_xml(document_id, file.read_bytes())
    _run(ctx, task)


@invoices.command("list")
def invoice_list(
    ctx: typer.Context,
    category: str = typer.Option(None, "--category", help="issued, received, corrections"),
    nip: str = typer.Option(None, "--nip"),
    environment: str = typer.Option(None, "--env"),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
) -> None:
    def task() -> Any:
        with Database() as db:
            profile = _profile(db, nip, environment)
            return db.list_documents(profile["nip"], category, limit, profile["environment"])
    _run(ctx, task)


@invoices.command("show")
def invoice_show(ctx: typer.Context, document_id: str) -> None:
    def task() -> Any:
        with Database() as db:
            return {k: v for k, v in db.get_document(document_id).items() if k not in {"xml", "upo_xml", "payload_json"}}
    _run(ctx, task)


@invoices.command("preview")
def invoice_preview(
    ctx: typer.Context,
    document_id: str,
    format: str = typer.Option("html", "--format", help="html albo pdf"),
    theme: str = typer.Option("light", "--theme", help="Motyw PDF: light albo crt"),
    output: Path = typer.Option(None, "--out"),
) -> None:
    def task() -> Any:
        with Database() as db:
            suffix = f"-{theme}" if format == "pdf" else ""
            path = output or Path.cwd() / f"asef-{document_id}{suffix}.{format}"
            return AsefService(db).preview(document_id, path, format, theme)
    _run(ctx, task)


@invoices.command("approve")
def invoice_approve(
    ctx: typer.Context,
    document_id: str,
    sha256: str = typer.Option(..., "--sha256"),
    yes: bool = typer.Option(False, "--yes", help="Zatwierdź bez pytania po wyraźnej zgodzie użytkownika"),
) -> None:
    def task() -> Any:
        if not yes and not sys.stdin.isatty():
            raise ValueError("Zatwierdzenie wymaga interaktywnego terminala albo --yes po zgodzie użytkownika.")
        with Database() as db:
            if not yes:
                doc = db.get_document(document_id)
                typer.echo(f"Faktura: {doc['number']} | Nabywca: {doc['buyer_name']} | Kwota: {doc['gross_amount']} {doc['currency']}")
                typer.echo(f"Środowisko: {doc['environment']} | SHA-256 XML: {doc['xml_sha256']}")
                if not typer.confirm("Czy sprawdzono podgląd tej wersji i zatwierdzić wysyłkę?", default=False):
                    raise ValueError("Zatwierdzenie anulowane.")
            return AsefService(db).approve(document_id, sha256)
    _run(ctx, task)


@invoices.command("send")
def invoice_send(ctx: typer.Context, document_id: str,
                 confirm_prod: str = typer.Option(None, "--confirm-prod", help="SHA-256 XML po osobnym potwierdzeniu PROD"),
                 sha256: str = typer.Option(None, "--sha256", help="Sprawdzony SHA-256 XML; wymagany dla OpenClaw")) -> None:
    def task() -> Any:
        with Database() as db:
            doc = db.get_document(document_id)
            if sha256 is not None and sha256 != doc["xml_sha256"]:
                raise ValueError("Sprawdzony SHA-256 nie odpowiada aktualnemu XML.")
            if _openclaw(ctx) and sha256 is None:
                raise ValueError("OpenClaw wymaga --sha256 sprawdzonej wersji XML.")
            confirmation = confirm_prod
            if doc["environment"] == "prod" and confirmation is None and sys.stdin.isatty():
                typer.echo(f"PROD | Faktura: {doc['number']} | Nabywca: {doc['buyer_name']} | Kwota: {doc['gross_amount']} {doc['currency']}")
                typer.echo(f"SHA-256 XML: {doc['xml_sha256']}")
                if typer.confirm("Czy osobno potwierdzasz wysyłkę tej faktury do PROD?", default=False):
                    confirmation = doc["xml_sha256"]
            if _openclaw(ctx):
                if doc["environment"] == "prod" and confirmation != sha256:
                    raise ValueError("PROD wymaga osobnego --confirm-prod zgodnego z --sha256.")
                return request_ksef({"action": "invoice_send", "nip": doc["profile_nip"],
                    "environment": doc["environment"], "documentId": document_id, "sha256": sha256,
                    **({"confirmProd": confirmation} if confirmation is not None else {})})
            return AsefService(db).send(document_id, confirmation)
    _run(ctx, task)


@invoices.command("status")
def invoice_status(ctx: typer.Context, document_id: str) -> None:
    def task() -> Any:
        with Database() as db:
            if _openclaw(ctx):
                doc = db.get_document(document_id)
                return request_ksef({"action": "invoice_status", "nip": doc["profile_nip"],
                    "environment": doc["environment"], "documentId": document_id})
            return AsefService(db).refresh_status(document_id)
    _run(ctx, task)


@invoices.command("xml")
def invoice_xml(ctx: typer.Context, document_id: str, output: Path = typer.Option(..., "--out")) -> None:
    def task() -> Any:
        with Database() as db:
            return {"path": AsefService(db).export_xml(document_id, output)}
    _run(ctx, task)


@invoices.command("upo")
def invoice_upo(ctx: typer.Context, document_id: str, output: Path = typer.Option(..., "--out")) -> None:
    def task() -> Any:
        with Database() as db:
            return {"path": AsefService(db).export_upo(document_id, output)}
    _run(ctx, task)


@invoices.command("export-bundle")
def invoice_export_bundle(ctx: typer.Context, output: Path,
                          nip: str = typer.Option(None, "--nip"),
                          environment: str = typer.Option(None, "--env")) -> None:
    """Eksportuj faktury XML do zaszyfrowanego pakietu."""
    def task() -> Any:
        with Database() as db:
            payload, counts = export_xml_bundle(db, nip, environment)
        password = typer.prompt("Hasło pakietu XML (min. 12 znaków)", hide_input=True,
                                confirmation_prompt=True, err=True)
        path = write_encrypted_file(output, payload, password, "xml-bundle")
        return {"path": str(path), "counts": counts}
    _run(ctx, task)


@invoices.command("import-bundle")
def invoice_import_bundle(ctx: typer.Context, source: Path) -> None:
    """Importuj faktury XML z zaszyfrowanego pakietu."""
    def task() -> Any:
        password = typer.prompt("Hasło pakietu XML", hide_input=True, err=True)
        payload = read_encrypted_file(source, password, "xml-bundle")
        with Database() as db:
            return import_xml_bundle(db, payload)
    _run(ctx, task)


@app.command("sync")
def sync(ctx: typer.Context, start_from: str = typer.Option(None, "--from"),
         nip: str = typer.Option(None, "--nip"), environment: str = typer.Option(None, "--env")) -> None:
    def task() -> Any:
        with Database() as db:
            profile = _profile(db, nip, environment)
            if _openclaw(ctx):
                return request_ksef({"action": "sync", "nip": profile["nip"], "environment": profile["environment"],
                    **({"startFrom": start_from} if start_from is not None else {})})
            return AsefService(db).sync(profile["nip"], start_from, profile["environment"])
    _run(ctx, task)


@templates.command("save")
def template_save(ctx: typer.Context, name: str, file: Path) -> None:
    def task() -> Any:
        payload = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Szablon musi być obiektem JSON.")
        from .db import now
        with Database() as db, db.connection:
            db.connection.execute(
                "INSERT INTO templates(name,payload_json,updated_at) VALUES(?,?,?) ON CONFLICT(name) "
                "DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                (name, json.dumps(payload, ensure_ascii=False), now()),
            )
        return {"saved": name}
    _run(ctx, task)


@templates.command("list")
def template_list(ctx: typer.Context) -> None:
    def task() -> Any:
        with Database() as db:
            return [dict(x) for x in db.connection.execute("SELECT name,updated_at FROM templates ORDER BY name")]
    _run(ctx, task)


@registry.command("check")
def registry_check(ctx: typer.Context, nip: str, krs: str = typer.Option(None, "--krs")) -> None:
    def task() -> Any:
        with Database() as db:
            return check_company(nip, db, krs)
    _run(ctx, task)


@vat.command("rates")
def vat_list_rates(ctx: typer.Context) -> None:
    _run(ctx, vat_rates)


@database.command("path")
def database_path(ctx: typer.Context) -> None:
    _run(ctx, lambda: {"path": str(db_path())})


@database.command("backup")
def database_backup(ctx: typer.Context, output: Path) -> None:
    def task() -> Any:
        target = output.expanduser().absolute()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("Kopia już istnieje; wybierz nową nazwę pliku.")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(descriptor)
        try:
            with Database() as db, closing(sqlite3.connect(temporary)) as destination:
                db.connection.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Weryfikacja kopii SQLite nie powiodła się.")
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            os.link(temporary, target)  # Fails atomically if another backup took this name.
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"path": str(target)}
    _run(ctx, task)


@database.command("export")
def database_export(ctx: typer.Context, output: Path) -> None:
    def task() -> Any:
        password = typer.prompt("Hasło archiwum ASEF (min. 12 znaków)", hide_input=True,
                                confirmation_prompt=True, err=True)
        return export_database(output, password)
    _run(ctx, task)


@database.command("import")
def database_import(ctx: typer.Context, source: Path) -> None:
    def task() -> Any:
        password = typer.prompt("Hasło archiwum ASEF", hide_input=True, err=True)
        return import_database(source, password)
    _run(ctx, task)


if __name__ == "__main__":
    app()
