import assert from "node:assert/strict";
import test from "node:test";

import {
  isPredictionGraphActualWritable,
  resolvePredictionGraphActualOutputId,
} from "../src/features/workbench/predictionGraphActualState.ts";

const snapshot = {
  terminal_outputs: [
    { output_id: "deposited-composition", status: "latest" },
    { output_id: "tensile-strength", status: "latest" },
    { output_id: "failed-output", status: "failed" },
  ],
};

test("late snapshot history preserves an explicitly selected latest output", () => {
  assert.equal(
    resolvePredictionGraphActualOutputId("tensile-strength", snapshot),
    "tensile-strength",
  );
});

test("snapshot changes fall back to the first latest output", () => {
  assert.equal(
    resolvePredictionGraphActualOutputId("failed-output", snapshot),
    "deposited-composition",
  );
  assert.equal(resolvePredictionGraphActualOutputId("", null), "");
});

test("an old candidate snapshot is never writable during candidate switching", () => {
  const candidate = { id: "candidate-b", revision: 1 };
  const oldSnapshot = {
    identity: { candidate_id: "candidate-a", candidate_revision: 1 },
  };
  const currentSnapshot = {
    identity: { candidate_id: "candidate-b", candidate_revision: 1 },
  };

  assert.equal(isPredictionGraphActualWritable(candidate, oldSnapshot), false);
  assert.equal(isPredictionGraphActualWritable(candidate, currentSnapshot), true);
});
