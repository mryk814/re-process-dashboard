import test from "node:test";
import assert from "node:assert/strict";
import {
  beginResourceLoad,
  initialResourceLoadState,
  rejectResourceLoad,
} from "../src/features/data-library/resourceLoadState.ts";

test("a failed refresh keeps the previous load timestamp so callers can retain stale data", () => {
  const ready = { phase: "ready", error: "", loadedAt: "2026-08-01T00:00:00.000Z" };
  const refreshing = beginResourceLoad(ready);
  const failed = rejectResourceLoad(refreshing, "Model Packageを取得できませんでした。");

  assert.deepEqual(refreshing, { ...ready, phase: "refreshing" });
  assert.deepEqual(failed, {
    phase: "error",
    error: "Model Packageを取得できませんでした。",
    loadedAt: "2026-08-01T00:00:00.000Z",
  });
});

test("an initial failure has no stale timestamp and is distinct from an empty ready response", () => {
  const failed = rejectResourceLoad(initialResourceLoadState(), "Datasetを取得できませんでした。");

  assert.equal(failed.loadedAt, null);
  assert.equal(failed.phase, "error");
});
