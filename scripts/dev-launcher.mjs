import { randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { resolveDevWorkspace } from "./dev-workspace.mjs";

function port(name, fallback) {
  const value = process.env[name] ?? fallback;
  if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error(`${name} must be a valid TCP port`);
  }
  return value;
}

const apiPort = port("WORKBENCH_DEV_API_PORT", "8765");
const webPort = port("WORKBENCH_DEV_WEB_PORT", "5180");
const workspace = resolveDevWorkspace({
  mainWorkspace: process.argv.includes("--main-workspace"),
});
const launchToken = randomBytes(32).toString("base64url");
const childEnvironment = {
  ...process.env,
  WORKBENCH_LAUNCH_TOKEN: launchToken,
  WORKBENCH_DEV_PROXY_TOKEN: launchToken,
  WORKBENCH_DEV_API_URL: `http://127.0.0.1:${apiPort}`,
  WORKBENCH_DEV_WEB_PORT: webPort,
  WORKBENCH_DB_PATH: workspace.database,
  WORKBENCH_DATA_LIBRARY_PATH: workspace.dataLibrary,
  WORKBENCH_WORKSPACE_KIND: workspace.source,
  PYTHONUTF8: "1",
};

if (process.argv.includes("--check")) {
  process.stdout.write(JSON.stringify({
    tokenBytes: 32,
    apiProtected: childEnvironment.WORKBENCH_LAUNCH_TOKEN === launchToken,
    viteProxyProtected: childEnvironment.WORKBENCH_DEV_PROXY_TOKEN === launchToken,
    apiUrl: childEnvironment.WORKBENCH_DEV_API_URL,
    webPort: childEnvironment.WORKBENCH_DEV_WEB_PORT,
    workspaceDatabase: childEnvironment.WORKBENCH_DB_PATH,
    workspaceDataLibrary: childEnvironment.WORKBENCH_DATA_LIBRARY_PATH,
    workspaceSource: workspace.source,
  }));
  process.exit(0);
}

process.stdout.write(
  `[workspace] database=${workspace.database}\n`
  + `[workspace] data-library=${workspace.dataLibrary}\n`,
);
const preflight = spawnSync(
  "uv",
  [
    "run",
    "python",
    "backend/scripts/workspace_check.py",
    "--database",
    workspace.database,
    "--json",
  ],
  {
    cwd: workspace.repositoryRoot,
    env: childEnvironment,
    encoding: "utf8",
  },
);
if (preflight.stdout) process.stdout.write(preflight.stdout);
if (preflight.stderr) process.stderr.write(preflight.stderr);
if (preflight.error) {
  process.stderr.write(`workspace preflightを実行できません: ${preflight.error.message}\n`);
  process.exit(1);
}
if (preflight.status !== 0) process.exit(preflight.status ?? 1);
if (process.argv.includes("--preflight-only")) process.exit(0);

const { default: concurrently } = await import("concurrently");
const { result } = concurrently(
  [
    {
      name: "api",
      command: `uv run python -m uvicorn main:app --app-dir backend/src --host 127.0.0.1 --port ${apiPort} --reload`,
      env: childEnvironment,
      prefixColor: "blue",
    },
    {
      name: "web",
      command: "npm run dev -w apps/web",
      env: childEnvironment,
      prefixColor: "cyan",
    },
  ],
  {
    killOthersOn: ["failure", "success"],
    prefix: "name",
  },
);

try {
  await result;
} catch {
  process.exitCode = 1;
}
