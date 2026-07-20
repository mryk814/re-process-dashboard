import test from "node:test";
import assert from "node:assert/strict";
import { checkSourceTree, featureCycle, importedSpecifiers, validateImport } from "../scripts/check-import-boundaries.mjs";

test("current frontend source obeys import boundaries", () => {
  assert.deepEqual(checkSourceTree(), []);
});

test("shared and feature dependencies only point inward", () => {
  assert.deepEqual(validateImport("shared/api/client.ts", "features/candidates"), ["shared may only depend on shared or generated modules"]);
  assert.deepEqual(validateImport("shared/api/client.ts", "App"), ["shared may only depend on shared or generated modules"]);
  assert.deepEqual(validateImport("features/candidates/editor.ts", "app/navigation"), ["features must not depend on app or root modules"]);
  assert.deepEqual(validateImport("features/candidates/editor.ts", "App"), ["features must not depend on app or root modules"]);
  assert.deepEqual(validateImport("features/candidates/editor.ts", "features/workbench/prediction"), ["feature dependency candidates->workbench is not allowed"]);
  assert.deepEqual(validateImport("features/workbench/WorkbenchPage.tsx", "features/candidates"), []);
  assert.deepEqual(validateImport("features/quality/QualityPage.tsx", "features/admin"), ["feature dependency quality->admin is not allowed"]);
});

test("all feature consumers use public entries", () => {
  assert.deepEqual(validateImport("app/App.tsx", "features/candidates/CandidateUi"), ["candidates consumers must use its public index"]);
  assert.deepEqual(validateImport("app/App.tsx", "features/candidates"), []);
  assert.deepEqual(validateImport("app/App.tsx", "features/lineage/LineageGraph"), ["lineage consumers must use its public index"]);
  assert.deepEqual(validateImport("features/admin/AdminPage.tsx", "features/quality/QualityPage"), ["quality consumers must use its public index"]);
});

test("the application style entry may load feature-local stylesheets", () => {
  assert.deepEqual(validateImport("app/styles.ts", "features/candidates/candidates.css"), []);
  assert.deepEqual(validateImport("app/App.tsx", "features/candidates/candidates.css"), ["candidates consumers must use its public index"]);
});

test("feature dependency cycles are rejected", () => {
  assert.deepEqual(featureCycle(new Map([
    ["alpha", new Set(["beta"])],
    ["beta", new Set(["gamma"])],
    ["gamma", new Set(["alpha"])],
  ])), ["alpha", "beta", "gamma", "alpha"]);
  assert.equal(featureCycle(new Map([
    ["alpha", new Set(["beta"])],
    ["beta", new Set(["gamma"])],
  ])), null);
});

test("only real syntax nodes are treated as imports", () => {
  const source = `
    // import { hidden } from "./features/candidates/CandidateUi";
    const example = 'import value from "./app/navigation"';
    import { CandidateInspector } from "./features/candidates";
    export type { CandidateViewModel } from "./features/candidates";
    const lazy = import("./shared/api/client");
  `;
  assert.deepEqual(importedSpecifiers(source, "fixture.ts"), [
    "./features/candidates",
    "./features/candidates",
    "./shared/api/client",
  ]);
});
