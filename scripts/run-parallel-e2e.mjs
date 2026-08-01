import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { resolveE2ePorts } from "./e2e-port-allocation.mjs";

const root = process.cwd();
const playwrightCli = join(root, "node_modules", "@playwright", "test", "cli.js");

function run(label, executable, args, environment = {}) {
  process.stdout.write(`\n== ${label} ==\n`);
  const result = spawnSync(executable, args, {
    cwd: root,
    env: { ...process.env, ...environment },
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

const { apiPort, webPort } = await resolveE2ePorts();
run(
  "Shared read-only E2E with Playwright workers",
  process.execPath,
  [playwrightCli, "test", "--config", "playwright.read-only.config.ts"],
  { PLAYWRIGHT_API_PORT: String(apiPort), PLAYWRIGHT_WEB_PORT: String(webPort) },
);
run(
  "Isolated mutable E2E with fresh spec processes",
  process.execPath,
  [join(root, "scripts", "run-isolated-e2e.mjs")],
);
