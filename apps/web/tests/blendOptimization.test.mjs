import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `export * from "./features/workbench/blendOptimization.ts";`,
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
const {
  availableBlendTargetComponents,
  blendTargetValidationError,
  nextBlendTarget,
  serializeBlendTargets,
} = module.exports;

test("multiple unique composition targets serialize as one API array", () => {
  const targets = [
    { id: 0, component: "C", lower: "0.04", upper: "0.08" },
    { id: 1, component: "Mn", lower: "1.2", upper: "1.8" },
  ];

  assert.equal(blendTargetValidationError(targets), "");
  assert.deepEqual(serializeBlendTargets(targets), [
    { component: "C", lower: 0.04, upper: 0.08 },
    { component: "Mn", lower: 1.2, upper: 1.8 },
  ]);
});

test("target choices exclude components already used by another row", () => {
  const targets = [
    { id: 0, component: "C", lower: "0", upper: "100" },
    { id: 1, component: "Mn", lower: "0", upper: "100" },
  ];

  assert.deepEqual(
    availableBlendTargetComponents(["Fe", "C", "Mn"], targets, 1),
    ["Fe", "Mn"],
  );
  assert.deepEqual(nextBlendTarget(["Fe", "C", "Mn"], targets, 2), {
    id: 2,
    component: "Fe",
    lower: "0",
    upper: "100",
  });
});

test("duplicate and invalid target ranges block request serialization", () => {
  const duplicates = [
    { id: 0, component: "C", lower: "0", upper: "1" },
    { id: 1, component: "C", lower: "1", upper: "2" },
  ];
  assert.match(blendTargetValidationError(duplicates), /1回だけ/);
  assert.throws(() => serializeBlendTargets(duplicates), /1回だけ/);

  const descending = [
    { id: 0, component: "Mn", lower: "2", upper: "1" },
  ];
  assert.match(blendTargetValidationError(descending), /昇順/);

  const empty = [
    { id: 0, component: "C", lower: "", upper: "1" },
  ];
  assert.match(blendTargetValidationError(empty), /入力/);
  assert.throws(() => serializeBlendTargets(empty), /入力/);
});
