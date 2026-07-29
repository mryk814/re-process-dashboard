import test from "node:test";
import assert from "node:assert/strict";
import { defaultResponseCurveRange } from "../src/shared/inputRangeDefaults.ts";

const input = {
  path: "process.temperature_c",
  kind: "number",
  order: 0,
  label: "温度",
  required: true,
  editable: true,
  id: "process.temperature_c",
  field: "temperature_c",
  group: "process",
  unit: "°C",
  allowed_range: { min: 0, max: 1500 },
  default_range: { min: 700, max: 1000 },
  training_range: { min: 760, max: 940 },
};

test("response curves request the task practical range, including outside training support", () => {
  assert.deepEqual(defaultResponseCurveRange(input, undefined), { min: 700, max: 1000 });
});

test("a project-specific practical range wins over the task default", () => {
  assert.deepEqual(defaultResponseCurveRange(input, { min: 720, max: 980 }), { min: 720, max: 980 });
});

test("runtime decides the range only when there is no numeric task input", () => {
  assert.equal(defaultResponseCurveRange(undefined, undefined), undefined);
});
