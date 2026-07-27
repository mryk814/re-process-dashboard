import { rm } from "node:fs/promises";

export default async function globalTeardown() {
  const database = process.env.PLAYWRIGHT_OWNED_DB_PATH;
  if (!database) return;
  await Promise.all(
    [database, `${database}-shm`, `${database}-wal`].map((path) =>
      rm(path, { force: true }),
    ),
  );
}
