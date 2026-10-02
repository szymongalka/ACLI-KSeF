import assert from "node:assert/strict";
import test from "node:test";
import { runCliAuthCheck, runCliRequest } from "../cli-runtime.mjs";

const tokenRef = {source: "store", provider: "default", id: "FICTIONAL_TOKEN"};
const options = {nip: "1234567890", env: "prod"};
function source() {
  return {plugins: {entries: {"asef-secretstore": {enabled: true, config: {allowSend: false,
    profiles: [{nip: "9999999999", environment: "test", token: tokenRef},
      {nip: options.nip, environment: options.env, token: tokenRef}]}}}}};
}
const path = "plugins.entries.asef-secretstore.config.profiles[1].token";

test("CLI resolves only selected profile then bridges auth, preserving source refs", async () => {
  const config = source();
  let bridgeCalls = 0;
  const result = await runCliAuthCheck(config, options, {
    async resolveSecrets(params) {
      assert.deepEqual([...params.allowedPaths], [path]);
      assert.deepEqual([...params.targetIds], ["plugins.entries.asef-secretstore.config.profiles.*.token"]);
      assert.equal(params.allowLocalExecSecretRefs, false);
      assert.equal(params.mode, "enforce_resolved");
      const resolvedConfig = structuredClone(config);
      resolvedConfig.plugins.entries["asef-secretstore"].config.profiles[1].token = "fictional-sensitive-token";
      return {resolvedConfig, targetStatesByPath: {[path]: "resolved_gateway"}, hadUnresolvedTargets: false};
    },
    async bridge(runtime, request) {
      bridgeCalls++;
      assert.deepEqual(runtime.profiles[0].token, tokenRef);
      assert.equal(runtime.profiles[1].token, "fictional-sensitive-token");
      assert.equal(runtime.allowSend, false);
      assert.deepEqual(request, {action: "auth_check", nip: options.nip, environment: "prod"});
      return {ok: true, result: {authenticated: true}};
    },
  });
  assert.equal(result.ok, true);
  assert.equal(bridgeCalls, 1);
  assert.deepEqual(config.plugins.entries["asef-secretstore"].config.profiles[1].token, tokenRef);
  assert.doesNotMatch(JSON.stringify(result), /fictional-sensitive-token/);
});

test("disabled, missing, duplicate and invalid profiles never resolve credentials", async () => {
  const variants = [source(), source(), source(), source()];
  variants[0].plugins.entries["asef-secretstore"].enabled = false;
  variants[1].plugins.entries["asef-secretstore"].config.profiles.pop();
  variants[2].plugins.entries["asef-secretstore"].config.profiles.push(variants[2].plugins.entries["asef-secretstore"].config.profiles[1]);
  variants[3].plugins.entries["asef-secretstore"].config.profiles[1].token = {source: "exec", provider: "default", id: "FICTIONAL"};
  for (const config of variants) {
    let calls = 0;
    const result = await runCliAuthCheck(config, options, {resolveSecrets: async () => { calls++; }, bridge: async () => { calls++; }});
    assert.deepEqual(result, {ok: false, error: "CONFIG_OR_REQUEST_ERROR"});
    assert.equal(calls, 0);
  }
});

test("resolver errors never expose diagnostics or run the bridge", async () => {
  let calls = 0;
  const result = await runCliAuthCheck(source(), options, {
    resolveSecrets: async () => { throw new Error("fictional-sensitive-token"); },
    bridge: async () => { calls++; },
  });
  assert.deepEqual(result, {ok: false, error: "SECRETREF_UNAVAILABLE"});
  assert.equal(calls, 0);
});

test("inactive, unresolved and mismatched resolution states cannot authenticate", async () => {
  for (const states of [{}, {[path]: "inactive_surface"}, {[path]: "unresolved"}, {"other.path": "resolved_local"}]) {
    let calls = 0;
    const result = await runCliAuthCheck(source(), options, {
      resolveSecrets: async () => ({resolvedConfig: source(), targetStatesByPath: states, hadUnresolvedTargets: false}),
      bridge: async () => { calls++; },
    });
    assert.deepEqual(result, {ok: false, error: "SECRETREF_UNAVAILABLE"});
    assert.equal(calls, 0);
  }
});

const documentId = "12345678-1234-1234-1234-123456789012";
const hash = "a".repeat(64);
const send = {action: "invoice_send", nip: options.nip, environment: "prod", documentId, sha256: hash, confirmProd: hash};

test("send-disabled, missing hash, missing PROD consent fail before secret resolution", async () => {
  for (const [enabled, changes, error] of [
    [false, {}, "SEND_DISABLED"],
    [true, {sha256: undefined}, "REVIEWED_HASH_REQUIRED"],
    [true, {confirmProd: undefined}, "PROD_CONFIRMATION_REQUIRED"],
    [true, {confirmProd: "b".repeat(64)}, "PROD_CONFIRMATION_REQUIRED"],
  ]) {
    const config = source();
    config.plugins.entries["asef-secretstore"].config.allowSend = enabled;
    const result = await runCliRequest(config, {...send, ...changes}, {
      resolveSecrets() { assert.fail("secret access"); }, bridge() { assert.fail("effect"); },
    });
    assert.deepEqual(result, {ok: false, error});
  }
});

test("full CLI routes sync, status and explicitly confirmed send through existing bridge", async () => {
  for (const request of [
    {action: "sync", nip: options.nip, environment: "prod", startFrom: "2026-06-27T22:00:00Z"},
    {action: "invoice_status", nip: options.nip, environment: "prod", documentId}, send,
  ]) {
    const config = source();
    config.plugins.entries["asef-secretstore"].config.allowSend = request.action === "invoice_send";
    let count = 0;
    const result = await runCliRequest(config, request, {
      async resolveSecrets(params) {
        assert.deepEqual([...params.allowedPaths], [path]);
        const resolvedConfig = structuredClone(config);
        resolvedConfig.plugins.entries["asef-secretstore"].config.profiles[1].token = "fictional-private";
        return {resolvedConfig, targetStatesByPath: {[path]: "resolved_local"}, hadUnresolvedTargets: false};
      },
      async bridge(runtime, actual) {
        count++;
        const {confirmProd, ...expected} = request;
        assert.deepEqual(actual, expected);
        assert.equal(runtime.allowSend, request.action === "invoice_send");
        return {ok: true, result: {mocked: true}};
      },
    });
    assert.equal(count, 1);
    assert.equal(result.ok, true);
    assert.doesNotMatch(JSON.stringify(result), /fictional-private/);
  }
});

test("plaintext source credentials and invalid CLI requests are refused", async () => {
  const config = source();
  const dependencies = {resolveSecrets() { assert.fail("secret access"); }, bridge() { assert.fail("effect"); }};
  for (const change of [{action: "unknown"}, {token: "plaintext"}, {documentId: "invalid"},
    {action: "sync", startFrom: "2026-06-27"}, {action: "auth_check", sha256: hash}]) {
    assert.equal((await runCliRequest(config, {...send, ...change}, dependencies)).ok, false);
  }
  config.plugins.entries["asef-secretstore"].config.profiles[1].token = "plaintext";
  assert.equal((await runCliAuthCheck(config, options, dependencies)).ok, false);
});
