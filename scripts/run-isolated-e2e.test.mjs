import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

test("isolated runner rejects retries before any mutable E2E process starts", () => {
  const result = spawnSync(process.execPath, ["scripts/run-isolated-e2e.mjs"], {
    cwd: process.cwd(),
    env: { ...process.env, PLAYWRIGHT_ISOLATED_RETRIES: "1" },
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /PLAYWRIGHT_ISOLATED_RETRIES must be 0/);
});
