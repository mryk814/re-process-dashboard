import { createServer } from "node:net";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { sharedReadOnlySpecs } from "../e2e/suite-inventory.mjs";

const root = process.cwd();
const playwrightCli = join(root, "node_modules", "@playwright", "test", "cli.js");
const seed = Number(process.env.PLAYWRIGHT_ORDER_GUARD_SEED ?? "628");

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") return reject(new Error("could not reserve a TCP port"));
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

function shuffled(entries, initialSeed) {
  const output = [...entries];
  let state = initialSeed >>> 0;
  for (let index = output.length - 1; index > 0; index -= 1) {
    state = (1664525 * state + 1013904223) >>> 0;
    const swap = state % (index + 1);
    [output[index], output[swap]] = [output[swap], output[index]];
  }
  return output;
}

async function run(label, specs, outputRoot) {
  const [apiPort, webPort] = await Promise.all([reservePort(), reservePort()]);
  if (apiPort === webPort) throw new Error(`duplicate order guard ports for ${label}`);
  const outputDir = join(outputRoot, label);
  mkdirSync(outputDir, { recursive: true });
  const result = spawnSync(
    process.execPath,
    [playwrightCli, "test", ...specs.map((spec) => `e2e/${spec}`), "--workers=1", "--retries=0"],
    {
      cwd: root,
      env: {
        ...process.env,
        PLAYWRIGHT_API_PORT: String(apiPort),
        PLAYWRIGHT_WEB_PORT: String(webPort),
        PLAYWRIGHT_OUTPUT_DIR: outputDir,
      },
      stdio: "inherit",
    },
  );
  return { label, specs, apiPort, webPort, code: result.status ?? 1, outputDir };
}

const outputRoot = join(root, "test-results", `e2e-order-guard-${Date.now()}-${process.pid}`);
mkdirSync(outputRoot, { recursive: true });
const variants = [
  ["single", [sharedReadOnlySpecs[0]]],
  ["reverse", [...sharedReadOnlySpecs].reverse()],
  ["shuffled", shuffled(sharedReadOnlySpecs, seed)],
];
const results = [];
for (const [label, specs] of variants) results.push(await run(label, specs, outputRoot));
const reportPath = join(outputRoot, "report.json");
writeFileSync(reportPath, `${JSON.stringify({ schema_version: "e2e-order-guard/v1", seed, results }, null, 2)}\n`);
process.stdout.write(`E2E order guard report: ${reportPath}\n`);
if (results.some((result) => result.code !== 0)) process.exitCode = 1;
