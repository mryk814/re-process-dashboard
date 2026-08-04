import test from "node:test";
import assert from "node:assert/strict";

import {
  comparisonRows,
  latencyLabel,
  latestAttempts,
} from "../src/features/model-playground/modelPlaygroundPresentation.ts";

const target = (mae) => ({
  targetKey: "strength",
  metrics: { mae, rmse: mae * 2 },
  inferenceLabel: "analytic",
});

test("comparison keeps the latest attempt per recipe and one fixed target", () => {
  const attempts = [
    { attemptId: "ridge-1", recipeId: "ridge", recipeLabel: "Ridge", sequence: 1, status: "failed", targets: [] },
    { attemptId: "ridge-2", recipeId: "ridge", recipeLabel: "Ridge", sequence: 2, status: "completed", targets: [target(2)] },
    { attemptId: "additive-1", recipeId: "additive", recipeLabel: "Additive", sequence: 1, status: "completed", targets: [target(1)] },
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

test("Model Playground Run and selected target survive a URL round trip", async () => {
  globalThis.window = { location: { search: "?view=model-playground&model_run=run-800&model_target=strength", pathname: "/", hash: "" } };
  const navigation = await import("../src/app/navigation.ts?model-playground");
  const intent = navigation.readNavigationIntent();

  assert.equal(intent.view, "model-playground");
  assert.equal(intent.modelPlaygroundRunId, "run-800");
  assert.equal(intent.modelPlaygroundTarget, "strength");
  assert.equal(navigation.navigationUrl(intent), "/?view=model-playground&model_run=run-800&model_target=strength");
});
