import { createServer } from "node:net";
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { pathToFileURL } from "node:url";
import {
  removeOwnedDatabaseFiles,
  removeOwnedStore,
} from "../e2e/owned-database-cleanup.mjs";
import {
  isolatedSpecs,
  parallelDedicatedSpecs,
} from "../e2e/suite-inventory.mjs";

const root = process.cwd();
const playwrightCli = join(root, "node_modules", "@playwright", "test", "cli.js");
const isolatedEnvironmentKeys = [
  "PLAYWRIGHT_REUSE_SERVER",
  "PLAYWRIGHT_DB_PATH",
  "PLAYWRIGHT_OWNED_DB_PATH",
  "PLAYWRIGHT_MODEL_STORE_PATH",
  "PLAYWRIGHT_OWNED_MODEL_STORE_PATH",
  "PLAYWRIGHT_PROFILE_STORE_PATH",
  "PLAYWRIGHT_OWNED_PROFILE_STORE_PATH",
  "PLAYWRIGHT_TASK_STORE_PATH",
  "PLAYWRIGHT_OWNED_TASK_STORE_PATH",
  "PLAYWRIGHT_CLEANUP_REPORT_PATH",
  "PLAYWRIGHT_API_PORT",
  "PLAYWRIGHT_WEB_PORT",
  "PLAYWRIGHT_OUTPUT_DIR",
  "PLAYWRIGHT_HTML_OUTPUT_DIR",
  "PLAYWRIGHT_BLOB_OUTPUT_DIR",
  "PLAYWRIGHT_JUNIT_OUTPUT_FILE",
  "PLAYWRIGHT_E2E_RUN_ID",
  "PLAYWRIGHT_BROKEN_TASK_PACKAGE",
  "PLAYWRIGHT_INCLUDE_PARALLEL_DEDICATED",
];

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("could not reserve a TCP port"));
        return;
      }
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

export function parseRunnerOptions(
  args,
  environment = process.env,
) {
  const mode = args.length === 0
    ? "focused-isolated"
    : args.length === 1 && args[0] === "--default-suite"
      ? "default-suite"
      : args.length === 1 && args[0] === "--parallel-dedicated"
        ? "parallel-dedicated"
      : null;
  if (!mode) {
    throw new Error(
      "usage: node scripts/run-isolated-e2e.mjs [--default-suite|--parallel-dedicated]",
    );
  }
  const maxWorkers = Number(environment.PLAYWRIGHT_ISOLATED_WORKERS ?? "2");
  if (!Number.isInteger(maxWorkers) || maxWorkers < 1) {
    throw new Error("PLAYWRIGHT_ISOLATED_WORKERS must be a positive integer");
  }
  const retries = Number(environment.PLAYWRIGHT_ISOLATED_RETRIES ?? "0");
  if (!Number.isInteger(retries) || retries !== 0) {
    throw new Error(
      "PLAYWRIGHT_ISOLATED_RETRIES must be 0: mutable E2E attempts are never retried against the same resource identity",
    );
  }
  if (mode === "default-suite" && maxWorkers < 2) {
    throw new Error(
      "PLAYWRIGHT_ISOLATED_WORKERS must be at least 2 for the parallel default suite",
    );
  }
  const processTimeoutMs = Number(
    environment.PLAYWRIGHT_ISOLATED_PROCESS_TIMEOUT_MS
      ?? String(30 * 60 * 1_000),
  );
  if (
    !Number.isInteger(processTimeoutMs) || processTimeoutMs < 1_000
  ) {
    throw new Error(
      "PLAYWRIGHT_ISOLATED_PROCESS_TIMEOUT_MS must be an integer of at least 1000",
    );
  }
  return { mode, maxWorkers, retries, processTimeoutMs };
}

export function buildExecutionPlan(mode) {
  const dedicatedTargets = parallelDedicatedSpecs.map((spec) => ({
    id: spec.replace(/\.spec\.ts$/, ""),
    spec,
    args: ["test", `e2e/${spec}`, "--workers=1", "--retries=0"],
    environment: {
      PLAYWRIGHT_INCLUDE_PARALLEL_DEDICATED: "1",
    },
  }));
  if (mode === "focused-isolated") {
    return isolatedSpecs.map((spec) => ({
      id: spec.replace(/\.spec\.ts$/, ""),
      spec,
      args: ["test", `e2e/${spec}`, "--workers=1", "--retries=0"],
      environment: {
        PLAYWRIGHT_INCLUDE_PARALLEL_DEDICATED: "0",
      },
    }));
  }
  if (mode === "parallel-dedicated") {
    return dedicatedTargets;
  }
  if (mode === "default-suite") {
    return [
      {
        id: "remaining-default-suite",
        spec: null,
        args: ["test", "--workers=1", "--retries=0"],
        environment: {
          PLAYWRIGHT_INCLUDE_PARALLEL_DEDICATED: "0",
        },
      },
      ...dedicatedTargets,
    ];
  }
  throw new Error(`unknown isolated E2E mode: ${mode}`);
}

export function exitCodeForProcesses(results) {
  return results.some((result) => result.code !== 0) ? 1 : 0;
}

export function isolatedChildEnvironment(
  baseEnvironment,
  targetEnvironment,
  resolvedEnvironment,
) {
  const environment = { ...baseEnvironment };
  for (const key of isolatedEnvironmentKeys) delete environment[key];
  return {
    ...environment,
    ...targetEnvironment,
    ...resolvedEnvironment,
  };
}

export async function runTargets(
  targets,
  {
    maxWorkers,
    execute,
  },
) {
  const pending = targets.map((target, index) => ({ target, index }));
  const results = [];
  const active = new Set();
  while (pending.length || active.size) {
    while (pending.length && active.size < maxWorkers) {
      const next = pending.shift();
      const task = execute(next.target, next.index)
        .then((result) => results.push(result))
        .finally(() => active.delete(task));
      active.add(task);
    }
    await Promise.race(active);
  }
  return results.sort((left, right) => left.index - right.index);
}

function terminateProcessTree(child) {
  if (!child.pid) return { succeeded: false, detail: "child pid is unavailable" };
  if (process.platform === "win32") {
    const result = spawnSync(
      "taskkill",
      ["/pid", String(child.pid), "/T", "/F"],
      { stdio: "ignore", windowsHide: true },
    );
    if (result.status === 0) return { succeeded: true, detail: "taskkill /T /F" };
    const fallback = child.kill("SIGKILL");
    return {
      succeeded: fallback,
      detail: `taskkill exited ${result.status ?? "without status"}; direct kill ${fallback ? "issued" : "failed"}`,
    };
  }
  try {
    process.kill(-child.pid, "SIGKILL");
    return { succeeded: true, detail: "process group SIGKILL" };
  } catch {
    const fallback = child.kill("SIGKILL");
    return {
      succeeded: fallback,
      detail: `direct SIGKILL ${fallback ? "issued" : "failed"}`,
    };
  }
}

function forcedCleanup(runId, outputDir) {
  const reportPath = join(
    outputDir,
    "test-results",
    "owned-e2e-cleanup-forced.jsonl",
  );
  const resources = [
    {
      label: "database",
      target: join(tmpdir(), `decision-workbench-e2e-${runId}.db`),
      remove: removeOwnedDatabaseFiles,
    },
    {
      label: "store",
      target: join(tmpdir(), `decision-workbench-e2e-models-${runId}`),
      remove: removeOwnedStore,
    },
    {
      label: "store",
      target: join(tmpdir(), `decision-workbench-e2e-profiles-${runId}`),
      remove: removeOwnedStore,
    },
    {
      label: "store",
      target: join(tmpdir(), `decision-workbench-e2e-tasks-${runId}`),
      remove: removeOwnedStore,
    },
  ];
  mkdirSync(join(outputDir, "test-results"), { recursive: true });
  return resources.map((resource) => {
    let outcome = "removed";
    let detail;
    try {
      if (!resource.remove(resource.target)) outcome = "busy";
    } catch (error) {
      outcome = "failed";
      detail = String(error);
    }
    const record = {
      schema_version: "e2e-cleanup-report/v1",
      label: resource.label,
      target: resource.target,
      outcome,
      ...(detail ? { detail } : {}),
    };
    appendFileSync(reportPath, `${JSON.stringify(record)}\n`);
    return record;
  });
}

export function superviseChild(
  child,
  {
    targetId,
    timeoutMs,
    cleanup,
    killGraceMs = 10_000,
  },
) {
  return new Promise((resolve) => {
    let settled = false;
    let timedOut = false;
    let termination = null;
    let graceTimeout = null;
    const settle = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(processTimeout);
      if (graceTimeout) clearTimeout(graceTimeout);
      resolve(result);
    };
    const cleanupAfterTimeout = () => {
      try {
        return cleanup();
      } catch (error) {
        return [{
          schema_version: "e2e-cleanup-report/v1",
          label: "runner",
          target: targetId,
          outcome: "failed",
          detail: String(error),
        }];
      }
    };
    const timeoutError = (suffix) => (
      `${targetId} exceeded isolated process timeout ${timeoutMs}ms${suffix}`
    );
    const processTimeout = setTimeout(() => {
      timedOut = true;
      termination = terminateProcessTree(child);
      graceTimeout = setTimeout(() => {
        child.unref();
        const forcedCleanup = cleanupAfterTimeout();
        settle({
          code: 1,
          signal: null,
          timedOut: true,
          error: timeoutError(
            `; process tree did not exit within ${killGraceMs}ms (${termination.detail})`,
          ),
          termination,
          forcedCleanup,
        });
      }, killGraceMs);
    }, timeoutMs);
    child.once("error", (error) => {
      settle({
        code: 1,
        signal: null,
        timedOut: false,
        error: error.message,
        termination: null,
        forcedCleanup: [],
      });
    });
    child.once("exit", (code, signal) => {
      if (!timedOut) {
        settle({
          code: code ?? 1,
          signal,
          timedOut: false,
          error: null,
          termination: null,
          forcedCleanup: [],
        });
        return;
      }
      const forcedCleanup = cleanupAfterTimeout();
      const cleanupFailed = forcedCleanup.some(
        (record) => record.outcome !== "removed",
      );
      settle({
        code: 1,
        signal,
        timedOut: true,
        error: timeoutError(
          `${cleanupFailed ? "; forced cleanup was incomplete" : ""} (${termination.detail})`,
        ),
        termination,
        forcedCleanup,
      });
    });
  });
}

async function runTarget(
  target,
  index,
  reportDirectory,
  parentRunId,
  processTimeoutMs,
) {
  // Ask the OS for separate API and Web ports for every fresh process. They are
  // passed through the same environment that e2e/helpers.ts resolves.
  const [apiPort, webPort] = await Promise.all([reservePort(), reservePort()]);
  if (apiPort === webPort) throw new Error(`duplicate isolated ports for ${target.id}`);
  const outputDir = join(reportDirectory, target.id);
  const runId = `${parentRunId}-${index}-${target.id}-${randomUUID()}`;
  const timeoutMs = processTimeoutMs;
  mkdirSync(outputDir, { recursive: true });
  const startedAt = new Date().toISOString();
  const child = spawn(
    process.execPath,
    [playwrightCli, ...target.args],
    {
      cwd: root,
      env: isolatedChildEnvironment(process.env, target.environment, {
        PLAYWRIGHT_API_PORT: String(apiPort),
        PLAYWRIGHT_WEB_PORT: String(webPort),
        PLAYWRIGHT_OUTPUT_DIR: join(outputDir, "test-results"),
        PLAYWRIGHT_HTML_OUTPUT_DIR: join(outputDir, "html"),
        PLAYWRIGHT_BLOB_OUTPUT_DIR: join(outputDir, "blob"),
        PLAYWRIGHT_JUNIT_OUTPUT_FILE: join(outputDir, "junit.xml"),
        PLAYWRIGHT_E2E_RUN_ID: runId,
      }),
      stdio: "inherit",
      detached: process.platform !== "win32",
      windowsHide: true,
    },
  );
  const supervised = await superviseChild(child, {
    targetId: target.id,
    timeoutMs,
    cleanup: () => forcedCleanup(runId, outputDir),
  });
  return {
    id: target.id,
    spec: target.spec,
    index,
    apiPort,
    webPort,
    runId,
    startedAt,
    finishedAt: new Date().toISOString(),
    outputDir,
    ...supervised,
  };
}

export async function run(options) {
  const parentRunId = process.env.PLAYWRIGHT_E2E_RUN_ID
    ?? `isolated-e2e-${Date.now()}-${process.pid}`;
  const outputRoot = resolve(process.env.PLAYWRIGHT_OUTPUT_DIR ?? join(root, "test-results"));
  const reportDirectory = join(outputRoot, parentRunId);
  mkdirSync(reportDirectory, { recursive: true });
  const results = await runTargets(buildExecutionPlan(options.mode), {
    maxWorkers: options.maxWorkers,
    execute: (target, index) => runTarget(
      target,
      index,
      reportDirectory,
      parentRunId,
      options.processTimeoutMs,
    ),
  });
  const report = {
    schema_version: "isolated-e2e-run/v1",
    run_id: parentRunId,
    mode: options.mode,
    workers: options.maxWorkers,
    retries: options.retries,
    process_timeout_ms: options.processTimeoutMs,
    processes: results,
    specs: results.filter((result) => result.spec !== null),
  };
  const reportPath = join(reportDirectory, "report.json");
  writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`Isolated E2E report: ${reportPath}\n`);
  return exitCodeForProcesses(results);
}

async function main() {
  const options = parseRunnerOptions(process.argv.slice(2));
  return run(options);
}

if (
  process.argv[1]
  && import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  main().then(
    (exitCode) => { process.exitCode = exitCode; },
    (error) => {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 2;
    },
  );
}
