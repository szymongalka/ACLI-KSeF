import { spawn } from "node:child_process";

const ACTIONS = new Set(["auth_check", "sync", "invoice_status", "invoice_send"]);
const FAILURES = new Set(["KSEF_ERROR", "NETWORK_ERROR", "INVALID_REQUEST_OR_STATE", "BRIDGE_ERROR"]);
const MAX_OUTPUT = 65536;

export function prepareRequest(config, params) {
  if (!params || Object.keys(params).some(key => ![
    "action", "nip", "environment", "startFrom", "documentId", "sha256",
  ].includes(key)) || !ACTIONS.has(params.action) || !/^[0-9]{10}$/.test(params.nip)
      || !["test", "demo", "prod"].includes(params.environment)) {
    throw new Error("Invalid ASEF action/profile");
  }
  const matches = (config.profiles ?? []).filter(p => p.nip === params.nip && p.environment === params.environment);
  if (matches.length !== 1) throw new Error("ASEF profile missing or ambiguous");
  const token = matches[0].token;
  if (typeof token !== "string" || !token.trim() || token.length > 16384) {
    throw new Error("ASEF SecretRef has not been resolved by OpenClaw");
  }
  if (params.action.startsWith("invoice_") && !/^[0-9a-f-]{36}$/.test(params.documentId ?? "")) {
    throw new Error("Invalid ASEF document id");
  }
  if (params.action === "invoice_send" && (config.allowSend !== true || !/^[0-9a-f]{64}$/.test(params.sha256 ?? ""))) {
    throw new Error("ASEF sending is disabled or lacks the reviewed SHA-256");
  }
  if (params.startFrom !== undefined && (typeof params.startFrom !== "string" || params.startFrom.length > 40)) {
    throw new Error("Invalid ASEF synchronization date");
  }
  return {action: params.action, nip: params.nip, environment: params.environment, token,
    ...(params.startFrom !== undefined ? {start_from: params.startFrom} : {}),
    ...(params.documentId !== undefined ? {document_id: params.documentId} : {}),
    ...(params.sha256 !== undefined ? {sha256: params.sha256} : {}),
    allow_send: config.allowSend === true};
}

function sanitizeResult(value, request) {
  if (value?.ok !== true) {
    return {ok: false, error: FAILURES.has(value?.error) ? value.error : "BRIDGE_ERROR",
      ...(Number.isInteger(value?.status_code) ? {status_code: value.status_code} : {})};
  }
  const result = value.result;
  if (request.action === "auth_check") {
    if (result?.authenticated !== true || result.nip !== request.nip || result.environment !== request.environment
        || result.method !== "token") throw new Error("Invalid authentication response");
    return {ok: true, result: {authenticated: true, nip: request.nip, environment: request.environment, method: "token"}};
  }
  if (request.action === "sync") {
    const clean = {};
    for (const key of ["imported", "existing", "related_skipped", "metadata_mismatches", "unsupported_skipped"]) {
      if (!Number.isSafeInteger(result?.[key]) || result[key] < 0) throw new Error("Invalid sync response");
      clean[key] = result[key];
    }
    return {ok: true, result: clean};
  }
  const doc = result?.document;
  if (!doc || doc.id !== request.document_id || doc.profile_nip !== request.nip
      || doc.environment !== request.environment || !/^[0-9a-f]{64}$/.test(doc.xml_sha256 ?? "")
      || !["draft", "approved", "preparing", "sending", "submitted", "accepted", "rejected"].includes(doc.status)) {
    throw new Error("Invalid ASEF document response");
  }
  const clean = {document: {id: doc.id, profile_nip: doc.profile_nip, environment: doc.environment,
    status: doc.status, xml_sha256: doc.xml_sha256,
    ksef_number: typeof doc.ksef_number === "string" && /^[0-9]{10}-[0-9]{8}-[A-Za-z0-9-]{1,40}$/.test(doc.ksef_number)
      ? doc.ksef_number : null}};
  if (request.action === "invoice_status") {
    clean.ksef_status_code = Number.isInteger(result.ksef_status_code) ? result.ksef_status_code : null;
    clean.session_close_failed = result.session_close_failed === true;
    clean.preflight_recovered = result.preflight_recovered === true;
  }
  return {ok: true, result: clean};
}

export async function runBridge(config, params, {signal, guard = () => {}, spawnProcess = spawn,
  timeoutMs = params.action === "sync" ? 600000 : 150000} = {}) {
  let request;
  try { request = prepareRequest(config, params); }
  catch { return {ok: false, error: "CONFIG_OR_REQUEST_ERROR"}; }
  if (signal?.aborted) return {ok: false, error: "CANCELLED"};
  const python = config.pythonPath ?? "/srv/asef/.venv/bin/python";
  if (typeof python !== "string" || !python.startsWith("/")) return {ok: false, error: "CONFIG_OR_REQUEST_ERROR"};
  // Deliberate minimal environment. No inherited provider secrets or sentinels,
  // no PYTHONPATH/user-site injection. The ASEF package is installed in the venv.
  const env = {LANG: "C.UTF-8"};
  for (const key of ["HOME", "XDG_DATA_HOME"]) if (process.env[key]) env[key] = process.env[key];
  return new Promise(resolve => {
    let child, timer, settled = false, output = "", size = 0;
    const done = result => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      request.token = "";
      output = "";
      resolve(result);
    };
    const abort = () => { child?.kill("SIGKILL"); done({ok: false, error: "CANCELLED"}); };
    try {
      guard(); // Live owner/plugin authority immediately before the effect.
      child = spawnProcess(python, ["-I", "-m", "asef.openclaw_bridge"], {
        shell: false, stdio: ["pipe", "pipe", "pipe"], env,
      });
      timer = setTimeout(() => {
        child.kill("SIGKILL"); done({ok: false, error: "TIMEOUT", timedOut: true, timeoutMs});
      }, timeoutMs);
      signal?.addEventListener("abort", abort, {once: true});
      child.on("error", () => done({ok: false, error: "PROCESS_ERROR"}));
      child.stdin.on("error", () => { child.kill("SIGKILL"); done({ok: false, error: "PROCESS_ERROR"}); });
      child.stdout.on("data", chunk => {
        size += chunk.length;
        if (size > MAX_OUTPUT) { child.kill("SIGKILL"); done({ok: false, error: "OUTPUT_LIMIT"}); }
        else output += chunk.toString("utf8");
      });
      child.stderr.on("data", chunk => {
        // Discard raw stderr; even an unexpected traceback must remain private.
        size += chunk.length;
        if (size > MAX_OUTPUT) { child.kill("SIGKILL"); done({ok: false, error: "OUTPUT_LIMIT"}); }
      });
      child.on("close", code => {
        if (settled) return;
        try {
          const value = JSON.parse(output);
          if (code !== 0 && value.ok === true) throw new Error("Process failed");
          done(sanitizeResult(value, request));
        } catch { done({ok: false, error: "INVALID_BRIDGE_RESPONSE"}); }
      });
      guard();
      child.stdin.end(JSON.stringify(request));
    } catch {
      child?.kill("SIGKILL"); done({ok: false, error: "AUTHORITY_OR_PROCESS_ERROR"});
    }
  });
}
