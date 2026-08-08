import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import {
  assertRedactedPayload,
  buildContextObservations,
  canonicalJson,
  compareResults,
  loadCaseCatalog,
  materializeFixture,
  redactSensitiveText,
  validateComparisonCoverage,
  validateResult,
  validateResultCoverage,
} from "./agent-throughput-eval.mjs";
import {
  createVerificationReceipt,
  createVerificationReceiptIdentity,
} from "./verification-receipts.mjs";

const repoRoot = resolve(import.meta.dirname, "..");

function clone(value) {
  return structuredClone(value);
}

function observedPair(caseId = "docs-typo") {
  const catalog = loadCaseCatalog({ repoRoot });
  const baseline = buildContextObservations({ repoRoot, mode: "baseline", catalog })
    .find((item) => item.case.id === caseId);
  const candidate = buildContextObservations({ repoRoot, mode: "current", catalog })
    .find((item) => item.case.id === caseId);
  return { catalog, baseline, candidate };
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function writeEvidence(repo, path, value) {
  const target = join(repo, path);
  mkdirSync(dirname(target), { recursive: true });
  const contents = `${JSON.stringify(value, null, 2)}\n`;
  writeFileSync(target, contents, "utf8");
  return { locator: path.replaceAll("\\", "/"), digest: sha256(contents) };
}

function fixtureEnvironment(label = "fixture") {
  return {
    schema_version: "verification-environment/v1",
    os: label,
    arch: "fixture",
    node: "fixture",
    python: null,
    uv: null,
    variables: {},
    result_affecting_variables: [],
    lockfile_digests: {},
  };
}

function writeRunReceipt(result, evidenceRoot) {
  const catalog = loadCaseCatalog({ repoRoot });
  const item = catalog.cases.find((candidate) => candidate.id === result.case.id);
  const identity = {
    schema_version: "agent-throughput-run-receipt/v1",
    source_kind: "codex_session_receipt",
    run_id: result.run_id,
    case_id: result.case.id,
    case_version: result.case.version,
    catalog_digest: result.case.catalog_digest,
    repository_commit: result.conditions.repository_commit,
    prompt_digest: result.case.prompt_digest,
    fixture: {
      patch_digest: item.fixture.patch_digest,
      materialized_diff_digest: item.fixture.materialized_diff_digest,
    },
    profile: {
      profile_id: result.profile.id,
      model_family: result.profile.model_family,
      reasoning_setting: result.profile.reasoning_setting,
      guidance_source_ref: result.profile.guidance_identity.source_ref,
      guidance_digest: result.profile.guidance_identity.root_instruction_digest,
      skill_tree_digest: result.profile.skill_inventory_identity.tree_digest,
    },
    session: {
      session_identity: result.conditions.session_identity,
      workspace_identity: result.conditions.workspace_identity,
      fresh_session: result.conditions.fresh_session,
      fresh_workspace: result.conditions.fresh_workspace,
    },
    metrics: Object.fromEntries([
      "first_edit_elapsed_ms", "first_edit_read_file_count", "first_edit_tool_call_count",
      "total_tool_call_count", "duplicate_command_count", "local_verification_duration_ms",
      "total_elapsed_ms", "edit_to_pass_iteration_count", "changed_file_count", "changed_line_count",
      "input_tokens", "output_tokens", "command_observations",
    ].map((field) => [field, result.metrics[field]])),
    provider_usage: {
      status: result.metrics.input_tokens === null || result.metrics.output_tokens === null ? "not_available" : "measured",
      input_tokens: result.metrics.input_tokens,
      output_tokens: result.metrics.output_tokens,
      provenance: result.metrics.input_tokens === null || result.metrics.output_tokens === null
        ? null
        : { kind: "codex_session_usage", locator: `codex-session-receipt:${result.run_id}`, digest: sha256(`usage:${result.run_id}`) },
    },
    human_review: {
      reviewer_kind: result.quality.human_review.reviewer_kind,
      completed_at: result.quality.human_review.completed_at,
      artifact_digest: result.quality.human_review.provenance?.digest ?? null,
    },
    recorded_at: "2026-08-08T00:00:00.000Z",
  };
  const receipt = { ...identity, content_digest: sha256(canonicalJson(identity)) };
  const locator = `evidence/${result.run_id}-run-receipt.json`;
  writeEvidence(evidenceRoot, locator, receipt);
  result.run_receipt = { status: "verified", locator, digest: receipt.content_digest };
  return receipt;
}

function writePassedReceipts(result, evidenceRoot, {
  commitSha = result.conditions.repository_commit,
  environment = fixtureEnvironment(),
  firstCommandArgv = null,
  catalogDigestOverride = null,
} = {}) {
  const catalog = loadCaseCatalog({ repoRoot });
  const item = catalog.cases.find((candidate) => candidate.id === result.case.id);
  const runReceipt = writeRunReceipt(result, evidenceRoot);
  const environmentDigest = sha256(canonicalJson(environment));
  result.conditions.environment_identity_digest = environmentDigest;
  result.verification.receipts = item.required_focused_evidence.map((gate, index) => {
    const identity = createVerificationReceiptIdentity({
      repoRoot: evidenceRoot,
      commitSha,
      gateId: gate.gate_id,
      commandArgv: index === 0 && firstCommandArgv ? firstCommandArgv : gate.command_argv,
      inputPaths: [],
      catalogDigest: catalogDigestOverride ?? runReceipt.content_digest,
      environment,
      gitState: {
        tracked_diff_digest: "1".repeat(64),
        status_digest: "2".repeat(64),
        untracked_input_digest: "3".repeat(64),
        dirty_tree_digest: "4".repeat(64),
      },
    });
    const receipt = createVerificationReceipt({ identity, status: "passed", exitCode: 0, durationSeconds: 3, createdAt: "2026-08-08T00:00:00.000Z" });
    const receiptLocator = `evidence/${result.run_id}-${gate.gate_id}-${receipt.receipt_id.slice(0, 8)}.json`;
    writeEvidence(evidenceRoot, receiptLocator, receipt);
    return {
      gate_id: gate.gate_id,
      receipt_digest: receipt.content_digest,
      receipt_locator: receiptLocator,
      environment_identity_digest: environmentDigest,
      duration_ms: receipt.duration_seconds * 1000,
    };
  });
  result.verification.duration_ms = result.verification.receipts.reduce((total, receipt) => total + receipt.duration_ms, 0);
  return result.verification.receipts;
}

function completeAgentRun(result, { profileId, evidenceRoot, metrics = {}, findings = [] } = {}) {
  const completed = clone(result);
  completed.run_id = `${profileId}-${result.case.id}`;
  completed.observation_kind = "agent_run";
  completed.profile.id = profileId;
  completed.profile.model_family = "gpt-5.6-sol";
  completed.profile.reasoning_setting = "medium";
  completed.profile.guidance_identity.materialization = "agent_run";
  completed.conditions.fresh_session = true;
  completed.conditions.fresh_workspace = true;
  completed.conditions.session_identity = `${profileId}-session`;
  completed.conditions.workspace_identity = `${profileId}-workspace`;
  const catalog = loadCaseCatalog({ repoRoot });
  const item = catalog.cases.find((candidate) => candidate.id === completed.case.id);
  const gateDuration = item.required_focused_evidence.length * 3000;
  const gateCommands = item.required_focused_evidence.map((gate) => ({
    command_digest: sha256(canonicalJson({ gate_id: gate.gate_id, command_argv: gate.command_argv })),
    count: 1,
    duration_ms: 3000,
  }));
  Object.assign(completed.metrics, {
    first_edit_elapsed_ms: 1000,
    first_edit_read_file_count: 4,
    first_edit_tool_call_count: 5,
    total_tool_call_count: 12,
    duplicate_command_count: 0,
    local_verification_duration_ms: gateDuration,
    total_elapsed_ms: gateDuration + 5000,
    edit_to_pass_iteration_count: 1,
    changed_file_count: 1,
    changed_line_count: 2,
    input_tokens: 100,
    output_tokens: 50,
    command_observations: gateCommands,
    ...metrics,
  });
  completed.verification.status = "passed";
  completed.verification.duration_ms = completed.metrics.local_verification_duration_ms;
  completed.quality.status = findings.some((finding) => ["high", "critical"].includes(finding.severity)) ? "failed" : "passed";
  completed.quality.success_criteria_met = completed.quality.status === "passed";
  completed.quality.findings = findings;
  const review = {
    schema_version: "agent-throughput-human-review/v1",
    run_id: completed.run_id,
    case_id: completed.case.id,
    reviewer_kind: "independent_human",
    reviewer_identity: "test-independent-reviewer",
    completed_at: "2026-08-08T00:01:00.000Z",
    verdict: completed.quality.status,
    findings,
  };
  const reviewEvidence = writeEvidence(evidenceRoot, `evidence/${completed.run_id}-review.json`, review);
  completed.quality.human_review = {
    status: "completed",
    reviewer_kind: "independent_human",
    completed_at: "2026-08-08T00:01:00.000Z",
    provenance: { kind: "human_review", ...reviewEvidence },
  };
  writePassedReceipts(completed, evidenceRoot);
  return completed;
}

test("catalog contains the six required representative categories", () => {
  const catalog = loadCaseCatalog({ repoRoot });
  assert.equal(catalog.cases.length, 6);
  assert.deepEqual(catalog.comparison_policy.external_authentication, { status: "unsupported", required_for_adoption: true });
  assert.deepEqual(
    new Set(catalog.cases.map((item) => item.category)),
    new Set([
      "pure_docs_typo",
      "backend_one_authority_bug",
      "web_local_interaction",
      "test_only_regression",
      "api_field_addition",
      "model_runtime_scientific_contract",
    ]),
  );
  assert.equal(catalog.repository_commit.length, 40);
});

test("baseline and current context observations are real but do not claim agent metrics", () => {
  const catalog = loadCaseCatalog({ repoRoot });
  for (const mode of ["baseline", "current"]) {
    const observations = buildContextObservations({ repoRoot, mode, catalog });
    assert.equal(observations.length, 6);
    for (const observation of observations) {
      validateResult(observation, catalog);
      assert.equal(observation.observation_kind, "context_only");
      assert.equal(observation.metrics.input_tokens, null);
      assert.equal(observation.metrics.output_tokens, null);
      assert.equal(observation.metrics.first_edit_elapsed_ms, null);
      assert.equal(observation.metrics.credit_proxy.not_provider_token, true);
      assert.ok(observation.metrics.credit_proxy.value > 0);
      assert.equal(observation.quality.status, "not_evaluated");
      assert.equal(observation.verification.status, "not_run");
      assert.equal(observation.profile.skill_inventory_identity.source_ref, mode === "baseline"
        ? "06f83a11a1a30695b40bd2be8e4181b17d6bb3dd"
        : catalog.repository_commit);
      assert.equal(observation.profile.skill_inventory_identity.tree_digest.length, 64);
      assert.equal(observation.profile.skill_inventory_identity.visible_skill_count, mode === "baseline" ? 12 : 6);
      assert.equal(observation.profile.skill_inventory_identity.typed_inventory_digest === null, mode === "baseline");
    }
  }
});

test("context-only observations are incomparable and fail closed to profile rollback", () => {
  const { catalog, baseline, candidate } = observedPair();
  const comparison = compareResults(baseline, candidate, catalog);
  assert.equal(comparison.comparability.status, "incomparable");
  assert.equal(comparison.quality_regression, null);
  assert.equal(comparison.decision.action, "rollback_candidate_profile");
  assert.equal(comparison.decision.rollback_required, true);
  assert.ok(comparison.comparability.reasons.includes("model_family:unknown"));
  assert.ok(comparison.comparability.reasons.includes("fresh_session:not_proven"));
  assert.ok(comparison.comparability.reasons.includes("agent_run:not_observed"));
  assert.ok(comparison.comparability.reasons.includes("authenticated_external_receipt:unsupported"));
});

test("same-condition integrity receipts remain unsupported for adoption without external authentication", () => {
  const { catalog, baseline, candidate } = observedPair("backend-one-authority");
  const evidenceRoot = mkdtempSync(join(tmpdir(), "agent-eval-evidence-"));
  try {
    const before = completeAgentRun(baseline, { profileId: "baseline", evidenceRoot });
    const after = completeAgentRun(candidate, {
      profileId: "candidate",
      evidenceRoot,
      metrics: { total_tool_call_count: 9, input_tokens: 80 },
    });
    const comparison = compareResults(before, after, catalog, { repoRoot: evidenceRoot });
    assert.equal(comparison.comparability.status, "incomparable");
    assert.equal(comparison.external_authentication.status, "unsupported");
    assert.equal(comparison.quality_regression, false);
    assert.equal(comparison.resource_deltas.total_tool_call_count, -3);
    assert.equal(comparison.resource_deltas.input_tokens, -20);
    assert.equal(comparison.decision.action, "rollback_candidate_profile");
    assert.equal(comparison.decision.rollback_required, true);
    assert.ok(comparison.decision.reasons.includes("authenticated_external_receipt:unsupported"));
  } finally {
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test("adoption rejects unmeasured metrics and missing receipt evidence", () => {
  const { catalog, baseline, candidate } = observedPair("docs-typo");
  const evidenceRoot = mkdtempSync(join(tmpdir(), "agent-eval-evidence-"));
  try {
    const before = completeAgentRun(baseline, { profileId: "baseline", evidenceRoot });
    const after = completeAgentRun(candidate, { profileId: "candidate", evidenceRoot });
    after.metrics.input_tokens = null;
    writePassedReceipts(after, evidenceRoot);
    let comparison = compareResults(before, after, catalog, { repoRoot: evidenceRoot });
    assert.equal(comparison.decision.rollback_required, true);
    assert.ok(comparison.decision.reasons.includes("candidate_metric:input_tokens:unmeasured"));

    after.metrics.input_tokens = 100;
    writePassedReceipts(after, evidenceRoot);
    after.verification.status = "not_run";
    after.verification.receipts = [];
    after.verification.duration_ms = null;
    comparison = compareResults(before, after, catalog, { repoRoot: evidenceRoot });
    assert.equal(comparison.decision.rollback_required, true);
    assert.ok(comparison.decision.reasons.includes("candidate_receipt:not_passed"));

    const invalidPassed = completeAgentRun(candidate, { profileId: "invalid-passed", evidenceRoot });
    invalidPassed.verification.receipts = [];
    assert.throws(() => validateResult(invalidPassed, catalog, { repoRoot: evidenceRoot }), /one #837 receipt per focused gate/);
  } finally {
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test("passed receipt and human review must exist and match commit, environment, and digest", () => {
  const { catalog, candidate } = observedPair("api-field-addition");
  const evidenceRoot = mkdtempSync(join(tmpdir(), "agent-eval-evidence-"));
  try {
    const run = completeAgentRun(candidate, { profileId: "candidate", evidenceRoot });

    writePassedReceipts(run, evidenceRoot, { commitSha: "0".repeat(40) });
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /receipt commit/);

    writePassedReceipts(run, evidenceRoot);
    run.conditions.environment_identity_digest = "f".repeat(64);
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /environment does not match/);

    writePassedReceipts(run, evidenceRoot);
    run.verification.receipts[0].receipt_digest = "e".repeat(64);
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /receipt digest/);

    writePassedReceipts(run, evidenceRoot, { firstCommandArgv: ["node", "--test", "unrelated"] });
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /required focused evidence/);

    writePassedReceipts(run, evidenceRoot);
    run.verification.receipts[0].duration_ms = 999;
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /receipt duration/);

    writePassedReceipts(run, evidenceRoot, { catalogDigestOverride: "a".repeat(64) });
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /not bound to the run\/session receipt/);

    writePassedReceipts(run, evidenceRoot);
    run.run_receipt.digest = "b".repeat(64);
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /run\/session receipt digest/);

    writePassedReceipts(run, evidenceRoot);
    const runReceiptPath = join(evidenceRoot, run.run_receipt.locator);
    const runReceipt = JSON.parse(readFileSync(runReceiptPath, "utf8"));
    runReceipt.provider_usage.provenance = null;
    const { content_digest: ignoredDigest, ...runReceiptIdentity } = runReceipt;
    runReceipt.content_digest = sha256(canonicalJson(runReceiptIdentity));
    writeEvidence(evidenceRoot, run.run_receipt.locator, runReceipt);
    run.run_receipt.digest = runReceipt.content_digest;
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /provider_usage\.provenance|provider usage must carry session provenance/);

    writePassedReceipts(run, evidenceRoot);
    run.metrics.input_tokens = 999;
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /run\/session receipt metric/);

    run.metrics.input_tokens = 100;
    writePassedReceipts(run, evidenceRoot);
    run.quality.human_review.provenance.digest = "d".repeat(64);
    assert.throws(() => validateResult(run, catalog, { repoRoot: evidenceRoot }), /run\/session receipt human review artifact/);
  } finally {
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test("resource ceiling and material baseline regressions force rollback", () => {
  const { catalog, baseline, candidate } = observedPair("backend-one-authority");
  const evidenceRoot = mkdtempSync(join(tmpdir(), "agent-eval-evidence-"));
  try {
    const before = completeAgentRun(baseline, { profileId: "baseline", evidenceRoot });
    const after = completeAgentRun(candidate, {
      profileId: "candidate",
      evidenceRoot,
      metrics: {
        total_elapsed_ms: 21 * 60_000,
        total_tool_call_count: 25,
        input_tokens: 40_001,
        output_tokens: 16_001,
      },
    });
    const ceiling = compareResults(before, after, catalog, { repoRoot: evidenceRoot });
    assert.equal(ceiling.resource_regression.status, "regressed");
    assert.ok(ceiling.resource_regression.reasons.includes("resource_ceiling:total_elapsed_ms"));
    assert.ok(ceiling.resource_regression.reasons.includes("resource_ceiling:total_tool_call_count"));
    assert.equal(ceiling.decision.rollback_required, true);

    const withinCeilingButSlower = completeAgentRun(candidate, {
      profileId: "candidate-ratio",
      evidenceRoot,
      metrics: { total_elapsed_ms: 12_001, total_tool_call_count: 16 },
    });
    const ratio = compareResults(before, withinCeilingButSlower, catalog, { repoRoot: evidenceRoot });
    assert.ok(ratio.resource_regression.reasons.includes("baseline_regression:total_elapsed_ms"));
    assert.ok(ratio.resource_regression.reasons.includes("baseline_regression:total_tool_call_count"));
    assert.equal(ratio.decision.action, "rollback_candidate_profile");
  } finally {
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test("quality regression rejects faster or cheaper candidate", () => {
  const { catalog, baseline, candidate } = observedPair("web-local-interaction");
  const evidenceRoot = mkdtempSync(join(tmpdir(), "agent-eval-evidence-"));
  try {
    const before = completeAgentRun(baseline, { profileId: "baseline", evidenceRoot });
    const after = completeAgentRun(candidate, {
      profileId: "candidate",
      evidenceRoot,
      metrics: { total_tool_call_count: 2, input_tokens: 10 },
      findings: [{ severity: "high", summary: "keyboard selection is still broken" }],
    });
    const comparison = compareResults(before, after, catalog, { repoRoot: evidenceRoot });
    assert.equal(comparison.comparability.status, "incomparable");
    assert.equal(comparison.quality_regression, true);
    assert.equal(comparison.decision.action, "rollback_candidate_profile");
    assert.ok(comparison.decision.reasons.includes("human_review:high_or_critical_finding"));
  } finally {
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test("repository, prompt profile, and freshness mismatches reject comparison", () => {
  const { catalog, baseline, candidate } = observedPair("api-field-addition");
  const evidenceRoot = mkdtempSync(join(tmpdir(), "agent-eval-evidence-"));
  try {
    const before = completeAgentRun(baseline, { profileId: "baseline", evidenceRoot });
    const after = completeAgentRun(candidate, { profileId: "candidate", evidenceRoot });
    after.conditions.repository_commit = "0".repeat(40);
    assert.throws(() => compareResults(before, after, catalog, { repoRoot: evidenceRoot }), /catalog fixed commit/);
  } finally {
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});

test("stored result coverage requires each catalog case exactly once", () => {
  const catalog = loadCaseCatalog({ repoRoot });
  const results = buildContextObservations({ repoRoot, mode: "current", catalog });
  const duplicated = [...results.slice(0, -1), clone(results[0])];
  assert.throws(() => validateResultCoverage(duplicated, catalog, { mode: "current" }), /duplicate case identities/);
});

test("stored comparison coverage requires each catalog case exactly once", () => {
  const catalog = loadCaseCatalog({ repoRoot });
  const comparisons = catalog.cases.map((item) => ({ case_id: item.id }));
  const duplicated = [...comparisons.slice(0, -1), clone(comparisons[0])];
  assert.throws(() => validateComparisonCoverage(duplicated, catalog), /duplicate case identities/);
});

test("secret-like values, HOME paths, raw transcripts, and file URLs are rejected", () => {
  assert.throws(() => assertRedactedPayload({ note: "api_key=super-private-value" }), /secret-like/);
  assert.throws(() => assertRedactedPayload({ locator: "C:\\Users\\person\\trace.json" }), /HOME\/absolute/);
  assert.throws(() => assertRedactedPayload({ raw_transcript: "hello" }), /raw\/private/);
  assert.throws(() => assertRedactedPayload({ locator: "file:///tmp/trace.json" }), /file URL/);
  for (const key of ["access_token", "client_secret", "private_key", "cookie", "set_cookie", "credential"]) {
    assert.throws(() => assertRedactedPayload({ [key]: "credential-value" }), /raw\/private/);
  }
  assert.throws(() => assertRedactedPayload({ note: "Bearer abcdefghijklmnopqrstuvwxyz" }), /secret-like/);
  assert.throws(() => assertRedactedPayload({ note: "-----BEGIN PRIVATE KEY-----" }), /secret-like/);
});

test("event summaries can redact secret-like values and HOME paths before validation", () => {
  const redacted = redactSensitiveText("access_token=super-private-value C:\\Users\\person\\trace.json\n-----BEGIN PRIVATE KEY-----\nprivate-value\n-----END PRIVATE KEY-----");
  assert.equal(redacted, "<redacted-secret> <redacted-home-path>\n<redacted-secret>");
  assert.doesNotThrow(() => assertRedactedPayload({ note: redacted }));
});

test("all six versioned fixtures setup and reset in a fresh pinned worktree", () => {
  const catalog = loadCaseCatalog({ repoRoot });
  const container = mkdtempSync(join(tmpdir(), "agent-eval-worktree-"));
  const workspace = join(container, "workspace");
  try {
    const command = spawnSync("git", ["worktree", "add", "--detach", workspace, catalog.repository_commit], { cwd: repoRoot, encoding: "utf8" });
    assert.equal(command.status, 0, command.stderr);
    for (const item of catalog.cases) {
      const setup = materializeFixture({ repoRoot, workspace, caseId: item.id });
      assert.deepEqual(setup.changed_paths, item.fixture.changed_paths);
      assert.equal(setup.materialized_diff_digest, item.fixture.materialized_diff_digest);
      const reset = materializeFixture({ repoRoot, workspace, caseId: item.id, reset: true });
      assert.equal(reset.clean, true);
    }
  } finally {
    spawnSync("git", ["worktree", "remove", "--force", workspace], { cwd: repoRoot, encoding: "utf8" });
    rmSync(container, { recursive: true, force: true });
  }
});

test("published JSON schemas keep nested runner contracts closed", () => {
  const caseSchema = JSON.parse(readFileSync(join(repoRoot, "benchmarks/agent-throughput/schema/agent-throughput-case-v1.json"), "utf8"));
  const resultSchema = JSON.parse(readFileSync(join(repoRoot, "benchmarks/agent-throughput/schema/agent-throughput-result-v1.json"), "utf8"));
  const comparisonSchema = JSON.parse(readFileSync(join(repoRoot, "benchmarks/agent-throughput/schema/agent-throughput-comparison-v1.json"), "utf8"));
  const runReceiptSchema = JSON.parse(readFileSync(join(repoRoot, "benchmarks/agent-throughput/schema/agent-throughput-run-receipt-v1.json"), "utf8"));
  assert.equal(caseSchema.additionalProperties, false);
  assert.equal(caseSchema.properties.fixture.additionalProperties, false);
  assert.equal(caseSchema.properties.required_focused_evidence.items.additionalProperties, false);
  assert.deepEqual(caseSchema.properties.required_focused_evidence.items.required, ["gate_id", "command_argv"]);
  assert.deepEqual(caseSchema.properties.resource_ceiling.required, ["elapsed_minutes", "tool_calls", "input_tokens", "output_tokens"]);
  assert.equal(resultSchema.additionalProperties, false);
  for (const field of ["case", "profile", "conditions", "metrics", "run_receipt", "verification", "quality"]) {
    assert.equal(resultSchema.properties[field].additionalProperties, false, field);
  }
  assert.equal(resultSchema.properties.quality.properties.human_review.additionalProperties, false);
  assert.equal(resultSchema.allOf.length, 3);
  assert.equal(resultSchema.allOf[0].then.properties.profile.properties.guidance_identity.properties.materialization.const, "agent_run");
  assert.equal(resultSchema.properties.verification.properties.receipts.items.additionalProperties, false);
  assert.equal(resultSchema.allOf[1].then.properties.verification.properties.receipts.minItems, 1);
  assert.equal(resultSchema.allOf[2].then.properties.quality.properties.human_review.properties.status.const, "completed");
  assert.equal(runReceiptSchema.additionalProperties, false);
  assert.equal(runReceiptSchema.properties.metrics.additionalProperties, false);
  assert.equal(runReceiptSchema.properties.session.properties.fresh_session.const, true);
  assert.equal(comparisonSchema.additionalProperties, false);
  assert.equal(comparisonSchema.properties.external_authentication.additionalProperties, false);
  assert.equal(comparisonSchema.properties.external_authentication.properties.status.const, "unsupported");
  assert.equal(comparisonSchema.properties.resource_regression.additionalProperties, false);
  assert.equal(comparisonSchema.properties.decision.additionalProperties, false);
});

test("result digest drift and unnamed agent model are rejected", () => {
  const { catalog, baseline } = observedPair("model-runtime-scientific-contract");
  const drifted = clone(baseline);
  drifted.case.prompt_digest = "0".repeat(64);
  assert.throws(() => validateResult(drifted, catalog), /prompt digest/);
  const evidenceRoot = mkdtempSync(join(tmpdir(), "agent-eval-evidence-"));
  try {
    const invalidRun = completeAgentRun(baseline, { profileId: "candidate", evidenceRoot });
    invalidRun.profile.model_family = null;
    assert.throws(() => validateResult(invalidRun, catalog, { repoRoot: evidenceRoot }), /model profile|model_family/);
  } finally {
    rmSync(evidenceRoot, { recursive: true, force: true });
  }
});
