import assert from "node:assert/strict";
import test from "node:test";

import {
  CHECK_CONTRACTS,
  CURRENT_MAIN_ACCEPTANCE_SCHEMA,
  createCurrentMainAcceptanceReceipt,
  notRun,
  passed,
  validateCurrentMainAcceptanceReceipt,
} from "./current-main-acceptance-report.mjs";

const digest = `sha256:${"a".repeat(64)}`;
const cleanTree = { status: "clean", porcelain: "" };

const identityValue = {
  action: "Projectを作成",
  actual_id: "actual-1",
  aria_busy: true,
  available_task: true,
  batch_member_role: "performance",
  batch_run_id: "batch-1",
  blocked_by_stage_id: "B",
  blocked_stage_ids: ["T", "U", "R"],
  candidate_id: "candidate-1",
  candidate_revision: 2,
  canonical_cycle_index: 400,
  changed_result_input_digest: `sha256:${"b".repeat(64)}`,
  current_revision: 3,
  dataset_revision_id: "dataset-revision-1",
  dataset_view_revision_id: "dataset-view-1",
  definition_digest: `sha256:${"c".repeat(64)}`,
  digest,
  draft_id: "draft-1",
  estimator_id: "ridge.v1",
  failed_stage_id: "B",
  fixed_revision: 2,
  format: "csv",
  graph_definition_digest: `sha256:${"d".repeat(64)}`,
  graph_revision_digest: `sha256:${"e".repeat(64)}`,
  graph_revision_id: "graph-1:r2",
  history_round_trip: true,
  label: "準備中…",
  latest_stage_ids: ["A", "B", "T", "U", "R", "W"],
  manifest_digest: `sha256:${"f".repeat(64)}`,
  model_package_manifest_digest: `sha256:${"1".repeat(64)}`,
  model_package_ref_id: "package-ref-1",
  notice: "実測と固定Snapshotは保存済みです",
  objective_definition_digest: `sha256:${"2".repeat(64)}`,
  package_id: "task-ridge-v1",
  point_index: 3,
  profile_digest: `sha256:${"3".repeat(64)}`,
  profile_revision_id: "profile-revision-1",
  previous_result_input_digest: `sha256:${"9".repeat(64)}`,
  project_binding_digest: `sha256:${"4".repeat(64)}`,
  project_binding_revision: 1,
  project_id: "project-1",
  promotable_control_count: 0,
  property: "capacity_percent",
  public_source_url: "https://calce.umd.edu/data",
  purpose: "goal_search",
  ready: true,
  refresh_status: 503,
  replicate_count: 1,
  request_id: "request-1",
  retained_result_input_digest: `sha256:${"5".repeat(64)}`,
  retained_stage_id: "W",
  retained_stage_ids: ["A", "B", "T", "U", "R"],
  retained_result_input_digests: { A: `sha256:${"0".repeat(64)}` },
  revision: 2,
  reused_existing: false,
  row_count: 3131,
  runtime_type: "builtin.linear.v1",
  snapshot_count: 0,
  snapshot_id: "snapshot-1",
  source_revision_digest: `sha256:${"6".repeat(64)}`,
  source_revision_id: "graph-1:r1",
  source_filename: "battery_calce_cs2_cycles.csv",
  source_run_id: "goal-1",
  source_sha256: "7".repeat(64),
  stage_id: "W",
  stale_stage_id: "W",
  status: "complete",
  state: "ready",
  status_code: 409,
  storage_scope: "personal",
  support_visible: true,
  surface: "Data Library",
  target: "capacity_percent",
  task_contract_digest: `sha256:${"8".repeat(64)}`,
  task_id: "current-main-calce-onboarding-v1",
  url_parameter: "candidate_section=actuals",
  valid: true,
  version: 1,
  control_count: 1,
  control_disabled: true,
  visible: true,
};

function realisticJourney(journey, status = "passed") {
  const resources = [];
  const checks = [];
  for (const [id, checkContract] of Object.entries(CHECK_CONTRACTS[journey])) {
    const resourceId = `${id}-evidence`;
    const resource = {
      kind: checkContract.resourceKind,
      id: resourceId,
      identity: Object.fromEntries(checkContract.requiredIdentity.map((field) => [
        field,
        identityValue[field] ?? `${field}-value`,
      ])),
    };
    resources.push(resource);
    checks.push(status === "passed"
      ? passed(id, `${resource.kind}:${resource.id}`, checkContract.assertion)
      : notRun(id, "not exercised", "follow-up"));
  }
  return { resources, checks };
}

test("receipt derives passed from contract-specific evidence and a clean tree", () => {
  const journey = realisticJourney("single-task");
  const receipt = createCurrentMainAcceptanceReceipt({
    journey: "single-task", atlasDigest: digest, ...journey,
    testedTree: cleanTree, commit: "a4695243",
  });
  assert.equal(receipt.schema_version, CURRENT_MAIN_ACCEPTANCE_SCHEMA);
  assert.equal(receipt.status, "passed");
  assert.equal(receipt.checks.length, Object.keys(CHECK_CONTRACTS["single-task"]).length);
  assert.equal(validateCurrentMainAcceptanceReceipt(receipt).status, "passed");
});

test("not_run or a dirty tested tree makes the receipt incomplete", () => {
  const journey = realisticJourney("prediction-graph", "not_run");
  const receipt = createCurrentMainAcceptanceReceipt({
    journey: "prediction-graph", atlasDigest: digest, ...journey,
    testedTree: cleanTree, commit: "a4695243",
  });
  assert.equal(receipt.status, "incomplete");
  const dirty = createCurrentMainAcceptanceReceipt({
    journey: "single-task", atlasDigest: digest, ...realisticJourney("single-task"),
    testedTree: { status: "dirty", porcelain: " M tracked.ts\n" }, commit: "a4695243",
  });
  assert.equal(dirty.status, "incomplete");
});

test("receipt rejects generic, reused, discriminator-free, or incomplete evidence", () => {
  const valid = {
    journey: "single-task", atlasDigest: digest, ...realisticJourney("single-task"),
    testedTree: cleanTree, commit: "a4695243",
  };
  const first = valid.checks[0];
  assert.throws(() => createCurrentMainAcceptanceReceipt({
    ...valid,
    checks: [{ ...first, evidence: [{ ...first.evidence[0], assertion: "generic_pass" }] }, ...valid.checks.slice(1)],
  }), /requires assertion/);
  const comparisonIndex = valid.checks.findIndex(({ id }) => id === "actual_fixed_prediction_distinct");
  const recoveryIndex = valid.checks.findIndex(({ id }) => id === "failure_safe_saved_evidence");
  const comparisonResourceKey = valid.checks[comparisonIndex].evidence[0].resource;
  const recoveryResource = valid.resources.find((resource) => (
    `${resource.kind}:${resource.id}` === valid.checks[recoveryIndex].evidence[0].resource
  ));
  assert.throws(() => createCurrentMainAcceptanceReceipt({
    ...valid,
    resources: valid.resources.map((resource) => (
      `${resource.kind}:${resource.id}` === comparisonResourceKey
        ? { ...resource, identity: { ...resource.identity, ...recoveryResource.identity } }
        : resource
    )),
    checks: valid.checks.map((check, index) => (
      index === recoveryIndex
        ? {
            ...check,
            evidence: [{
              resource: comparisonResourceKey,
              assertion: CHECK_CONTRACTS["single-task"].failure_safe_saved_evidence.assertion,
            }],
          }
        : check
    )),
  }), /cannot satisfy more than one check/);
  const bindingIndex = valid.checks.findIndex(({ id }) => id === "profile_dataset_package_bound");
  const bindingEvidence = valid.checks[bindingIndex].evidence[0].resource;
  assert.throws(() => createCurrentMainAcceptanceReceipt({
    ...valid,
    resources: valid.resources.map((resource) => (
      `${resource.kind}:${resource.id}` === bindingEvidence
        ? { ...resource, identity: { task_id: resource.identity.task_id } }
        : resource
    )),
  }), /evidence identity is missing/);
  assert.throws(() => createCurrentMainAcceptanceReceipt({
    ...valid,
    testedTree: { status: "clean", porcelain: " M tracked.ts\n" },
  }), /status must match porcelain/);
});
