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

export function registerOwnedDatabaseCleanup(
  database,
  {
    once = process.once.bind(process),
    remove = rmSync,
    report = (message) => process.stderr.write(message),
    setExitCode = (code) => {
      process.exitCode = code;
    },
  } = {},
) {
  once("exit", (exitCode) => {
    try {
      if (!removeOwnedDatabaseFiles(database, { remove })) {
        report(`Owned E2E database cleanup remained busy: ${database}\n`);
      }
    } catch (error) {
      report(`Owned E2E database cleanup failed: ${String(error)}\n`);
      if (exitCode === 0) setExitCode(1);
    }
  });
}
