import { registerOwnedDatabaseCleanup } from "./owned-database-cleanup.mjs";

export default function globalTeardown() {
  const database = process.env.PLAYWRIGHT_OWNED_DB_PATH;
  if (!database) return;
  registerOwnedDatabaseCleanup(database);
}
