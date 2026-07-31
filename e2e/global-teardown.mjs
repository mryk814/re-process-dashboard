import {
  registerOwnedDatabaseCleanup,
  registerOwnedStoreCleanup,
} from "./owned-database-cleanup.mjs";

export default function globalTeardown() {
  // Playwright runs global teardown before it stops webServer. Keep every
  // server-owned path available until this process exits.
  const database = process.env.PLAYWRIGHT_OWNED_DB_PATH;
  if (database) registerOwnedDatabaseCleanup(database);
  const modelStore = process.env.PLAYWRIGHT_OWNED_MODEL_STORE_PATH;
  if (modelStore) registerOwnedStoreCleanup(modelStore);
  const profileStore = process.env.PLAYWRIGHT_OWNED_PROFILE_STORE_PATH;
  if (profileStore) registerOwnedStoreCleanup(profileStore);
  const taskStore = process.env.PLAYWRIGHT_OWNED_TASK_STORE_PATH;
  if (taskStore) registerOwnedStoreCleanup(taskStore);
}
