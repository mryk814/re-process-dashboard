import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { defineConfig } from "@playwright/test";
import { parallelDedicatedSpecs } from "./e2e/suite-inventory.mjs";

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
// Runner-provided IDs make every mutable E2E process observable. The default
// remains unique for direct local Playwright execution.
const e2eRunId = process.env.PLAYWRIGHT_E2E_RUN_ID ?? randomUUID();
const ownsDatabase = !process.env.PLAYWRIGHT_DB_PATH && !reuseServer;
const database = process.env.PLAYWRIGHT_DB_PATH
  ?? join(tmpdir(), `decision-workbench-e2e-${e2eRunId}.db`);
if (ownsDatabase) process.env.PLAYWRIGHT_OWNED_DB_PATH = database;
const ownsModelStore = !process.env.PLAYWRIGHT_MODEL_STORE_PATH && !reuseServer;
const modelStore = process.env.PLAYWRIGHT_MODEL_STORE_PATH
  ?? join(tmpdir(), `decision-workbench-e2e-models-${e2eRunId}`);
if (ownsModelStore) process.env.PLAYWRIGHT_OWNED_MODEL_STORE_PATH = modelStore;
const ownsProfileStore = !process.env.PLAYWRIGHT_PROFILE_STORE_PATH && !reuseServer;
const profileStore = process.env.PLAYWRIGHT_PROFILE_STORE_PATH
  ?? join(tmpdir(), `decision-workbench-e2e-profiles-${e2eRunId}`);
if (ownsProfileStore) process.env.PLAYWRIGHT_OWNED_PROFILE_STORE_PATH = profileStore;
const ownsTaskStore = !process.env.PLAYWRIGHT_TASK_STORE_PATH && !reuseServer;
const taskStore = process.env.PLAYWRIGHT_TASK_STORE_PATH
  ?? join(tmpdir(), `decision-workbench-e2e-tasks-${e2eRunId}`);
if (ownsTaskStore) process.env.PLAYWRIGHT_OWNED_TASK_STORE_PATH = taskStore;
const outputDir = process.env.PLAYWRIGHT_OUTPUT_DIR ?? "test-results";
const ciDiagnostics = process.env.PLAYWRIGHT_CI_DIAGNOSTICS === "1";
const includeParallelDedicated = process.env.PLAYWRIGHT_INCLUDE_PARALLEL_DEDICATED === "1";
const reporter = ciDiagnostics
  ? [
      ["list"],
      ["html", {
        outputFolder: process.env.PLAYWRIGHT_HTML_OUTPUT_DIR ?? join(outputDir, "html"),
        open: "never",
      }],
      ["blob", {
        outputDir: process.env.PLAYWRIGHT_BLOB_OUTPUT_DIR ?? join(outputDir, "blob"),
      }],
      ["junit", {
        outputFile: process.env.PLAYWRIGHT_JUNIT_OUTPUT_FILE ?? join(outputDir, "junit.xml"),
      }],
    ]
  : undefined;
if (ownsDatabase || ownsModelStore || ownsProfileStore || ownsTaskStore) {
  process.env.PLAYWRIGHT_CLEANUP_REPORT_PATH = resolve(
    outputDir,
    `owned-e2e-cleanup-${randomUUID()}.jsonl`,
  );
}

export default defineConfig({
  testDir: "./e2e",
  // chain-degraded.spec.ts needs the broken evaluation fixtures and ports of
  // playwright.chain-degraded.config.ts. Running it here only produces a
  // connection error against a server this config never starts.
  testIgnore: [
    // Node contract tests live beside the specs but belong to `node --test`,
    // never to either Playwright process.
    "**/*.test.mjs",
    "chain-degraded.spec.ts",
    "startup-diagnostic.spec.ts",
    "sample-gallery.spec.ts",
    "current-main-acceptance-single-task.spec.ts",
    "current-main-acceptance-prediction-graph.spec.ts",
    ...(includeParallelDedicated ? [] : parallelDedicatedSpecs),
  ],
  timeout: 45_000,
  outputDir,
  reporter,
  metadata: {
    e2eCleanupReportPath: process.env.PLAYWRIGHT_CLEANUP_REPORT_PATH,
    e2eOwnedPaths: {
      database: ownsDatabase ? database : undefined,
      modelStore: ownsModelStore ? modelStore : undefined,
      profileStore: ownsProfileStore ? profileStore : undefined,
      taskStore: ownsTaskStore ? taskStore : undefined,
    },
    e2eRunId,
  },
  fullyParallel: false,
  workers: 1,
  globalTeardown: (ownsDatabase || ownsModelStore || ownsProfileStore || ownsTaskStore)
    ? "./e2e/global-teardown.mjs"
    : undefined,
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
        WORKBENCH_MODEL_STORE_PATH: modelStore,
        WORKBENCH_PROFILE_STORE_PATH: profileStore,
        WORKBENCH_TASK_STORE_PATH: taskStore,
        WORKBENCH_DEMO_SEED: "all",
        ...(brokenHeatTreatmentPackage
          ? { DECISION_WORKBENCH_HEAT_TREATMENT_MODEL_PACKAGE: brokenHeatTreatmentPackage }
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
