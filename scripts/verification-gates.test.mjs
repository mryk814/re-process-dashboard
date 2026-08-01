import assert from "node:assert/strict";
import test from "node:test";
import {
  appendNotRunResults,
  buildVerificationPlan,
  classifyChangedPath,
  evaluateAcceptanceApplicability,
  getVerificationLevel,
  loadVerificationCatalog,
  requiresBackendPytest,
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

test("catalog declares four distinct levels, path rules, and complete gate metadata", () => {
  assert.deepEqual(catalog.levels.map((level) => level.id), ["edit", "pr", "checkpoint", "release"]);
  assert.ok(getVerificationLevel(catalog, "pr").gates.includes("branch-diff"));
  assert.throws(() => validateVerificationCatalog({ ...catalog, levels: [...catalog.levels, catalog.levels[0]] }), /exactly four levels/);
  assert.throws(() => validateVerificationCatalog({ ...catalog, planning: { ...catalog.planning, pathRules: [] } }), /planning.pathRules/);
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
  assert.equal(productSpec.completion, "complete");
  assert.ok(!selectedIds(productSpec).includes("failure-state-e2e"));

  const infrastructure = planFor(["e2e/helpers.ts"]);
  assert.deepEqual(infrastructure.riskCategories, ["e2e-test-infrastructure"]);
  assert.equal(infrastructure.selectedLevel, "checkpoint");
  assert.equal(infrastructure.completion, "incomplete");
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
  assert.equal(migration.completion, "incomplete");
  assert.equal(migration.requiredFollowUp.command, "npm run acceptance:release");
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
  assert.equal(plan.completion, "incomplete");
  assert.equal(plan.requiredFollowUp.command, "npm run verify:checkpoint");
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
