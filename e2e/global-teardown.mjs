import { registerOwnedDatabaseCleanup } from "./owned-database-cleanup.mjs";
import { rmSync } from "node:fs";

export default function globalTeardown() {
  const database = process.env.PLAYWRIGHT_OWNED_DB_PATH;
  if (database) registerOwnedDatabaseCleanup(database);
  const modelStore = process.env.PLAYWRIGHT_OWNED_MODEL_STORE_PATH;
  if (modelStore) rmSync(modelStore, { force: true, recursive: true });
}
