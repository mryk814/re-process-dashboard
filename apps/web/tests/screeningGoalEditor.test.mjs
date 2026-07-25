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
      import { ScreeningGoalEditor, screeningGoalFromDraft } from "./features/screening/ScreeningGoalEditor.tsx";
      export { screeningGoalFromDraft };
      export const renderEditor = (props) => renderToStaticMarkup(React.createElement(ScreeningGoalEditor, props));
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
const { renderEditor, screeningGoalFromDraft } = module.exports;

test("goal drafts serialize all directional rules without legacy scalar fields", () => {
  assert.deepEqual(
    screeningGoalFromDraft({ direction: "at_least", lower: "450", upper: "" }),
    { direction: "at_least", lower: 450 },
  );
  assert.deepEqual(
    screeningGoalFromDraft({ direction: "at_most", lower: "", upper: "550" }),
    { direction: "at_most", upper: 550 },
  );
  assert.deepEqual(
    screeningGoalFromDraft({ direction: "between", lower: "450", upper: "550" }),
    { direction: "between", lower: 450, upper: 550 },
  );
  assert.equal(screeningGoalFromDraft({ direction: "between", lower: "", upper: "" }), null);
  assert.throws(
    () => screeningGoalFromDraft({ direction: "between", lower: "550", upper: "450" }),
    /下限より上限/,
  );
});

test("range goal editor names both bounds and exposes the three rules", () => {
  const html = renderEditor({
    label: "主目標: 引張強さ",
    unit: "MPa",
    value: { direction: "between", lower: "450", upper: "550" },
    onChange() {},
  });

  assert.match(html, /下限以上/);
  assert.match(html, /上限以下/);
  assert.match(html, /範囲内/);
  assert.match(html, /主目標: 引張強さの下限/);
  assert.match(html, /主目標: 引張強さの上限/);
});
