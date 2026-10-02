import { runBridge } from "./runtime.mjs";

// CLI registration reads source config; unlike a prepared Gateway tool call it
// must explicitly resolve the selected manifest-declared credential via SDK.
export async function runCliRequest(config, request, {
  resolveSecrets = async params => {
    const { resolveCommandSecretRefsViaGateway } = await import("openclaw/plugin-sdk/runtime");
    return resolveCommandSecretRefsViaGateway(params);
  },
  bridge = runBridge,
  signal,
} = {}) {
  try {
    if (!request || Object.keys(request).some(key => ![
      "action", "nip", "environment", "documentId", "sha256", "confirmProd", "startFrom",
    ].includes(key)) || !["auth_check", "sync", "invoice_status", "invoice_send"].includes(request.action)
        || !/^[0-9]{10}$/.test(request.nip) || !["test", "demo", "prod"].includes(request.environment)) {
      return {ok: false, error: "CONFIG_OR_REQUEST_ERROR"};
    }
    if (request.action.startsWith("invoice_") && !/^[0-9a-f-]{36}$/.test(request.documentId ?? "")) {
      return {ok: false, error: "CONFIG_OR_REQUEST_ERROR"};
    }
    if (request.startFrom !== undefined && (request.action !== "sync"
        || typeof request.startFrom !== "string" || request.startFrom.length > 40
        || !/(Z|\+00:00)$/.test(request.startFrom) || !Number.isFinite(Date.parse(request.startFrom)))) {
      return {ok: false, error: "CONFIG_OR_REQUEST_ERROR"};
    }
    if (request.action !== "invoice_send" && (request.sha256 !== undefined || request.confirmProd !== undefined)) {
      return {ok: false, error: "CONFIG_OR_REQUEST_ERROR"};
    }
    const entry = config?.plugins?.entries?.["asef-secretstore"];
    const profiles = entry?.config?.profiles ?? [];
    const matches = profiles.map((profile, index) => ({profile, index}))
      .filter(({profile}) => profile.nip === request.nip && profile.environment === request.environment);
    if (entry?.enabled !== true || matches.length !== 1) return {ok: false, error: "CONFIG_OR_REQUEST_ERROR"};
    // Reject a disabled send before resolving any credential. CLI cannot enable it.
    if (request.action === "invoice_send") {
      if (entry.config.allowSend !== true) return {ok: false, error: "SEND_DISABLED"};
      if (!/^[0-9a-f]{64}$/.test(request.sha256 ?? "")) return {ok: false, error: "REVIEWED_HASH_REQUIRED"};
      if (request.environment === "prod" && request.confirmProd !== request.sha256) {
        return {ok: false, error: "PROD_CONFIRMATION_REQUIRED"};
      }
    }
    const {profile, index} = matches[0];
    let runtimeConfig;
    {
      const ref = profile.token;
      if (ref?.source !== "store" || ref.provider !== "default" || !/^[A-Z][A-Z0-9_]*$/.test(ref.id)) {
        return {ok: false, error: "CONFIG_OR_REQUEST_ERROR"};
      }
      const path = `plugins.entries.asef-secretstore.config.profiles[${index}].token`;
      const resolved = await resolveSecrets({config, commandName: `asef ${request.action}`,
        targetIds: new Set(["plugins.entries.asef-secretstore.config.profiles.*.token"]),
        allowedPaths: new Set([path]), mode: "enforce_resolved",
        allowLocalExecSecretRefs: false, gatewaySecretResolveTimeoutMs: 10000});
      if (resolved.hadUnresolvedTargets || !["resolved_gateway", "resolved_local"].includes(resolved.targetStatesByPath?.[path])) {
        return {ok: false, error: "SECRETREF_UNAVAILABLE"};
      }
      runtimeConfig = resolved.resolvedConfig.plugins.entries["asef-secretstore"].config;
    }
    // Policy comes from source config, not a potentially stale resolved snapshot.
    runtimeConfig = {...runtimeConfig, allowSend: entry.config.allowSend === true,
      pythonPath: entry.config.pythonPath};
    const {confirmProd, ...params} = request;
    return await bridge(runtimeConfig, params, {signal});
  } catch {
    // Resolver diagnostics/exception messages must not enter CLI output.
    return {ok: false, error: "SECRETREF_UNAVAILABLE"};
  }
}

// Compatibility for the already installed operator auth-check command.
export async function runCliAuthCheck(config, options, dependencies) {
  return runCliRequest(config, {action: "auth_check", nip: options.nip, environment: options.env}, dependencies);
}
