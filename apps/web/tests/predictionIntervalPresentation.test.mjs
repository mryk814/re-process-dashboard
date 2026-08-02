import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `export { formatPredictionInterval, predictionIntervalLabel, predictionHasInterval } from "./shared/predictionPresentation.ts";`,
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
const { formatPredictionInterval, predictionIntervalLabel, predictionHasInterval } = module.exports;

const base = { target_kind: "continuous", predictive_family: "empirical_quantiles", quantiles: {} };

test("labels conformal, quantile, parametric, and Bayesian intervals without conflating them", () => {
  assert.equal(predictionIntervalLabel({ ...base, interval_method: "conformal", interval_coverage_level: 0.8 }), "Conformal予測区間（80%）");
  assert.equal(predictionIntervalLabel({ ...base, interval_method: "quantile" }), "予測分位点区間（coverage未記録）");
  assert.equal(predictionIntervalLabel({ ...base, interval_method: "parametric" }), "パラメトリック予測区間（coverage未記録）");
  assert.equal(predictionIntervalLabel({ ...base, interval_method: "bayesian" }), "Bayesian予測区間（coverage未記録）");
  assert.equal(
    predictionIntervalLabel({ ...base, target_kind: "binary", interval_method: "bayesian", interval_coverage_level: 0.9 }),
    "Bayesian確率区間（90%）",
  );
  assert.equal(predictionHasInterval({ ...base, interval_method: "conformal" }), true);
});

test("does not manufacture 90% semantics for legacy interval bounds", () => {
  const legacy = { ...base, lower: 1.6, upper: 2.4, quantiles: { "0.05": 1.6, "0.95": 2.4 } };
  assert.equal(predictionIntervalLabel(legacy), "区間の意味は未記録");
  assert.equal(formatPredictionInterval(legacy, String), "区間の意味は未記録 1.6–2.4");
});
