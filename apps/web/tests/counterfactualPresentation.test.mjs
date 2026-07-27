import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  entryPoints: [
    path.join(
      sourceRoot,
      "features/workbench/decisionActivities/counterfactualPresentation.ts",
    ),
  ],
  bundle: true,
  format: "esm",
  platform: "node",
  write: false,
});
const { presentCounterfactualTarget } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
const number = (value) => value.toFixed(1);

test("binary shortfall uses percentage points beside a probability prediction", () => {
  const presentation = presentCounterfactualTarget({
    target: "failure",
    unit: "1",
    predicted_value: 0.4,
    prediction: {
      value: 0.4,
      lower: 0.2,
      upper: 0.6,
      unit: "1",
      target_kind: "binary",
      point_statistic: "probability",
      predictive_family: "bernoulli_logit",
      quantiles: { "0.05": 0.2, "0.95": 0.6 },
      categories: [],
    },
    achieved: false,
    normalized_shortfall: 0.1,
    shortfall: 0.1,
    role: "primary_objective",
  }, "異常確率", number);

  assert.equal(presentation.point, "40.0%");
  assert.equal(presentation.state, "未達（あと 10.0パーセントポイント）");
  assert.match(presentation.interval, /20\.0%–60\.0%/);
});

test("ordinal shortfall is named as a category distance", () => {
  const presentation = presentCounterfactualTarget({
    target: "grade",
    unit: "1",
    predicted_value: 1.2,
    prediction: {
      value: 1.2,
      lower: 0,
      upper: 2,
      unit: "1",
      target_kind: "ordinal",
      point_statistic: "expected_category",
      predictive_family: "ordinal_logit",
      quantiles: { "0.05": 0, "0.95": 2 },
      categories: ["低", "中", "高"],
    },
    achieved: false,
    normalized_shortfall: 0.2,
    shortfall: 0.8,
    role: "hard_outcome_constraint",
  }, "等級", number);

  assert.match(presentation.point, /^中（期待 1\.2）$/);
  assert.equal(presentation.state, "未達（あと カテゴリ差 0.8）");
});

test("legacy target values are not presented as canonical point predictions", () => {
  const presentation = presentCounterfactualTarget({
    target: "legacy",
    unit: "1",
    predicted_value: 0.4,
    prediction: null,
    achieved: true,
    normalized_shortfall: 0,
    shortfall: null,
    role: "primary_objective",
  }, "旧特性", number);

  assert.equal(presentation.pointKind, "旧記録の要約値");
  assert.equal(presentation.point, "0.4");
  assert.match(presentation.accessibleName, /旧記録の要約値 0\.4/);
  assert.match(presentation.interval, /区間情報なし/);
});
