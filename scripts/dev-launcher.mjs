import { randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { resolveDevWorkspace } from "./dev-workspace.mjs";

function port(name, fallback) {
  const value = process.env[name] ?? fallback;
  if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error(`${name} must be a valid TCP port`);
  }
  return value;
}

function isAvailable(candidate) {
  return new Promise((resolve) => {
    const server = createServer();
    server.unref();
    server.once("error", () => resolve(false));
    server.listen(Number(candidate), "127.0.0.1", () => {
      server.close(() => resolve(true));
    });
  });
}

async function availablePort(name, fallback, excluded = new Set()) {
  const requested = Number(port(name, fallback));
  for (let candidate = requested; candidate <= Math.min(requested + 50, 65535); candidate += 1) {
    if (!excluded.has(candidate) && await isAvailable(candidate)) {
      if (candidate !== requested) {
        process.stdout.write(
          `[port] ${requested} は使用中のため ${name}=${candidate} で起動します。\n`,
        );
      }
      return String(candidate);
    }
  }
  throw new Error(
    `${name}の空きportが見つかりません。`
    + `PowerShellで $env:${name}="<空きport>"; npm run dev を実行してください。`,
  );
}

const checkOnly = process.argv.includes("--check");
const resolvePortsOnly = process.argv.includes("--resolve-ports");
const apiPort = checkOnly
  ? port("WORKBENCH_DEV_API_PORT", "8765")
  : await availablePort("WORKBENCH_DEV_API_PORT", "8765");
const webPort = checkOnly
  ? port("WORKBENCH_DEV_WEB_PORT", "5180")
  : await availablePort(
    "WORKBENCH_DEV_WEB_PORT",
    "5180",
    new Set([Number(apiPort)]),
  );
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
  VITE_API_URL: `http://127.0.0.1:${webPort}`,
  WORKBENCH_DB_PATH: workspace.database,
  WORKBENCH_DATA_LIBRARY_PATH: workspace.dataLibrary,
  WORKBENCH_WORKSPACE_KIND: workspace.source,
  WORKBENCH_DEFER_RESOURCES:
    process.env.WORKBENCH_DEFER_RESOURCES
    ?? (workspace.source === "branch-default" ? "1" : "0"),
  PYTHONUTF8: "1",
};

if (checkOnly || resolvePortsOnly) {
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
    "backend/scripts/operations/workspace_check.py",
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
if (process.argv.includes("--preflight-only")) process.exit(preflight.status ?? 1);
let startupDiagnostic;
if (preflight.status !== 0) {
  try {
    const report = JSON.parse(preflight.stdout.trim());
    startupDiagnostic = JSON.stringify({
      schema_version: "startup-diagnostic/v1",
      source: "workspace_preflight",
      log_path: "npm run dev の workspace preflight 出力",
      recovery_route: "docs/decisions/startup-failure-boundaries.md",
      report,
    });
  } catch {
    process.stderr.write("workspace preflightの診断JSONをWeb UIへ渡せませんでした。\n");
    process.exit(preflight.status ?? 1);
  }
}

const { default: concurrently } = await import("concurrently");
const { result } = concurrently(
  [
    ...(startupDiagnostic ? [] : [{
      name: "api",
      command: `uv run python -m uvicorn main:app --app-dir backend/src --host 127.0.0.1 --port ${apiPort} --reload --reload-dir backend/src`,
      env: childEnvironment,
      prefixColor: "blue",
    }]),
    {
      name: "web",
      command: "npm run dev -w apps/web",
      env: {
        ...childEnvironment,
        ...(startupDiagnostic ? { WORKBENCH_STARTUP_DIAGNOSTIC: startupDiagnostic } : {}),
      },
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
