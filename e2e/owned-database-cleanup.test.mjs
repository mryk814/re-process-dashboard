import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  registerOwnedDatabaseCleanup,
  removeOwnedDatabaseFiles,
} from "./owned-database-cleanup.mjs";

test("owned database and its sidecars are removed as one ordered group", () => {
  const calls = [];
  const removed = removeOwnedDatabaseFiles("owned.db", {
    remove: (path) => calls.push(path),
  });

  assert.equal(removed, true);
  assert.deepEqual(calls, ["owned.db", "owned.db-shm", "owned.db-wal"]);
});

test("a database still owned by the web server is reported without failing", () => {
  const busy = Object.assign(new Error("busy"), { code: "EBUSY" });
  const calls = [];
  const removed = removeOwnedDatabaseFiles("owned.db", {
    remove: (path) => {
      calls.push(path);
      if (path === "owned.db") throw busy;
    },
  });

  assert.equal(removed, false);
  assert.deepEqual(
    calls,
    ["owned.db"],
    "busy ownership must defer the sidecars as well",
  );
});

test("cleanup is deferred until process exit", () => {
  let exitCallback;
  const calls = [];
  registerOwnedDatabaseCleanup("owned.db", {
    once: (event, callback) => {
      assert.equal(event, "exit");
      exitCallback = callback;
    },
    remove: (path) => calls.push(path),
  });

  assert.deepEqual(calls, []);
  exitCallback(0);
  assert.deepEqual(calls, ["owned.db", "owned.db-shm", "owned.db-wal"]);
});

test("unexpected cleanup errors fail only an otherwise successful run", () => {
  const denied = Object.assign(new Error("denied"), { code: "EACCES" });
  let exitCallback;
  const exitCodes = [];
  const reports = [];
  registerOwnedDatabaseCleanup("owned.db", {
    once: (_event, callback) => {
      exitCallback = callback;
    },
    remove: () => {
      throw denied;
    },
    report: (message) => reports.push(message),
    setExitCode: (code) => exitCodes.push(code),
  });

  exitCallback(0);
  exitCallback(2);
  assert.deepEqual(exitCodes, [1]);
  assert.match(reports[0], /EACCES|denied/);
});

test("cleanup outcome is written to a test artifact when a report path is supplied", () => {
  const directory = mkdtempSync(join(tmpdir(), "decision-workbench-cleanup-report-"));
  const reportPath = join(directory, "cleanup.jsonl");
  let exitCallback;
  registerOwnedDatabaseCleanup("owned.db", {
    once: (_event, callback) => {
      exitCallback = callback;
    },
    remove: () => undefined,
    reportPath,
  });
  exitCallback(0);
  const [entry] = readFileSync(reportPath, "utf8").trim().split("\n").map(JSON.parse);
  assert.deepEqual(entry, {
    schema_version: "e2e-cleanup-report/v1",
    label: "database",
    target: "owned.db",
    outcome: "removed",
  });
  rmSync(directory, { recursive: true, force: true });
});
