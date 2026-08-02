import test from "node:test";
import assert from "node:assert/strict";

import {
  actualDraftHasUserInput,
  emptyActualDraft,
  reconcileActualDraftRevision,
} from "../src/features/workbench/actualMeasurementDraftState.ts";

test("candidate autosave preserves an entered Actual draft and requests a revision decision", () => {
  const draft = {
    ...emptyActualDraft("strength"),
    mean: "512",
    experimentNo: "EXP-42",
    note: "再試験",
  };

  assert.equal(actualDraftHasUserInput(draft, "strength"), true);
  assert.deepEqual(
    reconcileActualDraftRevision(
      { targetRevision: 3, pendingRevision: null },
      4,
      true,
    ),
    { targetRevision: 3, pendingRevision: 4 },
  );
});

test("a pristine Actual form follows the latest candidate revision without prompting", () => {
  assert.equal(
    actualDraftHasUserInput(emptyActualDraft("strength"), "strength"),
    false,
  );
  assert.deepEqual(
    reconcileActualDraftRevision(
      { targetRevision: 3, pendingRevision: null },
      4,
      false,
    ),
    { targetRevision: 4, pendingRevision: null },
  );
});
