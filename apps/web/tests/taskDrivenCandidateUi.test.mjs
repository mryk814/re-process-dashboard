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
      export const element = (text) => React.createElement("div", null, text);
      export const renderInspector = (props) => renderToStaticMarkup(React.createElement(CandidateInspector, props));
      export const renderComparison = (props) => renderToStaticMarkup(React.createElement(ComparisonTable, props));
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
const { element, renderInspector, renderComparison } = module.exports;

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
  assert.match(comparison, /<b>82%<\/b><small>目標<\/small>/);
  assert.match(comparison, /value="0.10000"/);
  assert.match(comparison, />480.0–520.0<\/span>/);
  assert.match(comparison, /title="90%予測区間 480.0–520.0 \/ モデル由来 ±12 \/ 測定由来 ±8"/);
});
