import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `export { originMeasurements } from "./features/workbench/originEvidence.ts";`,
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
const { originMeasurements } = module.exports;

test("maps source observations with task measurement aliases", () => {
  const lineage = {
    node: {
      property_summary: {
        "引張強さ": { count: 2, min: 470, mean: 472.5, std: 2.5, median: 472.5, max: 475 },
      },
    },
  };
  const outputs = [{
    key: "TS",
    label: "引張強さ",
    unit: "MPa",
    measurement_keys: ["引張強さ"],
  }];

  assert.deepEqual(originMeasurements(lineage, outputs), [{
    key: "TS",
    label: "TS",
    mean: 472.5,
    std: 2.5,
    count: 2,
    unit: "MPa",
  }]);
});
