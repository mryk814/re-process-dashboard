import {
  registerOwnedDatabaseCleanup,
  registerOwnedStoreCleanup,
} from "./owned-database-cleanup.mjs";

export default function globalTeardown(config) {
  // Playwright runs global teardown before it stops webServer. Keep every
  // server-owned path available until this process exits.
  // Config evaluation and teardown do not share a mutable process environment,
  // so the report path is carried by Playwright metadata rather than guessed.
  const reportPath = config.metadata?.e2eCleanupReportPath;
  const owned = config.metadata?.e2eOwnedPaths ?? {};
  const database = owned.database;
  if (database) registerOwnedDatabaseCleanup(database, { reportPath });
  const modelStore = owned.modelStore;
  if (modelStore) registerOwnedStoreCleanup(modelStore, { reportPath });
  const profileStore = owned.profileStore;
  if (profileStore) registerOwnedStoreCleanup(profileStore, { reportPath });
  const taskStore = owned.taskStore;
  if (taskStore) registerOwnedStoreCleanup(taskStore, { reportPath });
}
