import { appendFileSync, mkdirSync, rmSync } from "node:fs";
import { dirname } from "node:path";

function isBusy(error) {
  return error?.code === "EBUSY" || error?.code === "EPERM";
}

export function removeOwnedDatabaseFiles(
  database,
  { remove = rmSync } = {},
) {
  for (const path of [database, `${database}-shm`, `${database}-wal`]) {
    try {
      remove(path, { force: true, maxRetries: 10, retryDelay: 100 });
    } catch (error) {
      if (!isBusy(error)) throw error;
      return false;
    }
  }
  return true;
}

export function removeOwnedStore(
  store,
  { remove = rmSync } = {},
) {
  try {
    remove(store, { force: true, maxRetries: 10, recursive: true, retryDelay: 100 });
  } catch (error) {
    if (!isBusy(error)) throw error;
    return false;
  }
  return true;
}

function registerOwnedCleanup(
  label,
  target,
  cleanup,
  {
    once = process.once.bind(process),
    report = (message) => process.stderr.write(message),
    reportPath = process.env.PLAYWRIGHT_CLEANUP_REPORT_PATH,
    setExitCode = (code) => {
      process.exitCode = code;
    },
  } = {},
) {
  const record = (outcome, detail = undefined) => {
    if (!reportPath) return;
    mkdirSync(dirname(reportPath), { recursive: true });
    appendFileSync(reportPath, `${JSON.stringify({
      schema_version: "e2e-cleanup-report/v1",
      label,
      target,
      outcome,
      ...(detail ? { detail } : {}),
    })}\n`);
  };
  once("exit", (exitCode) => {
    try {
      if (!cleanup()) {
        report(`Owned E2E ${label} cleanup remained busy: ${target}\n`);
        record("busy");
      } else {
        record("removed");
      }
    } catch (error) {
      report(`Owned E2E ${label} cleanup failed: ${String(error)}\n`);
      record("failed", String(error));
      if (exitCode === 0) setExitCode(1);
    }
  });
}

export function registerOwnedDatabaseCleanup(
  database,
  { remove = rmSync, ...options } = {},
) {
  registerOwnedCleanup(
    "database",
    database,
    () => removeOwnedDatabaseFiles(database, { remove }),
    options,
  );
}

export function registerOwnedStoreCleanup(
  store,
  { remove = rmSync, ...options } = {},
) {
  registerOwnedCleanup(
    "store",
    store,
    () => removeOwnedStore(store, { remove }),
    options,
  );
}
