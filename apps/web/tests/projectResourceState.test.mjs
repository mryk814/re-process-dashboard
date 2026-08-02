import test from "node:test";
import assert from "node:assert/strict";
import {
  beginProjectResourceLoad,
  initialProjectResourceState,
  rejectProjectResourceLoad,
  resolveProjectResourceLoad,
} from "../src/features/projects/projectResourceState.ts";

test("project resource initial failure is distinct from a successful empty response", () => {
  const empty = resolveProjectResourceLoad(
    "archive-list",
    true,
    "2026-08-02T00:00:00.000Z",
  );
  const failed = rejectProjectResourceLoad(
    initialProjectResourceState("archive-list"),
    "archive-list",
    "アーカイブ一覧を取得できませんでした。",
  );

  assert.equal(empty.phase, "empty");
  assert.equal(failed.phase, "error");
  assert.equal(failed.loadedAt, null);
});

test("same-scope refresh failure retains the client retrieval time as stale", () => {
  const ready = resolveProjectResourceLoad(
    "project-a:snapshot-1",
    false,
    "2026-08-02T01:02:03.000Z",
  );
  const stale = rejectProjectResourceLoad(
    beginProjectResourceLoad(ready, "project-a:snapshot-1"),
    "project-a:snapshot-1",
    "Snapshotを更新できませんでした。",
  );

  assert.deepEqual(stale, {
    scope: "project-a:snapshot-1",
    phase: "stale",
    loadedAt: "2026-08-02T01:02:03.000Z",
    error: "Snapshotを更新できませんでした。",
    unavailable: false,
  });
});

test("scope changes discard previous evidence instead of relabeling it stale", () => {
  const ready = resolveProjectResourceLoad(
    "project-a",
    false,
    "2026-08-02T01:02:03.000Z",
  );
  const next = beginProjectResourceLoad(ready, "project-b");
  const failed = rejectProjectResourceLoad(
    next,
    "project-b",
    "Project概要を取得できませんでした。",
  );

  assert.deepEqual(next, initialProjectResourceState("project-b"));
  assert.equal(failed.phase, "error");
  assert.equal(failed.loadedAt, null);
});

test("same-scope unavailable keeps stale evidence and availability separately", () => {
  const ready = resolveProjectResourceLoad(
    "project-a:chain-r2",
    false,
    "2026-08-02T01:02:03.000Z",
  );
  const stale = rejectProjectResourceLoad(
    beginProjectResourceLoad(ready, "project-a:chain-r2"),
    "project-a:chain-r2",
    "Chain評価は現在利用できません。",
    true,
  );

  assert.equal(stale.phase, "stale");
  assert.equal(stale.unavailable, true);
  assert.equal(stale.loadedAt, "2026-08-02T01:02:03.000Z");
});
