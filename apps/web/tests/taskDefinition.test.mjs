import test from "node:test";
import assert from "node:assert/strict";
import {
  getCandidateInputValue,
  numericTaskInputs,
  orderedInputGroups,
  responseCurveVariables,
  setCandidateInputValue,
  validateResolvedTaskDefinition,
} from "../src/features/candidates/taskDefinition.ts";

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

test("response curves expose line speed and named stage temperatures without point-wise time", () => {
  const definition = {
    input_groups: [
      { key: "composition", order: 0, label: "成分", fields: [{ path: "composition.C", kind: "number", order: 0, label: "C", unit: "%", editable: true, required: true, allowed_range: { min: 0, max: 100 } }] },
      { key: "process", order: 1, label: "工程", fields: [{ path: "process.ls_mpm", kind: "number", order: 0, label: "LS", unit: "mpm", editable: true, required: true, allowed_range: { min: 1, max: 1000 } }] },
      { key: "heat_pattern", order: 2, label: "履歴", fields: [{ path: "heat_pattern", kind: "heat_pattern", order: 0, label: "履歴", editable: true, required: true }] },
    ],
    response_curve_variables: [
      { kind: "numeric_input", order: 0, label: "C", path: "composition.C", time_transform: "direct" },
      { kind: "numeric_input", order: 1, label: "ラインスピード", path: "process.ls_mpm", time_transform: "inverse_heat_time" },
      { kind: "heat_stage_temperature", order: 2, label: "工程温度", path: null, time_transform: "direct" },
    ],
  };
  const first = { composition: { C: 0.1 }, process: { ls_mpm: 120 }, categorical: {}, heat_pattern: [
    { time_s: 10, temperature_c: 20 },
    { time_s: 40, temperature_c: 700, stage_name: "加熱1" },
    { time_s: 70, temperature_c: 400 },
  ] };
  const second = { composition: { C: 0.1 }, process: { ls_mpm: 60 }, categorical: {}, heat_pattern: [
    { time_s: 0, temperature_c: 20 },
    { time_s: 30, temperature_c: 680, stage_name: "加熱1" },
    { time_s: 120, temperature_c: 400 },
  ] };

  const variables = responseCurveVariables(definition, first, [first, second], { 合金化: 90 });
  const variablesWithOtherSelection = responseCurveVariables(definition, second, [first, second], { 合金化: 90 });

  assert.deepEqual(variables.map((item) => item.id), ["composition.C", "process.ls_mpm", "heat.stage_temperature_c:加熱1", "heat.stage_temperature_c:合金化"]);
  assert.equal(variables[1].label, "ラインスピード");
  assert.equal(variables[2].stagePositionM, 60);
  assert.equal(variablesWithOtherSelection[2].stagePositionM, 60);
  assert.equal(variables[3].stagePositionM, 90);
  assert.equal(variables.some((item) => item.id.includes("time")), false);
});
