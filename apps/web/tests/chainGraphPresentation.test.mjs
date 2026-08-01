import assert from "node:assert/strict";
import test from "node:test";

import {
  buildChainGraph,
  revisionStage,
  stageBindingCounts,
  stageStatus,
} from "../src/features/projects/chainGraphPresentation.ts";

const definition = {
  external_inputs: [{
    path: "external.temperature_c",
    value_kind: "number",
    quantity: "temperature",
    basis: null,
    unit: "°C",
  }],
  stages: [
    { stage_id: "prepare", stage_kind: "deterministic_transform", contract_id: "prepare/v1" },
    { stage_id: "measure", stage_kind: "task", contract_id: "measure/v1" },
    { stage_id: "score", stage_kind: "task", contract_id: "score/v1" },
  ],
  bindings: [
    { target_stage_id: "prepare", target_input_path: "temperature", source: { source_kind: "external", path: "external.temperature_c" }, conversion: null },
    { target_stage_id: "measure", target_input_path: "prepared", source: { source_kind: "stage_output", stage_id: "prepare", output_key: "prepared" }, conversion: { conversion_id: "c-to-k", source_unit: "°C", target_unit: "K", factor: 1, offset: 273.15 } },
    { target_stage_id: "score", target_input_path: "measurement", source: { source_kind: "stage_output", stage_id: "measure", output_key: "measurement" }, conversion: null },
    { target_stage_id: "score", target_input_path: "temperature", source: { source_kind: "external", path: "external.temperature_c" }, conversion: null },
  ],
};

test("builds external, branch, merge, and conversion edges without stage-name assumptions", () => {
  const edges = buildChainGraph(definition);
  assert.equal(edges.length, 4);
  assert.equal(edges[0].source.kind, "external");
  assert.equal(edges[1].source.label, "prepare.prepared");
  assert.equal(edges[1].binding.conversion.conversion_id, "c-to-k");
  assert.equal(edges[3].target.label, "score.temperature");
  assert.equal(edges[0].sourcePort.unit, "°C");
});

test("derives node counts and live status per stage", () => {
  assert.deepEqual(stageBindingCounts(definition, "score"), { inputs: 2, outputs: 0 });
  assert.deepEqual(stageBindingCounts(definition, "prepare"), { inputs: 1, outputs: 1 });
  const execution = { stages: [{ stage_id: "measure", status: "stale" }] };
  assert.equal(stageStatus(execution, "measure"), "stale");
  assert.equal(stageStatus(execution, "missing"), "未実行");
});

test("looks up only the revision lock matching the generic stage id", () => {
  const revision = { stages: [{ stage_id: "score", contract_digest: "sha256:abc" }] };
  assert.equal(revisionStage(revision, "score").contract_digest, "sha256:abc");
  assert.equal(revisionStage(revision, "prepare"), undefined);
});
