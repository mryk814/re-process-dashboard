import assert from "node:assert/strict";
import test from "node:test";

import { resolveObservationAuthoringTaskId } from "../src/features/data-library/observationAuthoringState.ts";

const tasks = [
  { task_id: "corrosion" },
  { task_id: "tensile" },
];

test("late task catalog initialization preserves an explicit task selection", () => {
  assert.equal(resolveObservationAuthoringTaskId("tensile", tasks), "tensile");
});

test("task catalog initialization selects the first available task when needed", () => {
  assert.equal(resolveObservationAuthoringTaskId("", tasks), "corrosion");
  assert.equal(resolveObservationAuthoringTaskId("removed", tasks), "corrosion");
});
