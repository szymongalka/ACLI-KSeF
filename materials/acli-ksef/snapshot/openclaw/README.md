# ASEF Secret Store adapter

Native OpenClaw plugin, tested against OpenClaw **2026.9.6**, Node.js and the
existing Python ASEF installation. No new database, daemon, public HTTP route,
or additional runtime dependency is required. Source lives with ASEF in `openclaw/`.
Packaging uses the installer-required `esbuild` development dependency (locked in `package-lock.json`).

## Credential boundary

OpenClaw resolves `profiles.*.token` through the declared
`configContracts.secretInputs` contract. Only a native runtime receives the
resolved value; it is never a tool parameter or tool result. Source config
accepts a `store/default` SecretRef, not a plaintext token. The subprocess gets
the value through an anonymous stdin pipe, not argv, environment, a temporary
file, or a keyring write. Python keeps access/refresh tokens in invocation-local
memory. Core dumps are disabled in the Python subprocess. Raw stderr and
exception text are discarded. No secure erasure of immutable JS/Python strings
is claimed; the runtime and local operator remain trusted.

The child uses the existing fixed KSeF endpoints and TLS verification, with no
redirects. There is no endpoint parameter and no arbitrary shell/CLI interface.
Each invocation binds the credential to one explicit NIP/environment; document
ownership is checked before use. The adapter does not access the Secret Store
database or decode egress sentinels.

## Configuration

Install this directory using OpenClaw's managed plugin workflow, and merge only
the following entry into the existing Gateway configuration. Keep existing
allowlists and other plugins. Plugin install/reload is performed by the OpenClaw
system-management tool in the agent workflow, not by directly rewriting config.

```json
{
  "plugins": {
    "entries": {
      "asef-secretstore": {
        "enabled": true,
        "config": {
          "pythonPath": "/srv/asef/.venv/bin/python",
          "allowSend": false,
          "profiles": [{
            "nip": "1234567890",
            "environment": "test",
            "token": {"source": "store", "provider": "default", "id": "KSEF_TEST_TOKEN"}
          }]
        }
      }
    }
  }
}
```

Use the intended environment and a separately provisioned Secret Store entry
whose allowed host is the corresponding official KSeF API host. Do not copy the
illustrative NIP above to production. Missing, duplicate or unresolved credentials
fail closed without keyring/systemd fallback.

## Use

**Primary interface: ordinary ASEF CLI.** On the configured Gateway use
`/usr/local/bin/asef --json ...` (launcher for the Python CLI with
`--credentials openclaw`). Authentication, sync, invoice status and explicitly
approved send now use the same CLI as local drafting and previews. See
[commands and approval requirements](../docs/openclaw.md). `cli.mjs` loads fresh
source config and calls the existing supported SDK resolver and `runBridge`;
it does not require the tool factory or a Gateway restart. Plugin configuration
and its SecretRef contract must remain enabled. This is a trusted local-operator
interface, not a replacement for authenticating chat senders; agents must keep
the private-owner scope. `allowSend=false` remains enforced before secret access.
The Python CLI requires `--sha256` for send plus separate `--confirm-prod` in PROD.

The legacy optional chat tool below remains unchanged:

The `asef_ksef` tool is restricted to the authenticated `gateway-owner` in
private WebChat, using OpenClaw version-2 live invocation authority. Other
channels and sandboxed runs are deliberately not enabled in this first version.

- `auth_check`: explicit `nip` and `environment`; no invoice retrieval or send.
- `sync`: additionally `startFrom` (ISO 8601 UTC) on the first synchronization.
  Run only after the user chooses the time range; output contains counts only.
  The Python bridge uses `KsefExportClient` and `/invoices/exports` packages,
  not one request per invoice. It verifies encrypted and plaintext part sizes
  and SHA-256, XML hashes, ZIP membership and invoice counts before import.
  Downloads use HTTPS official `*.mf.gov.pl` hosts with no bearer token and no
  redirects. Dates use `PermanentStorage`; each role retains its own HWM cursor.
  Truncated exports are handled by the existing window-splitting sync loop.
  The `system` credential provider retains the older per-invoice path; the
  Gateway CLI launcher uses the export-package path through `openclaw`.
- `invoice_status`: `documentId`; recovers submission status and UPO through
  the existing ASEF service. Export the stored UPO through the local CLI.
- `invoice_send`: `documentId` and reviewed `sha256`. Disabled unless operator
  config explicitly enables `allowSend`. Even when enabled, the existing approved
  document/hash guards still apply, including separate PROD confirmation. A
  general integration request is not invoice-send consent. The adapter never
  creates approvals. Check status before any uncertain-result retry.

Local drafting, previews, approvals, search and exports still use the ASEF CLI.
The bare Python entry point requires `--credentials openclaw` to read Secret
Store; the Gateway `/usr/local/bin/asef` launcher supplies this option.
For a local operator-only login proof (sanitized JSON, no credential arguments):

```sh
openclaw asef-secretstore auth-check --nip 1234567890 --env test
```

The legacy `openclaw asef-secretstore` command exposes only auth-check. The
normal `asef` CLI additionally supports sync/status/send through the same bounded
bridge; neither interface supports secret export.
Standalone CLI registration receives source config, so the command explicitly
uses the public `resolveCommandSecretRefsViaGateway` SDK helper, restricted to
the selected profile's manifest-declared path. The helper may resolve that
SecretRef locally when the Gateway snapshot is incomplete; no direct Secret
Store access, secret output, exec-provider execution, or credential migration
is implemented by ASEF. Resolver failures are reduced to `SECRETREF_UNAVAILABLE`.
No background synchronization is installed. A timed-out sync may have imported a
partial batch; normal persisted cursors permit continuation. A send timeout must
be treated as uncertain and resolved with status before any resend.

## Tests and rollback

`npm test` runs Node's built-in test runner. ASEF's Python suite includes
`tests/test_openclaw_bridge.py` with a mocked cryptographic login, profile
isolation and existing-approval tests. Tests use fictional credentials and can
run with outbound sockets disabled. The deployment record must separately state
whether real login, sync and send have been verified.

Disable/remove only this plugin's config entry to roll back native access; keep
the Secret Store entry unless the user asks to remove it. The Python service's
optional `client_factory` leaves the original keyring/systemd CLI behavior intact.
Use narrow pre-change copies outside the data directory for source rollback.
