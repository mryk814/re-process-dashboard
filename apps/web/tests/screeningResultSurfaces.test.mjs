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
      import { ScreeningResultSurfaceTabs, ScreeningEvaluatedTable } from "./features/screening/ScreeningResultSurfaces.tsx";
      export const renderTabs = (props) => renderToStaticMarkup(React.createElement(ScreeningResultSurfaceTabs, props));
      export const renderTable = (props) => renderToStaticMarkup(React.createElement(ScreeningEvaluatedTable, props));
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
new Function("module", "exports", "require", bundle.outputFiles[0].text)(
  module,
  module.exports,
  createRequire(import.meta.url),
);
const { renderTabs, renderTable } = module.exports;

test("result surfaces use ordinary pressed buttons instead of incomplete tab semantics", () => {
  const html = renderTabs({
    value: "proposals",
    onChange() {},
    proposalCount: 5,
    evaluatedCount: 256,
    proposalsAvailable: true,
  });
  assert.match(html, /role="group" aria-label="探索結果の表示"/);
  assert.match(html, /aria-pressed="true">提案候補<span>5<\/span>/);
  assert.match(html, />全評価点<span>256<\/span>/);
  assert.doesNotMatch(html, /role="tab"|role="tablist"|role="tabpanel"/);
});

test("proposal entry is disabled when a landscape run has no shortlist", () => {
  const html = renderTabs({
    value: "map",
    onChange() {},
    proposalCount: 10,
    evaluatedCount: 64,
    proposalsAvailable: false,
  });
  assert.match(html, /<button type="button" aria-pressed="false" disabled="">提案候補/);
});

test("all evaluated points distinguish proposed, displayed, and evaluation-only rows", () => {
  const html = renderTable({
    result: {
      id: "run-1",
      target: "TS",
      variables: {
        C: { mode: "range", min: 0, max: 1 },
        Si: { mode: "range", min: 0, max: 1 },
      },
      proposal_pool: [
        {
          pool_index: 0,
          inputs: { C: 0.1, Si: 0.2 },
          acquisition_score: 0.2,
          acquisition_components: { mean: 510 },
          support_status: "supported",
          selected_rank: 1,
        },
        {
          pool_index: 1,
          inputs: { C: 0.2, Si: 0.3 },
          acquisition_score: 0.4,
          acquisition_components: { mean: 520 },
          support_status: "caution",
          selected_rank: 2,
        },
        {
          pool_index: 2,
          inputs: { C: 0.3, Si: 0.4 },
          acquisition_score: 0.8,
          acquisition_components: { mean: 530 },
          support_status: "extrapolated",
        },
      ],
      proposal_selection: {
        selected: [{ pool_index: 0 }],
      },
    },
    axisLabel: (axis) => axis,
    scoreLabel: "目標への近さ",
    targetLabel: "引張強さ",
  });
  assert.match(html, /modelが実際に評価した条件。補間値ではありません。/);
  assert.match(html, /<b>提案候補<\/b>/);
  assert.match(html, />図に表示</);
  assert.match(html, />評価のみ</);
  assert.match(html, />範囲内</);
  assert.match(html, />要確認</);
  assert.match(html, />外挿</);
});
