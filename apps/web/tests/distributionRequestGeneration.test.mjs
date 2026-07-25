import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DistributionRequestGeneration,
  distributionMatchesIdentity,
} from "../src/features/workbench/distributionRequestGeneration.ts";

function identity(candidateId, candidateRevision = 1, pointExecutionRequestId = `point-${candidateId}`) {
  return {
    projectId: "chain-project",
    candidateId,
    candidateRevision,
    chainRevisionDigest: `sha256:${"a".repeat(64)}`,
    pointExecutionRequestId,
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

test("a delayed distribution for candidate A cannot replace candidate B", async () => {
  const requests = new DistributionRequestGeneration();
  const slowA = deferred();
  const fastB = deferred();
  const committed = [];

  const identityA = identity("candidate-A");
  const tokenA = requests.activate(identityA);
  const responseA = slowA.promise.then((run) => {
    if (requests.isCurrent(tokenA) && distributionMatchesIdentity(run, identityA)) {
      committed.push(run.provenance.candidate_id);
    }
  });

  const identityB = identity("candidate-B");
  const tokenB = requests.activate(identityB);
  const responseB = fastB.promise.then((run) => {
    if (requests.isCurrent(tokenB) && distributionMatchesIdentity(run, identityB)) {
      committed.push(run.provenance.candidate_id);
    }
  });

  fastB.resolve({ provenance: {
    candidate_id: "candidate-B",
    candidate_revision: 1,
    chain_revision_digest: identityB.chainRevisionDigest,
    point_execution_request_id: identityB.pointExecutionRequestId,
  } });
  await responseB;
  slowA.resolve({ provenance: {
    candidate_id: "candidate-A",
    candidate_revision: 1,
    chain_revision_digest: identityA.chainRevisionDigest,
    point_execution_request_id: identityA.pointExecutionRequestId,
  } });
  await responseA;

  assert.deepEqual(committed, ["candidate-B"]);
});

test("same candidate revision rejects a distribution from an older point execution", () => {
  const current = identity("candidate-A", 2, "point-new");
  assert.equal(distributionMatchesIdentity({
    provenance: {
      candidate_id: "candidate-A",
      candidate_revision: 2,
      chain_revision_digest: current.chainRevisionDigest,
      point_execution_request_id: "point-old",
    },
  }, current), false);
});
