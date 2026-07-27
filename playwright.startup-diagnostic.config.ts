import { defineConfig } from "@playwright/test";

const webPort = Number(process.env.PLAYWRIGHT_WEB_PORT ?? "5453");
const diagnostic = JSON.stringify({
  schema_version: "startup-diagnostic/v1",
  source: "workspace_preflight",
  log_path: "npm run dev の workspace preflight 出力",
  recovery_route: "docs/decisions/startup-failure-boundaries.md",
  report: {
    status: "error",
    findings: [{
      severity: "error",
      stage: "catalog",
      resource_id: "welding-package-v1",
      cause: "task_contract_digestが一致しません",
      impact: "catalog bootstrapが停止します",
      recovery_hint: "新しいPackage versionとして再生成してください",
    }],
  },
});

export default defineConfig({
  testDir: "./e2e",
  testMatch: "startup-diagnostic.spec.ts",
  timeout: 20_000,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: "chrome",
    headless: true,
  },
  webServer: {
    command: `npm run dev -w apps/web -- --host 127.0.0.1 --port ${webPort}`,
    port: webPort,
    reuseExistingServer: false,
    env: {
      VITE_API_URL: "http://127.0.0.1:1",
      WORKBENCH_STARTUP_DIAGNOSTIC: diagnostic,
    },
  },
});
