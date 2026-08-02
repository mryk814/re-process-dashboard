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
      import {
        initialScreeningResultSurface,
        screeningSelectionSurface,
        ScreeningBatchTable,
        ScreeningEvaluatedTable,
        ScreeningResultSurfaceTabs,
      } from "./features/screening/ScreeningResultSurfaces.tsx";
      export const renderTabs = (props) => renderToStaticMarkup(React.createElement(ScreeningResultSurfaceTabs, props));
      export const renderTable = (props) => renderToStaticMarkup(React.createElement(ScreeningEvaluatedTable, props));
      export const renderBatch = (props) => renderToStaticMarkup(React.createElement(ScreeningBatchTable, props));
      export { initialScreeningResultSurface, screeningSelectionSurface };
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
const {
  initialScreeningResultSurface,
  renderBatch,
  renderTabs,
  renderTable,
  screeningSelectionSurface,
} = module.exports;

test("result surfaces use ordinary pressed buttons instead of incomplete tab semantics", () => {
  const html = renderTabs({
    value: "proposals",
    onChange() {},
    selectionLabel: "提案候補",
    selectionCount: 5,
    evaluatedCount: 256,
    selectionAvailable: true,
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
    selectionLabel: "提案候補",
    selectionCount: 0,
    evaluatedCount: 64,
    selectionAvailable: false,
  });
  assert.match(html, /<button type="button" aria-pressed="false" disabled="">提案候補/);
});

test("only an explicit goal selection opens the proposal surface", () => {
  const explicitGoal = {
    purpose: "goal_search",
    proposal_selection: { selected: [{ pool_index: 4 }, { pool_index: 8 }] },
  };
  assert.deepEqual(screeningSelectionSurface(explicitGoal), {
    kind: "proposal",
    label: "提案候補",
    count: 2,
    available: true,
  });
  assert.equal(initialScreeningResultSurface(explicitGoal), "proposals");
  assert.deepEqual(screeningSelectionSurface({
    purpose: "goal_search",
    proposal_selection: { selected: [] },
  }), {
    kind: "proposal",
    label: "提案候補",
    count: 0,
    available: true,
  });

  const legacyGoal = {
    purpose: undefined,
    proposal_selection: { selected: [{ pool_index: 4 }] },
  };
  assert.deepEqual(screeningSelectionSurface(legacyGoal), {
    kind: "none",
    label: "提案候補",
    count: 0,
    available: false,
  });
  assert.equal(initialScreeningResultSurface(legacyGoal), "map");
});

test("experiment batches have their own visible surface meaning and one-based order", () => {
  const run = {
    purpose: "experiment_batch",
    batch_proposal: {
      selected: [{
        order: 1,
        pool_index: 3,
        point_index: 2,
        source: "acquisition_ranked",
        role: "diversity",
        acquisition_component: 0.8,
        diversity_component: 0.4,
        estimated_cost: 1,
      }],
    },
  };
  assert.deepEqual(screeningSelectionSurface(run), {
    kind: "batch",
    label: "実験バッチ",
    count: 1,
    available: true,
  });
  assert.equal(initialScreeningResultSurface(run), "proposals");
  const html = renderBatch({
    result: run,
    stockedPointIndices: new Set(),
    remainingCandidateCapacity: 1,
    promotionPendingPointIndex: null,
    onPromote() {},
  });
  assert.match(html, /aria-label="実験バッチ"/);
  assert.match(html, /<h3>実験バッチ<\/h3>/);
  assert.match(html, /<th scope="row">1<\/th>/);
  assert.doesNotMatch(html, /<th scope="row">2<\/th>/);
  assert.match(html, />点 3</);
  assert.match(html, />多様性</);
  assert.match(html, /この条件を候補にする/);
  assert.doesNotMatch(html, /提案候補に共通|代表点に共通/);
});

test("all evaluated points distinguish proposed, displayed, and evaluation-only rows", () => {
  const html = renderTable({
    result: {
      id: "run-1",
      purpose: "goal_search",
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

test("all evaluated points name experiment-batch membership separately", () => {
  const html = renderTable({
    result: {
      id: "batch-run",
      purpose: "experiment_batch",
      target: "TS",
      variables: { C: { mode: "range", min: 0, max: 1 } },
      proposal_pool: [{
        pool_index: 0,
        inputs: { C: 0.1 },
        acquisition_score: 0.2,
        acquisition_components: { mean: 510 },
        support_status: "supported",
      }],
      batch_proposal: { selected: [{ pool_index: 0 }] },
    },
    axisLabel: (axis) => axis,
    scoreLabel: "目標への近さ",
    targetLabel: "引張強さ",
  });
  assert.match(html, /<b>実験バッチ<\/b>/);
  assert.doesNotMatch(html, /<b>提案候補<\/b>/);
});
