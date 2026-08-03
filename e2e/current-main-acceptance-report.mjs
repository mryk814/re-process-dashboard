import { execFileSync } from "node:child_process";
import { writeFile } from "node:fs/promises";

export const CURRENT_MAIN_ACCEPTANCE_SCHEMA = "current-main-acceptance/v2";

const contract = (resourceKind, assertion, requiredIdentity) => Object.freeze({
  resourceKind,
  assertion,
  requiredIdentity: Object.freeze(requiredIdentity),
});

export const CHECK_CONTRACTS = Object.freeze({
  "single-task": Object.freeze({
    capability_atlas_identity: contract(
      "capability_atlas", "atlas_digest_matches",
      ["digest"],
    ),
    public_single_table_onboarded: contract(
      "csv_onboarding_receipt", "ui_prepare_published",
      [
        "state", "task_id", "source_filename", "source_sha256",
        "dataset_revision_id", "format", "row_count", "public_source_url",
      ],
    ),
    onboarded_task_ready: contract(
      "readiness_receipt", "task_available_after_refresh",
      ["task_id", "ready", "available_task"],
    ),
    profile_dataset_package_bound: contract(
      "scientific_binding", "binding_exact",
      [
        "task_id", "profile_revision_id", "profile_digest", "dataset_revision_id",
        "dataset_view_revision_id", "model_package_ref_id", "task_contract_digest",
        "model_package_manifest_digest",
      ],
    ),
    standard_package_published: contract(
      "standard_package_publication", "build_verify_promote_register",
      [
        "package_id", "model_package_ref_id", "manifest_digest", "estimator_id",
        "runtime_type", "storage_scope", "reused_existing",
      ],
    ),
    model_library_handoff: contract(
      "model_library_surface", "package_action_enabled",
      ["task_id", "model_package_ref_id", "action"],
    ),
    representative_loading_state: contract(
      "loading_state", "csv_prepare_loading",
      ["surface", "label", "control_disabled"],
    ),
    candidate_prediction_support: contract(
      "candidate_evaluation", "prediction_support_separate",
      ["candidate_id", "revision", "target", "support_visible"],
    ),
    goal_screening_saved: contract(
      "screening_run", "goal_run_persisted",
      ["run_id", "purpose", "objective_definition_digest"],
    ),
    batch_controls_excluded: contract(
      "batch_exclusion", "controls_not_promotable",
      ["batch_run_id", "control_count", "replicate_count", "promotable_control_count"],
    ),
    batch_point_provenance_promoted: contract(
      "promoted_candidate", "batch_provenance_complete",
      ["candidate_id", "revision", "batch_run_id", "source_run_id", "batch_member_role", "point_index"],
    ),
    actual_fixed_prediction_distinct: contract(
      "actual_evidence", "fixed_prediction_not_current",
      ["actual_id", "snapshot_id", "fixed_revision", "current_revision", "property"],
    ),
    immutable_snapshot_revision: contract(
      "prediction_snapshot", "snapshot_revision_unchanged",
      ["snapshot_id", "candidate_id", "candidate_revision", "canonical_cycle_index"],
    ),
    failure_safe_saved_evidence: contract(
      "actual_evidence", "persistence_confirmed_after_refresh_failure",
      ["actual_id", "snapshot_id", "refresh_status", "notice"],
    ),
    candidate_resume: contract(
      "navigation_resume", "candidate_actual_section_resumed",
      ["candidate_id", "url_parameter", "history_round_trip"],
    ),
  }),
  "prediction-graph": Object.freeze({
    capability_atlas_identity: contract(
      "capability_atlas", "atlas_digest_matches",
      ["digest"],
    ),
    representative_loading_state: contract(
      "loading_state", "graph_draft_loading",
      ["surface", "label", "draft_id", "visible"],
    ),
    model_library_graph_copy: contract(
      "graph_draft_copy", "published_revision_copied",
      ["draft_id", "version", "source_revision_id", "source_revision_digest", "definition_digest"],
    ),
    draft_validated: contract(
      "graph_validation", "validation_valid",
      ["definition_digest", "candidate_adapter_id", "valid"],
    ),
    revision_published: contract(
      "published_graph_revision", "revision_identity_published",
      ["graph_revision_id", "revision", "graph_definition_digest", "revision_digest"],
    ),
    graph_project_candidate_bound: contract(
      "graph_binding", "project_candidate_exact",
      ["project_id", "candidate_id", "candidate_revision", "graph_revision_id", "graph_revision_digest"],
    ),
    direct_failure_and_blocked: contract(
      "failure_partition", "direct_failure_distinct_from_blocked",
      ["request_id", "failed_stage_id", "blocked_stage_ids", "blocked_by_stage_id"],
    ),
    unrelated_branch_retained: contract(
      "branch_retention", "independent_branch_latest",
      ["request_id", "retained_stage_id", "retained_result_input_digest"],
    ),
    partial_snapshot_rejected: contract(
      "snapshot_rejection", "partial_execution_not_snapshotted",
      ["request_id", "status_code", "snapshot_count"],
    ),
    retry_completed: contract(
      "graph_retry", "retry_all_required_latest",
      ["request_id", "status", "latest_stage_ids"],
    ),
    branch_specific_stale: contract(
      "branch_invalidation", "only_affected_branch_recomputed",
      [
        "request_id", "stale_stage_id", "retained_stage_ids",
        "previous_result_input_digest", "changed_result_input_digest",
        "retained_result_input_digests",
      ],
    ),
    graph_snapshot_identity: contract(
      "graph_snapshot", "snapshot_identity_exact",
      [
        "snapshot_id", "graph_revision_id", "graph_revision_digest",
        "project_binding_revision", "project_binding_digest", "candidate_id",
        "candidate_revision",
      ],
    ),
    graph_inspection_resume: contract(
      "navigation_resume", "graph_stage_inspector_resumed",
      ["candidate_id", "stage_id", "url_parameter", "history_round_trip"],
    ),
  }),
});

export const REQUIRED_CHECKS = Object.freeze(Object.fromEntries(
  Object.entries(CHECK_CONTRACTS).map(([journey, checks]) => [
    journey,
    Object.freeze(Object.keys(checks)),
  ]),
));

function requiredString(value, name) {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new TypeError(`${name} must be a non-empty string`);
  }
}

function resourceReceipt(value, name) {
  if (!value || typeof value !== "object") throw new TypeError(`${name} must be an object`);
  requiredString(value.id, `${name}.id`);
  requiredString(value.kind, `${name}.kind`);
  if (!value.identity || typeof value.identity !== "object" || Object.keys(value.identity).length === 0) {
    throw new TypeError(`${name}.identity must be a non-empty object`);
  }
}

function checkReceipt(value, name) {
  if (!value || typeof value !== "object") throw new TypeError(`${name} must be an object`);
  requiredString(value.id, `${name}.id`);
  requiredString(value.owner, `${name}.owner`);
  if (!["passed", "not_run"].includes(value.status)) {
    throw new TypeError(`${name}.status must be passed or not_run`);
  }
  if (value.status === "passed") {
    if (!Array.isArray(value.evidence) || value.evidence.length !== 1) {
      throw new TypeError(`${name}.evidence must contain exactly one check-owned resource`);
    }
    const [evidence] = value.evidence;
    if (!evidence || typeof evidence !== "object") {
      throw new TypeError(`${name}.evidence[0] must be an object`);
    }
    requiredString(evidence.resource, `${name}.evidence[0].resource`);
    requiredString(evidence.assertion, `${name}.evidence[0].assertion`);
  } else {
    requiredString(value.reason, `${name}.reason`);
  }
}

export function passed(id, resource, assertion, owner = "automation") {
  return { id, status: "passed", owner, evidence: [{ resource, assertion }] };
}

export function notRun(id, reason, owner) {
  return { id, status: "not_run", owner, reason };
}

function currentTree() {
  const porcelain = execFileSync("git", ["status", "--porcelain"], { encoding: "utf8" });
  return { status: porcelain.length === 0 ? "clean" : "dirty", porcelain };
}

export function createCurrentMainAcceptanceReceipt({
  journey,
  atlasDigest,
  resources,
  checks,
  testedTree = currentTree(),
  diagnostic,
  commit = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
}) {
  if (!Object.hasOwn(CHECK_CONTRACTS, journey)) throw new TypeError(`unknown journey: ${journey}`);
  requiredString(commit, "commit");
  if (!/^sha256:[a-f0-9]{64}$/.test(atlasDigest)) {
    throw new TypeError("atlasDigest must be sha256:<64 lowercase hex>");
  }
  if (!testedTree || !["clean", "dirty"].includes(testedTree.status) || typeof testedTree.porcelain !== "string") {
    throw new TypeError("testedTree must record clean/dirty status and porcelain");
  }
  if ((testedTree.status === "clean") !== (testedTree.porcelain.length === 0)) {
    throw new TypeError("testedTree status must match porcelain");
  }
  if (!Array.isArray(resources) || resources.length === 0) {
    throw new TypeError("resources must be a non-empty array");
  }
  resources.forEach((value, index) => resourceReceipt(value, `resources[${index}]`));
  const resourceByKey = new Map(resources.map((resource) => [`${resource.kind}:${resource.id}`, resource]));
  if (resourceByKey.size !== resources.length) throw new TypeError("resources must have unique kind/id pairs");
  if (!Array.isArray(checks)) throw new TypeError("checks must be an array");
  checks.forEach((value, index) => checkReceipt(value, `checks[${index}]`));
  const contracts = CHECK_CONTRACTS[journey];
  const expected = Object.keys(contracts);
  const received = checks.map(({ id }) => id);
  if (new Set(received).size !== received.length) throw new TypeError("checks must have unique ids");
  if (expected.some((id) => !received.includes(id)) || received.some((id) => !expected.includes(id))) {
    throw new TypeError(`checks must exactly cover required ${journey} checks`);
  }
  const claimedResources = new Set();
  for (const check of checks) {
    if (check.status !== "passed") continue;
    const checkContract = contracts[check.id];
    const evidence = check.evidence[0];
    const resource = resourceByKey.get(evidence.resource);
    if (!resource) throw new TypeError(`${check.id} references evidence absent from resources`);
    if (resource.kind !== checkContract.resourceKind) {
      throw new TypeError(`${check.id} requires ${checkContract.resourceKind} evidence`);
    }
    if (evidence.assertion !== checkContract.assertion) {
      throw new TypeError(`${check.id} requires assertion ${checkContract.assertion}`);
    }
    const missing = checkContract.requiredIdentity.filter((field) => !Object.hasOwn(resource.identity, field));
    if (missing.length > 0) {
      throw new TypeError(`${check.id} evidence identity is missing: ${missing.join(", ")}`);
    }
    if (claimedResources.has(evidence.resource)) {
      throw new TypeError(`${evidence.resource} cannot satisfy more than one check`);
    }
    claimedResources.add(evidence.resource);
  }
  const orderedChecks = expected.map((id) => checks.find((check) => check.id === id));
  const passedAll = orderedChecks.every(({ status }) => status === "passed");

  return {
    schema_version: CURRENT_MAIN_ACCEPTANCE_SCHEMA,
    status: passedAll && testedTree.status === "clean" ? "passed" : "incomplete",
    journey,
    tested_commit: commit,
    tested_tree: testedTree,
    capability_atlas_digest: atlasDigest,
    resources,
    checks: orderedChecks,
    ...(diagnostic ? { diagnostic } : {}),
    manual_visual_judgment: {
      status: "not_run",
      owner: "user",
      reason: "実アプリを触る判断はユーザー担当。自動化は保存・復元・表示契約まで。",
    },
  };
}

export function validateCurrentMainAcceptanceReceipt(receipt) {
  if (!receipt || typeof receipt !== "object") throw new TypeError("receipt must be an object");
  if (receipt.schema_version !== CURRENT_MAIN_ACCEPTANCE_SCHEMA) {
    throw new TypeError(`receipt must use ${CURRENT_MAIN_ACCEPTANCE_SCHEMA}`);
  }
  const validated = createCurrentMainAcceptanceReceipt({
    journey: receipt.journey,
    atlasDigest: receipt.capability_atlas_digest,
    resources: receipt.resources,
    checks: receipt.checks,
    testedTree: receipt.tested_tree,
    diagnostic: receipt.diagnostic,
    commit: receipt.tested_commit,
  });
  if (receipt.status !== validated.status) throw new TypeError("receipt status is inconsistent with checks/tree");
  return validated;
}

export async function writeCurrentMainAcceptanceReceipt(testInfo, receipt) {
  const body = `${JSON.stringify(receipt, null, 2)}\n`;
  const path = testInfo.outputPath("current-main-acceptance-receipt.json");
  await writeFile(path, body, "utf8");
  await testInfo.attach("current-main-acceptance-receipt", {
    body,
    contentType: "application/json",
  });
  return path;
}
