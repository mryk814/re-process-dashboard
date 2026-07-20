import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

const apiPort = 8875;
const webPort = 5199;
const database = join(tmpdir(), `material-workbench-e2e-${process.pid}.db`);

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: "chrome",
    headless: true,
  },
  webServer: [
    {
      command: `uv run uvicorn main:app --app-dir backend/src --host 127.0.0.1 --port ${apiPort}`,
      port: apiPort,
      reuseExistingServer: false,
      env: { WORKBENCH_DB_PATH: database },
    },
    {
      command: `npm run dev -w apps/web -- --host 127.0.0.1 --port ${webPort}`,
      port: webPort,
      reuseExistingServer: false,
      env: { VITE_API_URL: `http://127.0.0.1:${apiPort}` },
    },
  ],
});
