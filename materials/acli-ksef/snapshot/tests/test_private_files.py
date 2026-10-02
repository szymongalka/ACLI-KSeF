import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from asef.cli import app
from asef.db import Database
from asef.paths import write_private_file
from asef.service import AsefService


SAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "faktura.json").read_text())


@pytest.fixture(autouse=True)
def ordinary_umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_private_file_is_owner_only_before_atomic_replacement(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "invoice.xml"
    target.write_bytes(b"old complete file")
    target.chmod(0o644)
    real_replace = os.replace
    published = []

    def inspect_replace(source: str, destination: Path) -> None:
        temporary = Path(source)
        assert _mode(temporary) == 0o600
        assert temporary.read_bytes() == b"new complete file"
        assert target.read_bytes() == b"old complete file"
        published.append(temporary)
        real_replace(source, destination)

    monkeypatch.setattr("asef.paths.os.replace", inspect_replace)
    assert write_private_file(target, b"new complete file") == target

    assert len(published) == 1
    assert not published[0].exists()
    assert target.read_bytes() == b"new complete file"
    assert _mode(target) == 0o600


def test_failed_replacement_preserves_old_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "invoice.xml"
    target.write_bytes(b"old complete file")

    def fail_replace(*_args) -> None:
        raise OSError("publication failed")

    monkeypatch.setattr("asef.paths.os.replace", fail_replace)
    with pytest.raises(OSError, match="publication failed"):
        write_private_file(target, b"new complete file")

    assert target.read_bytes() == b"old complete file"
    assert not list(tmp_path.glob(".invoice.xml.*"))


def test_preview_and_exports_are_private(tmp_path: Path) -> None:
    with Database(tmp_path / "asef.sqlite3") as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, SAMPLE["seller"]["name"])
        document = service.draft_from_payload(nip, SAMPLE)
        xml = db.get_document(document["id"])["xml"]
        db.update_document(document["id"], upo_xml=b"<UPO/>")

        html = tmp_path / "preview.html"
        pdf = tmp_path / "preview.pdf"
        exported_xml = tmp_path / "export.xml"
        exported_upo = tmp_path / "upo.xml"
        service.preview(document["id"], html, "html")
        service.preview(document["id"], pdf, "pdf")
        service.export_xml(document["id"], exported_xml)
        service.export_upo(document["id"], exported_upo)

    assert SAMPLE["seller"]["name"].encode() in html.read_bytes()
    assert pdf.read_bytes().startswith(b"%PDF")
    assert exported_xml.read_bytes() == xml
    assert exported_upo.read_bytes() == b"<UPO/>"
    assert all(_mode(path) == 0o600 for path in (html, pdf, exported_xml, exported_upo))


def test_backup_is_private_and_integral_before_publication(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    with Database() as db:
        service = AsefService(db)
        nip = SAMPLE["seller"]["nip"]
        service.profile_add(nip, SAMPLE["seller"]["name"])
        document = service.draft_from_payload(nip, SAMPLE)

    target = tmp_path / "backup.sqlite3"
    real_link = os.link
    published = []

    def inspect_link(source: str, destination: Path) -> None:
        temporary = Path(source)
        assert _mode(temporary) == 0o600
        assert not target.exists()
        with sqlite3.connect(temporary) as copy:
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert copy.execute("SELECT xml_sha256 FROM documents WHERE id=?", (document["id"],)).fetchone()[0] == document["xml_sha256"]
        published.append(temporary)
        real_link(source, destination)

    monkeypatch.setattr("asef.cli.os.link", inspect_link)
    result = CliRunner().invoke(app, ["db", "backup", str(target)])

    assert result.exit_code == 0, result.output
    assert len(published) == 1
    assert not published[0].exists()
    assert _mode(target) == 0o600
    with sqlite3.connect(target) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copy.execute("SELECT xml_sha256 FROM documents WHERE id=?", (document["id"],)).fetchone()[0] == document["xml_sha256"]


def test_backup_never_overwrites_an_existing_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path / "data"))
    with Database() as db:
        AsefService(db).profile_add(SAMPLE["seller"]["nip"], SAMPLE["seller"]["name"])

    target = tmp_path / "backup.sqlite3"
    target.write_bytes(b"existing copy")
    first = CliRunner().invoke(app, ["db", "backup", str(target)])
    assert first.exit_code != 0
    assert target.read_bytes() == b"existing copy"
    assert not list(tmp_path.glob(".backup.sqlite3.*"))

    target.unlink()
    real_link = os.link

    def competing_backup(source: str, destination: Path) -> None:
        Path(destination).write_bytes(b"competing copy")
        real_link(source, destination)

    monkeypatch.setattr("asef.cli.os.link", competing_backup)
    raced = CliRunner().invoke(app, ["db", "backup", str(target)])
    assert raced.exit_code != 0
    assert target.read_bytes() == b"competing copy"
    assert not list(tmp_path.glob(".backup.sqlite3.*"))


def test_database_directory_and_wal_are_private_with_umask_022(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    data.chmod(0o755)
    monkeypatch.setenv("ASEF_DATA_DIR", str(data))

    with Database() as db:
        AsefService(db).profile_add(SAMPLE["seller"]["nip"], SAMPLE["seller"]["name"])
        assert _mode(data) == 0o700
        for path in (db.path, Path(str(db.path) + "-wal"), Path(str(db.path) + "-shm")):
            assert path.is_file()
            assert _mode(path) == 0o600


def test_explicit_database_path_requires_private_directory(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    public.chmod(0o755)
    with pytest.raises(PermissionError, match="0700"):
        Database(public / "asef.sqlite3")
    assert not (public / "asef.sqlite3").exists()
