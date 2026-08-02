import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(
  new URL("../src/features/workbench/CandidateWorkspaceControls.tsx", import.meta.url),
  "utf8",
);

test("historical evidence revalidation failure remains distinct from a missing actual output", () => {
  assert.match(source, /candidate-origin-integrity-error/);
  assert.match(source, /historicalActualOutputs\[output\.key\]/);
  assert.match(source, /保存時に固定したactualを表示しています/);
});
