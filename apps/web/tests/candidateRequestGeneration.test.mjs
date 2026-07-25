import assert from "node:assert/strict";
import { test } from "node:test";
import { CandidateRequestGeneration } from "../src/features/workbench/candidateRequestGeneration.ts";

function deferred() {
  let resolve;
  const promise = new Promise((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

test("a slow prior candidate response cannot replace the active candidate evidence", async () => {
  const requests = new CandidateRequestGeneration();
  const slowA = deferred();
  const fastB = deferred();
  const committed = [];

  const tokenA = requests.activate("project-1", "candidate-A");
  const responseA = slowA.promise.then((value) => {
    if (requests.isCurrent(tokenA)) committed.push(value);
  });

  const tokenB = requests.activate("project-1", "candidate-B");
  const responseB = fastB.promise.then((value) => {
    if (requests.isCurrent(tokenB)) committed.push(value);
  });

  fastB.resolve("candidate-B");
  await responseB;
  slowA.resolve("candidate-A");
  await responseA;

  assert.deepEqual(committed, ["candidate-B"]);
});

test("project changes invalidate candidate actions that are already awaiting a response", () => {
  const requests = new CandidateRequestGeneration();
  const token = requests.activate("project-1", "candidate-A");

  requests.activate("project-2", "candidate-A");

  assert.equal(requests.isCurrent(token), false);
});

test("a newer revision draft invalidates in-flight work for the same candidate", () => {
  const requests = new CandidateRequestGeneration();
  const priorRevision = requests.activate("project-1", "candidate-A");

  const nextRevision = requests.activate("project-1", "candidate-A");

  assert.equal(requests.isCurrent(priorRevision), false);
  assert.equal(requests.isCurrent(nextRevision), true);
});
