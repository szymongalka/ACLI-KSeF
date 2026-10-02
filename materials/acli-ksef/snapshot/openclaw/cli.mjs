// Private transport behind `asef --credentials openclaw`, not a secret CLI.
// Fresh source configuration and the supported resolver; no Gateway reload.
import { runCliRequest } from "./cli-runtime.mjs";

const output = process.stdout.write.bind(process.stdout);
const discard = (...args) => {
  const callback = args.at(-1);
  if (typeof callback === "function") callback();
  return true;
};
process.stdout.write = discard;
process.stderr.write = discard;
let result;
try {
  if (process.env.ASEF_DATA_DIR) throw new Error();
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk.toString("utf8");
    if (Buffer.byteLength(input) > 8192) throw new Error();
  }
  const request = JSON.parse(input);
  const {loadConfig} = await import("openclaw/plugin-sdk/config-runtime");
  result = await runCliRequest(loadConfig(), request);
} catch {
  result = {ok: false, error: "SECRETREF_OR_CONFIG_UNAVAILABLE"};
}
output(JSON.stringify(result) + "\n");
process.exitCode = result.ok ? 0 : 1;
