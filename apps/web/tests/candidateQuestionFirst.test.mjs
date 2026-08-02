import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `export { candidateInspectorDefaultCollapsed } from "./features/workbench/candidateQuestionFirst.ts";`,
    resolveDir: sourceRoot,
    loader: "ts",
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
const { candidateInspectorDefaultCollapsed } = module.exports;

test("candidate comparison and review start with judgment while explore keeps its editor", () => {
  assert.equal(candidateInspectorDefaultCollapsed("comparison"), true);
  assert.equal(candidateInspectorDefaultCollapsed("review"), true);
  assert.equal(candidateInspectorDefaultCollapsed("explore"), false);
});
