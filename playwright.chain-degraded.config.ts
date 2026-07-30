import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { defineConfig } from "@playwright/test";

const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 8876);
const webPort = Number(process.env.PLAYWRIGHT_WEB_PORT ?? 5201);
const database = join(tmpdir(), `material-workbench-chain-degraded-${process.pid}.db`);
const brokenEvaluation = resolve(
  "e2e",
  "fixtures",
  "broken-chain-evaluation.json",
);
const brokenTransform = resolve(
  "e2e",
  "fixtures",
  "broken-active-transforms.json",
);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "chain-degraded.spec.ts",
  timeout: 60_000,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: "chrome",
    headless: true,
  },
  webServer: [
    {
      command: `uv run python backend/scripts/acceptance/run_chain_degraded_e2e.py --db "${database}" --port ${apiPort} --broken-transform "${brokenTransform}" --broken-evaluation "${brokenEvaluation}"`,
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
