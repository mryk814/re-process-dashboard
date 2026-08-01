import assert from "node:assert/strict";
import test from "node:test";

import { buildChainGraph, revisionStage, stageBindingCounts, stageStatus } from "../src/features/projects/chainGraphPresentation.ts";

const port = (path, unit = "K") => ({ path, value_kind: "number", quantity: "temperature", basis: null, unit });
const surface = (stage_id, input_ports, output_ports) => ({
  stage_id, status: "available", surface: { stage_kind: "task", contract_id: `${stage_id}/v1`, contract_digest: `sha256:${stage_id}`, input_ports, output_ports },
});
const graph = {
  definition: {
    label: "generic graph", external_inputs: [port("external.feed", "°C")],
    stages: [{ stage_id: "alpha", stage_kind: "task", contract_id: "alpha/v1" }, { stage_id: "beta", stage_kind: "task", contract_id: "beta/v1" }, { stage_id: "gamma", stage_kind: "task", contract_id: "gamma/v1" }],
    bindings: [
      { target_stage_id: "alpha", target_input_path: "feed", source: { source_kind: "external", path: "external.feed" }, conversion: null },
      { target_stage_id: "beta", target_input_path: "feed", source: { source_kind: "external", path: "external.feed" }, conversion: { conversion_id: "c-to-k", source_unit: "°C", target_unit: "K", factor: 1, offset: 273.15 } },
      { target_stage_id: "gamma", target_input_path: "alpha_result", source: { source_kind: "stage_output", stage_id: "alpha", output_key: "result" }, conversion: null },
      { target_stage_id: "gamma", target_input_path: "beta_result", source: { source_kind: "stage_output", stage_id: "beta", output_key: "result" }, conversion: null },
    ],
  },
  revision: { stages: [{ stage_id: "gamma", contract_digest: "sha256:abc" }] },
  stage_contracts: [
    surface("alpha", [port("feed", "°C")], [port("result")]),
    surface("beta", [port("feed")], [port("result")]),
    surface("gamma", [port("alpha_result"), port("beta_result")], [port("score")]),
  ],
};

test("renders scalar ports plus actual branch, merge, and conversion bindings", () => {
  const edges = buildChainGraph(graph);
  assert.equal(edges.length, 4);
  assert.equal(edges[0].source.label, "external.feed");
  assert.equal(edges[0].target.label, "alpha.feed");
  assert.equal(edges[0].sourcePort.value_kind, "number");
  assert.equal(edges[0].targetPort.quantity, "temperature");
  assert.equal(edges[0].branchCount, 2);
  assert.equal(edges[1].binding.conversion.conversion_id, "c-to-k");
  assert.equal(edges[1].binding.conversion.offset, 273.15);
  assert.equal(edges[2].sourcePort.path, "result");
  assert.equal(edges[2].mergeCount, 2);
});

test("marks only a missing fixed stage surface as degraded without inferring a port", () => {
  const degraded = structuredClone(graph);
  degraded.stage_contracts = degraded.stage_contracts.map((item) => item.stage_id === "beta"
    ? { stage_id: "beta", status: "unavailable", reason: "このRevisionのsurfaceは保存されていません。", surface: null }
    : item);
  const edge = buildChainGraph(degraded).find((item) => item.source.label === "beta.result");
  assert.equal(edge.status, "unavailable");
  assert.equal(edge.sourcePort, undefined);
  assert.match(edge.reason, /保存されていません/);
});

test("uses fixed surface port counts and generic revision/live stage lookup", () => {
  assert.deepEqual(stageBindingCounts(graph, "gamma"), { inputs: 2, outputs: 1 });
  assert.deepEqual(stageBindingCounts(graph, "alpha"), { inputs: 1, outputs: 1 });
  assert.equal(stageStatus({ stages: [{ stage_id: "beta", status: "stale" }] }, "beta"), "stale");
  assert.equal(stageStatus(null, "missing"), "未実行");
  assert.equal(revisionStage(graph.revision, "gamma").contract_digest, "sha256:abc");
  assert.equal(revisionStage(graph.revision, "alpha"), undefined);
});
