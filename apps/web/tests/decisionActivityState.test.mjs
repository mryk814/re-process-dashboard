import test from "node:test";
import assert from "node:assert/strict";

import {
  acceptsDecisionActivityResponse,
  decisionActivityIdentity,
} from "../src/features/workbench/decisionActivityState.ts";

test("decision activity identity fixes project, candidate, and revision", () => {
  const original = decisionActivityIdentity("project-a", "candidate-a", 3);
  assert.notEqual(original, decisionActivityIdentity("project-b", "candidate-a", 3));
  assert.notEqual(original, decisionActivityIdentity("project-a", "candidate-b", 3));
  assert.notEqual(original, decisionActivityIdentity("project-a", "candidate-a", 4));
});

test("a robustness response for an older candidate revision is ignored", () => {
  const requested = decisionActivityIdentity("project-a", "candidate-a", 3);
  const current = decisionActivityIdentity("project-a", "candidate-a", 4);

  assert.equal(acceptsDecisionActivityResponse(current, requested), false);
  assert.equal(acceptsDecisionActivityResponse(current, current), true);
});
