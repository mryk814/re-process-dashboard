import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  entryPoints: [path.join(sourceRoot, "features/screening/screeningInterpolation.ts")],
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
const { buildScreeningInterpolation } = module.exports;

function denseRun(overrides = {}) {
  const pool = [];
  for (let yIndex = 0; yIndex < 8; yIndex += 1) {
    for (let xIndex = 0; xIndex < 8; xIndex += 1) {
      const x = xIndex / 7;
      const y = yIndex / 7;
      pool.push({
        pool_index: pool.length,
        inputs: { x, y },
        acquisition_score: x + y,
        acquisition_components: { mean: 100 + x * 20 - y * 10 },
        support_status: "supported",
      });
    }
  }
  return {
    target: "TS",
    variables: {
      x: { mode: "range", min: 0, max: 1 },
      y: { mode: "range", min: 0, max: 1 },
    },
    proposal_pool: pool,
    proposal_rejections: [],
    proposal_diagnostics: {
      generated_count: pool.length,
      valid_count: pool.length,
      evaluated_count: pool.length,
      rejected_count: 0,
    },
    ...overrides,
  };
}

test("dense complete two-axis evidence produces a versioned display interpolation", () => {
  const result = buildScreeningInterpolation(denseRun(), "x", "y", "TS");
  assert.equal(result.available, true);
  assert.equal(result.method, "inverse_distance_weighted_display");
  assert.equal(result.version, "1.0.0");
  assert.equal(result.columns, 18);
  assert.equal(result.rows, 12);
  assert.ok(result.cells.length > 150);
  assert.ok(result.cells.every((cell) => cell.neighborCount === 4));
});

test("constraint holes fail closed instead of painting rejected regions", () => {
  const run = denseRun();
  run.proposal_diagnostics.generated_count += 1;
  run.proposal_diagnostics.rejected_count = 1;
  run.proposal_rejections = [{
    pool_index: 64,
    inputs: { x: 0.5, y: 0.5 },
    reason: "constraint",
  }];
  const result = buildScreeningInterpolation(run, "x", "y", "TS");
  assert.deepEqual(
    { available: result.available, reason: result.reason },
    { available: false, reason: "constraint_holes" },
  );
});

test("three varying variables require a real fixed slice", () => {
  const run = denseRun({
    variables: {
      x: { mode: "range", min: 0, max: 1 },
      y: { mode: "range", min: 0, max: 1 },
      z: { mode: "range", min: 0, max: 1 },
    },
  });
  const result = buildScreeningInterpolation(run, "x", "y", "TS");
  assert.equal(result.available, false);
  assert.equal(result.reason, "requires_fixed_slice");
  assert.match(result.message, /固定して再実行/);
});

test("secondary outputs absent from the complete pool stay as points", () => {
  const result = buildScreeningInterpolation(denseRun(), "x", "y", "YS");
  assert.equal(result.available, false);
  assert.equal(result.reason, "metric_not_in_evaluated_pool");
});

test("sparse evidence fails closed", () => {
  const run = denseRun();
  run.proposal_pool = run.proposal_pool.slice(0, 16);
  run.proposal_diagnostics.generated_count = 16;
  run.proposal_diagnostics.valid_count = 16;
  run.proposal_diagnostics.evaluated_count = 16;
  const result = buildScreeningInterpolation(run, "x", "y", "score");
  assert.equal(result.available, false);
  assert.equal(result.reason, "sparse_evaluated_pool");
});
