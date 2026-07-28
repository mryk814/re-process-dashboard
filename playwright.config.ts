import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 8875);
const webPort = Number(process.env.PLAYWRIGHT_WEB_PORT ?? 5199);
const brokenHeatTreatmentPackage = process.env.PLAYWRIGHT_BROKEN_TASK_PACKAGE;
/**
 * Starting both servers costs 25-30s per run, which dominates the loop while
 * repairing specs one at a time. Opt in to an already-running `npm run dev` for
 * that loop only.
 *
 * Off by default, and it must stay that way for the verification gate: specs
 * rewrite shared projects such as `default`, so a second run against the same
 * database fails (measured: 0 failures on a fresh database, 8 on a reused one).
 * Each default run gets its own database below.
 */
const reuseServer = process.env.PLAYWRIGHT_REUSE_SERVER === "1";
const ownsDatabase = !process.env.PLAYWRIGHT_DB_PATH && !reuseServer;
const database = process.env.PLAYWRIGHT_DB_PATH
  ?? join(tmpdir(), `material-workbench-e2e-${randomUUID()}.db`);
if (ownsDatabase) process.env.PLAYWRIGHT_OWNED_DB_PATH = database;

export default defineConfig({
  testDir: "./e2e",
  // chain-degraded.spec.ts needs the broken evaluation fixtures and ports of
  // playwright.chain-degraded.config.ts. Running it here only produces a
  // connection error against a server this config never starts.
  testIgnore: ["chain-degraded.spec.ts", "startup-diagnostic.spec.ts"],
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  globalTeardown: ownsDatabase ? "./e2e/global-teardown.mjs" : undefined,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: "chrome",
    headless: true,
  },
  webServer: [
    {
      command: `uv run python -m uvicorn main:app --app-dir backend/src --host 127.0.0.1 --port ${apiPort}`,
      port: apiPort,
      reuseExistingServer: reuseServer,
      env: {
        WORKBENCH_DB_PATH: database,
        ...(brokenHeatTreatmentPackage
          ? { MATERIAL_WORKBENCH_HEAT_TREATMENT_MODEL_PACKAGE: brokenHeatTreatmentPackage }
          : {}),
      },
    },
    {
      command: `npm run dev -w apps/web -- --host 127.0.0.1 --port ${webPort}`,
      port: webPort,
      reuseExistingServer: reuseServer,
      env: { VITE_API_URL: `http://127.0.0.1:${apiPort}` },
    },
  ],
});
