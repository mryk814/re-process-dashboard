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
      import { ChangeGuideCard } from "./features/admin/ChangeGuideCard.tsx";
      export const renderCard = (entry) => renderToStaticMarkup(
        React.createElement(ChangeGuideCard, { entry, onOpenProfileWorkbench() {} }),
      );
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
const { renderCard } = module.exports;

const entry = {
  id: "decision-activity-new",
  label: "新しいDecision Activityを追加したい",
  risk: "specialist",
  changes: ["parameter / result contract"],
  unchanged: ["TaskDefinition"],
  artifacts: ["OpenAPI", "generated API types"],
  steps: [
    {
      label: "1. Python contract",
      paths: ["backend/src/material_workbench/contracts/decision_activity_contracts.py"],
      outcome: "parameterとresultを型として定義する",
    },
    {
      label: "2. React View",
      paths: ["apps/web/src/features/workbench/decisionActivities/"],
      outcome: "resultを表示する",
    },
  ],
  warnings: [
    "apps/web/src/generated/openapi.jsonとapi-types.tsは直接編集せず、npm run api:generateで再生成します。",
  ],
  commands: [
    {
      executable: "npm",
      arguments: ["run", "api:generate"],
      display_text: "npm run api:generate",
      platform: "cross-platform",
    },
  ],
  documents: [
    "docs/decision-activities.md",
    "docs/learning/chapters/contract-through-stack.qmd",
  ],
  human_review: "新しいActivityか既存Activityの拡張かを判断します。",
};

test("Decision Activity guide renders ordered implementation steps and generated-file warning", () => {
  const html = renderCard(entry);

  assert.match(html, /専門的レビューが必要/);
  assert.ok(
    html.indexOf("1. Python contract") < html.indexOf("2. React View"),
    "implementation steps retain API order",
  );
  assert.match(html, /decision_activity_contracts\.py/);
  assert.match(html, /直接編集せず/);
  assert.match(html, /npm run api:generate/);
  assert.match(html, /docs\/decision-activities\.md/);
  assert.match(html, /contract-through-stack\.qmd/);
});
