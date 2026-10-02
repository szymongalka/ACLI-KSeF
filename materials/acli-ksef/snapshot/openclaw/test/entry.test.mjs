import assert from "node:assert/strict";
import test from "node:test";
import entry from "../index.mjs";

function registered() {
  const rows = [];
  entry.register({get runtime() { throw new Error("metadata must not access runtime"); },
    pluginConfig: {}, registerTool: (...args) => rows.push(args), registerCli() {}});
  return rows[0][0];
}

test("registration retains v2 live authority without touching runtime helpers", () => {
  assert.equal(registered().contextVersion, 2);
});

test("only authenticated private WebChat owner receives tool", () => {
  const factory = registered();
  const owner = {senderIsOwner: true, messageChannel: "webchat", requesterSenderId: "gateway-owner", assertInvocationCurrent() {}};
  assert.equal(factory.create(owner).name, "asef_ksef");
  for (const override of [{senderIsOwner: false}, {messageChannel: "telegram"},
    {requesterSenderId: "other"}, {assertInvocationCurrent: undefined}, {sandboxed: true}]) {
    assert.equal(factory.create({...owner, ...override}), null);
  }
  assert.equal(factory.create({}), null);
});

test("source configuration requires SecretRef and rejects plaintext", () => {
  const profile = {nip: "1234567890", environment: "prod",
    token: {source: "store", provider: "default", id: "FICTIONAL_TOKEN"}};
  assert.equal(entry.configSchema.safeParse({profiles: [profile]}).success, true);
  assert.equal(entry.configSchema.safeParse({profiles: [{...profile, token: "fictional-plaintext"}]}).success, false);
});
