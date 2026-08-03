import assert from "node:assert/strict";
import test from "node:test";

import {
  aggregateReceipt,
  assertCleanTree,
  failureReceipt,
  validateAcceptanceEnvironment,
  validateReceipt,
} from "./run-current-main-acceptance.mjs";
import {
  CHECK_CONTRACTS,
  createCurrentMainAcceptanceReceipt,
  passed,
} from "../e2e/current-main-acceptance-report.mjs";

const digest = `sha256:${"a".repeat(64)}`;
const cleanTree = { status: "clean", porcelain: "" };

function completeGraphReceipt() {
  const resources = [];
  const checks = [];
  for (const [id, checkContract] of Object.entries(CHECK_CONTRACTS["prediction-graph"])) {
    const resource = {
      kind: checkContract.resourceKind,
      id: `${id}-evidence`,
      identity: Object.fromEntries(checkContract.requiredIdentity.map((field) => [
        field,
        field.includes("digest") ? `sha256:${"b".repeat(64)}` : `${field}-value`,
      ])),
    };
    resources.push(resource);
    checks.push(passed(
      id,
      `${resource.kind}:${resource.id}`,
      checkContract.assertion,
    ));
  }
  return createCurrentMainAcceptanceReceipt({
    journey: "prediction-graph",
    atlasDigest: digest,
    commit: "commit-1",
    testedTree: cleanTree,
    resources,
    checks,
  });
}

const receipt = completeGraphReceipt();

test("acceptance environment pins Atlas digest and forbids retries", () => {
  assert.deepEqual(validateAcceptanceEnvironment({
    CURRENT_MAIN_CAPABILITY_ATLAS_DIGEST: digest,
    CURRENT_MAIN_ACCEPTANCE_RETRIES: "0",
  }, digest), { atlasDigest: digest, retries: 0 });
  assert.throws(() => validateAcceptanceEnvironment({
    CURRENT_MAIN_CAPABILITY_ATLAS_DIGEST: digest,
    CURRENT_MAIN_ACCEPTANCE_RETRIES: "1",
  }, digest), /must be 0/);
  assert.throws(() => validateAcceptanceEnvironment({
    CURRENT_MAIN_CAPABILITY_ATLAS_DIGEST: `sha256:${"c".repeat(64)}`,
  }, digest), /digest mismatch/);
});

test("runner accepts only passed receipts from the exact commit, Atlas, and clean tree", () => {
  assert.deepEqual(validateReceipt(receipt, {
    spec: "graph", commit: "commit-1", atlasDigest: digest,
  }), receipt);
  assert.throws(() => validateReceipt({ ...receipt, tested_commit: "other" }, {
    spec: "graph", commit: "commit-1", atlasDigest: digest,
  }), /tested commit mismatch/);
  assert.throws(() => assertCleanTree(
    { status: "dirty", porcelain: " M tracked.ts\n" },
    "after Journey A",
  ), /after Journey A.*clean/s);
});

test("failure receipt is machine-readable, fail-closed, and assigns every check", () => {
  const failed = failureReceipt({
    journey: "prediction-graph",
    atlasDigest: digest,
    commit: "commit-1",
    tree: cleanTree,
    phase: "receipt_collection",
    code: "receipt_missing",
    message: "receipt was not written",
    childExitCode: 0,
  });
  assert.equal(failed.status, "incomplete");
  assert.equal(failed.diagnostic.code, "receipt_missing");
  assert.equal(failed.checks.length, Object.keys(CHECK_CONTRACTS["prediction-graph"]).length);
  assert.ok(failed.checks.every(({ status, owner }) => (
    status === "not_run" && owner === "current-main-acceptance follow-up"
  )));
  const aggregate = aggregateReceipt({
    results: [
      { spec: "current-main-acceptance-single-task.spec.ts", receipt: failed },
      { spec: "current-main-acceptance-prediction-graph.spec.ts", receipt },
    ],
    commit: "commit-1",
    atlasDigest: digest,
  });
  assert.equal(aggregate.status, "incomplete");
  assert.equal(aggregate.journeys[0].diagnostic.code, "receipt_missing");
});
