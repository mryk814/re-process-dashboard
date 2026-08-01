import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `export { predictionIntervalLabel, predictionHasInterval } from "./shared/predictionPresentation.ts";`,
    resolveDir: sourceRoot,
    loader: "ts",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { predictionIntervalLabel, predictionHasInterval } = module.exports;

const base = { target_kind: "continuous", predictive_family: "empirical_quantiles", quantiles: {} };

test("labels conformal, quantile, parametric, and Bayesian intervals without conflating them", () => {
  assert.equal(predictionIntervalLabel({ ...base, interval_method: "conformal", interval_coverage_level: 0.8 }), "Conformal予測区間（80%）");
  assert.equal(predictionIntervalLabel({ ...base, interval_method: "quantile" }), "予測分位点区間");
  assert.equal(predictionIntervalLabel({ ...base, interval_method: "parametric" }), "パラメトリック予測区間");
  assert.equal(predictionIntervalLabel({ ...base, interval_method: "bayesian" }), "Bayesian credible interval");
  assert.equal(predictionHasInterval({ ...base, interval_method: "conformal" }), true);
});
