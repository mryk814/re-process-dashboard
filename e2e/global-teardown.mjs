import { registerOwnedDatabaseCleanup } from "./owned-database-cleanup.mjs";
import { rmSync } from "node:fs";

export default function globalTeardown() {
  const database = process.env.PLAYWRIGHT_OWNED_DB_PATH;
  if (database) registerOwnedDatabaseCleanup(database);
  const modelStore = process.env.PLAYWRIGHT_OWNED_MODEL_STORE_PATH;
  if (modelStore) rmSync(modelStore, { force: true, recursive: true });
  const profileStore = process.env.PLAYWRIGHT_OWNED_PROFILE_STORE_PATH;
  if (profileStore) rmSync(profileStore, { force: true, recursive: true });
}
