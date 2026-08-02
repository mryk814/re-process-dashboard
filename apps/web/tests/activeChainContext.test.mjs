import assert from "node:assert/strict";
import test from "node:test";
import {
  resolveActiveChainContext,
} from "../src/features/projects/activeChainContext.ts";

const identity = {
  identity_kind: "chain",
  chain_revision_id: "scalar-proof:r2",
  chain_revision_digest: "sha256:revision",
};
const revision = {
  schema_version: "chain-revision/v1",
  chain_id: "scalar-proof",
  revision: 2,
  stages: [{ stage_id: "prepare" }, { stage_id: "score" }],
};
const templates = [{
  definition: {
    schema_version: "chain-definition/v1",
    chain_id: "scalar-proof",
    label: "Scalar proof",
  },
  revisions: [revision],
}];

const resolve = (overrides = {}) => resolveActiveChainContext({
  identity,
  templates,
  templatesLoaded: true,
  availability: [],
  availabilityLoaded: true,
  availabilityError: false,
  offline: false,
  ...overrides,
});

test("a Chain stays loading until both templates and availability are known", () => {
  assert.equal(resolve({ templatesLoaded: false }).status, "loading");
  assert.equal(resolve({ availabilityLoaded: false }).status, "loading");
});

test("an unrelated subsystem failure does not disable the selected Chain", () => {
  const context = resolve({
    availability: [{
      kind: "chain",
      owner_kind: "chain",
      owner_resource_id: "another-chain",
      status: "unavailable",
    }],
  });
  assert.equal(context.status, "available");
  assert.equal(context.revision, revision);
});

test("the selected Chain becomes unavailable only after explicit ownership matches", () => {
  const unavailable = {
    kind: "chain",
    owner_kind: "chain",
    owner_resource_id: "scalar-proof",
    status: "unavailable",
  };
  const context = resolve({ availability: [unavailable] });
  assert.equal(context.status, "unavailable");
  assert.equal(context.availability, unavailable);
});

test("offline and unresolved fixed revisions never mount as available", () => {
  assert.equal(resolve({ offline: true }).status, "offline");
  assert.equal(resolve({ availabilityError: true }).status, "error");
  assert.equal(resolve({ templates: [] }).status, "unresolved");
});
