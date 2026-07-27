import test from "node:test";
import assert from "node:assert/strict";
import {
  formatAllowedRange,
  formatTaskNumber,
  orderedTaskEntries,
  orderedTaskItems,
  taskDisplayDecimals,
  taskOutputUnit,
} from "../src/shared/taskPresentation.ts";

const definition = {
  outputs: [
    { key: "TS", label: "引張強さ", unit: "MPa" },
    { key: "EL", label: "伸び", unit: "%" },
    { key: "risk", label: "異常確率", unit: "1" },
  ],
  display_decimals: {
    "output.TS": 0,
    "output.EL": 1,
    "output.risk": 3,
  },
};

test("TaskDefinition controls output order instead of API or Package insertion order", () => {
  assert.deepEqual(
    orderedTaskEntries(definition, { risk: 0.1, EL: 12.3, TS: 780 }).map(([key]) => key),
    ["TS", "EL", "risk"],
  );
  assert.deepEqual(
    orderedTaskItems(definition, [{ key: "risk" }, { key: "TS" }, { key: "EL" }]).map(({ key }) => key),
    ["TS", "EL", "risk"],
  );
});

test("task default and project override resolve one stable numeric presentation", () => {
  assert.equal(taskDisplayDecimals(definition, "output.EL"), 1);
  assert.equal(taskDisplayDecimals(definition, "output.EL", { "output.EL": 2 }), 2);
  assert.equal(formatTaskNumber(12.345, definition, "output.EL"), "12.3");
  assert.equal(formatTaskNumber(12.345, definition, "output.EL", { "output.EL": 2 }), "12.35");
});

test("TaskDefinition is the unit source and dimensionless unit is not shown", () => {
  assert.equal(taskOutputUnit(definition, "TS"), "MPa");
  assert.equal(taskOutputUnit(definition, "risk"), "");
});

test("allowed range is shown at the input's decimals with its unit, never as a raw float", () => {
  const heatInput = {
    unit: "kJ/mm",
    display_decimals: 2,
    allowed_range: { min: 0.6316999999999999, max: 2.1593 },
  };
  assert.equal(formatAllowedRange(heatInput), "0.64〜2.15 kJ/mm");
  assert.doesNotMatch(formatAllowedRange(heatInput), /\d\.\d{3,}/);
  assert.equal(
    formatAllowedRange({ unit: "V", display_decimals: 1, allowed_range: { min: 23.295, max: 34.87499999999999 } }),
    "23.3〜34.8 V",
  );
});

test("allowed range rounds inward so a displayed bound is never rejected on entry", () => {
  const range = { min: 0.6316999999999999, max: 2.1593 };
  const shown = formatAllowedRange({ unit: "", display_decimals: 2, allowed_range: range });
  const [low, high] = shown.split("〜").map(Number);
  assert.ok(low >= range.min, `${low} must stay inside ${range.min}`);
  assert.ok(high <= range.max, `${high} must stay inside ${range.max}`);
});

test("a range narrower than one display step keeps its exact bounds instead of inverting", () => {
  assert.equal(
    formatAllowedRange({ unit: "mm", display_decimals: 0, allowed_range: { min: 1.2, max: 1.4 } }),
    "1.2〜1.4 mm",
  );
  assert.equal(formatAllowedRange({ unit: "mm", display_decimals: 2, allowed_range: null }), "");
});
