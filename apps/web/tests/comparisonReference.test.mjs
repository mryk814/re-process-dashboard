import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { comparisonValuesDiffer } from "../src/features/candidates/comparisonReference.ts";

test("comparison reference treats matching primitives and missing values as equal", () => {
  assert.equal(comparisonValuesDiffer(1.25, 1.25), false);
  assert.equal(comparisonValuesDiffer("A", "A"), false);
  assert.equal(comparisonValuesDiffer(undefined, null), false);
});

test("comparison reference detects numeric and categorical differences", () => {
  assert.equal(comparisonValuesDiffer(1.25, 1.5), true);
  assert.equal(comparisonValuesDiffer("A", "B"), true);
});

test("comparison table keeps the optional reference separate from candidate selection", async () => {
  const source = await readFile(new URL("../src/features/candidates/CandidateUi.tsx", import.meta.url), "utf8");
  assert.match(source, /を比較の基準にする/);
  assert.match(source, /referenceCandidateId === candidateId \? "" : candidateId/);
  assert.match(source, /reference-difference-cell/);
  assert.match(source, /reference-difference-marker/);
  assert.match(source, /candidates\.some\(\(candidate\) => candidate\.id === referenceCandidateId\)/);
});
