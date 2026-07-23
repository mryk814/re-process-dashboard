import test from "node:test";
import assert from "node:assert/strict";
import { suggestedInputRange } from "../src/features/admin/inputRangeDefaults.ts";

const input = (overrides = {}) => ({
  id: "composition.C",
  path: "composition.C",
  label: "C",
  unit: "%",
  group: "composition",
  kind: "number",
  editable: true,
  required: true,
  choices: [],
  allowed_range: { min: 0, max: 100 },
  ...overrides,
});

test("uses the task-defined practical range before the physical allowed range", () => {
  assert.deepEqual(suggestedInputRange(input({
    default_range: { min: 0, max: 0.2 },
    training_range: { min: 0.025, max: 0.14 },
  })), { min: 0, max: 0.2 });
});

test("expands the training range by ten percent when no practical default exists", () => {
  assert.deepEqual(suggestedInputRange(input({
    training_range: { min: 10, max: 20 },
  })), { min: 9, max: 21 });
});

test("clamps a derived range to the physical allowed range", () => {
  assert.deepEqual(suggestedInputRange(input({
    allowed_range: { min: 0, max: 100 },
    training_range: { min: 0, max: 90 },
  })), { min: 0, max: 99 });
});
