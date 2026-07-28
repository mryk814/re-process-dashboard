import { spawnSync } from "node:child_process";
import { join } from "node:path";

const root = process.cwd();
const playwrightCli = join(
  root,
  "node_modules",
  "@playwright",
  "test",
  "cli.js",
);
const apiPort = process.env.PLAYWRIGHT_API_PORT ?? "9121";
const webPort = process.env.PLAYWRIGHT_WEB_PORT ?? "5441";

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

run(
  "Owned E2E database cleanup policy",
  process.execPath,
  ["--test", "e2e/owned-database-cleanup.test.mjs"],
);
run(
  "API offline, retry, and accessibility",
  process.execPath,
  [
    playwrightCli,
    "test",
    "e2e/api-offline.spec.ts",
    "e2e/accessibility-smoke.spec.ts",
  ],
  {
    PLAYWRIGHT_API_PORT: apiPort,
    PLAYWRIGHT_WEB_PORT: webPort,
  },
);
run(
  "Workspace startup diagnostic without API",
  process.execPath,
  [
    playwrightCli,
    "test",
    "--config",
    "playwright.startup-diagnostic.config.ts",
  ],
  {
    PLAYWRIGHT_WEB_PORT: "5453",
  },
);
run(
  "Task unavailable",
  process.execPath,
  [join(root, "scripts", "run-degraded-task-e2e.mjs")],
  {
    PLAYWRIGHT_API_PORT: apiPort,
    PLAYWRIGHT_WEB_PORT: webPort,
  },
);
run(
  "Workspace catalog conflict",
  "uv",
  [
    "run",
    "python",
    "-m",
    "pytest",
    "backend/tests/test_catalog_failure_e2e.py",
    "-q",
  ],
);
