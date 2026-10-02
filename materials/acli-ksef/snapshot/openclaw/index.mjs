import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import manifest from "./openclaw.plugin.json" with {type: "json"};
import { runBridge } from "./runtime.mjs";
import { runCliAuthCheck } from "./cli-runtime.mjs";

export const parameters = {
  type: "object", additionalProperties: false, required: ["action", "nip", "environment"],
  properties: {
    action: {type: "string", enum: ["auth_check", "sync", "invoice_status", "invoice_send"]},
    nip: {type: "string", pattern: "^[0-9]{10}$"},
    environment: {type: "string", enum: ["test", "demo", "prod"]},
    startFrom: {type: "string", description: "First sync start date, ISO 8601 with UTC timezone, selected by the user."},
    documentId: {type: "string", description: "Existing ASEF document id; required for invoice actions."},
    sha256: {type: "string", pattern: "^[0-9a-f]{64}$", description: "Reviewed XML SHA-256; sending requires explicit user consent and enabled send policy."},
  },
};

export function createTool(api, context) {
    // Initial deployment is private owner WebChat only. Do not infer authority
    // from model parameters, quoted inbound JSON, cron, or a group session.
    if (context.senderIsOwner !== true || context.messageChannel !== "webchat"
        || context.requesterSenderId !== "gateway-owner" || context.sandboxed === true
        || typeof context.assertInvocationCurrent !== "function") return null;
    return {
      name: "asef_ksef", label: "ASEF / KSeF",
      description: "Use ASEF with its OpenClaw Secret Store credential. auth_check only tests login. sync requires user-selected start date on first use. invoice_send requires a reviewed, approved document, explicit send consent (separate for PROD), SHA-256 and enabled send policy. Never input a secret.",
      parameters, executionMode: "sequential",
      async execute(_id, params, signal) {
        const latest = context.getRuntimeConfig?.() ?? context.runtimeConfig;
        const config = latest?.plugins?.entries?.["asef-secretstore"]?.config ?? api.pluginConfig ?? {};
        const details = await runBridge(config, params, {signal, guard: context.assertInvocationCurrent});
        return {content: [{type: "text", text: JSON.stringify(details)}], details, isError: !details.ok};
      },
    };
}

export function registerCli(api) {
  api.registerCli(({program}) => {
    const root = program.command("asef-secretstore").description("Check ASEF KSeF authentication without displaying credentials");
    root.command("auth-check").requiredOption("--nip <nip>").requiredOption("--env <environment>")
      .action(async options => {
        const result = await runCliAuthCheck(api.config, options);
        process.stdout.write(JSON.stringify(result) + "\n");
        if (!result.ok) process.exitCode = 1;
      });
  }, {descriptors: [{name: "asef-secretstore", description: "Check ASEF KSeF authentication without displaying credentials", hasSubcommands: true,
    machineOutput: () => true}]});
}

const description = "Use ASEF with its OpenClaw Secret Store credential. auth_check only tests login. sync requires user-selected start date on first use. invoice_send requires a reviewed, approved document, explicit send consent (separate for PROD), SHA-256 and enabled send policy. Never input a secret.";
const entry = defineToolPlugin({id: "asef-secretstore", name: manifest.name,
  description: manifest.description, configSchema: manifest.configSchema,
  tools: tool => [tool({name: "asef_ksef", label: "ASEF / KSeF", description, parameters,
    factory: ({api, toolContext}) => createTool(api, toolContext)})]});
const registerTools = entry.register;
entry.register = api => {
  // The current authoring helper emits v1 factories. Register the same factory
  // through the supported v2 descriptor so live owner authority survives an
  // explicitly yielded continuation. Static authoring metadata is unchanged.
  const toolApi = Object.create(api);
  toolApi.registerTool = (factory, options) => {
    api.registerTool({contextVersion: 2, create: factory}, options);
  };
  registerTools(toolApi);
  registerCli(api);
};
export default entry;
