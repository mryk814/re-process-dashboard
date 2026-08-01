import assert from "node:assert/strict";
import test from "node:test";

import { preparedBindingBlockers } from "../src/features/projects/preparedBindingValidation.ts";

const binding = {
  datasetViewId: "view:r1",
  datasetRevisionId: "dataset:r3",
  taskId: "task-v1",
  taskLabel: "Task",
  modelPackageRefId: "package-ref:1",
  sourceSha256: "a".repeat(64),
  sourceFilename: "private.csv",
  estimatorId: "ridge.v1",
  estimatorLabel: "Ridge回帰",
  preparationResult: "new",
  workspaceKind: "branch",
  workspaceDatabasePath: "branch.db",
  reloaded: true,
};

test("exact prepared binding remains creatable", () => {
  assert.deepEqual(preparedBindingBlockers({
    binding,
    dataset: { revisionId: binding.datasetRevisionId, sourceSha256: binding.sourceSha256 },
    taskExists: true,
    modelPackage: { refId: binding.modelPackageRefId, taskId: binding.taskId },
    taskCompatible: true,
    packageCompatible: true,
    estimatorCompatible: true,
  }), []);
});

test("removed or drifted resources block instead of silently falling back", () => {
  const blockers = preparedBindingBlockers({
    binding,
    dataset: { revisionId: "dataset:r4", sourceSha256: "b".repeat(64) },
    taskExists: false,
    modelPackage: { refId: binding.modelPackageRefId, taskId: "other-task" },
    taskCompatible: false,
    packageCompatible: false,
    estimatorCompatible: false,
  });
  assert.equal(blockers.length, 7);
  assert.match(blockers.join("\n"), /現在のviewが一致しません/);
  assert.match(blockers.join("\n"), /Source content/);
  assert.match(blockers.join("\n"), /Prediction Task task-v1 がありません/);
  assert.match(blockers.join("\n"), /このDataset bindingで利用できません/);
  assert.match(blockers.join("\n"), /Estimator/);
});
