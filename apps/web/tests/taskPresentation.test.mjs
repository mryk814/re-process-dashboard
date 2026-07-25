import test from "node:test";
import assert from "node:assert/strict";
import {
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
