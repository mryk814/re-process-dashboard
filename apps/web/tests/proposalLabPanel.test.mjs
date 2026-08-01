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
      import { ProposalLabPanel } from "./features/screening/ProposalLabPanel.tsx";
      export const renderPanel = (props) => renderToStaticMarkup(React.createElement(ProposalLabPanel, props));
    `,
    resolveDir: sourceRoot,
    loader: "tsx",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  define: {
    "import.meta.env.VITE_API_URL": '""',
  },
});
const module = { exports: {} };
globalThis.window = {};
new Function("module", "exports", "require", bundle.outputFiles[0].text)(
  module,
  module.exports,
  createRequire(import.meta.url),
);
const { renderPanel } = module.exports;

test("Proposal Lab stays separate from production and exposes reproducibility inputs", () => {
  const html = renderPanel({
    projectId: "default",
    runs: [
      {
        id: "run-ucb-31",
        purpose: "goal_search",
        schema_version: "screening-run/v8",
        seed: 31,
        samples: 48,
        proposal_strategy: {
          id: "sobol_ucb_v1",
          pool_multiplier: 2,
        },
      },
      {
        id: "run-ei-31",
        purpose: "goal_search",
        schema_version: "screening-run/v8",
        seed: 31,
        samples: 48,
        proposal_strategy: {
          id: "sobol_ei_v1",
          pool_multiplier: 2,
        },
      },
    ],
  });
  assert.match(html, /<details class="proposal-lab">/);
  assert.match(html, /productionへ自動反映しない/);
  assert.match(html, /acquisition scoreは成功確率ではありません/);
  assert.match(html, /sobol_ucb_v1/);
  assert.match(html, /seed 31 · budget 96/);
  assert.match(html, /採用判定（registry変更なし）/);
  assert.match(html, /2種類以上のstrategyに、同じ2個以上のseedを揃えて選択/);
  assert.match(html, /disabled=""[^>]*>評価記録を保存/);
});
