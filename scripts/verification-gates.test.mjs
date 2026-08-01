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
  resolveExecutable,
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

test("npx uses the npm CLI through Node on a virtual Windows runner", () => {
  const runtime = {
    platform: "win32",
    execPath: "C:\\Program Files\\nodejs\\node.exe",
    npmExecPath: "C:\\Program Files\\nodejs\\node_modules\\npm\\bin\\npm-cli.js",
  };
  assert.deepEqual(resolveExecutable("npm", runtime), {
    command: runtime.execPath,
    prefix: [runtime.npmExecPath],
  });
  assert.deepEqual(resolveExecutable("npx", runtime), {
    command: runtime.execPath,
    prefix: [runtime.npmExecPath, "exec", "--"],
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

test("Node verification tests stay out of focused pytest and keep their owning gate", () => {
  const mixedPlan = planFor([
    "backend/src/decision_workbench/application/project_runtime.py",
    "scripts/verification-gates.test.mjs",
  ]);
  assert.deepEqual(mixedPlan.focusedTests.tests, ["backend/tests"]);
  assert.ok(!mixedPlan.focusedTests.tests.includes("scripts/verification-gates.test.mjs"));
  assert.ok(selectedIds(mixedPlan).includes("focused-pytest"));
  assert.ok(selectedIds(mixedPlan).includes("verification-policy-tests"));

  const explicitPlan = planFor(
    ["backend/src/decision_workbench/application/project_runtime.py"],
    {
      focusedArgs: [
        "backend/tests/test_api.py",
        "scripts/verification-gates.test.mjs",
        "-k",
        "focused_case",
      ],
    },
  );
  assert.deepEqual(explicitPlan.focusedTests.tests, [
    "backend/tests/test_api.py",
    "-k",
    "focused_case",
  ]);

  const nodeOnlyPlan = planFor(
    ["scripts/verification-gates.test.mjs"],
    { focusedArgs: ["scripts/verification-gates.test.mjs"] },
  );
  assert.deepEqual(nodeOnlyPlan.focusedTests.tests, []);
  assert.ok(!selectedIds(nodeOnlyPlan).includes("focused-pytest"));
  assert.ok(selectedIds(nodeOnlyPlan).includes("verification-policy-tests"));
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

test("direct release risks select one executable acceptance gate", () => {
  const migration = planFor(["backend/src/decision_workbench/persistence/project_lifecycle_migration.py"]);
  assert.equal(migration.minimumRequiredLevel, "release");
  assert.equal(migration.selectedLevel, "release");
  assert.equal(migration.completion, "direct_evidence_required");
  assert.equal(migration.requiredFollowUp, null);
  assert.equal(migration.directEvidenceRequirements[0].command, "npm run acceptance:release");
  assert.ok(selectedIds(migration).includes("release-acceptance"));
  assert.ok(!selectedIds(migration).includes("legacy-workspace"));
  const runtimeSecurity = planFor(["backend/src/decision_workbench/api/security.py"]);
  assert.equal(runtimeSecurity.minimumRequiredLevel, "release");
  assert.ok(selectedIds(runtimeSecurity).includes("release-acceptance"));
  assert.ok(!selectedIds(runtimeSecurity).includes("security-boundary-tests"));
  const artifactLoader = planFor(["backend/src/decision_workbench/modeling/packages/loader.py"]);
  assert.equal(artifactLoader.minimumRequiredLevel, "release");
  assert.ok(selectedIds(artifactLoader).includes("model-package-contract-tests"));
  assert.equal(artifactLoader.completion, "follow_up");
  assert.ok(artifactLoader.requiredFollowUps.some((item) => item.owner === "release-checkpoint"));
  assert.ok(artifactLoader.requiredFollowUps.some((item) => item.command.startsWith("manual:")));
  assert.deepEqual(artifactLoader.requiredManualGates, []);
});

test("model source is follow-up while an actual package artifact is direct release evidence", () => {
  const source = planFor(["backend/src/decision_workbench/modeling/runtime.py"]);
  assert.deepEqual(source.riskCategories, ["model-runtime"]);
  assert.equal(source.completion, "follow_up");
  assert.ok(selectedIds(source).includes("model-package-contract-tests"));
  assert.ok(!selectedIds(source).includes("release-acceptance"));
  assert.equal(evaluateVerificationOutcome({
    plan: source,
    gateResults: passedResults(source),
  }).outcome, "passed_with_follow_up");

  const artifact = planFor(["models/packages/example/manifest.json"]);
  assert.deepEqual(artifact.riskCategories, ["model-runtime-artifact"]);
  assert.equal(artifact.completion, "direct_evidence_required");
  assert.ok(selectedIds(artifact).includes("release-acceptance"));
  const missingResults = passedResults(artifact).map((result) => (
    result.id === "release-acceptance"
      ? { ...result, status: "not_run", exitCode: null }
      : result
  ));
  assert.equal(evaluateVerificationOutcome({
    plan: artifact,
    gateResults: missingResults,
  }).outcome, "failed");
  assert.equal(evaluateVerificationOutcome({
    plan: artifact,
    gateResults: passedResults(artifact),
  }).outcome, "passed");
});

test("stronger direct aggregate absorbs weaker follow-up gates without duplication", () => {
  const mixed = planFor([
    "models/packages/example/manifest.json",
    "e2e/helpers.ts",
  ]);
  assert.deepEqual(mixed.directEvidenceRequirements.map((item) => item.command), [
    "npm run acceptance:release",
  ]);
  assert.deepEqual(
    mixed.requiredFollowUps.map(({ command, owner }) => ({ command, owner })),
    [],
  );
  assert.ok(!selectedIds(mixed).includes("failure-state-e2e"));
  assert.deepEqual(
    selectedIds(mixed).filter((id) => id.endsWith("-acceptance")),
    ["release-acceptance"],
  );
});

test("a weaker direct aggregate preserves the stronger risk's follow-up owner", () => {
  const mixed = planFor([
    "unclassified.file",
    "backend/src/decision_workbench/modeling/runtime.py",
  ]);
  assert.deepEqual(mixed.directEvidenceRequirements.map((item) => item.command), [
    "npm run verify:checkpoint",
  ]);
  assert.ok(mixed.requiredFollowUps.every((item) => (
    item.level === "release" && item.owner === "release-checkpoint"
  )));
  assert.ok(selectedIds(mixed).includes("checkpoint-acceptance"));
  assert.ok(selectedIds(mixed).includes("model-package-contract-tests"));
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
  assert.ok(selectedIds(plan).includes("checkpoint-acceptance"));
  assert.ok(!selectedIds(plan).includes("full-pytest"));
  assert.equal(plan.fullSuiteOwner.owner, "local");
});

test("manual risk overrides require a reason and are recorded in the plan", () => {
  assert.throws(() => planFor(["docs/readme.md"], { manualRiskOverrides: ["security"] }), /requires --reason/);
  const plan = planFor(["docs/readme.md"], { manualRiskOverrides: ["security"], manualOverrideReason: "generated code changes Origin handling" });
  assert.deepEqual(plan.manualOverrides, [{ risk: "security", reason: "generated code changes Origin handling" }]);
  assert.ok(selectedIds(plan).includes("release-acceptance"));
});

test("changed paths use catalog-driven high-risk precedence", () => {
  assert.equal(classifyChangedPath("docs/reports/checkpoint.json", catalog), "evidence");
  assert.equal(classifyChangedPath("apps/web/src/generated/api-types.ts", catalog), "api-contract");
  assert.equal(classifyChangedPath("backend/src/decision_workbench/persistence/store.py", catalog), "migration-workspace");
  assert.equal(classifyChangedPath("models/packages/example/manifest.json", catalog), "model-runtime-artifact");
  assert.equal(classifyChangedPath("unclassified.file", catalog), "unknown");
  assert.deepEqual(
    planFor([".github/workflows/verify.yml"]).riskCategories,
    ["verification-tooling"],
  );
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

test("docs pass, direct acceptance is result-aware, and structural follow-up stays green", () => {
  const docs = planFor(["docs/operations/verification-policy.md"]);
  const docsOutcome = evaluateVerificationOutcome({
    plan: docs,
    gateResults: passedResults(docs),
  });
  assert.equal(docsOutcome.outcome, "passed");
  assert.match(verificationEvidenceMarkdown(docsOutcome), /Outcome: `passed`/);

  const migration = planFor([
    "backend/src/decision_workbench/persistence/project_lifecycle_migration.py",
  ]);
  const missingEvidence = passedResults(migration).map((result) => (
    result.id === "release-acceptance"
      ? { ...result, status: "not_run", exitCode: null }
      : result
  ));
  const failed = evaluateVerificationOutcome({
    plan: migration,
    gateResults: missingEvidence,
  });
  assert.equal(failed.outcome, "failed");
  assert.ok(failed.direct_failures.some((failure) => (
    failure.kind === "required_evidence"
  )));
  assert.equal(evaluateVerificationOutcome({
    plan: migration,
    gateResults: passedResults(migration),
  }).outcome, "passed");

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
  const acceptanceRunner = readFileSync(resolve(import.meta.dirname, "run-main-acceptance.ps1"), "utf8");
  assert.match(workflow, /name: direct verification/);
  assert.match(workflow, /name: verification follow-up/);
  assert.match(workflow, /artifacts\/verification\/latest-pr\.json/);
  assert.match(workflow, /name: main-acceptance-diagnostics/);
  assert.match(workflow, /artifacts\/main-acceptance\/\*\*\/\*\.log/);
  assert.match(acceptanceRunner, /Tee-Object -FilePath \$logPath[\s\S]+Write-Host "\$_"/);
  assert.match(acceptanceRunner, /Select-Object -Last 200/);
  assert.match(workflow, /runs-on: windows-latest/);
  assert.match(workflow, /timeout-minutes: 45/);
  assert.equal(gateRunsOnPlatform("windows", "linux"), false);
  assert.equal(gateRunsOnPlatform("windows", "windows"), true);
});
