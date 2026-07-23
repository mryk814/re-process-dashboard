import assert from "node:assert/strict";
import test from "node:test";
import {
  curveFamilyScopeIdentity,
  responseCurveSurfaceIdentity,
} from "../src/features/workbench/curveSurfaceIdentity.ts";

test("response curve keeps the rendered surface while a changed input gets a new request identity", () => {
  const base = {
    projectId: "project-1",
    taskId: "annealed-properties-v1",
    candidateId: "candidate-1",
    outputKey: "TS",
    variableId: "composition.C",
    rangeIdentity: "auto:scalar",
  };
  const before = responseCurveSurfaceIdentity({
    ...base,
    candidateRevision: 2,
    inputIdentity: '{"composition":{"C":0.08}}',
  });
  const after = responseCurveSurfaceIdentity({
    ...base,
    candidateRevision: 3,
    inputIdentity: '{"composition":{"C":0.09}}',
  });

  assert.equal(after.storageKey, before.storageKey);
  assert.notEqual(after.requestIdentity, before.requestIdentity);
});

test("response curve does not reuse a rendered surface across candidate or variable scope", () => {
  const base = {
    projectId: "project-1",
    taskId: "annealed-properties-v1",
    candidateId: "candidate-1",
    candidateRevision: 2,
    inputIdentity: "inputs",
    outputKey: "TS",
    variableId: "composition.C",
    rangeIdentity: "auto:scalar",
  };

  assert.notEqual(
    responseCurveSurfaceIdentity({ ...base, projectId: "project-2" }).storageKey,
    responseCurveSurfaceIdentity(base).storageKey,
  );
  assert.notEqual(
    responseCurveSurfaceIdentity({ ...base, candidateId: "candidate-2" }).storageKey,
    responseCurveSurfaceIdentity(base).storageKey,
  );
  assert.notEqual(
    responseCurveSurfaceIdentity({ ...base, variableId: "composition.Mn" }).storageKey,
    responseCurveSurfaceIdentity(base).storageKey,
  );
});

test("curve family scope is stable across input revisions but changes with its visible controls", () => {
  const base = {
    projectId: "project-1",
    taskId: "flank-wear-v1",
    candidateId: "candidate-1",
    axisPath: "process.cutting_distance_m",
    varyId: "composition.C",
    levels: 5,
    outputKeys: "VB",
  };

  assert.equal(curveFamilyScopeIdentity(base), curveFamilyScopeIdentity({ ...base }));
  assert.notEqual(curveFamilyScopeIdentity(base), curveFamilyScopeIdentity({ ...base, levels: 7 }));
});
