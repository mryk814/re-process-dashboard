import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 8875);
const webPort = Number(process.env.PLAYWRIGHT_WEB_PORT ?? 5199);
const ownsDatabase = !process.env.PLAYWRIGHT_DB_PATH;
const database = process.env.PLAYWRIGHT_DB_PATH
  ?? join(tmpdir(), `decision-workbench-sample-gallery-${randomUUID()}.db`);
if (ownsDatabase) process.env.PLAYWRIGHT_OWNED_DB_PATH = database;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "sample-gallery.spec.ts",
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
      reuseExistingServer: false,
      env: {
        WORKBENCH_DB_PATH: database,
      },
    },
    {
      command: `npm run dev -w apps/web -- --host 127.0.0.1 --port ${webPort}`,
      port: webPort,
      reuseExistingServer: false,
      env: { VITE_API_URL: `http://127.0.0.1:${apiPort}` },
    },
  ],
});
