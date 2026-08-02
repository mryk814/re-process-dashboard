import assert from "node:assert/strict";
import test from "node:test";

import {
  predictionGraphDraftContent,
  predictionGraphDraftSummary,
  samePredictionGraphDraft,
  unavailablePredictionGraphReferences,
} from "../src/features/projects/predictionGraphDraftPersistence.ts";

async function navigationModule(search, hash = "") {
  globalThis.window = { location: { search, pathname: "/", hash } };
  return import(`../src/app/navigation.ts?draft-case=${encodeURIComponent(`${search}${hash}`)}`);
}

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

test("scopes exact draft navigation identity to Chain Studio", async () => {
  const studio = await navigationModule("?view=chain-studio&project=default&draft=draft-a");
  const intent = studio.readNavigationIntent();
  assert.equal(intent.draftId, "draft-a");
  assert.equal(
    studio.navigationUrl(intent),
    "/?view=chain-studio&project=default&draft=draft-a",
  );

  const libraryIntent = studio.withView(intent, "data-library");
  assert.equal(libraryIntent.draftId, undefined);
  assert.doesNotMatch(studio.navigationUrl(libraryIntent), /draft=/);

  const leakedQuery = await navigationModule("?view=data-library&draft=draft-a");
  assert.equal(leakedQuery.readNavigationIntent().draftId, undefined);
  assert.equal(leakedQuery.navigationUrl(leakedQuery.readNavigationIntent()), "/?view=data-library");

  const leakedHash = await navigationModule("?view=chain-studio", "#draft=draft-a");
  assert.equal(leakedHash.readNavigationIntent().draftId, undefined);
  assert.equal(leakedHash.navigationUrl(leakedHash.readNavigationIntent()), "/?view=chain-studio");
});

test("reports unavailable stage identity and retained dependent work", () => {
  const graph = {
    ...definition,
    stages: [
      {
        stage_id: "model",
        stage_kind: "task",
        contract_id: "missing-task",
        contract_digest: "sha256:a".padEnd(71, "a"),
        package_manifest_digest: "sha256:b".padEnd(71, "b"),
      },
      {
        stage_id: "downstream",
        stage_kind: "task",
        contract_id: "available-task",
        contract_digest: "sha256:c".padEnd(71, "c"),
        package_manifest_digest: "sha256:d".padEnd(71, "d"),
      },
    ],
    bindings: [{
      target_stage_id: "downstream",
      target_input_path: "x",
      source: { source_kind: "stage_output", stage_id: "model", output_key: "y" },
      conversion: null,
    }],
    decision_outputs: [{
      output_id: "decision-y",
      source_stage_id: "model",
      source_output_key: "y",
      label: "Y",
      group: "decision",
      role: "primary_objective",
      required_for_complete_result: true,
    }],
  };
  const catalog = {
    candidate_adapter_ids: [],
    stages: [{
      stage_kind: "task",
      contract_id: "available-task",
      label: "Available",
      status: "available",
      reason: null,
      surface: null,
      stage_lock: null,
    }],
  };

  assert.deepEqual(unavailablePredictionGraphReferences(graph, catalog), [{
    stage: graph.stages[0],
    reason: "現在のcatalogに参照がありません",
    inboundBindingCount: 0,
    outboundBindingCount: 1,
    decisionOutputCount: 1,
  }, {
    stage: graph.stages[1],
    reason: "Node契約を現在利用できません",
    inboundBindingCount: 1,
    outboundBindingCount: 0,
    decisionOutputCount: 0,
  }]);
});
