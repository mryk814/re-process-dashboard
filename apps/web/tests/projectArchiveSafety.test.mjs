import assert from "node:assert/strict";
import test from "node:test";

import { archiveAfterCandidateSettlement } from "../src/features/workbench/projectArchiveSafety.ts";

test("active Project archive waits for pending candidate persistence", async () => {
  let releaseSettlement;
  const settlement = new Promise((resolve) => {
    releaseSettlement = resolve;
  });
  let archiveCalls = 0;

  const result = archiveAfterCandidateSettlement({
    active: true,
    settlePending: () => settlement,
    archive: async () => {
      archiveCalls += 1;
    },
  });

  assert.equal(archiveCalls, 0);
  releaseSettlement(true);
  assert.equal(await result, true);
  assert.equal(archiveCalls, 1);
});

test("failed candidate persistence prevents active Project archive", async () => {
  let archiveCalls = 0;

  const result = await archiveAfterCandidateSettlement({
    active: true,
    settlePending: async () => false,
    archive: async () => {
      archiveCalls += 1;
    },
  });

  assert.equal(result, false);
  assert.equal(archiveCalls, 0);
});
