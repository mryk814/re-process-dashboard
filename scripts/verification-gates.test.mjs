import assert from "node:assert/strict";
import test from "node:test";
import {
  appendNotRunResults,
  classifyChangedPath,
  evaluateAcceptanceApplicability,
  getVerificationLevel,
  loadVerificationCatalog,
  requiresBackendPytest,
  validateVerificationCatalog,
} from "./verification-gates.mjs";
import {
  inspectAcceptanceReport,
  normalizedTextSha256,
} from "./acceptance-status.mjs";

test("catalog declares four distinct levels and complete gate metadata", () => {
  const catalog = loadVerificationCatalog();
  assert.deepEqual(
    catalog.levels.map((level) => level.id),
    ["edit", "pr", "checkpoint", "release"],
  );
  assert.ok(getVerificationLevel(catalog, "pr").gates.includes("docs-check"));
  assert.ok(
    getVerificationLevel(catalog, "checkpoint").gates.includes("full-pytest"),
  );
  assert.throws(
    () =>
      validateVerificationCatalog({
        ...catalog,
        levels: [...catalog.levels, catalog.levels[0]],
      }),
    /exactly four levels/,
  );
});

test("high-risk rules retain migration, restore, package, security, and textbook evidence", () => {
  const catalog = loadVerificationCatalog();
  const rules = new Map(catalog.riskMatrix.map((rule) => [rule.risk, rule]));
  assert.ok(rules.get("sqlite-migration").requiredGates.includes("legacy-workspace"));
  assert.ok(rules.get("backup-restore").requiredGates.includes("windows-delivery"));
  assert.ok(
    rules.get("model-package").requiredGates.includes("model-package-contract-tests"),
  );
  assert.ok(
    rules.get("model-package").requiredGates.includes("model-package-release-evidence"),
  );
  assert.ok(rules.get("security").requiredGates.includes("security-boundary-tests"));
  assert.ok(rules.get("dependency-packaging").requiredGates.includes("dependency-audit"));
  assert.deepEqual(
    rules.get("textbook-edition").requiredGates.slice(-2),
    ["learning-clean-build", "learning-visual-review"],
  );
});

test("changed paths classify evidence separately from product risks", () => {
  assert.equal(classifyChangedPath("docs/reports/checkpoint.json"), "evidence");
  assert.equal(classifyChangedPath("docs/learning/index.qmd"), "textbook");
  assert.equal(
    classifyChangedPath("backend/src/decision_workbench/api/security.py"),
    "security",
  );
  assert.equal(
    classifyChangedPath("backend/src/decision_workbench/persistence/store.py"),
    "persistence",
  );
  assert.equal(classifyChangedPath("unclassified.file"), "unknown");
  assert.equal(requiresBackendPytest(["backend"]), true);
  assert.equal(requiresBackendPytest(["persistence"]), true);
  assert.equal(requiresBackendPytest(["security"]), true);
  assert.equal(requiresBackendPytest(["docs", "frontend"]), false);
});

test("acceptance stays applicable only for the same commit or evidence-only changes", () => {
  assert.equal(
    evaluateAcceptanceApplicability({
      testedCommit: "a",
      currentCommit: "a",
      commitsAhead: 0,
      commitsBehind: 0,
      changedPaths: [],
    }).applicability,
    "current",
  );
  assert.equal(
    evaluateAcceptanceApplicability({
      testedCommit: "a",
      currentCommit: "b",
      commitsAhead: 1,
      commitsBehind: 0,
      changedPaths: ["docs/reports/new.json"],
    }).applicability,
    "still_applicable",
  );
  assert.equal(
    evaluateAcceptanceApplicability({
      testedCommit: "a",
      currentCommit: "b",
      commitsAhead: 1,
      commitsBehind: 0,
      changedPaths: ["backend/src/decision_workbench/persistence/store.py"],
    }).applicability,
    "stale",
  );
});

test("dirty worktree and catalog drift cannot be reported as current", () => {
  const report = {
    schemaVersion: "main-acceptance/v2",
    testedCommit: "a",
    status: "passed",
    verificationCatalogSha256: "old",
  };
  const status = inspectAcceptanceReport(report, {
    currentCommit: "a",
    commitsAhead: 0,
    commitsBehind: 0,
    changedPaths: [],
    dirtyPaths: ["scripts/verify.mjs"],
    currentCatalogSha256: "new",
  });
  assert.equal(status.applicability, "invalid");
  assert.equal(status.catalogChanged, true);
});

test("catalog digest is stable across BOM and line-ending conventions", () => {
  const lf = '{\n  "levels": []\n}\n';
  const crlf = '\uFEFF{\r\n  "levels": []\r\n}\r\n';
  assert.equal(normalizedTextSha256(lf), normalizedTextSha256(crlf));
});

test("failed reports and dirty evidence-only successors are never accepted", () => {
  const common = {
    currentCatalogSha256: "same",
  };
  const failed = inspectAcceptanceReport(
    {
      schemaVersion: "main-acceptance/v2",
      testedCommit: "a",
      status: "failed",
      verificationCatalogSha256: "same",
    },
    {
      ...common,
      currentCommit: "a",
      commitsAhead: 0,
      commitsBehind: 0,
      changedPaths: [],
      dirtyPaths: [],
    },
  );
  assert.equal(failed.applicability, "invalid");

  const dirtySuccessor = inspectAcceptanceReport(
    {
      schemaVersion: "main-acceptance/v2",
      testedCommit: "a",
      status: "passed",
      verificationCatalogSha256: "same",
    },
    {
      ...common,
      currentCommit: "b",
      commitsAhead: 1,
      commitsBehind: 0,
      changedPaths: ["docs/reports/new.json"],
      dirtyPaths: ["backend/src/decision_workbench/app.py"],
    },
  );
  assert.equal(dirtySuccessor.applicability, "partial");
});

test("selected gates after an early failure are recorded as not_run", () => {
  const catalog = loadVerificationCatalog();
  const results = appendNotRunResults(
    ["docs-check", "web-unit"],
    [{ id: "docs-check", status: "failed" }],
    catalog,
  );
  assert.deepEqual(
    results.map(({ id, status }) => ({ id, status })),
    [
      { id: "docs-check", status: "failed" },
      { id: "web-unit", status: "not_run" },
    ],
  );
});
