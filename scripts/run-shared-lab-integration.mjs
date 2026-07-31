import { spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { resolve } from "node:path";
import { performance } from "node:perf_hooks";

const repositoryRoot = resolve(import.meta.dirname, "..");
const projectName = `decision-workbench-shared-test-${process.pid}`;

function freePort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => error ? reject(error) : resolvePort(port));
    });
  });
}

const [postgresPort, s3Port, s3ConsolePort] = await Promise.all([
  freePort(),
  freePort(),
  freePort(),
]);
const composeEnvironment = {
  ...process.env,
  WORKBENCH_POSTGRES_PORT: String(postgresPort),
  WORKBENCH_S3_PORT: String(s3Port),
  WORKBENCH_S3_CONSOLE_PORT: String(s3ConsolePort),
};

function compose(args) {
  return spawnSync(
    "docker",
    ["compose", "--project-name", projectName, "--profile", "infra", ...args],
    { cwd: repositoryRoot, env: composeEnvironment, stdio: "inherit" },
  );
}

function requireSuccess(label, result) {
  if (result.error) {
    console.error(`${label} could not start: ${result.error.message}`);
    return 1;
  }
  if (result.status !== 0) {
    console.error(`${label} failed.`);
    return result.status ?? 1;
  }
  return 0;
}

const started = performance.now();
let status = 1;
try {
  status = requireSuccess(
    "Shared infrastructure",
    compose(["up", "-d", "--wait", "postgres", "object-storage"]),
  );
  if (status === 0) {
    status = requireSuccess(
      "Shared schema migration",
      compose(["run", "--rm", "--no-deps", "migration"]),
    );
  }
  if (status === 0) {
    status = requireSuccess(
      "Shared schema migration rerun",
      compose(["run", "--rm", "--no-deps", "migration"]),
    );
  }
  if (status === 0) {
    status = requireSuccess(
      "Shared bucket initialization",
      compose(["run", "--rm", "--no-deps", "bucket-init"]),
    );
  }
  if (status === 0) {
    const testEnvironment = {
      ...composeEnvironment,
      WORKBENCH_RUN_SHARED_INTEGRATION: "1",
      WORKBENCH_SHARED_DATABASE_URL:
        `postgresql://workbench:local-development-only@127.0.0.1:${postgresPort}/workbench`,
      WORKBENCH_S3_ENDPOINT: `http://127.0.0.1:${s3Port}`,
      WORKBENCH_S3_BUCKET: "workbench-artifacts",
      WORKBENCH_S3_ACCESS_KEY: "workbench-local",
      WORKBENCH_S3_SECRET_KEY: "local-development-only",
    };
    const test = spawnSync(
      "uv",
      [
        "run",
        "--extra",
        "dev",
        "--extra",
        "shared",
        "python",
        "-m",
        "pytest",
        "backend/tests/integration/test_shared_lab_integration.py",
        "-q",
        "-n",
        "0",
      ],
      { cwd: repositoryRoot, env: testEnvironment, stdio: "inherit" },
    );
    status = requireSuccess("Shared API integration", test);
  }
} finally {
  const cleanup = compose(["down", "--volumes", "--remove-orphans"]);
  if (cleanup.status !== 0 && status === 0) status = cleanup.status ?? 1;
}

const durationSeconds = Math.round((performance.now() - started) / 100) / 10;
if (status === 0) {
  console.log(`Shared Workbench integration passed and was removed in ${durationSeconds}s.`);
} else {
  console.error(`Shared Workbench integration failed after ${durationSeconds}s.`);
  process.exitCode = status;
}
