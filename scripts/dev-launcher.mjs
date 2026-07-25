import { randomBytes } from "node:crypto";

function port(name, fallback) {
  const value = process.env[name] ?? fallback;
  if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error(`${name} must be a valid TCP port`);
  }
  return value;
}

const apiPort = port("WORKBENCH_DEV_API_PORT", "8765");
const webPort = port("WORKBENCH_DEV_WEB_PORT", "5180");
const launchToken = randomBytes(32).toString("base64url");
const childEnvironment = {
  ...process.env,
  WORKBENCH_LAUNCH_TOKEN: launchToken,
  WORKBENCH_DEV_PROXY_TOKEN: launchToken,
  WORKBENCH_DEV_API_URL: `http://127.0.0.1:${apiPort}`,
  WORKBENCH_DEV_WEB_PORT: webPort,
};

if (process.argv.includes("--check")) {
  process.stdout.write(JSON.stringify({
    tokenBytes: 32,
    apiProtected: childEnvironment.WORKBENCH_LAUNCH_TOKEN === launchToken,
    viteProxyProtected: childEnvironment.WORKBENCH_DEV_PROXY_TOKEN === launchToken,
    apiUrl: childEnvironment.WORKBENCH_DEV_API_URL,
    webPort: childEnvironment.WORKBENCH_DEV_WEB_PORT,
  }));
  process.exit(0);
}

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
