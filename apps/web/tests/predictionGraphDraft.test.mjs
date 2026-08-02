import assert from "node:assert/strict";
import test from "node:test";

import {
  addDecisionOutput,
  addInputAndBind,
  addStage,
  connectSource,
  emptyPredictionGraph,
  topologicalLayers,
} from "../src/features/projects/predictionGraphDraft.ts";

const port = (path, quantity = path, unit = "unit") => ({
  path,
  value_kind: "number",
  quantity,
  basis: null,
  unit,
});

const item = (contract_id, inputs, outputs) => ({
  stage_kind: "task",
  contract_id,
  label: contract_id,
  status: "available",
  reason: null,
  surface: {
    stage_kind: "task",
    contract_id,
    contract_digest: `sha256:${contract_id.padEnd(64, "a").slice(0, 64)}`,
    input_ports: inputs,
    output_ports: outputs,
  },
  stage_lock: null,
});

const catalog = {
  candidate_adapter_ids: ["scalar/v1"],
  stages: [
    item("source-model", [port("x", "x")], [port("shared", "shared")]),
    item("parallel-model", [port("x", "x")], [port("parallel", "parallel")]),
    item("merge-model", [port("left", "shared"), port("right", "parallel")], [port("result", "result")]),
  ],
};

test("builds parallel layers, branch/merge, and terminal intermediate output from one draft", () => {
  const source = addStage(emptyPredictionGraph(), catalog.stages[0]);
  const parallel = addStage(source.definition, catalog.stages[1]);
  const merge = addStage(parallel.definition, catalog.stages[2]);
  let definition = addInputAndBind(merge.definition, source.stageId, port("x", "x"));
  const graphInput = definition.inputs[0];
  definition = connectSource(
    definition,
    catalog,
    parallel.stageId,
    port("x", "x"),
    { source_kind: "external", path: graphInput.input_id },
  ).definition;
  definition = connectSource(
    definition,
    catalog,
    merge.stageId,
    port("left", "shared"),
    { source_kind: "stage_output", stage_id: source.stageId, output_key: "shared" },
  ).definition;
  definition = connectSource(
    definition,
    catalog,
    merge.stageId,
    port("right", "parallel"),
    { source_kind: "stage_output", stage_id: parallel.stageId, output_key: "parallel" },
  ).definition;
  definition = addDecisionOutput(definition, source.stageId, port("shared", "shared"));
  definition = addDecisionOutput(definition, merge.stageId, port("result", "result"));

  assert.deepEqual(topologicalLayers(definition), [
    [source.stageId, parallel.stageId],
    [merge.stageId],
  ]);
  assert.equal(definition.bindings.filter((binding) => (
    binding.source.source_kind === "external"
    && binding.source.path === graphInput.input_id
  )).length, 2, "one Input fans out into two branches");
  assert.equal(definition.bindings.filter((binding) => binding.target_stage_id === merge.stageId).length, 2);
  assert.equal(definition.decision_outputs.some((output) => output.source_stage_id === source.stageId), true);
});

test("rejects incompatible and cyclic edges without mutating the draft", () => {
  const source = addStage(emptyPredictionGraph(), catalog.stages[0]);
  const merge = addStage(source.definition, catalog.stages[2]);
  const linked = connectSource(
    merge.definition,
    catalog,
    merge.stageId,
    port("left", "shared"),
    { source_kind: "stage_output", stage_id: source.stageId, output_key: "shared" },
  ).definition;

  const incompatible = connectSource(
    linked,
    catalog,
    merge.stageId,
    port("right", "parallel"),
    { source_kind: "stage_output", stage_id: source.stageId, output_key: "shared" },
  );
  assert.equal(incompatible.definition, linked);
  assert.match(incompatible.error, /互換ではありません/);

  const cycleCatalog = {
    candidate_adapter_ids: ["scalar/v1"],
    stages: [
      item("loop-a", [port("x", "x")], [port("x", "x")]),
      item("loop-b", [port("x", "x")], [port("x", "x")]),
    ],
  };
  const loopA = addStage(emptyPredictionGraph(), cycleCatalog.stages[0]);
  const loopB = addStage(loopA.definition, cycleCatalog.stages[1]);
  const forward = connectSource(
    loopB.definition,
    cycleCatalog,
    loopB.stageId,
    port("x", "x"),
    { source_kind: "stage_output", stage_id: loopA.stageId, output_key: "x" },
  ).definition;
  const cyclic = connectSource(
    forward,
    cycleCatalog,
    loopA.stageId,
    port("x", "x"),
    { source_kind: "stage_output", stage_id: loopB.stageId, output_key: "x" },
  );
  assert.equal(cyclic.definition, forward);
  assert.match(cyclic.error, /互換ではありません/);
});

test("presentation-only canvas state never enters the scientific definition", () => {
  const definition = emptyPredictionGraph();
  const before = JSON.stringify(definition);
  const presentation = { zoom: 0.8, compact: true, selectedNodeId: "model" };
  presentation.zoom = 1.2;

  assert.equal(JSON.stringify(definition), before);
  assert.equal("zoom" in definition, false);
  assert.equal("selectedNodeId" in definition, false);
});
