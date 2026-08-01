import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import {
  appendNotRunResults,
  buildVerificationPlan,
  classifyChangedPath,
  evaluateAcceptanceApplicability,
  evaluateVerificationOutcome,
  gateRunsOnPlatform,
  getVerificationLevel,
  loadVerificationCatalog,
  parseVerificationArguments,
  requiresBackendPytest,
  verificationEvidenceMarkdown,
  validateVerificationCatalog,
} from "./verification-gates.mjs";
import { inspectAcceptanceReport, normalizedTextSha256 } from "./acceptance-status.mjs";

const catalog = loadVerificationCatalog();
const planFor = (changedPaths, options = {}) => buildVerificationPlan({
  catalog,
  requestedLevel: "pr",
  changedPaths,
  commitSha: "abc123",
  ...options,
});
const selectedIds = (plan) => plan.selectedGateIds;
const passedResults = (plan) => plan.selectedGateIds.map((id) => ({
  id,
  status: "passed",
  command: catalog.gates[id].command,
  exitCode: 0,
}));

test("verification CLI keeps the documented direct focused-test syntax", () => {
  assert.deepEqual(
    parseVerificationArguments(["backend/tests/test_screening_score.py", "-k", "focused_case"]),
    {
      planOnly: false,
      asJson: false,
      risks: [],
      reason: null,
      focusedArgs: ["backend/tests/test_screening_score.py", "-k", "focused_case"],
    },
  );
  assert.deepEqual(
    parseVerificationArguments(["--risk", "backend-application", "--reason", "focused review", "--", "backend/tests/test_api.py"]),
    {
      planOnly: false,
      asJson: false,
      risks: ["backend-application"],
      reason: "focused review",
      focusedArgs: ["backend/tests/test_api.py"],
    },
  );
});

test("explicit focused tests are selected even when changed paths are not backend paths", () => {
  const plan = planFor(
    ["apps/web/src/features/data-library/DataLibraryPage.tsx"],
    { focusedArgs: ["backend/tests/test_data_library_api.py"] },
  );
  assert.ok(selectedIds(plan).includes("focused-pytest"));
  assert.deepEqual(plan.focusedTests, {
    tests: ["backend/tests/test_data_library_api.py"],
    source: "explicit",
    fallback: false,
  });
});

test("catalog declares four distinct levels, path rules, and complete gate metadata", () => {
  assert.deepEqual(catalog.levels.map((level) => level.id), ["edit", "pr", "checkpoint", "release"]);
  assert.ok(getVerificationLevel(catalog, "pr").gates.includes("branch-diff"));
  assert.throws(() => validateVerificationCatalog({ ...catalog, levels: [...catalog.levels, catalog.levels[0]] }), /exactly four levels/);
  assert.throws(() => validateVerificationCatalog({ ...catalog, planning: { ...catalog.planning, pathRules: [] } }), /planning.pathRules/);
  assert.throws(
    () => validateVerificationCatalog({
      ...catalog,
      riskMatrix: catalog.riskMatrix.map((rule) => (
        rule.risk === "pure-docs"
          ? Object.fromEntries(Object.entries(rule).filter(([key]) => key !== "higherLevelDisposition"))
          : rule
      )),
    }),
    /higherLevelDisposition/,
  );
});

test("docs-only plan retains document and diff gates without application build", () => {
  const plan = planFor(["docs/operations/verification-policy.md"]);
  assert.deepEqual(plan.riskCategories, ["pure-docs"]);
  assert.ok(selectedIds(plan).includes("docs-check"));
  assert.ok(selectedIds(plan).includes("branch-diff"));
  assert.ok(!selectedIds(plan).includes("application-build"));
  assert.equal(plan.selectedLevel, "pr");
  assert.equal(plan.completion, "ready");
  assert.equal(plan.fullSuiteOwner.owner, "ci");
});

test("frontend presentation plan selects web evidence rather than desktop build", () => {
  const plan = planFor(["apps/web/src/features/workbench/Panel.tsx"]);
  assert.deepEqual(plan.riskCategories, ["frontend-presentation"]);
  assert.deepEqual(selectedIds(plan).filter((id) => ["web-unit", "typecheck", "web-build"].includes(id)), ["web-unit", "typecheck", "web-build"]);
  assert.ok(!selectedIds(plan).includes("desktop-unit"));
  assert.ok(!selectedIds(plan).includes("application-build"));
});

test("focused product E2E specs stay at PR level while E2E infrastructure requires checkpoint evidence", () => {
  const productSpec = planFor(["e2e/data-library-structure.spec.ts"]);
  assert.deepEqual(productSpec.riskCategories, ["frontend-presentation"]);
  assert.equal(productSpec.selectedLevel, "pr");
  assert.equal(productSpec.completion, "ready");
  assert.ok(!selectedIds(productSpec).includes("failure-state-e2e"));

  const infrastructure = planFor(["e2e/helpers.ts"]);
  assert.deepEqual(infrastructure.riskCategories, ["e2e-test-infrastructure"]);
  assert.equal(infrastructure.selectedLevel, "checkpoint");
  assert.equal(infrastructure.completion, "follow_up");
  assert.equal(infrastructure.followUpOwner, "epic-checkpoint");
  assert.ok(selectedIds(infrastructure).includes("failure-state-e2e"));
});

test("backend plan resolves authority-map focused tests and CI owns the full suite", () => {
  const plan = planFor(["backend/src/decision_workbench/application/project_runtime.py"]);
  assert.deepEqual(plan.riskCategories, ["backend-application"]);
  assert.deepEqual(plan.focusedTests, { tests: ["backend/tests"], source: "authority-map", fallback: false });
  assert.ok(selectedIds(plan).includes("focused-pytest"));
  assert.equal(plan.fullSuiteOwner.owner, "ci");
  assert.equal(plan.fullSuiteOwner.commitSha, "abc123");
  assert.ok(!selectedIds(plan).includes("full-pytest"));
  const ciPlan = planFor(["backend/src/decision_workbench/application/project_runtime.py"], { ci: true });
  assert.ok(selectedIds(ciPlan).includes("full-pytest"));
  assert.ok(!selectedIds(ciPlan).includes("focused-pytest"));
  assert.equal(ciPlan.fullSuiteOwner.owner, "ci");
  assert.match(ciPlan.skippedGates.find((gate) => gate.id === "focused-pytest").reason, /sole full-suite/);
});

test("unresolved backend authority is an explicit broad fallback, never an accidental full-suite default", () => {
  const noAuthorityCatalog = {
    ...catalog,
    planning: { ...catalog.planning, focusedTestAuthority: [] },
  };
  const plan = buildVerificationPlan({
    catalog: noAuthorityCatalog,
    requestedLevel: "pr",
    changedPaths: ["backend/src/decision_workbench/application/project_runtime.py"],
    commitSha: "abc123",
  });
  assert.deepEqual(plan.focusedTests, { tests: ["backend/tests"], source: "unresolved-backend-fallback", fallback: true });
  assert.ok(selectedIds(plan).includes("focused-pytest"));
  assert.ok(!selectedIds(plan).includes("full-pytest"));
});

test("migration and runtime security plans force safety gates regardless of normal PR level", () => {
  const migration = planFor(["backend/src/decision_workbench/persistence/project_lifecycle_migration.py"]);
  assert.equal(migration.minimumRequiredLevel, "release");
  assert.equal(migration.selectedLevel, "release");
  assert.equal(migration.completion, "direct_evidence_required");
  assert.equal(migration.requiredFollowUp, null);
  assert.equal(migration.directEvidenceRequirements[0].command, "npm run acceptance:release");
  assert.ok(selectedIds(migration).includes("legacy-workspace"));
  const runtimeSecurity = planFor(["backend/src/decision_workbench/api/security.py"]);
  assert.equal(runtimeSecurity.minimumRequiredLevel, "release");
  assert.ok(selectedIds(runtimeSecurity).includes("security-boundary-tests"));
  assert.ok(selectedIds(runtimeSecurity).includes("dependency-audit"));
  const artifactLoader = planFor(["backend/src/decision_workbench/modeling/packages/loader.py"]);
  assert.equal(artifactLoader.minimumRequiredLevel, "release");
  assert.ok(selectedIds(artifactLoader).includes("model-package-contract-tests"));
  assert.ok(artifactLoader.requiredManualGates.some((gate) => gate.id === "model-package-release-evidence"));
});

test("checkpoint-only gates become selected only at checkpoint evidence level", () => {
  const prPlan = planFor(["apps/web/src/features/workbench/Panel.tsx"]);
  assert.ok(!selectedIds(prPlan).includes("default-playwright"));
  const checkpointPlan = planFor(["apps/web/src/features/workbench/Panel.tsx"], { requestedLevel: "checkpoint" });
  assert.equal(checkpointPlan.completion, "ready");
  assert.ok(selectedIds(checkpointPlan).includes("default-playwright"));
  assert.match(checkpointPlan.requiredGates.find((gate) => gate.id === "default-playwright").reasons.join(" "), /checkpoint evidence/);
});

test("unclassified paths receive the conservative full verification plan", () => {
  const plan = planFor(["unclassified.file"]);
  assert.deepEqual(plan.riskCategories, ["unknown"]);
  assert.equal(plan.minimumRequiredLevel, "checkpoint");
  assert.equal(plan.completion, "direct_evidence_required");
  assert.equal(plan.directEvidenceRequirements[0].command, "npm run verify:checkpoint");
  assert.ok(selectedIds(plan).includes("full-pytest"));
  assert.equal(plan.fullSuiteOwner.owner, "local");
});

test("manual risk overrides require a reason and are recorded in the plan", () => {
  assert.throws(() => planFor(["docs/readme.md"], { manualRiskOverrides: ["security"] }), /requires --reason/);
  const plan = planFor(["docs/readme.md"], { manualRiskOverrides: ["security"], manualOverrideReason: "generated code changes Origin handling" });
  assert.deepEqual(plan.manualOverrides, [{ risk: "security", reason: "generated code changes Origin handling" }]);
  assert.ok(selectedIds(plan).includes("security-boundary-tests"));
});

test("changed paths use catalog-driven high-risk precedence", () => {
  assert.equal(classifyChangedPath("docs/reports/checkpoint.json", catalog), "evidence");
  assert.equal(classifyChangedPath("apps/web/src/generated/api-types.ts", catalog), "api-contract");
  assert.equal(classifyChangedPath("backend/src/decision_workbench/persistence/store.py", catalog), "migration-workspace");
  assert.equal(classifyChangedPath("unclassified.file", catalog), "unknown");
  assert.equal(requiresBackendPytest(["backend-application"]), true);
  assert.equal(requiresBackendPytest(["pure-docs", "frontend-presentation"]), false);
});

test("acceptance stays applicable only for the same commit or evidence-only changes", () => {
  assert.equal(evaluateAcceptanceApplicability({ testedCommit: "a", currentCommit: "a", commitsAhead: 0, commitsBehind: 0, changedPaths: [], catalog }).applicability, "current");
  assert.equal(evaluateAcceptanceApplicability({ testedCommit: "a", currentCommit: "b", commitsAhead: 1, commitsBehind: 0, changedPaths: ["docs/reports/new.json"], catalog }).applicability, "still_applicable");
  assert.equal(evaluateAcceptanceApplicability({ testedCommit: "a", currentCommit: "b", commitsAhead: 1, commitsBehind: 0, changedPaths: ["backend/src/decision_workbench/persistence/store.py"], catalog }).applicability, "stale");
});

test("dirty worktree and catalog drift cannot be reported as current", () => {
  const report = { schemaVersion: "main-acceptance/v2", testedCommit: "a", status: "passed", verificationCatalogSha256: "old" };
  const status = inspectAcceptanceReport(report, { currentCommit: "a", commitsAhead: 0, commitsBehind: 0, changedPaths: [], dirtyPaths: ["scripts/verify.mjs"], currentCatalogSha256: "new" });
  assert.equal(status.applicability, "invalid");
  assert.equal(status.catalogChanged, true);
});

test("failed reports and dirty evidence-only successors are never accepted", () => {
  const common = { currentCatalogSha256: "same" };
  const failed = inspectAcceptanceReport(
    { schemaVersion: "main-acceptance/v2", testedCommit: "a", status: "failed", verificationCatalogSha256: "same" },
    { ...common, currentCommit: "a", commitsAhead: 0, commitsBehind: 0, changedPaths: [], dirtyPaths: [] },
  );
  assert.equal(failed.applicability, "invalid");
  const dirtySuccessor = inspectAcceptanceReport(
    { schemaVersion: "main-acceptance/v2", testedCommit: "a", status: "passed", verificationCatalogSha256: "same" },
    { ...common, currentCommit: "b", commitsAhead: 1, commitsBehind: 0, changedPaths: ["docs/reports/new.json"], dirtyPaths: ["backend/src/decision_workbench/app.py"] },
  );
  assert.equal(dirtySuccessor.applicability, "partial");
});

test("catalog digest is stable across BOM and line-ending conventions", () => {
  assert.equal(normalizedTextSha256('{\n  "levels": []\n}\n'), normalizedTextSha256('\uFEFF{\r\n  "levels": []\r\n}\r\n'));
});

test("selected gates after an early failure are recorded as not_run", () => {
  const results = appendNotRunResults(["docs-check", "web-unit"], [{ id: "docs-check", status: "failed" }], catalog);
  assert.deepEqual(results.map(({ id, status }) => ({ id, status })), [{ id: "docs-check", status: "failed" }, { id: "web-unit", status: "not_run" }]);
});

test("verification outcome fixtures keep direct failures separate from follow-up", () => {
  const followUpCatalog = {
    ...catalog,
    riskMatrix: catalog.riskMatrix.map((rule) => (
      rule.risk === "backend-application"
        ? {
            ...rule,
            minimumLevel: "release",
            higherLevelDisposition: "follow_up",
            followUpOwner: "release-checkpoint",
          }
        : rule
    )),
  };
  const focused = buildVerificationPlan({
    catalog: followUpCatalog,
    requestedLevel: "pr",
    changedPaths: ["backend/src/decision_workbench/application/project_runtime.py"],
    focusedArgs: ["backend/tests/test_api.py"],
    commitSha: "fixture-sha",
  });
  const passedWithFollowUp = evaluateVerificationOutcome({
    plan: focused,
    gateResults: focused.selectedGateIds.map((id) => ({
      id,
      status: "passed",
      command: followUpCatalog.gates[id].command,
      exitCode: 0,
    })),
  });
  assert.equal(passedWithFollowUp.outcome, "passed_with_follow_up");
  assert.equal(
    passedWithFollowUp.outcome_schema_version,
    "verification-outcome/v1",
  );
  assert.deepEqual(passedWithFollowUp.direct_failures, []);
  assert.equal(passedWithFollowUp.required_follow_ups[0].level, "release");
  assert.equal(passedWithFollowUp.follow_up_owner, "release-checkpoint");

  const failed = evaluateVerificationOutcome({
    plan: focused,
    gateResults: focused.selectedGateIds.map((id, index) => ({
      id,
      status: index === 0 ? "failed" : "passed",
      command: followUpCatalog.gates[id].command,
      exitCode: index === 0 ? 1 : 0,
    })),
  });
  assert.equal(failed.outcome, "failed");
  assert.equal(failed.direct_failures.length, 1);
  assert.equal(failed.required_follow_ups.length, 1);
});

test("CI full-suite ownership remains pending until completion and fails on regression", () => {
  const plan = planFor(
    ["backend/src/decision_workbench/application/project_runtime.py"],
    { ci: true },
  );
  const pending = evaluateVerificationOutcome({
    plan,
    gateResults: plan.selectedGateIds.map((id) => ({
      id,
      status: id === "full-pytest" ? "pending" : "passed",
      command: catalog.gates[id].command,
      exitCode: null,
    })),
  });
  assert.equal(pending.outcome, "pending");
  assert.deepEqual(pending.pending_gates, ["full-pytest"]);

  const failed = evaluateVerificationOutcome({
    plan,
    gateResults: plan.selectedGateIds.map((id) => ({
      id,
      status: id === "full-pytest" ? "failed" : "passed",
      command: catalog.gates[id].command,
      exitCode: id === "full-pytest" ? 1 : 0,
    })),
  });
  assert.equal(failed.outcome, "failed");
  assert.equal(failed.direct_failures[0].id, "full-pytest");
});

test("docs pass, release-sensitive changes fail closed, and structural follow-up stays green", () => {
  const docs = planFor(["docs/operations/verification-policy.md"]);
  const docsOutcome = evaluateVerificationOutcome({
    plan: docs,
    gateResults: passedResults(docs),
  });
  assert.equal(docsOutcome.outcome, "passed");
  assert.match(verificationEvidenceMarkdown(docsOutcome), /Outcome: `passed`/);

  for (const path of [
    "backend/src/decision_workbench/persistence/project_lifecycle_migration.py",
    "backend/src/decision_workbench/modeling/packages/loader.py",
  ]) {
    const plan = planFor([path]);
    const outcome = evaluateVerificationOutcome({
      plan,
      gateResults: passedResults(plan),
    });
    assert.equal(outcome.outcome, "failed");
    assert.ok(outcome.direct_failures.some((failure) => (
      failure.kind === "required_evidence"
    )));
  }

  const structural = planFor(["e2e/helpers.ts"]);
  const structuralOutcome = evaluateVerificationOutcome({
    plan: structural,
    gateResults: passedResults(structural),
  });
  assert.equal(structuralOutcome.outcome, "passed_with_follow_up");
  assert.equal(structuralOutcome.follow_up_owner, "epic-checkpoint");
});

test("verification workflow has separate direct and follow-up checks", () => {
  const workflow = readFileSync(resolve(import.meta.dirname, "../.github/workflows/verify.yml"), "utf8");
  assert.match(workflow, /name: direct verification/);
  assert.match(workflow, /name: verification follow-up/);
  assert.match(workflow, /artifacts\/verification\/latest-pr\.json/);
  assert.match(workflow, /runs-on: windows-latest/);
  assert.equal(gateRunsOnPlatform("windows", "linux"), false);
  assert.equal(gateRunsOnPlatform("windows", "windows"), true);
});
