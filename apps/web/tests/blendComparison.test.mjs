import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `
      export { blendComparisonRows, blendCost } from "./features/workbench/blendComparison.ts";
      export { toApiCandidate } from "./features/candidates/candidateModel.ts";
    `,
    resolveDir: sourceRoot,
    loader: "ts",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(
  module,
  module.exports,
  createRequire(import.meta.url),
);
const { blendComparisonRows, blendCost, toApiCandidate } = module.exports;

const candidate = (items) => ({ blend: { items } });

test("transposed blend comparison defaults to changed union rows", () => {
  const left = candidate([
    { material_id: "RM-1", ratio: 80 },
    { material_id: "RM-2", ratio: 20 },
  ]);
  const right = candidate([
    { material_id: "RM-1", ratio: 80 },
    { material_id: "RM-3", ratio: 20 },
  ]);

  assert.deepEqual(blendComparisonRows([left, right], false), ["RM-2", "RM-3"]);
  assert.deepEqual(blendComparisonRows([left, right], true), ["RM-1", "RM-2", "RM-3"]);
});

test("core blend cost uses the candidate catalog pinned material prices", () => {
  const materials = new Map([
    ["RM-1", { unit_price_yen_per_kg_core: 100 }],
    ["RM-2", { unit_price_yen_per_kg_core: 300 }],
  ]);
  assert.equal(blendCost(candidate([
    { material_id: "RM-1", ratio: 75 },
    { material_id: "RM-2", ratio: 25 },
  ]), materials), 150);
});

test("core blend cost stays unavailable when any used material price is missing", () => {
  const materials = new Map([
    ["RM-1", { unit_price_yen_per_kg_core: 100 }],
  ]);
  assert.equal(blendCost(candidate([
    { material_id: "RM-1", ratio: 75 },
    { material_id: "RM-2", ratio: 25 },
  ]), materials), null);
  assert.equal(blendCost(candidate([
    { material_id: "RM-1", ratio: 100 },
    { material_id: "RM-2", ratio: 0 },
  ]), materials), 100);
});

test("editing another field does not drop sparse blend revision state", () => {
  const raw = {
    name: "配合候補",
    inputs: {
      composition: {},
      process: {},
      categorical: {},
      heat_pattern: null,
      heat_time_basis: "elapsed_time",
    },
    blend: {
      items: [{ material_id: "RM-1", ratio: 100 }],
      scientific_master: { revision: 1 },
      commercial_catalog: { revision: 2 },
      design_space: { revision: 3 },
    },
    editor_state: { locked_material_ids: ["RM-1"] },
    blend_validation: { status: "valid", issues: [] },
    provenance: { source_kind: "direct", source_ref: null },
  };
  const request = toApiCandidate({
    id: "candidate",
    label: "配合候補",
    heatTimeBasis: "elapsed_time",
    heat: [],
    raw,
  });

  assert.equal(request.blend, raw.blend);
  assert.equal(request.editor_state, raw.editor_state);
  assert.equal(request.blend_validation, raw.blend_validation);
});
