import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import test from "node:test";
import { prepareRequest, runBridge } from "../runtime.mjs";

const TOKEN = "fictional-private-credential";
const config = {profiles: [{nip: "1234567890", environment: "prod", token: TOKEN}]};
const params = {action: "auth_check", nip: "1234567890", environment: "prod"};
const success = {ok: true, result: {authenticated: true, nip: params.nip, environment: "prod", method: "token"}};

function processStub(response, {stderr = "", exit = 0, hang = false} = {}) {
  const capture = {};
  const spawnProcess = (file, args, options) => {
    Object.assign(capture, {file, args, options});
    const child = new EventEmitter();
    child.stdout = new PassThrough(); child.stderr = new PassThrough();
    child.kill = () => { capture.killed = true; setImmediate(() => child.emit("close", null)); };
    let payload = "";
    child.stdin = new Writable({write(chunk, _encoding, next) { payload += chunk; next(); },
      final(next) { capture.payload = JSON.parse(payload); next();
        if (!hang) setImmediate(() => {
          child.stderr.write(stderr);
          child.stdout.write(typeof response === "string" ? response : JSON.stringify(response));
          child.emit("close", exit);
        });
      }});
    return child;
  };
  return {capture, spawnProcess};
}

test("token is in pipe only, never argv/env; output is allowlisted", async () => {
  const stub = processStub({...success, unexpectedToken: TOKEN, result: {...success.result, token: TOKEN}});
  let guards = 0;
  const result = await runBridge(config, params, {...stub, guard: () => guards++});
  assert.deepEqual(result, success);
  assert.equal(stub.capture.payload.token, TOKEN);
  assert.equal(JSON.stringify(stub.capture.args).includes(TOKEN), false);
  assert.equal(JSON.stringify(stub.capture.options).includes(TOKEN), false);
  assert.equal(stub.capture.options.shell, false);
  assert.deepEqual(stub.capture.args, ["-I", "-m", "asef.openclaw_bridge"]);
  assert.equal(guards, 2);
});

test("missing, duplicate, and unresolved SecretRefs fail without spawning", async () => {
  for (const bad of [{}, {profiles: [...config.profiles, ...config.profiles]},
    {profiles: [{...config.profiles[0], token: {source: "store", provider: "default", id: "DUMMY"}}]}]) {
    assert.equal((await runBridge(bad, params, {spawnProcess: () => assert.fail("spawn")})).ok, false);
  }
});

test("model cannot inject token, paths, arbitrary commands, or another profile", () => {
  for (const extra of [{token: TOKEN}, {pythonPath: "/bin/echo"}, {action: "export_secret"},
    {nip: "9999999999"}, {allow_send: true}]) {
    assert.throws(() => prepareRequest(config, {...params, ...extra}));
  }
});

test("send disabled by default and requires reviewed digest", () => {
  const send = {...params, action: "invoice_send", documentId: "12345678-1234-1234-1234-123456789012", sha256: "a".repeat(64)};
  assert.throws(() => prepareRequest(config, send));
  assert.equal(prepareRequest({...config, allowSend: true}, send).allow_send, true);
  assert.throws(() => prepareRequest({...config, allowSend: true}, {...send, sha256: undefined}));
});

test("stderr and malformed response containing credentials never reach caller", async () => {
  const result = await runBridge(config, params, processStub(TOKEN, {stderr: TOKEN}));
  assert.deepEqual(result, {ok: false, error: "INVALID_BRIDGE_RESPONSE"});
});

test("error response cannot smuggle private text", async () => {
  const result = await runBridge(config, params, processStub({ok: false, error: TOKEN, raw: TOKEN}, {exit: 1}));
  assert.deepEqual(result, {ok: false, error: "BRIDGE_ERROR"});
});

test("expired authority prevents process start", async () => {
  const result = await runBridge(config, params, {guard: () => { throw new Error(TOKEN); },
    spawnProcess: () => assert.fail("spawn")});
  assert.deepEqual(result, {ok: false, error: "AUTHORITY_OR_PROCESS_ERROR"});
});

test("timeout kills process and returns no private content", async () => {
  const stub = processStub(null, {hang: true});
  const result = await runBridge(config, params, {...stub, timeoutMs: 10});
  assert.equal(result.error, "TIMEOUT"); assert.equal(stub.capture.killed, true);
});

test("output size bounded", async () => {
  const stub = processStub("x".repeat(65537));
  assert.equal((await runBridge(config, params, stub)).error, "OUTPUT_LIMIT");
  assert.equal(stub.capture.killed, true);
});

test("aborted invocation does not spawn", async () => {
  const controller = new AbortController(); controller.abort();
  assert.equal((await runBridge(config, params, {signal: controller.signal,
    spawnProcess: () => assert.fail("spawn")})).error, "CANCELLED");
});
