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
      import { ScreeningProposalSummary } from "./features/screening/ScreeningProposalSummary.tsx";
      export const renderSummary = (props) => renderToStaticMarkup(React.createElement(ScreeningProposalSummary, props));
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
const { renderSummary } = module.exports;

test("proposal summary leads with a Japanese decision summary and nests reproducibility identity", () => {
  const html = renderSummary({
    result: {
      purpose: "goal_search",
      target: "TS",
      target_goal: { direction: "at_least", lower: 500 },
      seed: 42,
      points: Array.from({ length: 48 }, () => ({})),
      proposal_strategy: {
        id: "latin_hypercube_v1",
        version: "1.0.0",
        seed: 42,
        requested_count: 48,
        pool_multiplier: 4,
        support_policy: "supported_first",
      },
      proposal_diagnostics: {
        generated_count: 192,
        valid_count: 180,
        evaluated_count: 48,
        selected_count: 48,
        rejected_count: 12,
        rejection_rate: 0.0625,
        rejected_by_reason: { "resource constraint": 12 },
      },
      design_space_digest: "sha256:1234567890abcdef",
      objective_definition_digest: "sha256:abcdef1234567890",
    },
    targetLabel: "引張強さ",
    showAnotherSample: true,
    onAnotherSample() {},
    onSaveBatch() {},
    batchSaveCount: 0,
  });

  const headline = html.match(/<div class="screening-proposal-headline">([\s\S]*?)<\/div>/)?.[1] ?? "";
  assert.match(headline, /引張強さの下限目標を満たす条件を優先（学習範囲内を優先）/);
  assert.match(headline, /生成 192件 → 制約内 180件 → 提案 48件（除外 12件）/);
  assert.doesNotMatch(headline, /seed|sha256|latin_hypercube|1234567890/);
  assert.match(html, /<summary>再現情報<\/summary>/);
  assert.match(html, /seed 42/);
  assert.match(html, /strategy latin_hypercube_v1 1\.0\.0/);
  assert.match(html, /sha256:1234567890abcdef/);
  assert.match(html, /title="resource constraint">コスト・設備条件を満たさない/);
  assert.match(html, />別サンプル</);
});

test("legacy runs do not present their early-stop rejection count as a rate", () => {
  const html = renderSummary({
    result: {
      seed: 20260719,
      rejection_summary: { "旧制約": 3 },
    },
    showAnotherSample: false,
    onAnotherSample() {},
    onSaveBatch() {},
    batchSaveCount: 0,
  });

  assert.match(html, /除外 3（旧記録・生成総数なし）/);
  assert.match(html, /全生成数に対する除外率は算出できません/);
  assert.match(html, /旧記録の探索結果（支持範囲を確認）/);
  assert.doesNotMatch(html, /有望な条件を優先/);
  assert.doesNotMatch(html, /%/);
});

test("batch details are closed and expand value, diversity, and cost labels", () => {
  const html = renderSummary({
    result: {
      purpose: "experiment_batch",
      target: "TS",
      seed: 7,
      points: [{}],
      proposal_strategy: {
        id: "sobol_ucb_v1",
        version: "1.1.0",
        support_policy: "supported_first",
      },
      proposal_diagnostics: {
        generated_count: 32,
        valid_count: 30,
        evaluated_count: 8,
        selected_count: 8,
        rejected_count: 2,
        rejection_rate: 0.0625,
        rejected_by_reason: {},
      },
      batch_proposal: {
        selector_id: "greedy_value_diversity_v1",
        distance_id: "normalized_rms",
        distance_version: "1.0.0",
        summary: {
          min_pairwise_distance: 0.2,
          estimated_total_cost: 1,
        },
        selected: [{
          order: 1,
          pool_index: 0,
          point_index: 0,
          role: "performance",
          reason: "batch全体の価値で選抜外",
          source: "acquisition_ranked",
          acquisition_component: 0.9,
          diversity_component: 0.4,
          estimated_cost: 1,
          canonical_identity_digest: "sha256:condition",
        }],
        excluded: [{
          reason: "選抜済み条件とのnear-duplicate",
        }],
      },
    },
    targetLabel: "引張強さ",
    showAnotherSample: false,
    onAnotherSample() {},
    onSaveBatch() {},
    batchSaveCount: 1,
  });

  assert.match(html, /<details class="screening-batch-result"><summary>/);
  assert.doesNotMatch(html, /<details class="screening-batch-result" open/);
  assert.match(html, /価値 0\.9/);
  assert.match(html, /多様性 0\.4/);
  assert.match(html, /コスト 1/);
  assert.match(html, /title="選抜済み条件とのnear-duplicate">すでに選んだ条件に近すぎる/);
});
