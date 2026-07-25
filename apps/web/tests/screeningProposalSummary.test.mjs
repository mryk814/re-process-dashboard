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

test("proposal summary shows one explicit denominator and reproducibility identity", () => {
  const html = renderSummary({
    result: {
      seed: 42,
      proposal_strategy: {
        id: "latin_hypercube_v1",
        version: "1.0.0",
        seed: 42,
        requested_count: 48,
        pool_multiplier: 4,
      },
      proposal_diagnostics: {
        generated_count: 192,
        valid_count: 180,
        evaluated_count: 48,
        rejected_count: 12,
        rejection_rate: 0.0625,
        rejected_by_reason: { "組成合計が範囲外です": 12 },
      },
      design_space_digest: "sha256:1234567890abcdef",
    },
    onAnotherSample() {},
  });

  assert.match(html, /Latin hypercube/);
  assert.match(html, /seed 42/);
  assert.match(html, /生成 192 · 制約内 180 · 評価 48 · 除外 12（6\.3%）/);
  assert.match(html, /space 1234567890/);
  assert.match(html, /組成合計が範囲外です/);
  assert.match(html, />別サンプル</);
});

test("legacy runs do not present their early-stop rejection count as a rate", () => {
  const html = renderSummary({
    result: {
      seed: 20260719,
      rejection_summary: { "旧制約": 3 },
    },
    onAnotherSample() {},
  });

  assert.match(html, /除外 3（旧記録・生成総数なし）/);
  assert.match(html, /全生成数に対する除外率は算出できません/);
  assert.doesNotMatch(html, /%/);
});
