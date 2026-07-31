import { rmSync } from "node:fs";

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
    setExitCode = (code) => {
      process.exitCode = code;
    },
  } = {},
) {
  once("exit", (exitCode) => {
    try {
      if (!cleanup()) {
        report(`Owned E2E ${label} cleanup remained busy: ${target}\n`);
      }
    } catch (error) {
      report(`Owned E2E ${label} cleanup failed: ${String(error)}\n`);
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
