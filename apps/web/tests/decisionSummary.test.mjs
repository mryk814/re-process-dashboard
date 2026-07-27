import test from "node:test";
import assert from "node:assert/strict";
import { buildCandidateDecisionSummary } from "../src/features/candidates/decisionSummary.ts";

function preview(intervals, statuses = {}) {
  return {
    predictions: Object.fromEntries(
      Object.entries(intervals).map(([key, [lower, upper]]) => [key, { lower, upper }]),
    ),
    model_support: Object.fromEntries(
      Object.entries(statuses).map(([key, status]) => [key, { status }]),
    ),
  };
}

test("summarizes support conservatively and detects common interval overlap", () => {
  const summary = buildCandidateDecisionSummary({
    candidateIds: ["a", "b", "c"],
    outputKeys: ["ys", "ts"],
    previewsByCandidate: {
      a: preview({ ys: [400, 500], ts: [700, 760] }, { ys: "supported", ts: "supported" }),
      b: preview({ ys: [450, 550], ts: [750, 800] }, { ys: "caution", ts: "supported" }),
      c: preview({ ys: [480, 520], ts: [790, 830] }, { ys: "supported", ts: "extrapolated" }),
    },
  });

  assert.deepEqual(summary.supportCounts, {
    supported: 1,
    caution: 1,
    extrapolated: 1,
    unknown: 0,
  });
  assert.equal(summary.uniformSupportStatus, null);
  assert.deepEqual(summary.assessableOutputKeys, ["ys", "ts"]);
  assert.deepEqual(summary.overlappingOutputKeys, ["ys"]);
});

test("does not claim interval evidence until every candidate has a valid interval", () => {
  const summary = buildCandidateDecisionSummary({
    candidateIds: ["a", "b"],
    outputKeys: ["ys", "ts"],
    previewsByCandidate: {
      a: preview({ ys: [400, 450], ts: [700, 720] }, { ys: "supported", ts: "supported" }),
      b: preview({ ys: [460, 500] }, { ys: "supported" }),
    },
  });

  assert.deepEqual(summary.assessableOutputKeys, ["ys"]);
  assert.deepEqual(summary.overlappingOutputKeys, []);
  assert.equal(summary.supportCounts.unknown, 1);
  assert.equal(summary.uniformSupportStatus, null);
});

test("reports when every loaded candidate has the same support state", () => {
  const summary = buildCandidateDecisionSummary({
    candidateIds: ["a", "b"],
    outputKeys: ["ys"],
    previewsByCandidate: {
      a: preview({ ys: [400, 450] }, { ys: "supported" }),
      b: preview({ ys: [420, 470] }, { ys: "supported" }),
    },
  });

  assert.equal(summary.uniformSupportStatus, "supported");
});

test("does not invent a uniform support state for an empty comparison", () => {
  const summary = buildCandidateDecisionSummary({
    candidateIds: [],
    outputKeys: ["ys"],
    previewsByCandidate: {},
  });

  assert.equal(summary.uniformSupportStatus, null);
});
