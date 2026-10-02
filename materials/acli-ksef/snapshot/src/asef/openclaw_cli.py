"""CLI transport: credentials stay inside the existing Node/Python bridge."""
from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any


def request_ksef(request: dict[str, Any]) -> dict[str, Any]:
    # The existing bridge uses Gateway's normal DB. Never silently switch an
    # isolated test/migration CLI back to the operator's live database.
    if os.environ.get("ASEF_DATA_DIR"):
        raise ValueError("OPENCLAW_REQUIRES_DEFAULT_DATA_DIR")
    script = Path(__file__).resolve().parents[2] / "openclaw" / "cli.mjs"
    if not script.is_file():
        raise RuntimeError("OPENCLAW_CLI_NOT_INSTALLED")
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    for key in ("HOME", "XDG_DATA_HOME"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    timeout = 640 if request.get("action") == "sync" else 190
    try:
        process = subprocess.Popen(
            ["/usr/bin/node", str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=env, start_new_session=True,
        )
    except OSError:
        raise RuntimeError("OPENCLAW_CLI_UNAVAILABLE") from None
    try:
        output, _ = process.communicate(json.dumps(request), timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        # Kill this invocation's entire process group, including the bridge;
        # no orphan send/sync may continue after the caller reports a timeout.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise RuntimeError("OPENCLAW_TIMEOUT_OR_CANCELLED_CHECK_STATUS_BEFORE_RETRY") from None
    try:
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise RuntimeError("INVALID_OPENCLAW_RESPONSE") from None
    if process.returncode != 0 or result.get("ok") is not True:
        allowed = {
            "CONFIG_OR_REQUEST_ERROR", "SECRETREF_UNAVAILABLE", "SECRETREF_OR_CONFIG_UNAVAILABLE",
            "SEND_DISABLED", "REVIEWED_HASH_REQUIRED", "PROD_CONFIRMATION_REQUIRED", "KSEF_ERROR",
            "NETWORK_ERROR", "INVALID_REQUEST_OR_STATE", "BRIDGE_ERROR", "CANCELLED", "TIMEOUT",
            "PROCESS_ERROR", "OUTPUT_LIMIT", "INVALID_BRIDGE_RESPONSE", "AUTHORITY_OR_PROCESS_ERROR",
        }
        error = result.get("error")
        if not isinstance(error, str) or error not in allowed:
            error = "OPENCLAW_CLI_FAILED"
        if error in {"TIMEOUT", "CANCELLED"}:
            error += "_CHECK_STATUS_BEFORE_RETRY"
        raise RuntimeError(error)
    if not isinstance(result.get("result"), dict):
        raise RuntimeError("INVALID_OPENCLAW_RESPONSE")
    return result["result"]
