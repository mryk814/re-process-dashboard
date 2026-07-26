import test from "node:test";
import assert from "node:assert/strict";
import {
  assessOutputValues,
  assessPrediction,
  clampToRange,
  measurementSpreadText,
  resolveOutputDefinition,
} from "../src/shared/outputPresentation.ts";

const output = {
  key: "TS",
  label: "引張強さ",
  unit: "MPa",
  measurement_keys: ["TS[MPa]"],
  plausibility_range: { min: 100, max: 2500 },
  preferred_display_range: { min: 200, max: 1500 },
};

test("resolves canonical and workbook measurement keys through one output contract", () => {
  assert.equal(resolveOutputDefinition([output], "TS"), output);
  assert.equal(resolveOutputDefinition([output], "TS[MPa]"), output);
});

test("uses one canonical-unit evaluator for actual and lineage values", () => {
  assert.equal(assessOutputValues(output, [5201], "実測値").implausible, true);
  assert.match(assessOutputValues(output, [5201], "実測値").warning, /100–2,500 MPa/);
  assert.equal(assessOutputValues(output, [800], "実測値").implausible, false);
});

test("warns when only a prediction interval leaves the plausible range", () => {
  const assessment = assessPrediction(output, { value: 800, lower: 50, upper: 1200, quantiles: {} });
  assert.equal(assessment.implausible, true);
  assert.match(assessment.warning, /^予測区間/);
});

test("a single observation states its repeat count instead of a zero spread", () => {
  const format = (value) => value.toFixed(1);
  const single = measurementSpreadText(0, 1, format);
  assert.equal(single.text, "1点測定");
  assert.match(single.title, /ばらつきは不明/);
  assert.doesNotMatch(single.text, /±/);
});

test("repeated observations keep the standard deviation and the repeat count", () => {
  const format = (value) => value.toFixed(1);
  const repeated = measurementSpreadText(1.25, 3, format);
  assert.equal(repeated.text, "±1.3 · n=3");
  assert.match(repeated.title, /標準偏差 ±1\.3 \/ n=3/);
});

test("a missing standard deviation is reported as unrecorded, not as zero", () => {
  const spread = measurementSpreadText(Number.NaN, 4, (value) => String(value));
  assert.equal(spread.text, "n=4");
  assert.match(spread.title, /記録がありません/);
});

test("clips presentation coordinates without changing raw values", () => {
  const prediction = { value: 5201, lower: -25, upper: 5300, quantiles: { "0.5": 5201 } };
  const before = structuredClone(prediction);
  assert.equal(clampToRange(prediction.value, output.preferred_display_range), 1500);
  assessPrediction(output, prediction);
  assert.deepEqual(prediction, before);
});
