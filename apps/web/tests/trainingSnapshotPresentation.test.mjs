import assert from "node:assert/strict";
import test from "node:test";
import {
  collectTrainingTargetFields,
  trainingRecipeIdForRevision,
} from "../src/features/data-library/trainingSnapshotPresentation.ts";

test("Training Snapshot target identity unions every eligibility step", () => {
  assert.deepEqual(collectTrainingTargetFields([
    { kind: "target_eligibility_v1", fields: ["yield", "elongation"] },
    { kind: "required_fields_v1", fields: ["lot_id"] },
    { kind: "target_eligibility_v1", fields: ["elongation", "hardness"] },
  ]), ["yield", "elongation", "hardness"]);
});

test("Training Snapshot uses the recipe of its approved revision, not a newer run", () => {
  const runs = [
    { id: "run-approved", recipe_id: "recipe-v1" },
    { id: "run-newer-unapproved", recipe_id: "recipe-v2" },
  ];
  assert.equal(
    trainingRecipeIdForRevision("run-approved", runs),
    "recipe-v1",
  );
});
