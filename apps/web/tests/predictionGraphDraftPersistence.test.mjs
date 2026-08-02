import assert from "node:assert/strict";
import test from "node:test";

import {
  predictionGraphDraftContent,
  predictionGraphDraftSummary,
  samePredictionGraphDraft,
} from "../src/features/projects/predictionGraphDraftPersistence.ts";

const definition = {
  schema_version: "prediction-graph-definition/v1",
  graph_id: "",
  label: "",
  stages: [],
  inputs: [],
  bindings: [],
  decision_outputs: [],
};

test("keeps an incomplete graph and Project name in the mutable draft content", () => {
  const content = predictionGraphDraftContent(definition, "途中のProject");

  assert.deepEqual(content, {
    schema_version: "prediction-graph-draft-content/v1",
    definition,
    project_name: "途中のProject",
  });
  assert.equal(samePredictionGraphDraft(content, { ...content }), true);
  assert.equal(
    samePredictionGraphDraft(content, { ...content, project_name: "手元の変更" }),
    false,
  );
});

test("summarizes the current server revision without replacing local content", () => {
  const document = {
    schema_version: "prediction-graph-draft/v1",
    draft_id: "draft-1",
    version: 4,
    content: predictionGraphDraftContent(
      { ...definition, label: "サーバー版Graph" },
      "サーバー版Project",
    ),
    created_at: "2026-08-02T01:00:00Z",
    updated_at: "2026-08-02T02:00:00Z",
  };

  assert.deepEqual(predictionGraphDraftSummary(document), {
    version: 4,
    graphLabel: "サーバー版Graph",
    projectName: "サーバー版Project",
    updatedAt: "2026-08-02T02:00:00Z",
  });
});
