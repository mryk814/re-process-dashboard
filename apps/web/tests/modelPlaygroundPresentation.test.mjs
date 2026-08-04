import test from "node:test";
import assert from "node:assert/strict";

import {
  comparisonRows,
  latencyLabel,
  latestAttempts,
} from "../src/features/model-playground/modelPlaygroundPresentation.ts";
import {
  intervalSemantics,
  presentModelPlaygroundPreview,
} from "../src/features/model-playground/modelPlaygroundAdapter.ts";

const target = (mae) => ({
  targetKey: "strength",
  metrics: { mae, rmse: mae * 2 },
  inferenceLabel: "analytic",
  intervalSemantics: "posterior predictive 90%",
});

test("comparison keeps the latest attempt per recipe and one fixed target", () => {
  const attempts = [
    { attemptId: "ridge-1", recipeId: "ridge", recipeLabel: "Ridge", sequence: 1, status: "failed", capabilities: [], targets: [] },
    { attemptId: "ridge-2", recipeId: "ridge", recipeLabel: "Ridge", sequence: 2, status: "completed", capabilities: ["point"], targets: [target(2)] },
    { attemptId: "additive-1", recipeId: "additive", recipeLabel: "Additive", sequence: 1, status: "completed", capabilities: ["point", "interval"], targets: [target(1)] },
  ];

  assert.deepEqual(latestAttempts(attempts).map((item) => item.attemptId), [
    "ridge-2",
    "additive-1",
  ]);
  assert.deepEqual(comparisonRows(attempts, "strength"), [
    { metric: "MAE", values: { ridge: 2, additive: 1 } },
    { metric: "RMSE", values: { ridge: 4, additive: 2 } },
  ]);
});

test("unmeasured prediction latency is not presented as zero", () => {
  assert.equal(latencyLabel(null), "未計測");
  assert.equal(latencyLabel(undefined), "未計測");
  assert.equal(latencyLabel(0), "0 ms");
  assert.equal(latencyLabel(12.34), "12.3 ms");
});

test("preview keeps exact capacity and dependency reasons for unavailable recipes", () => {
  const preview = presentModelPlaygroundPreview({
    context: {
      task_id: "ordinary-x-y",
      profile_revision_id: "profile-r1",
      training_snapshot_id: "snapshot-r1",
      targets: [{
        target_key: "strength",
        validation_plan: { strategy: "grouped_kfold", folds: 3 },
      }],
    },
    recipes: [{
      recipe_id: "exact-gp-rbf.v1",
      recipe_version: "1",
      label: "Exact GP",
      lifecycle: "unavailable",
      availability: "capacity_exceeded",
      reasons: ["row capacity exceeded"],
      comparison_role: "candidate",
      required_dependency: "gpytorch",
      training_cost: "high",
      predictive_capabilities: ["point", "interval"],
      target_readiness: [{
        target_key: "strength",
        reasons: ["5001 rows exceed 2000"],
      }],
      task_structure: "standard_independent_targets",
      effective_parameters: {},
      inference_unavailable_reason: "not executable",
    }],
  });

  assert.equal(preview.recipes[0].executable, false);
  assert.deepEqual(preview.recipes[0].reasons, [
    "row capacity exceeded",
    "strength: 5001 rows exceed 2000",
  ]);
  assert.equal(
    intervalSemantics({ interval_coverage_method: "posterior-predictive-interval" }),
    "新しい1観測のposterior predictive interval",
  );
  assert.equal(
    intervalSemantics({ interval_coverage_method: "nested-grouped-oof-residual-quantiles" }),
    "nested grouped OOF残差分位点によるpredictive interval",
  );
  assert.equal(
    intervalSemantics({ interval_coverage_method: "grouped-fold-predictive-interval" }),
    "grouped outer-foldのposterior predictive interval",
  );
});

test("Model Playground Run and selected target survive a URL round trip", async () => {
  globalThis.window = { location: { search: "?view=model-playground&model_run=run-800&model_target=strength", pathname: "/", hash: "" } };
  const navigation = await import("../src/app/navigation.ts?model-playground");
  const intent = navigation.readNavigationIntent();

  assert.equal(intent.view, "model-playground");
  assert.equal(intent.modelPlaygroundRunId, "run-800");
  assert.equal(intent.modelPlaygroundTarget, "strength");
  assert.equal(navigation.navigationUrl(intent), "/?view=model-playground&model_run=run-800&model_target=strength");
});

test("Model Library fixed context handoff survives reload before Run creation", async () => {
  globalThis.window = { location: { search: "", pathname: "/", hash: "" } };
  const navigation = await import("../src/app/navigation.ts?model-playground-preview");
  const url = navigation.navigationUrl({
    view: "model-playground",
    modelPlaygroundTaskId: "task-v1",
    modelPlaygroundProfileRevisionId: "profile-r3",
    modelPlaygroundTrainingSnapshotId: "snapshot-r7",
  });
  const roundTrip = navigation.readNavigationIntent(
    new URL(url, "http://localhost").search,
  );

  assert.equal(roundTrip.modelPlaygroundTaskId, "task-v1");
  assert.equal(roundTrip.modelPlaygroundProfileRevisionId, "profile-r3");
  assert.equal(roundTrip.modelPlaygroundTrainingSnapshotId, "snapshot-r7");
});
