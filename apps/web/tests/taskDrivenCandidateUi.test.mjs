import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `
      import React from "react";
      import { renderToStaticMarkup } from "react-dom/server";
      import { CandidateInspector, ComparisonTable } from "./features/candidates/CandidateUi.tsx";
      import { similarObservationRowKey } from "./features/workbench/similarObservationIdentity.ts";
      export const element = (text) => React.createElement("div", null, text);
      export const renderInspector = (props) => renderToStaticMarkup(React.createElement(CandidateInspector, props));
      export { similarObservationRowKey };
      export const renderComparison = (props) => renderToStaticMarkup(React.createElement(ComparisonTable, {
        decisionCandidateId: "",
        detailedPredictionAvailable: true,
        saveStates: {},
        savedRevisionsByCandidate: {},
        savingCandidateIds: [],
        snapshotHistoryState: "ready",
        onCopy() {},
        onDelete() {},
        onSave() {},
        onConfigureGoals() {},
        onConfigureSupport() {},
        pendingPreviewCount: 0,
        loadingRemainingPreviews: false,
        onLoadRemainingPreviews() {},
        ...props,
      }));
    `,
    resolveDir: sourceRoot,
    loader: "tsx",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { element, renderInspector, renderComparison, similarObservationRowKey } = module.exports;

const numberField = (path, label, order = 0) => ({ path, label, order, kind: "number", unit: path.startsWith("composition") ? "mass%" : "°C", required: true, editable: true, choices: [], allowed_range: { min: 0, max: 1500 }, training_range: { min: 1, max: 1000 } });
const candidate = {
  id: "candidate-1",
  label: "候補A",
  heat: [],
  raw: {
    id: "candidate-1",
    name: "候補A",
    revision: 1,
    project_id: "project",
    inputs: { composition: { C: 0.1 }, process: { temperature: 900 }, categorical: { route: "A" }, heat_pattern: [] },
  },
};

test("annealed definition renders canonical groups, heat pattern, and four outputs", () => {
  const definition = {
    input_groups: [
      { key: "composition", order: 0, label: "組成", fields: [numberField("composition.C", "C")] },
      { key: "process", order: 1, label: "焼鈍条件", fields: [numberField("process.temperature", "温度")] },
      { key: "heat_pattern", order: 2, label: "ヒートパターン", fields: [{ path: "heat_pattern", label: "熱履歴", order: 0, kind: "heat_pattern", required: true, editable: true, choices: [] }] },
      { key: "categorical", order: 3, label: "区分", fields: [{ path: "categorical.route", label: "Route", order: 0, kind: "categorical", required: true, editable: true, choices: ["A", "B"] }] },
    ],
    outputs: ["TS", "YS", "EL", "lambda"].map((key) => ({ key, label: key, unit: key === "TS" || key === "YS" ? "MPa" : "%", goal_direction: "at_least" })),
    display_decimals: { "composition.C": 5, "process.temperature": 1, "output.TS": 1, "output.YS": 1, "output.EL": 1, "output.lambda": 1 },
    fixed_context: [],
  };
  const inspector = renderInspector({ candidate, taskDefinition: definition, saveState: "idle", fieldErrors: [], onInput() {}, heatPattern: element("heat proof") });
  const comparison = renderComparison({ candidates: [candidate], selectedId: candidate.id, taskDefinition: definition, previewsByCandidate: {}, targetValues: {}, onSelect() {}, onName() {}, onInput() {} });
  assert.match(inspector, /data-input-group="composition"/);
  assert.match(inspector, /data-input-group="process"/);
  assert.match(inspector, /data-input-group="categorical"/);
  assert.match(inspector, /heat proof/);
  assert.ok(inspector.indexOf("heat proof") < inspector.indexOf("その他の入力"));
  for (const output of ["TS", "YS", "EL", "lambda"]) assert.match(comparison, new RegExp(`>${output}<`));
  assert.ok(comparison.indexOf("予測結果") < comparison.indexOf("焼鈍条件"));
  assert.match(comparison, /目標を設定/);
  assert.match(comparison, /入力範囲を確認/);
  assert.equal((comparison.match(/↑ 大きい側が目標/g) ?? []).length, 4);
});

test("hot rolling definition omits heat pattern and renders process, categorical, fixed context, and TS only", () => {
  const definition = {
    input_groups: [
      { key: "process", order: 0, label: "熱延条件", fields: [numberField("process.temperature", "温度")] },
      { key: "categorical", order: 1, label: "区分", fields: [{ path: "categorical.route", label: "Route", order: 0, kind: "categorical", required: true, editable: true, choices: ["A", "B", "C"] }] },
    ],
    outputs: [{ key: "TS", label: "引張強さ", unit: "MPa", goal_direction: "at_least" }],
    display_decimals: { "process.temperature": 1, "output.TS": 1 },
    fixed_context: [{ path: "context.line", order: 0, label: "設備", value: "HR-1" }],
  };
  const inspector = renderInspector({ candidate, taskDefinition: definition, saveState: "saved", fieldErrors: [], onInput() {} });
  const comparison = renderComparison({ candidates: [candidate], selectedId: candidate.id, taskDefinition: definition, previewsByCandidate: {}, targetValues: {}, onSelect() {}, onName() {}, onInput() {} });
  assert.doesNotMatch(inspector, /heat_pattern|heat proof/);
  assert.match(inspector, /熱延条件/);
  assert.match(inspector, /Route/);
  assert.match(inspector, /HR-1/);
  assert.match(comparison, /引張強さ/);
  assert.doesNotMatch(comparison, />YS</);
});

test("mixed-unit input groups do not borrow the first field unit for the group heading", () => {
  const definition = {
    input_groups: [{
      key: "process",
      order: 0,
      label: "工程条件",
      fields: [
        numberField("process.temperature", "温度"),
        { ...numberField("process.time", "時間", 1), unit: "s" },
      ],
    }],
    outputs: [],
    display_decimals: { "process.temperature": 1, "process.time": 1 },
    fixed_context: [],
  };
  const inspector = renderInspector({ candidate, taskDefinition: definition, saveState: "idle", fieldErrors: [], onInput() {} });
  assert.match(inspector, /<h3>工程条件<\/h3>.*?<\/div><span><\/span>/);
  assert.doesNotMatch(inspector, /<h3>工程条件<\/h3>.*?<\/div><span>°C<\/span>/);
});

test("a shared input unit is shown once in the group heading", () => {
  const definition = {
    input_groups: [{
      key: "composition",
      order: 0,
      label: "材料成分",
      fields: [
        numberField("composition.Fe", "Fe"),
        numberField("composition.C", "C", 1),
      ],
    }],
    outputs: [],
    display_decimals: { "composition.Fe": 5, "composition.C": 5 },
    fixed_context: [],
  };
  const inspector = renderInspector({ candidate, taskDefinition: definition, saveState: "idle", fieldErrors: [], onInput() {} });
  assert.equal((inspector.match(/mass%/g) ?? []).length, 1);
  assert.match(inspector, /<h3>材料成分<\/h3>.*?<\/div><span>mass%<\/span>/);
});

test("candidate inspector keeps the candidate identity and one shared training-range legend", () => {
  const definition = {
    input_groups: [{
      key: "process",
      order: 0,
      label: "工程条件（安全に単一値へ正規化できた行）",
      fields: [
        numberField("process.temperature", "温度"),
        { ...numberField("process.time", "時間", 1), unit: "s" },
      ],
    }],
    outputs: [],
    display_decimals: { "process.temperature": 1, "process.time": 1 },
    fixed_context: [],
  };
  const inspector = renderInspector({ candidate, taskDefinition: definition, saveState: "idle", fieldErrors: [], onInput() {} });
  assert.match(inspector, /選択候補の入力/);
  assert.match(inspector, /<h2>候補A<\/h2>/);
  assert.match(inspector, /<h3>工程条件<\/h3>/);
  assert.doesNotMatch(inspector, /安全に単一値へ正規化できた行/);
  assert.equal((inspector.match(/緑帯：学習範囲/g) ?? []).length, 1);
});

test("comparison puts the input group that differs between candidates first", () => {
  const definition = {
    input_groups: [
      { key: "composition", order: 0, label: "組成", fields: [numberField("composition.C", "C")] },
      { key: "process", order: 1, label: "熱延条件", fields: [numberField("process.temperature", "温度")] },
    ],
    outputs: [],
    display_decimals: { "composition.C": 5, "process.temperature": 1 },
    fixed_context: [],
  };
  const second = {
    ...candidate,
    id: "candidate-2",
    label: "候補B",
    raw: { ...candidate.raw, id: "candidate-2", name: "候補B", inputs: { ...candidate.raw.inputs, process: { temperature: 950 } } },
  };
  const comparison = renderComparison({ candidates: [candidate, second], selectedId: candidate.id, taskDefinition: definition, previewsByCandidate: {}, targetValues: {}, onSelect() {}, onName() {}, onInput() {} });
  assert.ok(comparison.indexOf(">熱延条件<") < comparison.indexOf(">組成<"));
});

test("conflict state keeps recovery actions next to the draft", () => {
  const definition = {
    input_groups: [{ key: "composition", order: 0, label: "組成", fields: [numberField("composition.C", "C")] }],
    outputs: [],
    display_decimals: { "composition.C": 5 },
    fixed_context: [],
  };
  const inspector = renderInspector({
    candidate,
    taskDefinition: definition,
    saveState: "conflict",
    fieldErrors: [
      { path: "body.inputs.composition.C", message: "範囲外です" },
      { path: "body.name", message: "候補名を確認してください" },
      { path: "body.inputs.heat_pattern.1.time_s", message: "時刻順を確認してください" },
    ],
    onInput() {},
    onReload() {},
    onCopyDraft() {},
  });
  assert.match(inspector, /競合/);
  assert.match(inspector, /再読込/);
  assert.match(inspector, /変更をコピー/);
  assert.match(inspector, /範囲外です/);
  assert.match(inspector, /候補名を確認してください/);
  assert.match(inspector, /時刻順を確認してください/);
});

test("candidate sliders prefer project range, then default range, while keeping the training band", () => {
  const field = {
    ...numberField("composition.C", "C"),
    allowed_range: { min: 0, max: 100 },
    default_range: { min: 0, max: 0.2 },
    training_range: { min: 0.025, max: 0.14 },
  };
  const definition = {
    input_groups: [{ key: "composition", order: 0, label: "組成", fields: [field] }],
    outputs: [],
    display_decimals: { "composition.C": 5 },
    fixed_context: [],
  };
  const defaults = renderInspector({ candidate, taskDefinition: definition, saveState: "idle", fieldErrors: [], onInput() {}, onReload() {}, onCopyDraft() {} });
  assert.match(defaults, /type="range" min="0" max="0.2"/);
  assert.match(defaults, /linear-gradient\(90deg, #dfe6ee 0 12.5%, #6bb69e 12.5% 70%/);

  const configured = renderInspector({ candidate, taskDefinition: definition, inputRanges: { "composition.C": { min: 0, max: 0.5 } }, saveState: "idle", fieldErrors: [], onInput() {}, onReload() {}, onCopyDraft() {} });
  assert.match(configured, /type="range" min="0" max="0.5"/);
  assert.match(configured, /linear-gradient\(90deg, #dfe6ee 0 5%, #6bb69e 5% 28%/);
});

test("similar observations with a shared parent retain distinct row identities", () => {
  const base = {
    observation_id: "",
    observation_ids: [],
    parent_key: "HR-02",
    source: "fixture",
    layer: "historical",
    source_scope: "project_reference_data",
    distance: 0.1,
    process_key: "HR-02",
    process_label: "熱延履歴",
    relation_context_ids: [],
  };
  const first = similarObservationRowKey({ ...base, melt_key: "ME-01", observation_id: "OBS-01" });
  const second = similarObservationRowKey({ ...base, melt_key: "ME-02", observation_id: "OBS-02" });
  assert.notEqual(first, second);
  assert.equal(first, similarObservationRowKey({ ...base, melt_key: "ME-01", observation_id: "OBS-01" }));
});

test("non-editable fields are disabled and goal probability remains visible", () => {
  const readonly = { ...numberField("composition.C", "C"), editable: false };
  const definition = {
    input_groups: [{ key: "composition", order: 0, label: "組成", fields: [readonly] }],
    outputs: [{ key: "TS", label: "引張強さ", unit: "MPa", goal_direction: "at_least" }],
    display_decimals: { "composition.C": 5, "output.TS": 1 },
    fixed_context: [],
  };
  const preview = {
    predictions: { TS: { value: 500, lower: 480, upper: 520, unit: "MPa", target_kind: "continuous", point_statistic: "mean", predictive_family: "normal", quantiles: { "0.05": 480, "0.95": 520 }, goal_probability: 0.82, uncertainty_components: { latent_model_std: 12, observation_noise_std: 8 } } },
    support: { status: "supported" },
  };
  const inspector = renderInspector({ candidate, taskDefinition: definition, saveState: "idle", fieldErrors: [], onInput() {}, onReload() {}, onCopyDraft() {} });
  const comparison = renderComparison({ candidates: [candidate], selectedId: candidate.id, taskDefinition: definition, previewsByCandidate: { [candidate.id]: preview }, targetValues: {}, onSelect() {}, onName() {}, onInput() {} });
  assert.match(inspector, /disabled=""/);
  assert.match(comparison, /disabled=""/);
  assert.match(comparison, /達成確率 82%/);
  assert.match(comparison, /value="0.10000"/);
  assert.match(comparison, /区間 480.0–520.0/);
  assert.match(comparison, /title="90%予測区間 480.0–520.0 \/ モデル由来 ±12 \/ 測定由来 ±8"/);
});

test("physically implausible interval is marked on the interval without condemning an in-range point", () => {
  const definition = {
    input_groups: [{ key: "composition", order: 0, label: "組成", fields: [numberField("composition.C", "C")] }],
    outputs: [{ key: "TS", label: "引張強さ", unit: "MPa", goal_direction: "at_least", plausibility_range: { min: 0, max: 1500 } }],
    display_decimals: { "composition.C": 5, "output.TS": 1 },
    fixed_context: [],
  };
  const preview = {
    predictions: { TS: { value: 628.3, lower: -11.9, upper: 981.1, unit: "MPa", target_kind: "continuous", point_statistic: "mean", predictive_family: "normal", quantiles: { "0.05": -11.9, "0.95": 981.1 }, goal_probability: null } },
    support: { status: "supported" },
  };
  const comparison = renderComparison({ candidates: [candidate], selectedId: candidate.id, taskDefinition: definition, previewsByCandidate: { [candidate.id]: preview }, targetValues: {}, onSelect() {}, onName() {}, onInput() {} });
  assert.match(comparison, /⚠ 範囲外含む/);
  assert.match(comparison, /予測区間が物理範囲外です/);
  assert.doesNotMatch(comparison, /⚠ 物理範囲外/);
});

test("quantile-only output keeps quantile wording and unavailable goal semantics", () => {
  const definition = {
    input_groups: [{ key: "composition", order: 0, label: "組成", fields: [numberField("composition.C", "C")] }],
    outputs: [{ key: "Q", label: "分位予測", unit: "MPa", goal_direction: "at_least" }],
    display_decimals: { "composition.C": 5, "output.Q": 1 },
    fixed_context: [],
  };
  const preview = {
    predictions: { Q: { value: 12, lower: 8, upper: 17, unit: "MPa", target_kind: "continuous", point_statistic: "median", predictive_family: "empirical_quantiles", quantiles: { "0.05": 8, "0.5": 12, "0.95": 17 }, goal_probability: null } },
    support: { status: "supported" },
  };
  const comparison = renderComparison({ candidates: [candidate], selectedId: candidate.id, taskDefinition: definition, previewsByCandidate: { [candidate.id]: preview }, targetValues: { Q: 15 }, onSelect() {}, onName() {}, onInput() {} });
  assert.match(comparison, /title="5–95%分位 8.0–17.0"/);
  assert.match(comparison, /達成率なし/);
  assert.doesNotMatch(comparison, /90%予測区間|計算中|±0/);
});

test("binary count and ordinal outputs avoid regression-only presentation", () => {
  const definition = {
    input_groups: [{ key: "composition", order: 0, label: "入力", fields: [numberField("composition.C", "C")] }],
    outputs: [
      { key: "binary", label: "合格確率", unit: "", goal_direction: "at_least" },
      { key: "count", label: "欠陥数", unit: "個", goal_direction: "at_most" },
      { key: "ordinal", label: "等級", unit: "", goal_direction: "at_least" },
    ],
    display_decimals: { "composition.C": 5, "output.binary": 2, "output.count": 0, "output.ordinal": 1 },
    fixed_context: [],
  };
  const base = { goal_probability: null, uncertainty_components: null };
  const preview = {
    predictions: {
      binary: { ...base, value: 0.42, lower: 0.2, upper: 0.7, unit: "1", target_kind: "binary", point_statistic: "probability", predictive_family: "bernoulli_logit", quantiles: { "0.05": 0.2, "0.95": 0.7 } },
      count: { ...base, value: 3, lower: 1, upper: 7, unit: "個", target_kind: "count", point_statistic: "rate", predictive_family: "poisson_log", quantiles: { "0.05": 1, "0.95": 7 } },
      ordinal: { ...base, value: 1.4, lower: 0, upper: 3, unit: "1", target_kind: "ordinal", point_statistic: "expected_category", predictive_family: "ordinal_logit", quantiles: { "0.05": 0, "0.95": 3 }, categories: ["low", "medium", "high", "very high"] },
    },
    support: { status: "supported" },
  };
  const comparison = renderComparison({ candidates: [candidate], selectedId: candidate.id, taskDefinition: definition, previewsByCandidate: { [candidate.id]: preview }, targetValues: {}, onSelect() {}, onName() {}, onInput() {} });
  assert.match(comparison, />42%/);
  assert.match(comparison, /5–95%確率分位/);
  assert.match(comparison, /medium（期待 1.4）/);
  assert.match(comparison, /5–95%カテゴリ分位/);
  assert.doesNotMatch(comparison, />0.42 <small>1|medium（期待 1.4） <small>1/);
});

test("response curve source renders every declared quantile with explicit labeling", async () => {
  const source = await import("node:fs/promises").then(({ readFile }) => readFile(new URL("../src/features/workbench/ResponseCurvePanels.tsx", import.meta.url), "utf8"));
  assert.match(source, /data-quantile=\{level\}/);
  assert.match(source, /分位線/);
  assert.match(source, /point\.quantiles\[level\]/);
  assert.match(source, /value \* 100/);
  assert.match(source, /校正済み点確率/);
});

test("training data details reads the toggle target synchronously instead of React currentTarget", async () => {
  const source = await import("node:fs/promises").then(({ readFile }) => readFile(new URL("../src/features/admin/ModelTrainingDataInspector.tsx", import.meta.url), "utf8"));
  assert.match(source, /event\.target === detailsRef\.current/);
  assert.match(source, /\(event\.target as HTMLDetailsElement\)\.open/);
  assert.doesNotMatch(source, /event\.currentTarget\.open/);
});

test("candidates without a preview are named as uncomputed and can be finished in one action", () => {
  const definition = {
    input_groups: [{ key: "composition", order: 0, label: "組成", fields: [numberField("composition.C", "C")] }],
    outputs: [{ key: "TS", label: "引張強さ", unit: "MPa", goal_direction: "at_least" }],
    display_decimals: { "composition.C": 5, "output.TS": 1 },
    fixed_context: [],
  };
  const preview = {
    predictions: { TS: { value: 500, lower: 480, upper: 520, unit: "MPa", target_kind: "continuous", point_statistic: "mean", predictive_family: "normal", quantiles: {}, goal_probability: null } },
    support: { status: "supported" },
    model_support: { TS: { status: "supported" } },
  };
  const second = { ...candidate, id: "candidate-2", label: "候補B", raw: { ...candidate.raw, id: "candidate-2", name: "候補B" } };
  const comparison = renderComparison({
    candidates: [candidate, second],
    selectedId: candidate.id,
    taskDefinition: definition,
    previewsByCandidate: { [candidate.id]: preview },
    targetValues: {},
    pendingPreviewCount: 1,
    onSelect() {},
    onName() {},
    onInput() {},
  });
  assert.match(comparison, />未計算</);
  assert.match(comparison, /title="候補Bの引張強さはまだ計算していません"/);
  assert.match(comparison, /未計算 1候補/);
  assert.match(comparison, />残りを計算</);
  assert.match(comparison, /1候補はまだ予測を計算していません/);
  // An incomplete comparison must not present an interval verdict.
  assert.doesNotMatch(comparison, /特性で全候補の予測区間が重なっています/);
});

test("a complete comparison keeps its interval verdict and offers no recomputation", () => {
  const definition = {
    input_groups: [{ key: "composition", order: 0, label: "組成", fields: [numberField("composition.C", "C")] }],
    outputs: [{ key: "TS", label: "引張強さ", unit: "MPa", goal_direction: "at_least" }],
    display_decimals: { "composition.C": 5, "output.TS": 1 },
    fixed_context: [],
  };
  const preview = (value) => ({
    predictions: { TS: { value, lower: value - 20, upper: value + 20, unit: "MPa", target_kind: "continuous", point_statistic: "mean", predictive_family: "normal", quantiles: {}, goal_probability: null } },
    support: { status: "supported" },
    model_support: { TS: { status: "supported" } },
  });
  const second = { ...candidate, id: "candidate-2", label: "候補B", raw: { ...candidate.raw, id: "candidate-2", name: "候補B" } };
  const comparison = renderComparison({
    candidates: [candidate, second],
    selectedId: candidate.id,
    taskDefinition: definition,
    previewsByCandidate: { [candidate.id]: preview(500), [second.id]: preview(505) },
    targetValues: {},
    pendingPreviewCount: 0,
    onSelect() {},
    onName() {},
    onInput() {},
  });
  assert.doesNotMatch(comparison, />残りを計算</);
  assert.doesNotMatch(comparison, /未計算 0候補/);
  assert.match(comparison, /1 \/ 1特性/);
});

test("every comparison pane states which candidate each row belongs to", () => {
  const definition = {
    input_groups: [{ key: "composition", order: 0, label: "組成", fields: [numberField("composition.C", "C")] }],
    outputs: [{ key: "TS", label: "引張強さ", unit: "MPa", goal_direction: "at_least" }],
    display_decimals: { "composition.C": 5, "output.TS": 1 },
    fixed_context: [],
  };
  const preview = {
    predictions: { TS: { value: 500, lower: 480, upper: 520, unit: "MPa", target_kind: "continuous", point_statistic: "mean", predictive_family: "normal", quantiles: {}, goal_probability: null } },
    support: { status: "supported" },
    model_support: { TS: { status: "supported" } },
  };
  const candidateWithHeat = {
    ...candidate,
    heatTimeBasis: "elapsed_time",
    heat: [{ time: 1.5, temperature: 780, stageName: "均熱" }],
  };
  const comparison = renderComparison({
    candidates: [candidateWithHeat],
    selectedId: candidate.id,
    taskDefinition: definition,
    previewsByCandidate: { [candidate.id]: preview },
    targetValues: {},
    onSelect() {},
    onName() {},
    onInput() {},
  });
  // One hidden row header per pane that has no visible candidate column.
  const rowHeaders = comparison.match(/<th scope="row" class="comparison-row-header">候補A<\/th>/g) ?? [];
  assert.equal(rowHeaders.length, 3, "input, prediction and action rows carry a row header");
  assert.match(comparison, /<th scope="row"><input aria-label="候補Aの候補名"/);
  assert.equal((comparison.match(/data-candidate-id="candidate-1"/g) ?? []).length, 4);
  assert.match(comparison, /<th scope="colgroup"/);
  assert.match(comparison, /<th scope="col" class="composition-col">/);
  assert.match(comparison, /<summary>選択候補を1件ずつ読む<\/summary>/);
  assert.match(comparison, /role="region" aria-labelledby="selected-candidate-reading-heading"/);
  assert.match(comparison, /<h4>入力条件<\/h4>/);
  assert.match(comparison, /<h4>予測・支持範囲・目標達成<\/h4>/);
  assert.match(comparison, /<dt>支持範囲<\/dt>/);
  assert.match(comparison, /<dt>目標達成<\/dt>/);
  assert.match(comparison, /<h5>ヒートパターン<\/h5>/);
  assert.match(comparison, /時間基準：経過時間/);
  assert.match(comparison, /<b>均熱<\/b><span>1.5分、780 °C<\/span>/);
  assert.match(comparison, /500.0 MPa/);
  assert.doesNotMatch(comparison, /<tr aria-hidden="true"><th\/><th\/><\/tr>/);
});
