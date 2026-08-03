import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import test from "node:test";
import {
  buildExecutionPlan,
  exitCodeForProcesses,
  isolatedChildEnvironment,
  parseRunnerOptions,
  runTargets,
  superviseChild,
} from "./run-isolated-e2e.mjs";

test("isolated runner rejects retries before any mutable E2E process starts", () => {
  const result = spawnSync(process.execPath, ["scripts/run-isolated-e2e.mjs"], {
    cwd: process.cwd(),
    env: { ...process.env, PLAYWRIGHT_ISOLATED_RETRIES: "1" },
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /PLAYWRIGHT_ISOLATED_RETRIES must be 0/);
});

test("isolated runner rejects an invalid process deadline before server startup", () => {
  const result = spawnSync(process.execPath, ["scripts/run-isolated-e2e.mjs"], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PLAYWRIGHT_ISOLATED_PROCESS_TIMEOUT_MS: "999",
    },
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0);
  assert.match(
    result.stderr,
    /PLAYWRIGHT_ISOLATED_PROCESS_TIMEOUT_MS must be an integer of at least 1000/,
  );
});

test("default suite runs remaining specs and dedicated source lifecycle exactly once", () => {
  const options = parseRunnerOptions(["--default-suite"], {
    PLAYWRIGHT_ISOLATED_WORKERS: "2",
    PLAYWRIGHT_ISOLATED_RETRIES: "0",
  });
  const plan = buildExecutionPlan(options.mode);

  assert.equal(options.maxWorkers, 2);
  assert.equal(options.retries, 0);
  assert.equal(options.processTimeoutMs, 1_800_000);
  assert.deepEqual(plan.map((target) => target.id), [
    "remaining-default-suite",
    "source-lifecycle",
  ]);
  assert.deepEqual(
    plan.filter((target) => target.spec === "source-lifecycle.spec.ts"),
    [{
      id: "source-lifecycle",
      spec: "source-lifecycle.spec.ts",
      args: [
        "test",
        "e2e/source-lifecycle.spec.ts",
        "--workers=1",
        "--retries=0",
      ],
      environment: {
        PLAYWRIGHT_INCLUDE_PARALLEL_DEDICATED: "1",
      },
    }],
  );
  assert.equal(
    plan[0].environment.PLAYWRIGHT_INCLUDE_PARALLEL_DEDICATED,
    "0",
  );
  assert.deepEqual(
    buildExecutionPlan("parallel-dedicated"),
    plan.slice(1),
  );
});

test("default suite requires two process slots and aggregates either failure", () => {
  assert.throws(
    () => parseRunnerOptions(["--default-suite"], {
      PLAYWRIGHT_ISOLATED_WORKERS: "1",
      PLAYWRIGHT_ISOLATED_RETRIES: "0",
    }),
    /at least 2/,
  );
  assert.equal(exitCodeForProcesses([{ code: 0 }, { code: 0 }]), 0);
  assert.equal(exitCodeForProcesses([{ code: 1 }, { code: 0 }]), 1);
  assert.equal(exitCodeForProcesses([{ code: 0 }, { code: 1 }]), 1);
});

test("isolated children reject every inherited server and workspace identity", () => {
  const environment = isolatedChildEnvironment(
    {
      PLAYWRIGHT_REUSE_SERVER: "1",
      PLAYWRIGHT_DB_PATH: "shared.db",
      PLAYWRIGHT_OWNED_DB_PATH: "shared-owned.db",
      PLAYWRIGHT_MODEL_STORE_PATH: "shared-models",
      PLAYWRIGHT_OWNED_MODEL_STORE_PATH: "shared-owned-models",
      PLAYWRIGHT_PROFILE_STORE_PATH: "shared-profiles",
      PLAYWRIGHT_OWNED_PROFILE_STORE_PATH: "shared-owned-profiles",
      PLAYWRIGHT_TASK_STORE_PATH: "shared-tasks",
      PLAYWRIGHT_OWNED_TASK_STORE_PATH: "shared-owned-tasks",
      PLAYWRIGHT_CLEANUP_REPORT_PATH: "shared-cleanup.jsonl",
      PLAYWRIGHT_API_PORT: "1",
      PLAYWRIGHT_WEB_PORT: "2",
      PLAYWRIGHT_E2E_RUN_ID: "shared-run",
      KEEP_ME: "yes",
    },
    { PLAYWRIGHT_INCLUDE_PARALLEL_DEDICATED: "0" },
    {
      PLAYWRIGHT_API_PORT: "5001",
      PLAYWRIGHT_WEB_PORT: "5002",
      PLAYWRIGHT_E2E_RUN_ID: "fresh-run",
    },
  );
  assert.equal(environment.KEEP_ME, "yes");
  assert.equal(environment.PLAYWRIGHT_REUSE_SERVER, undefined);
  assert.equal(environment.PLAYWRIGHT_DB_PATH, undefined);
  assert.equal(environment.PLAYWRIGHT_MODEL_STORE_PATH, undefined);
  assert.equal(environment.PLAYWRIGHT_PROFILE_STORE_PATH, undefined);
  assert.equal(environment.PLAYWRIGHT_TASK_STORE_PATH, undefined);
  assert.equal(environment.PLAYWRIGHT_CLEANUP_REPORT_PATH, undefined);
  assert.equal(environment.PLAYWRIGHT_API_PORT, "5001");
  assert.equal(environment.PLAYWRIGHT_WEB_PORT, "5002");
  assert.equal(environment.PLAYWRIGHT_E2E_RUN_ID, "fresh-run");
});

test("default suite scheduler starts remaining and source processes concurrently", async () => {
  let active = 0;
  let maximumActive = 0;
  let release;
  const blocked = new Promise((resolve) => {
    release = resolve;
  });
  const started = [];
  const runPromise = runTargets(buildExecutionPlan("default-suite"), {
    maxWorkers: 2,
    execute: async (target, index) => {
      started.push(target.id);
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      if (started.length === 2) release();
      await blocked;
      active -= 1;
      return { id: target.id, index, code: 0 };
    },
  });

  const results = await runPromise;
  assert.deepEqual(started, [
    "remaining-default-suite",
    "source-lifecycle",
  ]);
  assert.equal(maximumActive, 2);
  assert.deepEqual(results.map((result) => result.id), started);
});

test("timed out child exits before forced cleanup and failure aggregation", async () => {
  const running = spawn(
    process.execPath,
    ["-e", "setInterval(() => {}, 1000)"],
    {
      stdio: "ignore",
      windowsHide: true,
      detached: process.platform !== "win32",
    },
  );
  let cleanupCalls = 0;
  const result = await superviseChild(running, {
    targetId: "synthetic-timeout",
    timeoutMs: 1_000,
    killGraceMs: 5_000,
    cleanup: () => {
      cleanupCalls += 1;
      return [
        { label: "database", outcome: "removed" },
        { label: "store", outcome: "removed" },
        { label: "store", outcome: "removed" },
        { label: "store", outcome: "removed" },
      ];
    },
  });

  assert.equal(result.code, 1);
  assert.equal(result.timedOut, true);
  assert.equal(result.termination.succeeded, true);
  assert.equal(cleanupCalls, 1);
  assert.equal(result.forcedCleanup.length, 4);
  assert.equal(running.exitCode === null && running.signalCode === null, false);
});
