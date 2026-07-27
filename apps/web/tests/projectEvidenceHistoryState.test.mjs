import assert from "node:assert/strict";
import test from "node:test";
import {
  projectEvidenceHistoryViewState,
  terminalHistoryStage,
} from "../src/features/projects/projectEvidenceHistoryState.ts";

test("history surface resolves loading, error, empty, and ready without inspecting source", () => {
  assert.equal(projectEvidenceHistoryViewState({
    loading: true,
    error: false,
    candidateCount: 0,
  }), "loading");
  assert.equal(projectEvidenceHistoryViewState({
    loading: true,
    error: true,
    candidateCount: 2,
  }), "error");
  assert.equal(projectEvidenceHistoryViewState({
    loading: false,
    error: false,
    candidateCount: 0,
  }), "empty");
  assert.equal(projectEvidenceHistoryViewState({
    loading: false,
    error: false,
    candidateCount: 2,
  }), "ready");
});

test("history selects the last stage that carries defined predictions", () => {
  const stages = [
    {
      stage_id: "prepare",
      output_definitions: [{ key: "prepared" }],
      result: { predictions: { prepared: { value: 1 } } },
    },
    {
      stage_id: "inspect",
      output_definitions: [],
      result: {},
    },
    {
      stage_id: "score",
      output_definitions: [{ key: "score" }],
      result: { predictions: { score: { value: 0.9 } } },
    },
  ];

  assert.equal(terminalHistoryStage(stages)?.stage_id, "score");
  assert.equal(terminalHistoryStage([{
    stage_id: "empty",
    output_definitions: [{ key: "score" }],
    result: {},
  }]), undefined);
});
