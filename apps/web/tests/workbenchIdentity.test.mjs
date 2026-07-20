import test from "node:test";
import assert from "node:assert/strict";
import { WorkbenchRequestGuard, workbenchInferenceKey, workbenchRequestKey } from "../src/features/workbench/workbenchIdentity.ts";

const identity = { projectId: "project-a", taskId: "task-a", candidateId: "candidate-a", candidateRevision: 3 };

test("server state identity includes project, task, candidate, revision, and operation", () => {
  assert.notEqual(workbenchRequestKey(identity, "preview"), workbenchRequestKey(identity, "snapshots"));
  assert.notEqual(workbenchRequestKey(identity, "preview"), workbenchRequestKey({ ...identity, candidateRevision: 4 }, "preview"));
  assert.notEqual(workbenchRequestKey(identity, "preview"), workbenchRequestKey({ ...identity, projectId: "project-b" }, "preview"));
});

test("a late response becomes stale on candidate, revision, or operation change", () => {
  const guard = new WorkbenchRequestGuard();
  const oldRequest = guard.begin(identity, "preview");
  assert.equal(oldRequest.isCurrent(), true);
  const nextRequest = guard.begin({ ...identity, candidateId: "candidate-b", candidateRevision: 1 }, "preview");
  assert.equal(oldRequest.isCurrent(), false);
  assert.equal(nextRequest.isCurrent(), true);
  guard.invalidate();
  assert.equal(nextRequest.isCurrent(), false);
});

test("inference identity ignores display-only revisions but changes with canonical inputs", () => {
  const inference = { projectId: "project-a", taskId: "task-a", candidateId: "candidate-a", inputIdentity: "inputs-a" };
  assert.equal(
    workbenchInferenceKey(inference, "preview"),
    workbenchInferenceKey({ ...inference }, "preview"),
  );
  assert.notEqual(
    workbenchInferenceKey(inference, "preview"),
    workbenchInferenceKey({ ...inference, inputIdentity: "inputs-b" }, "preview"),
  );
});
