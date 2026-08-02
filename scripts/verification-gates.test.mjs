import assert from "node:assert/strict";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import {
  appendNotRunResults,
  buildVerificationPlan,
  classifyChangedPaths,
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
  verificationCatalogSha256,
  validateVerificationCatalog,
} from "./verification-gates.mjs";
import {
  aggregateVerificationShards,
  buildParallelAcceptanceReport,
  boundDiagnosticLog,
  ciPlanSchemaVersion,
  createCiPlan,
  diagnosticIdentity,
  exitCodeForResult,
  failureExcerpt,
  materializeReusedShardReport,
  planShardEvidenceReuse,
  readDiagnosticTail,
  runWithDiagnosticHandles,
  shardReportSchemaVersion,
  selectReusableWorkflowRun,
  validateCiPlan,
} from "./verification-ci.mjs";
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
  assert.deepEqual(resolveExecutable("npx", {
    platform: "win32",
    execPath: runtime.execPath,
  }), {
    command: runtime.execPath,
    prefix: [
      "C:\\Program Files\\nodejs\\node_modules\\npm\\bin\\npm-cli.js",
      "exec",
      "--",
    ],
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
        "backend/tests/test_api.py::test_health",
        "scripts/verification-gates.test.mjs",
        "-k",
        "TestApi.test_health",
        "--junitxml",
        "reports/focused.xml",
        "--rootdir",
        "config.v1",
        "--ignore",
        "scripts/ignored.mjs",
      ],
    },
  );
  assert.deepEqual(explicitPlan.focusedTests.tests, [
    "backend/tests/test_api.py::test_health",
    "-k",
    "TestApi.test_health",
    "--junitxml",
    "reports/focused.xml",
    "--rootdir",
    "config.v1",
    "--ignore",
    "scripts/ignored.mjs",
  ]);

  const nodeOnlyPlan = planFor(
    ["scripts/verification-gates.test.mjs"],
    {
      focusedArgs: [
        "scripts/verification-gates.test.mjs",
        "tests/verification-gates.test.mjs",
      ],
    },
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
  assert.deepEqual(
    planFor(["scripts/verification-ci.mjs"]).riskCategories,
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

test("edit loop defers higher evidence instead of running aggregate acceptance", () => {
  const plan = planFor([
    "backend/src/decision_workbench/persistence/project_lifecycle_migration.py",
  ], {
    requestedLevel: "edit",
    focusedArgs: ["backend/tests/test_legacy_workspace_acceptance.py"],
  });
  assert.deepEqual(plan.selectedGateIds, ["focused-pytest", "typecheck"]);
  assert.ok(!plan.selectedGateIds.includes("release-acceptance"));
  assert.equal(plan.directEvidenceRequirements.length, 0);
  assert.ok(
    plan.requiredFollowUps.some(
      (item) => item.command === "npm run acceptance:release"
        && item.owner === "pr-verification",
    ),
  );
  assert.equal(
    evaluateVerificationOutcome({
      plan,
      gateResults: passedResults(plan),
    }).outcome,
    "passed_with_follow_up",
  );

  const modelRuntimePlan = planFor([
    "backend/src/decision_workbench/modeling/runtime.py",
  ], {
    requestedLevel: "edit",
    focusedArgs: ["backend/tests/test_runtime.py"],
  });
  assert.deepEqual(modelRuntimePlan.selectedGateIds, [
    "focused-pytest",
    "typecheck",
  ]);
  assert.ok(
    modelRuntimePlan.requiredFollowUps.some(
      (item) => item.command.startsWith(
        "manual: task-specific model:build",
      ) && item.owner === "release-checkpoint",
    ),
  );
});

test("catalog file digest stays compatible with acceptance status across line endings", () => {
  const scratch = mkdtempSync(join(tmpdir(), "verification-catalog-digest-"));
  const lfPath = join(scratch, "lf.json");
  const crlfPath = join(scratch, "crlf.json");
  try {
    const lf = '{\n  "schemaVersion": "verification-gates/v2"\n}\n';
    const crlf = `\uFEFF${lf.replaceAll("\n", "\r\n")}`;
    writeFileSync(lfPath, lf);
    writeFileSync(crlfPath, crlf);
    const lfDigest = verificationCatalogSha256(lfPath);
    const crlfDigest = verificationCatalogSha256(crlfPath);
    assert.equal(lfDigest, crlfDigest);
    assert.equal(lfDigest, normalizedTextSha256(lf));
    const status = inspectAcceptanceReport(
      {
        schemaVersion: "main-acceptance/v2",
        testedCommit: "same",
        status: "passed",
        verificationCatalogSha256: crlfDigest,
      },
      {
        currentCommit: "same",
        commitsAhead: 0,
        commitsBehind: 0,
        changedPaths: [],
        dirtyPaths: [],
        currentCatalogSha256: lfDigest,
      },
    );
    assert.equal(status.catalogChanged, false);
    assert.equal(status.applicability, "current");
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
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

function ciPlanFor(changedPaths) {
  const plan = planFor(changedPaths, { ci: true });
  return createCiPlan({
    plan: {
      ...plan,
      verificationCatalogSha256: "catalog-sha",
    },
    catalog,
  });
}

function passedShardReports(ciPlan) {
  return ciPlan.shards.map((shard) => ({
    schemaVersion: shardReportSchemaVersion,
    shardId: shard.id,
    testedCommit: ciPlan.testedCommit,
    verificationCatalogSha256: ciPlan.verificationCatalogSha256,
    planDigest: ciPlan.planDigest,
    runnerOS: "windows",
    expectedGateIds: shard.gateIds,
    startedAt: "2026-08-02T00:00:00.000Z",
    finishedAt: "2026-08-02T00:01:00.000Z",
    durationSeconds: 60,
    status: "passed",
    cleanIsolatedPlaywright: true,
    clearedInheritedPlaywrightEnvironment: {},
    artifacts: shard.gateIds.includes("windows-delivery")
      ? [
          {
            name: "Evidence-Decision-Workbench-Setup-0.1.0.exe",
            bytes: 100,
            sha256: "a".repeat(64),
          },
          {
            name: "Evidence-Decision-Workbench-folder-0.1.0.zip",
            bytes: 200,
            sha256: "b".repeat(64),
          },
        ]
      : [],
    gates: shard.gateIds.map((id) => ({
      id,
      status: "passed",
      command: catalog.gates[id].command,
      exitCode: 0,
      durationSeconds: 1,
      error: null,
    })),
    evidence: {
      kind: "executed",
      sourceCommit: ciPlan.testedCommit,
    },
  }));
}

test("CI plan expands aggregate acceptance without dropping or duplicating gates", () => {
  const releasePlan = ciPlanFor([
    "backend/src/decision_workbench/persistence/project_lifecycle_migration.py",
  ]);
  assert.equal(releasePlan.schemaVersion, ciPlanSchemaVersion);
  assert.ok(releasePlan.originalPlan.selectedGateIds.includes("release-acceptance"));
  assert.deepEqual(
    releasePlan.logicalGateExpansions["release-acceptance"],
    getVerificationLevel(catalog, "release").gates.filter(
      (gateId) => gateId !== "windows-delivery",
    ),
  );
  assert.ok(!releasePlan.coverageGateIds.includes("windows-delivery"));
  assert.equal(
    releasePlan.executionGateIds.length,
    new Set(releasePlan.executionGateIds).size,
  );
  assert.deepEqual(releasePlan.absorbedGates, {
    "security-boundary-tests": "full-pytest",
    "model-package-contract-tests": "full-pytest",
    "legacy-workspace": "full-pytest",
  });
  assert.ok(
    Object.keys(releasePlan.absorbedGates).every(
      (gateId) => !releasePlan.executionGateIds.includes(gateId),
    ),
  );
  assert.deepEqual(
    releasePlan.coverageGateIds,
    [...new Set(Object.values(releasePlan.logicalGateExpansions).flat())],
  );
  assert.deepEqual(
    releasePlan.shards.flatMap((shard) => shard.gateIds).sort(),
    [...releasePlan.executionGateIds].sort(),
  );

  const checkpointPlan = ciPlanFor(["unclassified.file"]);
  assert.ok(checkpointPlan.originalPlan.selectedGateIds.includes("checkpoint-acceptance"));
  assert.equal(
    checkpointPlan.executionGateIds.filter((id) => id === "branch-diff").length,
    1,
  );
});

test("catalog-declared full pytest absorption removes every contained pytest gate", () => {
  assert.deepEqual(catalog.gates["full-pytest"].absorbs, [
    "focused-pytest",
    "security-boundary-tests",
    "model-package-contract-tests",
    "legacy-workspace",
  ]);
  const basePlan = planFor(
    ["backend/src/decision_workbench/api/projects.py"],
    { ci: true },
  );
  const ciPlan = createCiPlan({
    plan: {
      ...basePlan,
      selectedGateIds: [
        "focused-pytest",
        "checkpoint-acceptance",
      ],
      verificationCatalogSha256: "catalog-sha",
    },
    catalog,
  });
  assert.deepEqual(ciPlan.absorbedGates, {
    "focused-pytest": "full-pytest",
    "security-boundary-tests": "full-pytest",
  });
  assert.ok(ciPlan.coverageGateIds.includes("focused-pytest"));
  assert.ok(ciPlan.coverageGateIds.includes("security-boundary-tests"));
  assert.ok(!ciPlan.executionGateIds.includes("focused-pytest"));
  assert.ok(!ciPlan.executionGateIds.includes("security-boundary-tests"));
  assert.ok(ciPlan.executionGateIds.includes("full-pytest"));
  validateCiPlan(ciPlan, { catalog });
});

test("CI aggregation restores the logical verification outcome", () => {
  const ciPlan = ciPlanFor([
    "backend/src/decision_workbench/persistence/project_lifecycle_migration.py",
  ]);
  const report = aggregateVerificationShards({
    ciPlan,
    shardReports: passedShardReports(ciPlan),
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(report.outcome, "passed");
  assert.equal(report.commit_sha, "abc123");
  assert.equal(report.ci_aggregation.integrityFailures.length, 0);
  assert.ok(report.ci_aggregation.shards.every((shard) => shard.status === "passed"));
  assert.equal(
    report.gates.find((gate) => gate.id === "release-acceptance").status,
    "passed",
  );
  assert.deepEqual(
    report.execution_gates.map((gate) => gate.id),
    ciPlan.executionGateIds,
  );
  assert.deepEqual(
    report.coverage_gates.map((gate) => gate.id),
    ciPlan.coverageGateIds,
  );
  assert.ok(
    Object.entries(ciPlan.absorbedGates).every(([gateId, ownerGateId]) => {
      const gate = report.coverage_gates.find((candidate) => candidate.id === gateId);
      return gate?.status === "passed" && gate.evidenceSource === ownerGateId;
    }),
  );
  assert.equal(report.artifacts.length, 0);
  assert.equal(report.cleanIsolatedPlaywright, true);
  const acceptance = buildParallelAcceptanceReport({
    verificationReport: report,
    ciPlan,
    catalog,
  });
  assert.equal(acceptance.schemaVersion, "main-acceptance/v2");
  assert.equal(acceptance.testedCommit, "abc123");
  assert.equal(acceptance.status, "passed");
  assert.equal(acceptance.artifacts.length, 0);
  assert.equal(acceptance.cleanIsolatedPlaywright, true);
  assert.ok(
    acceptance.gates.some(
      (gate) => gate.name === "security-boundary-tests"
        && gate.summary.includes("covered by full-pytest"),
    ),
  );

  const distributionPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  assert.ok(distributionPlan.coverageGateIds.includes("windows-delivery"));
  const packagingPlan = ciPlanFor(["packaging/electron-builder.yml"]);
  assert.deepEqual(packagingPlan.originalPlan.riskCategories, [
    "electron-distribution",
  ]);
  assert.ok(packagingPlan.coverageGateIds.includes("windows-delivery"));
  const packagedSmokePlan = ciPlanFor(["scripts/smoke-packaged.mjs"]);
  assert.deepEqual(packagedSmokePlan.originalPlan.riskCategories, [
    "electron-distribution",
  ]);
  assert.ok(packagedSmokePlan.coverageGateIds.includes("windows-delivery"));
  const distributionReport = aggregateVerificationShards({
    ciPlan: distributionPlan,
    shardReports: passedShardReports(distributionPlan),
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(distributionReport.outcome, "passed");
  assert.equal(distributionReport.artifacts.length, 2);
});

test("CI aggregation fails closed for missing, stale, and duplicate evidence", () => {
  const ciPlan = ciPlanFor(["unclassified.file"]);
  const reports = passedShardReports(ciPlan);
  const missing = aggregateVerificationShards({
    ciPlan,
    shardReports: reports.slice(1),
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(missing.outcome, "failed");
  assert.match(
    missing.ci_aggregation.integrityFailures.join(" "),
    /missing shard artifact/,
  );
  assert.ok(
    missing.ci_aggregation.shards.some((shard) => shard.status === "not_run"),
  );

  const diagnosticsUploadFailedReports = structuredClone(reports);
  diagnosticsUploadFailedReports[0].status = "failed";
  diagnosticsUploadFailedReports[0].gates[0].status = "failed";
  diagnosticsUploadFailedReports[0].gates[0].exitCode = 1;
  diagnosticsUploadFailedReports[0].diagnostics = {
    upload: { required: true, attempted: true, outcome: "failure" },
  };
  const diagnosticsUploadFailed = aggregateVerificationShards({
    ciPlan,
    shardReports: diagnosticsUploadFailedReports,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(diagnosticsUploadFailed.outcome, "failed");
  assert.match(
    diagnosticsUploadFailed.ci_aggregation.integrityFailures.join(" "),
    /failed to upload its diagnostics artifact/,
  );

  const staleReports = structuredClone(reports);
  staleReports[0].testedCommit = "stale-sha";
  const stale = aggregateVerificationShards({
    ciPlan,
    shardReports: staleReports,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(stale.outcome, "failed");
  assert.match(
    stale.ci_aggregation.integrityFailures.join(" "),
    /different commit/,
  );

  const catalogDriftReports = structuredClone(reports);
  catalogDriftReports[0].verificationCatalogSha256 = "other-catalog";
  const catalogDrift = aggregateVerificationShards({
    ciPlan,
    shardReports: catalogDriftReports,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(catalogDrift.outcome, "failed");
  assert.match(
    catalogDrift.ci_aggregation.integrityFailures.join(" "),
    /different verification catalog/,
  );

  const planDriftReports = structuredClone(reports);
  planDriftReports[0].planDigest = "other-plan";
  const planDrift = aggregateVerificationShards({
    ciPlan,
    shardReports: planDriftReports,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(planDrift.outcome, "failed");
  assert.match(
    planDrift.ci_aggregation.integrityFailures.join(" "),
    /different CI plan/,
  );

  const duplicateReports = structuredClone(reports);
  duplicateReports.push(structuredClone(duplicateReports[0]));
  const duplicate = aggregateVerificationShards({
    ciPlan,
    shardReports: duplicateReports,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(duplicate.outcome, "failed");
  assert.match(
    duplicate.ci_aggregation.integrityFailures.join(" "),
    /duplicate shard artifact/,
  );

  const duplicateGateReports = structuredClone(reports);
  duplicateGateReports[0].gates.push(
    structuredClone(duplicateGateReports[0].gates[0]),
  );
  const duplicateGate = aggregateVerificationShards({
    ciPlan,
    shardReports: duplicateGateReports,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(duplicateGate.outcome, "failed");
  assert.match(
    duplicateGate.ci_aggregation.integrityFailures.join(" "),
    /duplicate gate result/,
  );

  const pendingReports = structuredClone(reports);
  pendingReports[0].status = "pending";
  const pending = aggregateVerificationShards({
    ciPlan,
    shardReports: pendingReports,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(pending.outcome, "failed");
  assert.match(
    pending.ci_aggregation.integrityFailures.join(" "),
    /invalid status pending/,
  );

  const corruptGateReports = structuredClone(reports);
  corruptGateReports[0].status = "failed";
  corruptGateReports[0].gates[0].status = "corrupt";
  const corruptGate = aggregateVerificationShards({
    ciPlan,
    shardReports: corruptGateReports,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(corruptGate.outcome, "failed");
  assert.match(
    corruptGate.ci_aggregation.integrityFailures.join(" "),
    /gate .* has invalid status corrupt/,
  );

  const releaseCiPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  const missingDeliveryEvidence = structuredClone(
    passedShardReports(releaseCiPlan),
  );
  const delivery = missingDeliveryEvidence.find(
    (report) => report.shardId === "windows-delivery",
  );
  delivery.artifacts = [];
  const withoutDeliveryEvidence = aggregateVerificationShards({
    ciPlan: releaseCiPlan,
    shardReports: missingDeliveryEvidence,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(withoutDeliveryEvidence.outcome, "failed");
  assert.match(
    withoutDeliveryEvidence.ci_aggregation.integrityFailures.join(" "),
    /missing valid Windows delivery evidence/,
  );
  assert.equal(
    buildParallelAcceptanceReport({
      verificationReport: withoutDeliveryEvidence,
      ciPlan: releaseCiPlan,
      catalog,
    }).status,
    "failed",
  );
});

function reusableSource(ciPlan, { baseRef = ciPlan.originalPlan.baseRef } = {}) {
  const reports = passedShardReports(ciPlan).map((report) => ({
    ...report,
    testedCommit: "source-merge-sha",
    evidence: { kind: "executed", sourceCommit: "source-merge-sha" },
  }));
  return {
    headSha: "source-head-sha",
    testedCommit: "source-merge-sha",
    runStatus: "completed",
    runConclusion: "success",
    directReport: {
      status: "passed",
      commit_sha: "source-merge-sha",
      baseRef,
      verificationCatalogSha256: "catalog-sha",
    },
    shardReports: reports,
  };
}

test("same-base E2E follow-up reuses green backend and delivery shards", () => {
  const ciPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  const source = reusableSource(ciPlan);
  assert.notEqual(source.headSha, source.testedCommit);
  const reuse = planShardEvidenceReuse({
    ciPlan,
    source,
    changedPathsSinceSource: ["e2e/prediction-graph-material-fixture.spec.ts"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.ok(reuse.reusedShardIds.includes("backend-science"));
  assert.ok(reuse.reusedShardIds.includes("windows-delivery"));
  assert.ok(reuse.executedShardIds.includes("browser-standard"));

  const report = materializeReusedShardReport({
    ciPlan,
    source,
    shardId: "backend-science",
    sourceRunId: "1234",
  });
  assert.equal(report.testedCommit, ciPlan.testedCommit);
  assert.equal(report.evidence.kind, "reused");
  assert.equal(report.evidence.sourceCommit, "source-merge-sha");
  assert.equal(report.evidence.sourceRunConclusion, "success");
});

test("direct verification records executed, reused, and skipped shard evidence", () => {
  const ciPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  const source = reusableSource(ciPlan);
  const reports = passedShardReports(ciPlan);
  const backendIndex = reports.findIndex((report) => report.shardId === "backend-science");
  reports[backendIndex] = materializeReusedShardReport({
    ciPlan,
    source,
    shardId: "backend-science",
    sourceRunId: "1234",
  });
  const aggregation = aggregateVerificationShards({
    ciPlan,
    shardReports: reports,
    catalog,
    checkoutCommit: "abc123",
    checkoutCatalogSha256: "catalog-sha",
  });
  assert.equal(aggregation.outcome, "passed");
  assert.match(aggregation.pr_body_evidence, /executed: /);
  assert.match(aggregation.pr_body_evidence, /reused: 1/);
  assert.match(aggregation.pr_body_evidence, /skipped: /);
  assert.match(aggregation.pr_body_evidence, /run 1234 \(success\)/);
  assert.equal(
    aggregation.ci_aggregation.shards.find((shard) => shard.id === "backend-science").evidence.kind,
    "reused",
  );
});

test("same-base backend-only follow-up reuses green browser evidence", () => {
  const ciPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  const reuse = planShardEvidenceReuse({
    ciPlan,
    source: reusableSource(ciPlan),
    changedPathsSinceSource: ["backend/src/decision_workbench/application/candidates.py"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.ok(reuse.executedShardIds.includes("backend-science"));
  assert.ok(reuse.reusedShardIds.includes("browser-standard"));
});

test("base or high-risk changes never reuse shard evidence", () => {
  const ciPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  const baseChanged = planShardEvidenceReuse({
    ciPlan,
    source: reusableSource(ciPlan, { baseRef: "different-base-sha" }),
    changedPathsSinceSource: ["e2e/navigation-intent.spec.ts"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.equal(baseChanged.reusedShardIds.length, 0);
  assert.deepEqual(baseChanged.executedShardIds, ciPlan.shards.map((shard) => shard.id));

  const highRisk = planShardEvidenceReuse({
    ciPlan,
    source: reusableSource(ciPlan),
    changedPathsSinceSource: ["backend/src/decision_workbench/api/security.py"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.equal(highRisk.reusedShardIds.length, 0);

  const mismatchedMergeEvidence = reusableSource(ciPlan);
  mismatchedMergeEvidence.directReport.commit_sha = "different-merge-sha";
  const mismatched = planShardEvidenceReuse({
    ciPlan,
    source: mismatchedMergeEvidence,
    changedPathsSinceSource: ["e2e/navigation-intent.spec.ts"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.equal(mismatched.reusedShardIds.length, 0);
});

test("completed failed or cancelled runs reuse only individually green shard artifacts", () => {
  const ciPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  for (const conclusion of ["failure", "cancelled"]) {
    const source = reusableSource(ciPlan);
    source.runConclusion = conclusion;
    source.directReport.status = "failed";
    const failed = source.shardReports.find((report) => report.shardId === "browser-standard");
    failed.status = "failed";
    failed.gates[0].status = "failed";
    const cancelled = source.shardReports.find((report) => report.shardId === "windows-delivery");
    cancelled.status = "cancelled";
    cancelled.gates = cancelled.gates.map((gate) => ({ ...gate, status: "not_run" }));
    const pending = source.shardReports.find((report) => report.shardId === "recovery-failure-state");
    pending.status = "pending";
    pending.gates = pending.gates.map((gate) => ({ ...gate, status: "not_run" }));
    source.shardReports = source.shardReports.filter(
      (report) => report.shardId !== "recovery-chain-degraded",
    );

    const reuse = planShardEvidenceReuse({
      ciPlan,
      source,
      changedPathsSinceSource: ["docs/reports/follow-up.md"],
      sourceIsAncestor: true,
      classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
    });
    assert.ok(reuse.reusedShardIds.includes("backend-science"));
    assert.ok(reuse.executedShardIds.includes("browser-standard"));
    assert.ok(reuse.executedShardIds.includes("windows-delivery"));
    assert.ok(reuse.executedShardIds.includes("recovery-failure-state"));
    assert.ok(reuse.executedShardIds.includes("recovery-chain-degraded"));
  }
});

test("cancelled E2E run reuses unaffected green contract delivery and recovery shards", () => {
  const ciPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  const source = reusableSource(ciPlan);
  source.runConclusion = "cancelled";
  source.directReport.status = "failed";
  const browser = source.shardReports.find((report) => report.shardId === "browser-standard");
  browser.status = "failed";
  browser.gates[0].status = "failed";
  source.shardReports = source.shardReports.filter(
    (report) => report.shardId !== "backend-science",
  );

  const reuse = planShardEvidenceReuse({
    ciPlan,
    source,
    changedPathsSinceSource: [
      "e2e/profile-workbench-authoring.spec.ts",
      "e2e/source-lifecycle.spec.ts",
    ],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.ok(reuse.executedShardIds.includes("browser-standard"));
  assert.ok(reuse.executedShardIds.includes("backend-science"));
  for (const shardId of [
    "contract-build",
    "windows-delivery",
    "recovery-failure-state",
    "recovery-chain-degraded",
  ]) {
    assert.ok(reuse.reusedShardIds.includes(shardId), shardId);
  }
});

test("recovery-specific E2E edits invalidate only their owning recovery shard", () => {
  const ciPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  const source = reusableSource(ciPlan);
  const failureState = planShardEvidenceReuse({
    ciPlan,
    source,
    changedPathsSinceSource: ["e2e/api-offline.spec.ts"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.ok(failureState.executedShardIds.includes("recovery-failure-state"));
  assert.ok(failureState.reusedShardIds.includes("recovery-chain-degraded"));

  const chainDegraded = planShardEvidenceReuse({
    ciPlan,
    source,
    changedPathsSinceSource: ["e2e/chain-degraded.spec.ts"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.ok(chainDegraded.executedShardIds.includes("recovery-chain-degraded"));
  assert.ok(chainDegraded.reusedShardIds.includes("recovery-failure-state"));

  const sharedAxe = planShardEvidenceReuse({
    ciPlan,
    source,
    changedPathsSinceSource: ["e2e/axe.ts"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.ok(selectedIds(planFor(["e2e/axe.ts"])).includes("failure-state-e2e"));
  assert.ok(selectedIds(planFor(["e2e/axe.ts"])).includes("chain-degraded-e2e"));
  for (const shardId of [
    "browser-standard",
    "recovery-failure-state",
    "recovery-chain-degraded",
  ]) {
    assert.ok(sharedAxe.executedShardIds.includes(shardId), shardId);
  }
  for (const shardId of ["backend-science", "contract-build", "windows-delivery"]) {
    assert.ok(sharedAxe.reusedShardIds.includes(shardId), shardId);
  }

  for (const runner of [
    "scripts/run-failure-state-e2e.mjs",
    "scripts/run-degraded-task-e2e.mjs",
  ]) {
    assert.ok(selectedIds(planFor([runner])).includes("failure-state-e2e"), runner);
    const runnerReuse = planShardEvidenceReuse({
      ciPlan,
      source,
      changedPathsSinceSource: [runner],
      sourceIsAncestor: true,
      classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
    });
    assert.deepEqual(runnerReuse.executedShardIds, ["recovery-failure-state"]);
  }

  const chainConfig = planShardEvidenceReuse({
    ciPlan,
    source,
    changedPathsSinceSource: ["playwright.chain-degraded.config.ts"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.ok(
    selectedIds(planFor(["playwright.chain-degraded.config.ts"]))
      .includes("chain-degraded-e2e"),
  );
  assert.ok(
    !selectedIds(planFor(["playwright.chain-degraded.config.ts"]))
      .includes("failure-state-e2e"),
  );
  assert.deepEqual(chainConfig.executedShardIds, ["recovery-chain-degraded"]);

  const ownershipCases = [
    {
      path: "e2e/fixtures/broken-chain-evaluation.json",
      executed: ["recovery-chain-degraded"],
      requiredGates: ["chain-degraded-e2e"],
    },
    {
      path: "e2e/helpers.ts",
      executed: ["browser-standard", "recovery-failure-state"],
      requiredGates: ["failure-state-e2e"],
    },
    {
      path: "e2e/global-teardown.mjs",
      executed: ["browser-standard", "recovery-failure-state"],
      requiredGates: ["failure-state-e2e"],
    },
    {
      path: "e2e/owned-database-cleanup.mjs",
      executed: ["browser-standard", "recovery-failure-state"],
      requiredGates: ["failure-state-e2e"],
    },
    {
      path: "e2e/owned-database-cleanup.test.mjs",
      executed: ["recovery-failure-state"],
      requiredGates: ["failure-state-e2e"],
    },
    {
      path: "e2e/helpers/build-profile-workbench-fixture.py",
      executed: ["browser-standard"],
      requiredGates: [],
    },
    {
      path: "e2e/helpers/seed-degraded-task.py",
      executed: ["recovery-failure-state"],
      requiredGates: ["failure-state-e2e"],
    },
  ];
  for (const ownership of ownershipCases) {
    const requestedGateIds = selectedIds(planFor([ownership.path]));
    for (const gateId of ownership.requiredGates) {
      assert.ok(requestedGateIds.includes(gateId), `${ownership.path}: ${gateId}`);
    }
    for (const gateId of ["failure-state-e2e", "chain-degraded-e2e"]) {
      if (!ownership.requiredGates.includes(gateId)) {
        assert.ok(!requestedGateIds.includes(gateId), `${ownership.path}: ${gateId}`);
      }
    }
    const result = planShardEvidenceReuse({
      ciPlan,
      source,
      changedPathsSinceSource: [ownership.path],
      sourceIsAncestor: true,
      classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
    });
    assert.deepEqual(result.executedShardIds, ownership.executed, ownership.path);
    assert.equal(
      result.reusedShardIds.length,
      ciPlan.shards.length - ownership.executed.length,
      ownership.path,
    );
  }
});

test("in-progress workflow runs cannot provide reusable shard evidence", () => {
  const ciPlan = ciPlanFor(["apps/desktop/src/main.ts"]);
  const source = reusableSource(ciPlan);
  source.runStatus = "in_progress";
  source.runConclusion = null;
  const reuse = planShardEvidenceReuse({
    ciPlan,
    source,
    changedPathsSinceSource: ["docs/reports/follow-up.md"],
    sourceIsAncestor: true,
    classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
  });
  assert.equal(reuse.reusedShardIds.length, 0);
  assert.deepEqual(reuse.executedShardIds, ciPlan.shards.map((shard) => shard.id));
});

test("reusable run selection accepts completed ancestors regardless of overall conclusion", () => {
  const selected = selectReusableWorkflowRun({
    currentHeadSha: "current-head",
    pullRequestNumber: 735,
    isAncestorCommit: (candidate, current) => candidate === "older" && current === "current-head",
    runs: [
      { id: 1, status: "in_progress", conclusion: null, head_sha: "older", pull_requests: [{ number: 735 }], updated_at: "2026-08-02T04:00:00Z" },
      { id: 2, status: "completed", conclusion: "success", head_sha: "other", pull_requests: [{ number: 735 }], updated_at: "2026-08-02T01:00:00Z" },
      { id: 3, status: "completed", conclusion: "success", head_sha: "older", pull_requests: [{ number: 734 }], updated_at: "2026-08-02T02:00:00Z" },
      { id: 4, status: "completed", conclusion: "cancelled", head_sha: "older", pull_requests: [{ number: 735 }], updated_at: "2026-08-02T03:00:00Z" },
    ],
  });
  assert.equal(selected.id, 4);
  assert.equal(selected.conclusion, "cancelled");
});

test("CI plan validation binds commit, catalog, and plan contents", () => {
  const ciPlan = ciPlanFor(["docs/operations/verification-policy.md"]);
  assert.equal(
    validateCiPlan(ciPlan, {
      currentCommit: "abc123",
      currentCatalogSha256: "catalog-sha",
    }),
    ciPlan,
  );
  assert.throws(
    () => validateCiPlan({ ...ciPlan, testedCommit: "stale-sha" }),
    /tested commit does not match/,
  );
  assert.throws(
    () => validateCiPlan(ciPlan, { currentCatalogSha256: "other-catalog" }),
    /catalog digest does not match checkout/,
  );
  assert.throws(
    () => validateCiPlan({
      ...ciPlan,
      shards: ciPlan.shards.map((shard, index) => (
        index === 0 ? { ...shard, gateIds: [] } : shard
      )),
    }),
    /digest does not match/,
  );
});

test("verification workflow shards execution and preserves required check compatibility", () => {
  const workflow = readFileSync(resolve(import.meta.dirname, "../.github/workflows/verify.yml"), "utf8");
  const acceptanceRunner = readFileSync(resolve(import.meta.dirname, "run-main-acceptance.ps1"), "utf8");
  const failureStateRunner = readFileSync(resolve(import.meta.dirname, "run-failure-state-e2e.mjs"), "utf8");
  assert.equal(workflow.match(/name: direct verification/g)?.length, 1);
  assert.match(workflow, /name: verification follow-up/);
  assert.match(workflow, /artifacts\/verification\/latest-pr\.json/);
  assert.match(workflow, /verification-plan:/);
  assert.match(workflow, /verification-shards:/);
  assert.match(workflow, /node scripts\/verification-ci\.mjs plan/);
  assert.match(workflow, /node scripts\/verification-ci\.mjs run-shard/);
  assert.match(workflow, /node scripts\/verification-ci\.mjs aggregate/);
  assert.match(workflow, /verification-evidence-reuse:/);
  assert.match(workflow, /needs: \[verification-plan, verification-evidence-reuse, verification-shards\]/);
  assert.match(workflow, /find-reusable-run/);
  assert.match(workflow, /verification-evidence-reuse/);
  assert.match(workflow, /name: direct-verification-report/);
  assert.match(workflow, /name: main-acceptance-diagnostics/);
  assert.match(workflow, /name: Publish shard diagnostics summary/);
  assert.match(workflow, /name: verification-shard-diagnostics-\$\{\{ matrix\.shard\.id \}\}/);
  assert.match(workflow, /PLAYWRIGHT_CI_DIAGNOSTICS: "1"/);
  assert.match(workflow, /artifacts\/verification\/diagnostics\/\$\{\{ matrix\.shard\.id \}\}/);
  assert.match(workflow, /artifacts\/playwright\/\$\{\{ matrix\.shard\.id \}\}/);
  assert.match(workflow, /if: always\(\)/);
  assert.match(workflow, /artifacts\/main-acceptance\/latest\.json/);
  assert.match(acceptanceRunner, /Tee-Object -FilePath \$logPath[\s\S]+Write-Host "\$_"/);
  assert.match(acceptanceRunner, /Select-Object -Last 200/);
  assert.match(workflow, /runs-on: windows-latest/);
  assert.match(workflow, /timeout-minutes: 60/);
  assert.match(failureStateRunner, /VERIFICATION_SKIP_STANDARD_FAILURE_SPECS/);
  assert.match(failureStateRunner, /covered by default-playwright/);
  assert.equal(gateRunsOnPlatform("windows", "linux"), false);
  assert.equal(gateRunsOnPlatform("windows", "windows"), true);
});

test("CI shard diagnostics preserve success and failure identity without changing gate outcomes", () => {
  const previousRunId = process.env.PLAYWRIGHT_E2E_RUN_ID;
  process.env.PLAYWRIGHT_E2E_RUN_ID = "run-1-browser-standard";
  try {
    const success = diagnosticIdentity({ shardId: "contract-build", testedCommit: "abc123" });
    assert.equal(success.testedMergeSha, "abc123");
    assert.equal(success.shardId, "contract-build");
    assert.equal(success.playwright.workers, "1");
    assert.equal(success.playwright.retries, "0");
    assert.equal(success.playwright.ports.api, "8875");
    assert.equal(success.playwright.ports.web, "5199");
    assert.match(success.playwright.workspace.database, /decision-workbench-e2e-run-1-browser-standard\.db$/);
    assert.match(success.playwright.workspace.modelStore, /decision-workbench-e2e-models-run-1-browser-standard$/);
  } finally {
    if (previousRunId === undefined) delete process.env.PLAYWRIGHT_E2E_RUN_ID;
    else process.env.PLAYWRIGHT_E2E_RUN_ID = previousRunId;
  }

  assert.equal(failureExcerpt("all green"), null);
  assert.match(
    failureExcerpt("1) source lifecycle\nError: synthetic failure\nExpected: ready"),
    /source lifecycle[\s\S]+synthetic failure/,
  );
});

test("CI shard diagnostics stream and bound logs without an in-memory runner buffer", () => {
  const scratch = mkdtempSync(join(tmpdir(), "verification-diagnostics-"));
  const logPath = join(scratch, "long.log");
  try {
    writeFileSync(logPath, "a".repeat(1024 * 1024));
    assert.match(readDiagnosticTail(logPath, 64), /earlier .* bytes preserved/);
    const omitted = boundDiagnosticLog(logPath, 256);
    assert.equal(omitted, 1024 * 1024 - 256);
    assert.ok(statSync(logPath).size < 512);
    assert.match(readFileSync(logPath, "utf8"), /omitted to keep this diagnostic artifact bounded/);
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
  assert.equal(exitCodeForResult({ status: null, signal: "SIGTERM" }), 1);
  assert.equal(exitCodeForResult({ status: 0, signal: null }), 0);
});

test("CI shard diagnostics close stdout when stderr setup fails", () => {
  const closed = [];
  let opens = 0;
  assert.throws(
    () => runWithDiagnosticHandles({
      stdoutPath: "stdout.log",
      stderrPath: "stderr.log",
      open: () => {
        opens += 1;
        if (opens === 1) return 42;
        throw new Error("cannot open stderr");
      },
      close: (handle) => closed.push(handle),
      run: () => assert.fail("runner must not start after stderr open failure"),
    }),
    /cannot open stderr/,
  );
  assert.deepEqual(closed, [42]);
});

test("CI workflow uploads diagnostics even when a shard fails or the workflow is cancelled", () => {
  const workflow = readFileSync(resolve(import.meta.dirname, "../.github/workflows/verify.yml"), "utf8");
  const diagnosticsUpload = workflow.slice(
    workflow.indexOf("- name: Upload shard diagnostics and Playwright reports"),
    workflow.indexOf("- name: Record diagnostics artifact upload outcome"),
  );
  assert.match(diagnosticsUpload, /if: failure\(\) \|\| cancelled\(\)/);
  assert.match(diagnosticsUpload, /continue-on-error: true/);
  assert.match(diagnosticsUpload, /if-no-files-found: warn/);
  assert.match(diagnosticsUpload, /retention-days: 7/);
  assert.match(diagnosticsUpload, /verification-shard-diagnostics-/);
});
