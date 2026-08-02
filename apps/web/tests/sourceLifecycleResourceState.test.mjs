import test from "node:test";
import assert from "node:assert/strict";
import {
  beginSourceLifecycleResourceLoad,
  initialSourceLifecycleResourceState,
  rejectSourceLifecycleResourceLoad,
  resolveSourceLifecycleResourceLoad,
} from "../src/features/data-library/sourceLifecycleResourceState.ts";

test("initial failure is distinct from a successful empty lifecycle resource", () => {
  const empty = resolveSourceLifecycleResourceLoad(
    "catalog",
    true,
    "2026-08-02T00:00:00.000Z",
  );
  const failed = rejectSourceLifecycleResourceLoad(
    initialSourceLifecycleResourceState("catalog"),
    "catalog",
    "接続先一覧を取得できませんでした。",
  );

  assert.equal(empty.phase, "empty");
  assert.equal(failed.phase, "error");
  assert.equal(failed.loadedAt, null);
});

test("same resource scope keeps previous evidence stale with its client time", () => {
  const ready = resolveSourceLifecycleResourceLoad(
    "raw-snapshot-1",
    false,
    "2026-08-02T01:02:03.000Z",
  );
  const stale = rejectSourceLifecycleResourceLoad(
    beginSourceLifecycleResourceLoad(ready, "raw-snapshot-1"),
    "raw-snapshot-1",
    "取得行を更新できませんでした。",
  );

  assert.deepEqual(stale, {
    scope: "raw-snapshot-1",
    phase: "stale",
    loadedAt: "2026-08-02T01:02:03.000Z",
    error: "取得行を更新できませんでした。",
  });
});

test("connector scope change discards previous evidence", () => {
  const ready = resolveSourceLifecycleResourceLoad(
    "connector-a",
    false,
    "2026-08-02T01:02:03.000Z",
  );
  const next = beginSourceLifecycleResourceLoad(ready, "connector-b");
  const failed = rejectSourceLifecycleResourceLoad(
    next,
    "connector-b",
    "接続先履歴を取得できませんでした。",
  );

  assert.deepEqual(next, initialSourceLifecycleResourceState("connector-b"));
  assert.equal(failed.phase, "error");
  assert.equal(failed.loadedAt, null);
});
