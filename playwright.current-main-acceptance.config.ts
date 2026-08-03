import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { defineConfig } from "@playwright/test";

const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 8891);
const webPort = Number(process.env.PLAYWRIGHT_WEB_PORT ?? 5211);
const runId = process.env.PLAYWRIGHT_E2E_RUN_ID ?? randomUUID();
const outputDir = process.env.PLAYWRIGHT_OUTPUT_DIR ?? join("test-results", `current-main-${runId}`);
const ownedRoot = join(tmpdir(), `decision-workbench-current-main-${runId}`);
const owned = {
  database: join(ownedRoot, "workspace.db"),
  dataLibrary: join(ownedRoot, "data-library"),
  modelStore: join(ownedRoot, "models"),
  profileStore: join(ownedRoot, "profiles"),
  taskStore: join(ownedRoot, "tasks"),
};
export default defineConfig({
  testDir: "./e2e",
  testMatch: [
    "current-main-acceptance-single-task.spec.ts",
    "current-main-acceptance-prediction-graph.spec.ts",
  ],
  timeout: 180_000,
  outputDir,
  retries: 0,
  workers: 1,
  fullyParallel: false,
  globalTeardown: "./e2e/current-main-acceptance-teardown.mjs",
  metadata: {
    currentMainOwnedRoot: ownedRoot,
    e2eRunId: runId,
  },
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: "chrome",
    headless: true,
  },
  webServer: [
    {
      command: [
        "uv run python e2e/helpers/run-current-main-acceptance-api.py",
        `--db "${owned.database}"`,
        `--data-library "${owned.dataLibrary}"`,
        `--model-store "${owned.modelStore}"`,
        `--profile-store "${owned.profileStore}"`,
        `--task-store "${owned.taskStore}"`,
        `--port ${apiPort}`,
      ].join(" "),
      port: apiPort,
      reuseExistingServer: false,
      env: { PYTHONPATH: resolve("backend", "src") },
    },
    {
      command: `npm run dev -w apps/web -- --host 127.0.0.1 --port ${webPort}`,
      port: webPort,
      reuseExistingServer: false,
      env: { VITE_API_URL: `http://127.0.0.1:${apiPort}` },
    },
  ],
});
