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
      import { ScreeningRepresentativeTable } from "./features/screening/ScreeningRepresentativeTable.tsx";
      export const renderTable = (props) => renderToStaticMarkup(React.createElement(ScreeningRepresentativeTable, props));
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
const { renderTable } = module.exports;

const prediction = (value) => ({
  value,
  lower: value - 5,
  upper: value + 5,
  unit: "MPa",
  target_kind: "continuous",
});

test("representative points use labeled varying columns and one shared support message", () => {
  const commonMessage = "学習範囲から離れているため、結果を要確認";
  const points = [0, 1].map((index) => ({
    index,
    inputs: {
      "composition.C": 0.1 + index * 0.02,
      "process.internal_temperature": 900,
    },
    prediction: prediction(500 + index * 10),
    predictions: {
      YS: { ...prediction(420 + index * 8), unit: "MPa" },
    },
    support: {
      status: "caution",
      message: commonMessage,
      percentile: 96,
      reference_count: 12,
    },
  }));
  points[0].prediction.lower = -5;
  const html = renderTable({
    result: {
      target: "TS",
      variables: {
        "composition.C": { mode: "range", min: 0.1, max: 0.2 },
        "process.internal_temperature": { mode: "fixed", value: 900 },
      },
      representative_points: points,
    },
    outputs: [
      { key: "TS", label: "引張強さ", unit: "MPa", plausibility_range: { min: 0, max: 2_000 } },
      { key: "YS", label: "降伏強さ", unit: "MPa" },
    ],
    options: [
      { value: "composition.C", label: "C (mass%)" },
      { value: "process.internal_temperature", label: "加熱温度 (°C)" },
    ],
    baseCandidateLabel: "基準案A",
    selectedPointIndices: [],
    stockedPointIndices: new Set(),
    onToggle() {},
  });

  assert.match(html, />C \(mass%\)</);
  assert.match(html, />引張強さ<small>MPa<\/small>/);
  assert.match(html, />降伏強さ<small>MPa<\/small>/);
  assert.match(html, />支持範囲</);
  assert.match(html, /基準案A/);
  assert.match(html, /加熱温度 \(°C\)/);
  assert.doesNotMatch(html, /composition\.C|process\.internal_temperature/);
  assert.equal(html.split(commonMessage).length - 1, 1);
  assert.match(html, /-5\.0–505\.0 MPa · ⚠ 範囲外含む/);
  assert.doesNotMatch(html, /class="implausible-output screening-prediction-cell"/);
});
