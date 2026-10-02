import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from asef.cli import app
from asef.db import Database
from asef.openclaw_cli import request_ksef
from asef.service import AsefService


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path))
    sample = json.loads((Path(__file__).parent.parent / "examples/faktura.json").read_text())
    with Database() as db:
        service = AsefService(db)
        service.profile_add(sample["seller"]["nip"], "Fictional", "prod")
        doc = service.draft_from_payload(sample["seller"]["nip"], sample, "prod")
    return doc


def invoke(*args):
    return CliRunner().invoke(app, ["--credentials", "openclaw", "--json", *args])


@pytest.mark.parametrize("args,action", [
    (["auth", "check"], "auth_check"),
    (["sync", "--from", "2026-06-27T22:00:00Z"], "sync"),
    (["invoice", "status", "DOC"], "invoice_status"),
])
def test_cli_routes_selected_profile_without_system_fallback(fixture_db, monkeypatch, args, action):
    doc = fixture_db
    requests = []
    def handle(request):
        requests.append(request)
        return {"verified": True}
    monkeypatch.setattr("asef.cli.request_ksef", handle)
    monkeypatch.setattr("asef.cli.KsefClient", lambda *_: pytest.fail("system fallback"))
    result = invoke(*(doc["id"] if arg == "DOC" else arg for arg in args))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"ok": True, "result": {"verified": True}}
    assert requests[0]["action"] == action
    assert requests[0]["nip"] == doc["profile_nip"] and requests[0]["environment"] == "prod"
    if action == "sync":
        assert requests[0]["startFrom"] == "2026-06-27T22:00:00Z"
    if action == "invoice_status":
        assert requests[0]["documentId"] == doc["id"]


def test_cli_send_requires_reviewed_hash_and_separate_prod_confirmation(fixture_db, monkeypatch):
    doc = fixture_db
    requests = []
    monkeypatch.setattr("asef.cli.request_ksef", lambda req: requests.append(req) or {"sent": False})
    cases = [[], ["--sha256", "a" * 64], ["--sha256", doc["xml_sha256"]],
             ["--sha256", doc["xml_sha256"], "--confirm-prod", "a" * 64]]
    for options in cases:
        result = invoke("invoice", "send", doc["id"], *options)
        assert result.exit_code == 1, result.output
        assert not requests
    result = invoke("invoice", "send", doc["id"], "--sha256", doc["xml_sha256"],
                    "--confirm-prod", doc["xml_sha256"])
    assert result.exit_code == 0
    assert requests[0]["confirmProd"] == requests[0]["sha256"] == doc["xml_sha256"]
    with Database() as db:
        assert db.get_document(doc["id"])["status"] == "draft"  # no implicit approval


def test_cli_cannot_write_token_to_another_store(fixture_db):
    result = invoke("auth", "set-token")
    assert result.exit_code == 1 and "Secret Store" in result.output


def test_isolated_directory_cannot_fall_through_to_live_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("ASEF_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("spawned"))
    with pytest.raises(ValueError, match="DEFAULT_DATA_DIR"):
        request_ksef({"action": "sync"})


@pytest.mark.parametrize("error_value", ["fictional-private", {"private": "fictional-private"}])
def test_subprocess_has_no_inherited_secrets_and_transport_does_not_leak_errors(monkeypatch, error_value):
    monkeypatch.delenv("ASEF_DATA_DIR", raising=False)
    monkeypatch.setenv("SOME_PROVIDER_TOKEN", "fictional-private")
    observed = {}
    def spawn(argv, **kwargs):
        observed.update(argv=argv, **kwargs)
        def communicate(data, timeout):
            assert json.loads(data)["action"] == "auth_check"
            return json.dumps({"ok": False, "error": error_value}), None
        return SimpleNamespace(communicate=communicate, returncode=1)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="^OPENCLAW_CLI_FAILED$"):
        request_ksef({"action": "auth_check"})
    assert "SOME_PROVIDER_TOKEN" not in observed["env"]
    assert "fictional-private" not in json.dumps(observed["argv"])
    assert observed["stderr"] == subprocess.DEVNULL and observed["start_new_session"] is True


def test_timeout_kills_process_group_and_never_retries(monkeypatch):
    monkeypatch.delenv("ASEF_DATA_DIR", raising=False)
    killed = []
    calls = []
    def communicate(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("node", 190)
        return "", None
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: SimpleNamespace(pid=54321, communicate=communicate))
    monkeypatch.setattr("asef.openclaw_cli.os.killpg", lambda pid, sig: killed.append(pid))
    with pytest.raises(RuntimeError, match="CHECK_STATUS_BEFORE_RETRY"):
        request_ksef({"action": "invoice_send"})
    assert killed == [54321] and len(calls) == 2
