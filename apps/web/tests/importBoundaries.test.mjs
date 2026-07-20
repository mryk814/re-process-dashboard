import test from "node:test";
import assert from "node:assert/strict";
import { checkSourceTree, importedSpecifiers, validateImport } from "../scripts/check-import-boundaries.mjs";

test("current frontend source obeys import boundaries", () => {
  assert.deepEqual(checkSourceTree(), []);
});

test("shared and candidate feature dependencies only point inward", () => {
  assert.deepEqual(validateImport("shared/api/client.ts", "features/candidates"), ["shared may only depend on shared or generated modules"]);
  assert.deepEqual(validateImport("shared/api/client.ts", "App"), ["shared may only depend on shared or generated modules"]);
  assert.deepEqual(validateImport("features/candidates/editor.ts", "app/navigation"), ["features must not depend on app or root modules"]);
  assert.deepEqual(validateImport("features/candidates/editor.ts", "App"), ["features must not depend on app or root modules"]);
  assert.deepEqual(validateImport("features/candidates/editor.ts", "features/workbench/prediction"), ["candidates must not depend on another feature"]);
});

test("candidate consumers use the feature public entry", () => {
  assert.deepEqual(validateImport("App.tsx", "features/candidates/CandidateUi"), ["candidate feature consumers must use its public index"]);
  assert.deepEqual(validateImport("App.tsx", "features/candidates"), []);
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
