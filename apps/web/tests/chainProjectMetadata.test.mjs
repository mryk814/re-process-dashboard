import assert from "node:assert/strict";
import test from "node:test";
import {
  chainAvailability,
  chainStagePath,
  projectOperationDisabled,
  resolveFixedChain,
} from "../src/features/projects/chainProjectMetadata.ts";

const scalarTemplate = {
  definition: { chain_id: "scalar-proof", label: "Scalar proof" },
  revisions: [{
    chain_id: "scalar-proof",
    revision: 2,
    revision_digest: "sha256:revision",
    stages: [
      { stage_id: "prepare" },
      { stage_id: "score" },
    ],
  }],
};

test("resolves a generic fixed revision and derives its ordered stage path", () => {
  const fixed = resolveFixedChain(
    {
      identity_kind: "chain",
      chain_revision_id: "scalar-proof:r2",
      chain_revision_digest: "sha256:revision",
    },
    [scalarTemplate],
  );
  assert.equal(fixed.template, scalarTemplate);
  assert.equal(fixed.revision, scalarTemplate.revisions[0]);
  assert.equal(chainStagePath(fixed.revision), "prepare → score");
});

test("matches availability by explicit Chain ownership and ignores unrelated failures", () => {
  const unrelated = {
    kind: "chain_evaluation",
    owner_kind: "chain",
    owner_resource_id: "welding-chain",
    status: "unavailable",
  };
  const selected = {
    kind: "chain_evaluation",
    owner_kind: "chain",
    owner_resource_id: "scalar-proof",
    status: "available",
  };
  assert.equal(
    chainAvailability([unrelated, selected], "scalar-proof", "chain_evaluation"),
    selected,
  );
  assert.equal(chainAvailability([unrelated], "scalar-proof", "chain_evaluation"), undefined);
});

test("operation guards keep metadata available while prediction respects scientific availability", () => {
  assert.equal(projectOperationDisabled({
    operation: "metadata",
    offline: false,
    taskUnavailable: true,
    subsystemUnavailable: true,
  }), false);
  assert.equal(projectOperationDisabled({
    operation: "prediction",
    offline: false,
    subsystemUnavailable: true,
  }), true);
  assert.equal(projectOperationDisabled({
    operation: "destructive",
    offline: false,
    archived: true,
  }), true);
  assert.equal(projectOperationDisabled({
    operation: "metadata",
    offline: true,
  }), true);
});
