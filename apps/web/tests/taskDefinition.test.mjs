import test from "node:test";
import assert from "node:assert/strict";
import {
  getCandidateInputValue,
  numericTaskInputs,
  orderedInputGroups,
  setCandidateInputValue,
  validateResolvedTaskDefinition,
} from "../src/taskDefinition.ts";

function resolvedDefinition() {
  return {
    task_definition: {
      id: "task",
      input_groups: [
        { key: "categorical", order: 2, label: "区分", fields: [{ path: "categorical.route", kind: "categorical", order: 0, label: "Route", choices: ["A"], editable: true, required: true }] },
        { key: "process", order: 1, label: "工程", fields: [{ path: "process.stage.temperature", kind: "number", order: 0, label: "温度", choices: [], editable: true, required: true }] },
        { key: "composition", order: 0, label: "組成", fields: [{ path: "composition.C", kind: "number", order: 0, label: "C", choices: [], editable: true, required: true }] },
      ],
      outputs: [{ key: "TS", label: "引張強さ", unit: "MPa", goal_direction: "at_least" }],
      fixed_context: [{ path: "context.line", order: 0, label: "設備", value: "L1" }],
    },
    runtime_capability: { task_id: "task", operations: {} },
  };
}

test("validates task identity and preserves ordered canonical groups without a projection", () => {
  const resolved = resolvedDefinition();
  assert.equal(validateResolvedTaskDefinition(resolved), resolved);
  assert.deepEqual(orderedInputGroups(resolved.task_definition).map((group) => group.key), ["composition", "process", "categorical"]);
  assert.deepEqual(numericTaskInputs(resolved.task_definition).map((input) => input.field), ["C", "stage.temperature"]);
  assert.equal(resolved.task_definition.fixed_context[0].value, "L1");
});

test("fails fast for task mismatch, malformed paths, and unsupported field kinds", () => {
  const mismatch = resolvedDefinition();
  mismatch.runtime_capability.task_id = "other";
  assert.throws(() => validateResolvedTaskDefinition(mismatch), /task mismatch/);

  const malformed = resolvedDefinition();
  malformed.task_definition.input_groups[1].fields[0].path = "composition.temperature";
  assert.throws(() => validateResolvedTaskDefinition(malformed), /path does not match group/);

  const wrongKind = resolvedDefinition();
  wrongKind.task_definition.input_groups[0].fields[0].kind = "number";
  assert.throws(() => validateResolvedTaskDefinition(wrongKind), /kind does not match group/);
});

test("candidate path getter/setter preserves nested suffixes and unrelated groups", () => {
  const inputs = { composition: { C: 0.1 }, process: { "stage.temperature": 800 }, categorical: { route: "A" }, heat_pattern: null };
  assert.equal(getCandidateInputValue(inputs, "process.stage.temperature"), 800);
  const changed = setCandidateInputValue(inputs, "categorical.route", "B");
  assert.equal(changed.categorical.route, "B");
  assert.deepEqual(changed.process, inputs.process);
});
