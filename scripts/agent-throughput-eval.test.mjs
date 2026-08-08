import assert from "node:assert/strict";
import test from "node:test";
import { resolve } from "node:path";
import {
  assertRedactedPayload,
  buildContextObservations,
  compareResults,
  loadCaseCatalog,
  redactSensitiveText,
  validateResult,
} from "./agent-throughput-eval.mjs";

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

function completeAgentRun(result, { profileId, metrics = {}, findings = [] } = {}) {
  const completed = clone(result);
  completed.run_id = `${profileId}-${result.case.id}`;
  completed.observation_kind = "agent_run";
  completed.profile.id = profileId;
  completed.profile.model_family = "gpt-5.6-sol";
  completed.profile.reasoning_setting = "medium";
  completed.conditions.fresh_session = true;
  completed.conditions.fresh_workspace = true;
  completed.conditions.session_identity = `${profileId}-session`;
  completed.conditions.workspace_identity = `${profileId}-workspace`;
  Object.assign(completed.metrics, {
    first_edit_elapsed_ms: 1000,
    first_edit_read_file_count: 4,
    first_edit_tool_call_count: 5,
    total_tool_call_count: 12,
    duplicate_command_count: 0,
    local_verification_duration_ms: 3000,
    total_elapsed_ms: 8000,
    edit_to_pass_iteration_count: 1,
    changed_file_count: 1,
    changed_line_count: 2,
    input_tokens: 100,
    output_tokens: 50,
    command_observations: [{ command_digest: "1".repeat(64), count: 1, duration_ms: 3000 }],
    ...metrics,
  });
  completed.verification.status = "passed";
  completed.verification.duration_ms = completed.metrics.local_verification_duration_ms;
  completed.quality.status = findings.some((finding) => ["high", "critical"].includes(finding.severity)) ? "failed" : "passed";
  completed.quality.success_criteria_met = completed.quality.status === "passed";
  completed.quality.human_review_status = "completed";
  completed.quality.findings = findings;
  return completed;
}

test("catalog contains the six required representative categories", () => {
  const catalog = loadCaseCatalog({ repoRoot });
  assert.equal(catalog.cases.length, 6);
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
});

test("same-condition passed agent runs can adopt the candidate", () => {
  const { catalog, baseline, candidate } = observedPair("backend-one-authority");
  const before = completeAgentRun(baseline, { profileId: "baseline" });
  const after = completeAgentRun(candidate, {
    profileId: "candidate",
    metrics: { total_tool_call_count: 9, input_tokens: 80 },
  });
  const comparison = compareResults(before, after, catalog);
  assert.equal(comparison.comparability.status, "comparable");
  assert.equal(comparison.quality_regression, false);
  assert.equal(comparison.resource_deltas.total_tool_call_count, -3);
  assert.equal(comparison.resource_deltas.input_tokens, -20);
  assert.equal(comparison.decision.action, "adopt_candidate");
  assert.equal(comparison.decision.rollback_required, false);
});

test("quality regression rejects faster or cheaper candidate", () => {
  const { catalog, baseline, candidate } = observedPair("web-local-interaction");
  const before = completeAgentRun(baseline, { profileId: "baseline" });
  const after = completeAgentRun(candidate, {
    profileId: "candidate",
    metrics: { total_tool_call_count: 2, input_tokens: 10 },
    findings: [{ severity: "high", summary: "keyboard selection is still broken" }],
  });
  const comparison = compareResults(before, after, catalog);
  assert.equal(comparison.comparability.status, "comparable");
  assert.equal(comparison.quality_regression, true);
  assert.equal(comparison.decision.action, "rollback_candidate_profile");
  assert.ok(comparison.decision.reasons.includes("human_review:high_or_critical_finding"));
});

test("repository, prompt profile, and freshness mismatches reject comparison", () => {
  const { catalog, baseline, candidate } = observedPair("api-field-addition");
  const before = completeAgentRun(baseline, { profileId: "baseline" });
  const after = completeAgentRun(candidate, { profileId: "candidate" });
  after.conditions.repository_commit = "0".repeat(40);
  after.profile.reasoning_setting = "low";
  after.conditions.fresh_workspace = false;
  const comparison = compareResults(before, after, catalog);
  assert.equal(comparison.comparability.status, "incomparable");
  assert.ok(comparison.comparability.reasons.includes("repository_commit:mismatch"));
  assert.ok(comparison.comparability.reasons.includes("reasoning_setting:mismatch"));
  assert.ok(comparison.comparability.reasons.includes("fresh_workspace:not_proven"));
  assert.equal(comparison.decision.rollback_required, true);
});

test("secret-like values, HOME paths, raw transcripts, and file URLs are rejected", () => {
  assert.throws(() => assertRedactedPayload({ note: "api_key=super-private-value" }), /secret-like/);
  assert.throws(() => assertRedactedPayload({ locator: "C:\\Users\\person\\trace.json" }), /HOME\/absolute/);
  assert.throws(() => assertRedactedPayload({ raw_transcript: "hello" }), /raw\/private/);
  assert.throws(() => assertRedactedPayload({ locator: "file:///tmp/trace.json" }), /file URL/);
});

test("event summaries can redact secret-like values and HOME paths before validation", () => {
  const redacted = redactSensitiveText("token=super-private-value C:\\Users\\person\\trace.json");
  assert.equal(redacted, "<redacted-secret> <redacted-home-path>");
  assert.doesNotThrow(() => assertRedactedPayload({ note: redacted }));
});

test("result digest drift and unnamed agent model are rejected", () => {
  const { catalog, baseline } = observedPair("model-runtime-scientific-contract");
  const drifted = clone(baseline);
  drifted.case.prompt_digest = "0".repeat(64);
  assert.throws(() => validateResult(drifted, catalog), /prompt digest/);
  const invalidRun = completeAgentRun(baseline, { profileId: "candidate" });
  invalidRun.profile.model_family = null;
  assert.throws(() => validateResult(invalidRun, catalog), /model_family/);
});
