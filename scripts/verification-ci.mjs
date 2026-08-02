import { createHash } from "node:crypto";
import {
  appendFileSync,
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import {
  appendNotRunResults,
  classifyChangedPaths,
  evaluateVerificationOutcome,
  gateRunsOnPlatform,
  getVerificationLevel,
  loadVerificationCatalog,
  resolveExecutable,
  resolveRunner,
  verificationCatalogSha256,
  verificationEvidenceMarkdown,
} from "./verification-gates.mjs";

export const ciPlanSchemaVersion = "verification-ci-plan/v1";
export const shardReportSchemaVersion = "verification-shard/v2";
const legacyShardReportSchemaVersion = "verification-shard/v1";
const reuseInvalidatingRisks = new Set([
  "migration-workspace",
  "model-runtime-artifact",
  "model-runtime",
  "security",
  "electron-distribution",
  "verification-tooling",
  "unknown",
]);

const aggregateLevels = new Map([
  ["checkpoint-acceptance", "checkpoint"],
  ["release-acceptance", "release"],
]);
const shardOrder = [
  "backend-science",
  "browser-standard",
  "contract-build",
  "recovery-failure-state",
  "recovery-chain-degraded",
  "windows-delivery",
];
const shardByGate = new Map([
  ["focused-pytest", "backend-science"],
  ["full-pytest", "backend-science"],
  ["security-boundary-tests", "backend-science"],
  ["model-package-contract-tests", "backend-science"],
  ["legacy-workspace", "backend-science"],
  ["default-playwright", "browser-standard"],
  ["failure-state-e2e", "recovery-failure-state"],
  ["chain-degraded-e2e", "recovery-chain-degraded"],
  ["windows-delivery", "windows-delivery"],
]);
const absorbedByFullPytest = [
  "security-boundary-tests",
  "model-package-contract-tests",
  "legacy-workspace",
];

function changedPathMatchesShard(path, shardId) {
  const normalized = path.replaceAll("\\", "/");
  const starts = (prefix) => normalized.startsWith(prefix);
  const axeInfrastructure = normalized === "e2e/axe.ts";
  const browserFailureInfrastructure = [
    "e2e/helpers.ts",
    "e2e/global-teardown.mjs",
    "e2e/owned-database-cleanup.mjs",
  ].includes(normalized);
  const failureOnlyInfrastructure = [
    "e2e/owned-database-cleanup.test.mjs",
    "e2e/helpers/seed-degraded-task.py",
  ].includes(normalized);
  const browserExcludedE2e = starts("e2e/fixtures/")
    || failureOnlyInfrastructure
    || [
      "e2e/chain-degraded.spec.ts",
      "e2e/startup-diagnostic.spec.ts",
      "e2e/sample-gallery.spec.ts",
    ].includes(normalized);
  switch (shardId) {
    case "backend-science":
      return starts("backend/") || starts("models/");
    case "browser-standard":
      return starts("apps/web/")
        || (
          starts("e2e/")
          && !browserExcludedE2e
        )
        || normalized === "playwright.config.ts";
    case "contract-build":
      return starts("apps/web/")
        || starts("apps/desktop/")
        || starts("backend/src/decision_workbench/api/")
        || normalized === "backend/scripts/operations/export_openapi.py"
        || normalized === "package.json"
        || normalized === "package-lock.json"
        || normalized === "uv.lock";
    case "recovery-failure-state":
      return starts("apps/web/")
        || starts("backend/")
        || axeInfrastructure
        || browserFailureInfrastructure
        || failureOnlyInfrastructure
        || normalized === "scripts/run-failure-state-e2e.mjs"
        || normalized === "scripts/run-degraded-task-e2e.mjs"
        || normalized === "playwright.config.ts"
        || normalized === "playwright.startup-diagnostic.config.ts"
        || [
          "e2e/api-offline.spec.ts",
          "e2e/accessibility-smoke.spec.ts",
          "e2e/degraded-task.spec.ts",
          "e2e/startup-diagnostic.spec.ts",
        ].includes(normalized);
    case "recovery-chain-degraded":
      return starts("apps/web/")
        || starts("backend/")
        || starts("models/")
        || axeInfrastructure
        || starts("e2e/fixtures/")
        || normalized === "playwright.chain-degraded.config.ts"
        || normalized === "e2e/chain-degraded.spec.ts";
    case "windows-delivery":
      return starts("apps/desktop/")
        || starts("packaging/")
        || normalized === "package.json"
        || normalized === "package-lock.json"
        || normalized === "uv.lock"
        || normalized === "backend/src/sidecar.py";
    default:
      return true;
  }
}

function sourceShardIsGreen(report, sourceCommit, catalogSha256, expectedShard) {
  if (!report || ![legacyShardReportSchemaVersion, shardReportSchemaVersion].includes(report.schemaVersion)) {
    return false;
  }
  if (report.shardId !== expectedShard.id || report.testedCommit !== sourceCommit) return false;
  if (report.verificationCatalogSha256 !== catalogSha256 || report.runnerOS !== "windows") return false;
  if (JSON.stringify(report.expectedGateIds) !== JSON.stringify(expectedShard.gateIds)) return false;
  if (report.status !== "passed" || report.cleanIsolatedPlaywright !== true) return false;
  return expectedShard.gateIds.every((gateId) => (
    report.gates?.find((gate) => gate.id === gateId)?.status === "passed"
  ));
}

export function planShardEvidenceReuse({
  ciPlan,
  source = null,
  changedPathsSinceSource = [],
  sourceIsAncestor = false,
  classifyPaths,
}) {
  const executeEverything = (reason) => ({
    reason,
    reusedShardIds: [],
    executedShardIds: ciPlan.shards.map((shard) => shard.id),
  });
  if (!source || !sourceIsAncestor) return executeEverything("no eligible ancestor source run");
  const sourceReport = source.directReport;
  if (source.runStatus !== "completed") {
    return executeEverything("source workflow run is not completed");
  }
  if (!source.testedCommit || !sourceReport.commit_sha || sourceReport.commit_sha !== source.testedCommit) {
    return executeEverything("source direct report does not identify its tested merge commit");
  }
  if (sourceReport.baseRef !== ciPlan.originalPlan.baseRef) {
    return executeEverything("base SHA changed since the source evidence");
  }
  if (sourceReport.verificationCatalogSha256 !== ciPlan.verificationCatalogSha256) {
    return executeEverything("verification catalog changed since the source evidence");
  }
  const changedRisks = classifyPaths(changedPathsSinceSource);
  if (changedRisks.some((risk) => reuseInvalidatingRisks.has(risk))) {
    return executeEverything(`reuse is forbidden for ${changedRisks.filter((risk) => reuseInvalidatingRisks.has(risk)).join(", ")}`);
  }
  const sourceByShard = new Map((source.shardReports ?? []).map((report) => [report.shardId, report]));
  const reusedShardIds = [];
  for (const shard of ciPlan.shards) {
    const report = sourceByShard.get(shard.id);
    const changed = changedPathsSinceSource.some((path) => changedPathMatchesShard(path, shard.id));
    if (!changed && sourceShardIsGreen(report, source.testedCommit, ciPlan.verificationCatalogSha256, shard)) {
      reusedShardIds.push(shard.id);
    }
  }
  return {
    reason: reusedShardIds.length > 0 ? "same-base green source evidence" : "no shard has reusable source evidence",
    reusedShardIds,
    executedShardIds: ciPlan.shards.map((shard) => shard.id).filter((id) => !reusedShardIds.includes(id)),
  };
}

export function materializeReusedShardReport({ ciPlan, source, shardId, sourceRunId }) {
  const shard = ciPlan.shards.find((candidate) => candidate.id === shardId);
  const sourceReport = source.shardReports.find((candidate) => candidate.shardId === shardId);
  if (!shard || !sourceReport) throw new Error(`cannot materialize reusable shard: ${shardId}`);
  return {
    ...structuredClone(sourceReport),
    schemaVersion: shardReportSchemaVersion,
    testedCommit: ciPlan.testedCommit,
    planDigest: ciPlan.planDigest,
    expectedGateIds: shard.gateIds,
    evidence: {
      kind: "reused",
      sourceRunId: String(sourceRunId),
      sourceRunConclusion: source.runConclusion ?? null,
      sourceCommit: source.testedCommit,
      sourceBaseRef: source.directReport.baseRef,
      sourcePlanDigest: sourceReport.planDigest,
    },
  };
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function sha256File(path) {
  const hash = createHash("sha256");
  const descriptor = openSync(path, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let bytesRead;
    do {
      bytesRead = readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead > 0) hash.update(buffer.subarray(0, bytesRead));
    } while (bytesRead > 0);
    return hash.digest("hex");
  } finally {
    closeSync(descriptor);
  }
}

function expectedDeliveryArtifacts() {
  const { version } = JSON.parse(
    readFileSync(resolve(import.meta.dirname, "../package.json"), "utf8"),
  );
  return [
    resolve(`release/Evidence-Decision-Workbench-Setup-${version}.exe`),
    resolve(`release/Evidence-Decision-Workbench-folder-${version}.zip`),
  ];
}

function collectDeliveryArtifacts() {
  return expectedDeliveryArtifacts().map((path) => {
    if (!existsSync(path)) {
      throw new Error(`windows-delivery did not produce ${path}`);
    }
    return {
      name: path.split(/[\\/]/).at(-1),
      bytes: statSync(path).size,
      sha256: sha256File(path),
    };
  });
}

function planDigestSource(ciPlan) {
  const { planDigest, ...source } = ciPlan;
  return source;
}

function digestCiPlan(ciPlan) {
  return sha256(JSON.stringify(planDigestSource(ciPlan)));
}

function executionGatesFor(logicalGateId, catalog, plan) {
  const aggregateLevel = aggregateLevels.get(logicalGateId);
  const gateIds = aggregateLevel
    ? [...getVerificationLevel(catalog, aggregateLevel).gates]
    : [logicalGateId];
  if (
    logicalGateId === "release-acceptance"
    && !plan.riskCategories.includes("electron-distribution")
  ) {
    return gateIds.filter((gateId) => gateId !== "windows-delivery");
  }
  return gateIds;
}

function deduplicateExecutionGates(gateIds) {
  const absorbedGates = {};
  if (gateIds.includes("full-pytest")) {
    for (const gateId of absorbedByFullPytest) {
      if (gateIds.includes(gateId)) absorbedGates[gateId] = "full-pytest";
    }
  }
  return {
    executionGateIds: gateIds.filter(
      (gateId) => !Object.hasOwn(absorbedGates, gateId),
    ),
    absorbedGates,
  };
}

export function createCiPlan({ plan, catalog = loadVerificationCatalog() }) {
  if (plan.schemaVersion !== "verification-plan/v1") {
    throw new Error("CI planning requires verification-plan/v1");
  }
  if (!plan.fullSuiteOwner?.commitSha) {
    throw new Error("CI planning requires a tested commit SHA");
  }
  if (!plan.verificationCatalogSha256) {
    throw new Error("CI planning requires the verification catalog digest");
  }
  const logicalGateExpansions = Object.fromEntries(
    plan.selectedGateIds.map((gateId) => [
      gateId,
      executionGatesFor(gateId, catalog, plan),
    ]),
  );
  const coverageGateIds = [...new Set(Object.values(logicalGateExpansions).flat())];
  for (const gateId of coverageGateIds) {
    const gate = catalog.gates[gateId];
    if (!gate) throw new Error(`CI plan references unknown gate: ${gateId}`);
    if (gate.manual) throw new Error(`CI plan cannot execute manual gate: ${gateId}`);
  }
  const { executionGateIds, absorbedGates } = deduplicateExecutionGates(
    coverageGateIds,
  );
  const shards = shardOrder
    .map((id) => ({
      id,
      gateIds: executionGateIds.filter(
        (gateId) => (shardByGate.get(gateId) ?? "contract-build") === id,
      ),
    }))
    .filter((shard) => shard.gateIds.length > 0);
  const ciPlan = {
    schemaVersion: ciPlanSchemaVersion,
    testedCommit: plan.fullSuiteOwner.commitSha,
    verificationCatalogSha256: plan.verificationCatalogSha256,
    originalPlan: plan,
    logicalGateExpansions,
    coverageGateIds,
    executionGateIds,
    absorbedGates,
    shards,
  };
  return { ...ciPlan, planDigest: digestCiPlan(ciPlan) };
}

export function validateCiPlan(
  ciPlan,
  {
    currentCommit = null,
    currentCatalogSha256 = null,
    catalog = null,
  } = {},
) {
  if (ciPlan.schemaVersion !== ciPlanSchemaVersion) {
    throw new Error(`CI plan schema must be ${ciPlanSchemaVersion}`);
  }
  if (ciPlan.originalPlan?.schemaVersion !== "verification-plan/v1") {
    throw new Error("CI plan must contain verification-plan/v1");
  }
  if (ciPlan.originalPlan.fullSuiteOwner?.commitSha !== ciPlan.testedCommit) {
    throw new Error("CI plan tested commit does not match the verification plan");
  }
  if (ciPlan.originalPlan.verificationCatalogSha256 !== ciPlan.verificationCatalogSha256) {
    throw new Error("CI plan catalog digest does not match the verification plan");
  }
  if (digestCiPlan(ciPlan) !== ciPlan.planDigest) {
    throw new Error("CI plan digest does not match its contents");
  }
  if (currentCommit && currentCommit !== ciPlan.testedCommit) {
    throw new Error(
      `CI plan commit ${ciPlan.testedCommit} does not match checkout ${currentCommit}`,
    );
  }
  if (
    currentCatalogSha256
    && currentCatalogSha256 !== ciPlan.verificationCatalogSha256
  ) {
    throw new Error("CI plan verification catalog digest does not match checkout");
  }
  const assignedGateIds = ciPlan.shards.flatMap((shard) => shard.gateIds);
  if (
    assignedGateIds.length !== new Set(assignedGateIds).size
    || assignedGateIds.length !== ciPlan.executionGateIds.length
    || ciPlan.executionGateIds.some((gateId) => !assignedGateIds.includes(gateId))
  ) {
    throw new Error("CI plan must assign every execution gate to exactly one shard");
  }
  if (catalog) {
    const logicalGateIds = ciPlan.originalPlan.selectedGateIds;
    if (
      Object.keys(ciPlan.logicalGateExpansions).length !== logicalGateIds.length
      || logicalGateIds.some(
        (gateId) => !Object.hasOwn(ciPlan.logicalGateExpansions, gateId),
      )
    ) {
      throw new Error("CI plan logical gates do not match the verification plan");
    }
    const expectedCoverageGateIds = [];
    for (const logicalGateId of logicalGateIds) {
      const expectedExpansion = executionGatesFor(
        logicalGateId,
        catalog,
        ciPlan.originalPlan,
      );
      const actualExpansion = ciPlan.logicalGateExpansions[logicalGateId];
      if (JSON.stringify(actualExpansion) !== JSON.stringify(expectedExpansion)) {
        throw new Error(`CI plan has an invalid expansion for ${logicalGateId}`);
      }
      for (const gateId of expectedExpansion) {
        if (!expectedCoverageGateIds.includes(gateId)) {
          expectedCoverageGateIds.push(gateId);
        }
      }
    }
    const expectedDeduplication = deduplicateExecutionGates(
      expectedCoverageGateIds,
    );
    if (
      JSON.stringify(ciPlan.coverageGateIds)
        !== JSON.stringify(expectedCoverageGateIds)
      || JSON.stringify(ciPlan.executionGateIds)
        !== JSON.stringify(expectedDeduplication.executionGateIds)
      || JSON.stringify(ciPlan.absorbedGates)
        !== JSON.stringify(expectedDeduplication.absorbedGates)
    ) {
      throw new Error("CI plan execution deduplication does not match logical gates");
    }
  }
  return ciPlan;
}

function currentCommit() {
  const result = spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" });
  if (result.status !== 0) throw new Error("cannot resolve current commit");
  return result.stdout.trim();
}

function writeJson(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`);
}

function diagnosticRoot(shardId) {
  return resolve(
    process.env.VERIFICATION_DIAGNOSTICS_DIR ?? "artifacts/verification/diagnostics",
    shardId,
  );
}

export function diagnosticIdentity({ shardId, testedCommit }) {
  const e2eRunId = process.env.PLAYWRIGHT_E2E_RUN_ID ?? null;
  const database = process.env.PLAYWRIGHT_DB_PATH
    ?? (e2eRunId ? resolve(tmpdir(), `decision-workbench-e2e-${e2eRunId}.db`) : null);
  return {
    schemaVersion: "verification-shard-diagnostics/v1",
    shardId,
    testedMergeSha: testedCommit,
    pullRequestHeadSha: pullRequestHeadSha(),
    github: {
      runId: process.env.GITHUB_RUN_ID ?? null,
      runAttempt: process.env.GITHUB_RUN_ATTEMPT ?? null,
      job: process.env.GITHUB_JOB ?? null,
      workflow: process.env.GITHUB_WORKFLOW ?? null,
      eventName: process.env.GITHUB_EVENT_NAME ?? null,
    },
    playwright: {
      e2eRunId,
      workers: process.env.PLAYWRIGHT_WORKERS ?? "1",
      retries: process.env.PLAYWRIGHT_RETRIES ?? "0",
      ports: {
        api: process.env.PLAYWRIGHT_API_PORT ?? null,
        web: process.env.PLAYWRIGHT_WEB_PORT ?? null,
      },
      workspace: {
        database,
        modelStore: process.env.PLAYWRIGHT_MODEL_STORE_PATH ?? null,
        profileStore: process.env.PLAYWRIGHT_PROFILE_STORE_PATH ?? null,
        taskStore: process.env.PLAYWRIGHT_TASK_STORE_PATH ?? null,
      },
    },
  };
}

export function failureExcerpt(output) {
  return output
    .split(/\r?\n/)
    .filter((line) => /(?:^\s*\d+\) |(?:Error:|Expected:|Received:|expect\(|× ))/.test(line))
    .slice(-40)
    .join("\n") || null;
}

function writeGateDiagnostics({ directory, gateId, stdout, stderr, identity }) {
  mkdirSync(directory, { recursive: true });
  const safeGateId = gateId.replaceAll(/[^a-z0-9-]/gi, "-");
  const stdoutPath = resolve(directory, `${safeGateId}.stdout.log`);
  const stderrPath = resolve(directory, `${safeGateId}.stderr.log`);
  const serverLogPath = resolve(directory, `${safeGateId}.server.log`);
  writeFileSync(stdoutPath, stdout);
  writeFileSync(stderrPath, stderr);
  // Playwright's webServer output is emitted through the test runner. Keep the
  // combined stream under a server-log name so it is available beside reports.
  writeFileSync(serverLogPath, `${stdout}${stderr}`);
  return {
    stdout: stdoutPath,
    stderr: stderrPath,
    serverLog: serverLogPath,
    failureExcerpt: failureExcerpt(`${stdout}\n${stderr}`),
    identity,
  };
}

function runnerFailureDiagnostics({ shardId, testedCommit, error }) {
  const directory = diagnosticRoot(shardId);
  const identity = diagnosticIdentity({ shardId, testedCommit });
  mkdirSync(directory, { recursive: true });
  writeJson(resolve(directory, "identity.json"), identity);
  const errorPath = resolve(directory, "runner-error.log");
  writeFileSync(errorPath, `${error.stack ?? error.message}\n`);
  return {
    directory,
    identity,
    failedGates: [{
      id: "runner",
      error: error.message,
      failureExcerpt: error.message,
      stdout: null,
      stderr: errorPath,
      serverLog: null,
    }],
  };
}

function runGateIds({
  gateIds,
  plan,
  catalog,
  skipDefaultFailureSpecs = false,
  diagnosticsDirectory = null,
  diagnosticIdentity: shardIdentity = null,
}) {
  const startedAt = new Date();
  const results = [];
  let exitCode = 0;
  const currentPlatform = process.platform === "win32" ? "windows" : process.platform;
  const environment = { ...process.env };
  const clearedInheritedPlaywrightEnvironment = {};
  for (const key of [
    "PLAYWRIGHT_REUSE_SERVER",
    "PLAYWRIGHT_DB_PATH",
    "PLAYWRIGHT_OWNED_DB_PATH",
    "PLAYWRIGHT_API_PORT",
    "PLAYWRIGHT_WEB_PORT",
    "PLAYWRIGHT_BROKEN_TASK_PACKAGE",
    "VERIFICATION_SKIP_STANDARD_FAILURE_SPECS",
  ]) {
    if (environment[key] !== undefined) {
      clearedInheritedPlaywrightEnvironment[key] = environment[key];
    }
    delete environment[key];
  }
  for (const gateId of gateIds) {
    const gate = catalog.gates[gateId];
    const resolvedRunner = resolveRunner(gate, {
      focusedArgs: plan.focusedTests.tests,
      baseRef: plan.baseRef,
    });
    const executable = resolveExecutable(resolvedRunner.executable);
    const args = [...executable.prefix, ...resolvedRunner.args];
    const gateEnvironment = { ...environment };
    if (gateId === "failure-state-e2e" && skipDefaultFailureSpecs) {
      gateEnvironment.VERIFICATION_SKIP_STANDARD_FAILURE_SPECS = "1";
    }
    process.stdout.write(`::group::${gateId}\n`);
    const gateStartedAt = new Date();
    const platformSupported = gateRunsOnPlatform(gate.platform, currentPlatform);
    const result = platformSupported
      ? spawnSync(executable.command, args, {
          encoding: "utf8",
          env: gateEnvironment,
        })
      : {
          status: 1,
          error: new Error(
            `${gateId} requires ${gate.platform}; current runner is ${currentPlatform}`,
          ),
        };
    const gateFinishedAt = new Date();
    const stdout = result.stdout ?? "";
    const stderr = result.stderr ?? "";
    const diagnosticStderr = result.error ? `${stderr}${result.error.message}\n` : stderr;
    if (stdout) process.stdout.write(stdout);
    if (stderr) process.stderr.write(stderr);
    process.stdout.write("::endgroup::\n");
    const status = result.error || result.status !== 0 ? "failed" : "passed";
    results.push({
      id: gateId,
      status,
      command: [executable.command, ...args].join(" "),
      exitCode: result.status ?? (result.error ? 1 : 0),
      durationSeconds: Number(
        ((gateFinishedAt - gateStartedAt) / 1_000).toFixed(3),
      ),
      error: result.error?.message ?? null,
      diagnostics: diagnosticsDirectory
        ? writeGateDiagnostics({
            directory: diagnosticsDirectory,
            gateId,
            stdout,
            stderr: diagnosticStderr,
            identity: shardIdentity,
          })
        : null,
    });
    if (status === "failed") {
      exitCode = result.status ?? 1;
      break;
    }
  }
  const gates = exitCode === 0
    ? results
    : appendNotRunResults(gateIds, results, catalog);
  const finishedAt = new Date();
  return {
    startedAt: startedAt.toISOString(),
    finishedAt: finishedAt.toISOString(),
    durationSeconds: Number(((finishedAt - startedAt) / 1_000).toFixed(3)),
    gates,
    exitCode,
    clearedInheritedPlaywrightEnvironment,
  };
}

export function runVerificationShard({
  ciPlan,
  shardId,
  catalog = loadVerificationCatalog(),
  checkoutCommit = currentCommit(),
  checkoutCatalogSha256 = verificationCatalogSha256(),
}) {
  validateCiPlan(ciPlan, {
    currentCommit: checkoutCommit,
    currentCatalogSha256: checkoutCatalogSha256,
    catalog,
  });
  const shard = ciPlan.shards.find((candidate) => candidate.id === shardId);
  if (!shard) throw new Error(`CI plan does not contain shard: ${shardId}`);
  const diagnostics = diagnosticIdentity({ shardId, testedCommit: checkoutCommit });
  const diagnosticsDirectory = diagnosticRoot(shardId);
  writeJson(resolve(diagnosticsDirectory, "identity.json"), diagnostics);
  const execution = runGateIds({
    gateIds: shard.gateIds,
    plan: ciPlan.originalPlan,
    catalog,
    skipDefaultFailureSpecs: ciPlan.executionGateIds.includes(
      "default-playwright",
    ),
    diagnosticsDirectory,
    diagnosticIdentity: diagnostics,
  });
  let artifacts = [];
  if (
    shard.gateIds.includes("windows-delivery")
    && execution.gates.find((result) => result.id === "windows-delivery")?.status === "passed"
  ) {
    try {
      artifacts = collectDeliveryArtifacts();
    } catch (error) {
      const result = execution.gates.find(
        (candidate) => candidate.id === "windows-delivery",
      );
      result.status = "failed";
      result.exitCode = 1;
      result.error = error.message;
      execution.exitCode = 1;
    }
  }
  return {
    schemaVersion: shardReportSchemaVersion,
    shardId,
    testedCommit: checkoutCommit,
    verificationCatalogSha256: checkoutCatalogSha256,
    planDigest: ciPlan.planDigest,
    runnerOS: process.platform === "win32" ? "windows" : process.platform,
    expectedGateIds: shard.gateIds,
    startedAt: execution.startedAt,
    finishedAt: execution.finishedAt,
    durationSeconds: execution.durationSeconds,
    status: execution.exitCode === 0 ? "passed" : "failed",
    cleanIsolatedPlaywright: true,
    clearedInheritedPlaywrightEnvironment:
      execution.clearedInheritedPlaywrightEnvironment,
    diagnostics: {
      directory: diagnosticsDirectory,
      identity: diagnostics,
      failedGates: execution.gates
        .filter((gate) => gate.status === "failed")
        .map((gate) => ({
          id: gate.id,
          error: gate.error,
          failureExcerpt: gate.diagnostics?.failureExcerpt ?? null,
          stdout: gate.diagnostics?.stdout ?? null,
          stderr: gate.diagnostics?.stderr ?? null,
          serverLog: gate.diagnostics?.serverLog ?? null,
        })),
    },
    artifacts,
    gates: execution.gates,
    evidence: {
      kind: "executed",
      sourceCommit: checkoutCommit,
    },
  };
}

function invalidShardReason({ report, expectedShard, ciPlan }) {
  if (report.schemaVersion !== shardReportSchemaVersion) {
    return `shard ${expectedShard.id} has an unsupported schema`;
  }
  if (report.shardId !== expectedShard.id) {
    return `shard file ${expectedShard.id} reports identity ${report.shardId}`;
  }
  if (report.testedCommit !== ciPlan.testedCommit) {
    return `shard ${expectedShard.id} tested a different commit`;
  }
  if (report.verificationCatalogSha256 !== ciPlan.verificationCatalogSha256) {
    return `shard ${expectedShard.id} used a different verification catalog`;
  }
  if (report.planDigest !== ciPlan.planDigest) {
    return `shard ${expectedShard.id} used a different CI plan`;
  }
  if (report.evidence?.kind === "executed" && report.evidence.sourceCommit !== ciPlan.testedCommit) {
    return `executed shard ${expectedShard.id} has an invalid evidence source`;
  }
  if (report.evidence?.kind === "reused") {
    if (
      !report.evidence.sourceRunId
      || !Object.hasOwn(report.evidence, "sourceRunConclusion")
      || !report.evidence.sourceCommit
      || !report.evidence.sourceBaseRef
    ) {
      return `reused shard ${expectedShard.id} has incomplete source evidence`;
    }
  } else if (report.evidence?.kind !== "executed") {
    return `shard ${expectedShard.id} has an invalid evidence kind`;
  }
  if (report.runnerOS !== "windows") {
    return `shard ${expectedShard.id} did not run on Windows`;
  }
  if (report.cleanIsolatedPlaywright !== true) {
    return `shard ${expectedShard.id} did not clear inherited Playwright state`;
  }
  if (
    JSON.stringify(report.expectedGateIds)
    !== JSON.stringify(expectedShard.gateIds)
  ) {
    return `shard ${expectedShard.id} expected different gates`;
  }
  if (!["passed", "failed"].includes(report.status)) {
    return `shard ${expectedShard.id} has invalid status ${report.status}`;
  }
  const invalidGateStatus = report.gates?.find(
    (result) => !["passed", "failed", "not_run"].includes(result.status),
  );
  if (invalidGateStatus) {
    return `shard ${expectedShard.id} gate ${invalidGateStatus.id} has invalid status ${invalidGateStatus.status}`;
  }
  const derivedStatus = expectedShard.gateIds.every((gateId) => (
    report.gates?.find((result) => result.id === gateId)?.status === "passed"
  ))
    ? "passed"
    : "failed";
  if (report.status !== derivedStatus) {
    return `shard ${expectedShard.id} status does not match its gate results`;
  }
  return null;
}

function failedExecutionResult(gateId, catalog, error) {
  return {
    id: gateId,
    status: "failed",
    command: catalog.gates[gateId]?.command ?? null,
    exitCode: 1,
    durationSeconds: 0,
    error,
  };
}

export function aggregateVerificationShards({
  ciPlan,
  shardReports,
  catalog = loadVerificationCatalog(),
  checkoutCommit = currentCommit(),
  checkoutCatalogSha256 = verificationCatalogSha256(),
}) {
  validateCiPlan(ciPlan, {
    currentCommit: checkoutCommit,
    currentCatalogSha256: checkoutCatalogSha256,
    catalog,
  });
  const integrityFailures = [];
  const reportsByShard = new Map();
  for (const report of shardReports) {
    if (!report || typeof report !== "object" || typeof report.shardId !== "string") {
      integrityFailures.push("encountered a shard artifact without an identity");
      continue;
    }
    if (reportsByShard.has(report.shardId)) {
      integrityFailures.push(`duplicate shard artifact: ${report.shardId}`);
      continue;
    }
    reportsByShard.set(report.shardId, report);
  }
  for (const shardId of reportsByShard.keys()) {
    if (!ciPlan.shards.some((shard) => shard.id === shardId)) {
      integrityFailures.push(`unexpected shard artifact: ${shardId}`);
    }
  }

  const executionResults = new Map();
  for (const expectedShard of ciPlan.shards) {
    const report = reportsByShard.get(expectedShard.id);
    if (!report) {
      integrityFailures.push(`missing shard artifact: ${expectedShard.id}`);
      continue;
    }
    const invalidReason = invalidShardReason({ report, expectedShard, ciPlan });
    if (invalidReason) {
      integrityFailures.push(invalidReason);
      continue;
    }
    for (const result of report.gates ?? []) {
      if (!expectedShard.gateIds.includes(result.id)) {
        integrityFailures.push(
          `shard ${expectedShard.id} reported unplanned gate: ${result.id}`,
        );
        continue;
      }
      if (executionResults.has(result.id)) {
        integrityFailures.push(`duplicate gate result: ${result.id}`);
        executionResults.set(
          result.id,
          failedExecutionResult(result.id, catalog, "duplicate gate result"),
        );
        continue;
      }
      executionResults.set(result.id, result);
    }
  }
  for (const gateId of ciPlan.executionGateIds) {
    if (!executionResults.has(gateId)) {
      executionResults.set(
        gateId,
        failedExecutionResult(gateId, catalog, "missing expected gate result"),
      );
    }
  }
  for (const [gateId, ownerGateId] of Object.entries(ciPlan.absorbedGates)) {
    const owner = executionResults.get(ownerGateId);
    const passed = owner?.status === "passed";
    executionResults.set(gateId, {
      id: gateId,
      status: passed ? "passed" : "failed",
      command: catalog.gates[gateId].command,
      exitCode: passed ? 0 : 1,
      durationSeconds: 0,
      error: passed
        ? null
        : `absorbing gate failed or was missing: ${ownerGateId}`,
      evidenceSource: ownerGateId,
    });
  }
  const releaseSelected = ciPlan.originalPlan.selectedGateIds.includes(
    "release-acceptance",
  );
  const deliverySelected = ciPlan.coverageGateIds.includes("windows-delivery");
  const deliveryReport = reportsByShard.get("windows-delivery");
  const artifacts = deliveryReport?.artifacts ?? [];
  if (releaseSelected && deliverySelected) {
    const expectedArtifactNames = expectedDeliveryArtifacts().map(
      (path) => path.split(/[\\/]/).at(-1),
    );
    for (const name of expectedArtifactNames) {
      const artifact = artifacts.find((candidate) => candidate.name === name);
      if (
        !artifact
        || !Number.isInteger(artifact.bytes)
        || artifact.bytes <= 0
        || !/^[a-f0-9]{64}$/.test(artifact.sha256 ?? "")
      ) {
        integrityFailures.push(`missing valid Windows delivery evidence: ${name}`);
      }
    }
  }

  const logicalResults = ciPlan.originalPlan.selectedGateIds.map((logicalGateId) => {
    const expandedGateIds = ciPlan.logicalGateExpansions[logicalGateId];
    const expandedResults = expandedGateIds.map((gateId) => executionResults.get(gateId));
    if (expandedGateIds.length === 1 && expandedGateIds[0] === logicalGateId) {
      return expandedResults[0];
    }
    const failed = expandedResults.filter((result) => result.status !== "passed");
    return {
      id: logicalGateId,
      status: failed.length === 0 ? "passed" : "failed",
      command: catalog.gates[logicalGateId].command,
      exitCode: failed.length === 0 ? 0 : 1,
      durationSeconds: Number(
        expandedResults
          .reduce((total, result) => total + (result.durationSeconds ?? 0), 0)
          .toFixed(3),
      ),
      error: failed.length === 0
        ? null
        : `expanded gates failed: ${failed.map((result) => result.id).join(", ")}`,
      expandedGateIds,
    };
  });
  if (integrityFailures.length > 0) {
    logicalResults.push({
      id: "ci-aggregation-integrity",
      status: "failed",
      command: null,
      exitCode: 1,
      durationSeconds: 0,
      error: integrityFailures.join("; "),
    });
  }
  const outcome = evaluateVerificationOutcome({
    plan: ciPlan.originalPlan,
    gateResults: logicalResults,
  });
  const evidenceSummary = ciPlan.shards.map((shard) => {
    const report = reportsByShard.get(shard.id);
    if (!report) return { id: shard.id, status: "not_run", evidence: "missing", source: null };
    const reused = report.evidence?.kind === "reused";
    return {
      id: shard.id,
      status: report.status ?? "invalid",
      evidence: reused ? "reused" : "executed",
      source: reused
        ? `run ${report.evidence.sourceRunId} (${report.evidence.sourceRunConclusion ?? "unknown"}) @ ${report.evidence.sourceCommit}`
        : report.testedCommit,
    };
  });
  const skippedShardIds = shardOrder.filter((id) => !ciPlan.shards.some((shard) => shard.id === id));
  const shardEvidenceMarkdown = [
    "",
    "## CI shard evidence",
    "",
    "| shard | evidence | source | status |",
    "| --- | --- | --- | --- |",
    ...evidenceSummary.map((item) => `| ${item.id} | ${item.evidence} | ${item.source ?? "-"} | ${item.status} |`),
    ...skippedShardIds.map((id) => `| ${id} | skipped | not selected by this plan | not_run |`),
    "",
    `- executed: ${evidenceSummary.filter((item) => item.evidence === "executed").length}`,
    `- reused: ${evidenceSummary.filter((item) => item.evidence === "reused").length}`,
    `- skipped: ${skippedShardIds.length}`,
  ].join("\n");
  const evidenceMarkdown = `${verificationEvidenceMarkdown(outcome)}${shardEvidenceMarkdown}`;
  const startedTimes = shardReports
    .map((report) => Date.parse(report?.startedAt))
    .filter(Number.isFinite);
  const startedAt = startedTimes.length > 0
    ? new Date(Math.min(...startedTimes))
    : new Date();
  const finishedAt = new Date();
  return {
    ...ciPlan.originalPlan,
    startedAt: startedAt.toISOString(),
    finishedAt: finishedAt.toISOString(),
    durationSeconds: Number(((finishedAt - startedAt) / 1_000).toFixed(3)),
    status: outcome.outcome,
    ...outcome,
    pr_body_evidence: evidenceMarkdown,
    gates: logicalResults,
    execution_gates: ciPlan.executionGateIds.map(
      (gateId) => executionResults.get(gateId),
    ),
    coverage_gates: ciPlan.coverageGateIds.map(
      (gateId) => executionResults.get(gateId),
    ),
    absorbed_gates: ciPlan.absorbedGates,
    cleanIsolatedPlaywright: ciPlan.shards.every(
      (shard) => reportsByShard.get(shard.id)?.cleanIsolatedPlaywright === true,
    ),
    artifacts,
    ci_aggregation: {
      schemaVersion: ciPlan.schemaVersion,
      planDigest: ciPlan.planDigest,
      expectedShards: ciPlan.shards.map((shard) => shard.id),
      receivedShards: [...reportsByShard.keys()].sort(),
      integrityFailures,
      shards: ciPlan.shards.map((shard) => {
        const report = reportsByShard.get(shard.id);
        return report
          ? {
              id: shard.id,
              status: report.status ?? "invalid",
              gateIds: shard.gateIds,
              startedAt: report.startedAt ?? null,
              finishedAt: report.finishedAt ?? null,
              clearedInheritedPlaywrightEnvironment:
                report.clearedInheritedPlaywrightEnvironment ?? {},
              error: report.error ?? null,
              evidence: report.evidence ?? null,
            }
          : {
              id: shard.id,
              status: "not_run",
              gateIds: shard.gateIds,
              startedAt: null,
              finishedAt: null,
              clearedInheritedPlaywrightEnvironment: {},
              error: "missing shard artifact",
              evidence: null,
            };
      }),
    },
  };
}

export function buildParallelAcceptanceReport({
  verificationReport,
  ciPlan,
  catalog = loadVerificationCatalog(),
}) {
  if (!ciPlan.originalPlan.selectedGateIds.includes("release-acceptance")) {
    return null;
  }
  const releaseGateIds = ciPlan.logicalGateExpansions["release-acceptance"];
  const releaseResult = verificationReport.gates.find(
    (gate) => gate.id === "release-acceptance",
  );
  const releasePassed = releaseResult?.status === "passed"
    && verificationReport.ci_aggregation.integrityFailures.length === 0
    && verificationReport.cleanIsolatedPlaywright === true;
  const selected = new Set(releaseGateIds);
  return {
    schemaVersion: "main-acceptance/v2",
    runId: process.env.GITHUB_RUN_ID
      ? `github-${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT ?? "1"}`
      : `parallel-${verificationReport.finishedAt}`,
    level: "release",
    testedCommit: ciPlan.testedCommit,
    currentCommitAtInspection: ciPlan.testedCommit,
    commitsAhead: 0,
    changedRiskCategories: [],
    applicability: "current",
    worktreeChangesAtStart: [],
    verificationCatalogSha256: ciPlan.verificationCatalogSha256,
    selectedGates: releaseGateIds,
    omittedGates: Object.entries(catalog.gates)
      .filter(([gateId]) => !selected.has(gateId))
      .map(([id, gate]) => ({
        id,
        status: "not_run",
        reason: id === "windows-delivery"
          ? "electron-distribution risk is not present in this release plan"
          : gate.manual
          ? "manual evidence is outside the automated release profile"
          : "not selected by the release profile",
        priorEvidence: gate.priorEvidence ?? [],
      })),
    clearedInheritedPlaywrightEnvironment: Object.fromEntries(
      verificationReport.ci_aggregation.shards.map((shard) => [
        shard.id,
        shard.clearedInheritedPlaywrightEnvironment ?? {},
      ]),
    ),
    cleanIsolatedPlaywright: verificationReport.cleanIsolatedPlaywright,
    startedAt: verificationReport.startedAt,
    finishedAt: verificationReport.finishedAt,
    durationSeconds: verificationReport.durationSeconds,
    environment: {
      mode: "parallel-windows-shards",
      shards: verificationReport.ci_aggregation.shards.map((shard) => shard.id),
    },
    gates: verificationReport.coverage_gates
      .filter((gate) => selected.has(gate.id))
      .map((gate) => ({
        name: gate.id,
        status: gate.status,
        command: gate.command,
        exitCode: gate.exitCode,
        durationSeconds: gate.durationSeconds,
        log: null,
        summary: gate.evidenceSource
          ? [`covered by ${gate.evidenceSource}`]
          : gate.error
            ? [gate.error]
            : [],
      })),
    artifacts: verificationReport.artifacts,
    status: releasePassed ? "passed" : "failed",
    failure: releasePassed
      ? null
      : [
          releaseResult?.error,
          ...verificationReport.ci_aggregation.integrityFailures,
        ].filter(Boolean).join("; ") || "parallel release acceptance failed",
  };
}

function parseOptions(args) {
  const options = {};
  for (let index = 0; index < args.length; index += 2) {
    const key = args[index];
    const value = args[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error(`invalid CI verification option: ${key ?? ""}`);
    }
    options[key.slice(2)] = value;
  }
  return options;
}

function appendGitHubOutput(path, ciPlan) {
  if (!path) return;
  const matrix = {
    include: ciPlan.shards.map((shard) => ({ shard: { id: shard.id } })),
  };
  appendFileSync(
    path,
    [
      `commit_sha=${ciPlan.testedCommit}`,
      `catalog_sha256=${ciPlan.verificationCatalogSha256}`,
      `plan_digest=${ciPlan.planDigest}`,
      `matrix=${JSON.stringify(matrix)}`,
      "",
    ].join("\n"),
  );
}

function loadShardReports(directory) {
  try {
    return readdirSync(directory, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
      .map((entry) => JSON.parse(readFileSync(resolve(directory, entry.name), "utf8")));
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

function readJsonIfPresent(path) {
  if (!path || !existsSync(resolve(path))) return null;
  return JSON.parse(readFileSync(resolve(path), "utf8"));
}

function gitDiffPaths(fromCommit, toCommit) {
  const result = spawnSync(
    "git",
    ["diff", "--name-only", "--find-renames", `${fromCommit}..${toCommit}`],
    { encoding: "utf8" },
  );
  if (result.status !== 0) throw new Error("cannot inspect commits after reusable evidence");
  return [...new Set(result.stdout.split(/\r?\n/).filter(Boolean))].sort();
}

function isAncestor(ancestor, descendant) {
  return spawnSync("git", ["merge-base", "--is-ancestor", ancestor, descendant]).status === 0;
}

export function selectReusableWorkflowRun({ runs, pullRequestNumber, currentHeadSha, isAncestorCommit }) {
  return runs
    .filter((run) => (
      run.status === "completed"
      && run.head_sha
      && run.head_sha !== currentHeadSha
      && run.pull_requests?.some((pr) => pr.number === pullRequestNumber)
      && isAncestorCommit(run.head_sha, currentHeadSha)
    ))
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))[0] ?? null;
}

function pullRequestHeadSha() {
  const eventPath = process.env.GITHUB_EVENT_PATH;
  if (!eventPath || !existsSync(eventPath)) return null;
  return JSON.parse(readFileSync(eventPath, "utf8")).pull_request?.head?.sha ?? null;
}

async function findReusableWorkflowRun() {
  const token = process.env.GITHUB_TOKEN;
  const repository = process.env.GITHUB_REPOSITORY;
  const eventPath = process.env.GITHUB_EVENT_PATH;
  if (!token || !repository || !eventPath || !existsSync(eventPath)) return null;
  const event = JSON.parse(readFileSync(eventPath, "utf8"));
  const pullRequestNumber = event.pull_request?.number;
  const currentHeadSha = event.pull_request?.head?.sha;
  if (!pullRequestNumber || !currentHeadSha) return null;
  const apiUrl = process.env.GITHUB_API_URL ?? "https://api.github.com";
  const url = `${apiUrl}/repos/${repository}/actions/workflows/verify.yml/runs?event=pull_request&status=completed&per_page=100`;
  const response = await fetch(url, {
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  if (!response.ok) return null;
  const payload = await response.json();
  const run = selectReusableWorkflowRun({
    runs: payload.workflow_runs ?? [],
    pullRequestNumber,
    currentHeadSha,
    isAncestorCommit: isAncestor,
  });
  return run
    ? {
        runId: String(run.id),
        headSha: run.head_sha,
        status: run.status,
        conclusion: run.conclusion ?? null,
      }
    : null;
}

function appendReuseOutput(path, reuse) {
  if (!path) return;
  const matrix = {
    include: reuse.executedShardIds.map((id) => ({ shard: { id } })),
  };
  appendFileSync(path, [
    `matrix=${JSON.stringify(matrix)}`,
    `executed_shards=${reuse.executedShardIds.join(",")}`,
    `reused_shards=${reuse.reusedShardIds.join(",")}`,
    "",
  ].join("\n"));
}

function planFromVerify() {
  const result = spawnSync(
    process.execPath,
    [resolve(import.meta.dirname, "verify.mjs"), "pr", "--plan", "--json"],
    {
      encoding: "utf8",
      env: { ...process.env, GITHUB_ACTIONS: "true" },
    },
  );
  if (result.status !== 0) {
    throw new Error(result.stderr.trim() || "verification planner failed");
  }
  return JSON.parse(result.stdout);
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  const options = parseOptions(args);
  const catalog = loadVerificationCatalog();
  if (command === "plan") {
    const ciPlan = createCiPlan({ plan: planFromVerify(), catalog });
    const output = resolve(options.output ?? "artifacts/verification/ci-plan.json");
    writeJson(output, ciPlan);
    appendGitHubOutput(options["github-output"], ciPlan);
    process.stdout.write(`CI verification plan: ${output}\n`);
    return 0;
  }
  if (command === "find-reusable-run") {
    const source = await findReusableWorkflowRun();
    writeJson(resolve(options.output ?? "artifacts/verification/reusable-run.json"), source);
    if (options["github-output"]) {
      appendFileSync(options["github-output"], `run_id=${source?.runId ?? ""}\n`);
    }
    return 0;
  }
  if (command === "reuse") {
    if (!options.plan || !options.output) {
      throw new Error("reuse requires --plan and --output");
    }
    const ciPlan = JSON.parse(readFileSync(resolve(options.plan), "utf8"));
    validateCiPlan(ciPlan, {
      currentCommit: currentCommit(),
      currentCatalogSha256: verificationCatalogSha256(),
      catalog,
    });
    const sourceRun = readJsonIfPresent(options["source-run"]);
    const directReport = readJsonIfPresent(options["source-report"]);
    const source = sourceRun && directReport
      ? {
          headSha: sourceRun.headSha,
          testedCommit: directReport.commit_sha,
          runStatus: sourceRun.status,
          runConclusion: sourceRun.conclusion,
          directReport,
          shardReports: loadShardReports(resolve(options["source-shards"] ?? "artifacts/verification/prior-shards")),
        }
      : null;
    const currentHeadSha = pullRequestHeadSha();
    const changedPathsSinceSource = source && currentHeadSha
      ? gitDiffPaths(source.headSha, currentHeadSha)
      : [];
    const reuse = planShardEvidenceReuse({
      ciPlan,
      source,
      changedPathsSinceSource,
      sourceIsAncestor: source && currentHeadSha
        ? isAncestor(source.headSha, currentHeadSha)
        : false,
      classifyPaths: (paths) => classifyChangedPaths(paths, catalog),
    });
    const reportsDirectory = resolve(dirname(options.output), "reused-shards");
    mkdirSync(reportsDirectory, { recursive: true });
    if (source) {
      for (const shardId of reuse.reusedShardIds) {
        writeJson(
          resolve(reportsDirectory, `${shardId}.json`),
          materializeReusedShardReport({
            ciPlan,
            source,
            shardId,
            sourceRunId: sourceRun.runId,
          }),
        );
      }
    }
    const output = {
      schemaVersion: "verification-evidence-reuse/v1",
      currentCommit: ciPlan.testedCommit,
      sourceRun: sourceRun ?? null,
      changedPathsSinceSource,
      ...reuse,
    };
    writeJson(resolve(options.output), output);
    appendReuseOutput(options["github-output"], reuse);
    return 0;
  }
  if (command === "run-shard") {
    if (!options.plan || !options.shard || !options.output) {
      throw new Error("run-shard requires --plan, --shard, and --output");
    }
    const ciPlan = JSON.parse(readFileSync(resolve(options.plan), "utf8"));
    let report;
    try {
      report = runVerificationShard({ ciPlan, shardId: options.shard, catalog });
    } catch (error) {
      const testedCommit = currentCommit();
      const diagnostics = runnerFailureDiagnostics({
        shardId: options.shard,
        testedCommit,
        error,
      });
      report = {
        schemaVersion: shardReportSchemaVersion,
        shardId: options.shard,
        testedCommit,
        verificationCatalogSha256: verificationCatalogSha256(),
        planDigest: ciPlan.planDigest ?? null,
        runnerOS: process.platform === "win32" ? "windows" : process.platform,
        expectedGateIds: ciPlan.shards?.find(
          (shard) => shard.id === options.shard,
        )?.gateIds ?? [],
        startedAt: new Date().toISOString(),
        finishedAt: new Date().toISOString(),
        durationSeconds: 0,
        status: "failed",
        cleanIsolatedPlaywright: false,
        clearedInheritedPlaywrightEnvironment: {},
        artifacts: [],
        error: error.message,
        gates: [],
        diagnostics,
      };
    }
    writeJson(resolve(options.output), report);
    return report.status === "passed" ? 0 : 1;
  }
  if (command === "aggregate") {
    if (!options.plan || !options.shards || !options.output) {
      throw new Error("aggregate requires --plan, --shards, and --output");
    }
    const ciPlan = JSON.parse(readFileSync(resolve(options.plan), "utf8"));
    const report = aggregateVerificationShards({
      ciPlan,
      shardReports: loadShardReports(resolve(options.shards)),
      catalog,
    });
    writeJson(resolve(options.output), report);
    const acceptanceReport = buildParallelAcceptanceReport({
      verificationReport: report,
      ciPlan,
      catalog,
    });
    if (acceptanceReport && options["acceptance-output"]) {
      writeJson(resolve(options["acceptance-output"]), acceptanceReport);
    }
    process.stdout.write(`${report.pr_body_evidence}\n`);
    if (process.env.GITHUB_STEP_SUMMARY) {
      appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${report.pr_body_evidence}\n`);
    }
    return ["failed", "pending"].includes(report.outcome) ? 1 : 0;
  }
  throw new Error("CI verification command is required: plan, run-shard, or aggregate");
}

if (
  process.argv[1]
  && import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  main().then(
    (exitCode) => { process.exitCode = exitCode; },
    (error) => {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 2;
    },
  );
}
