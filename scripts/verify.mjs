import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import {
  appendNotRunResults,
  buildVerificationPlan,
  getVerificationLevel,
  gateRunsOnPlatform,
  loadVerificationCatalog,
  parseVerificationArguments,
  resolveExecutable,
  resolveRunner,
  evaluateVerificationOutcome,
  verificationEvidenceMarkdown,
  verificationCatalogSha256,
} from "./verification-gates.mjs";

const argv = process.argv.slice(2);
const catalog = loadVerificationCatalog();
const catalogSha256 = verificationCatalogSha256();

function gitOutput(args) {
  const result = spawnSync("git", args, { encoding: "utf8" });
  return result.status === 0 ? result.stdout.trim() : null;
}

function describeCatalog(asJson) {
  if (asJson) return process.stdout.write(`${JSON.stringify(catalog, null, 2)}\n`);
  for (const level of catalog.levels) {
    process.stdout.write(`${level.label} (${level.estimatedMinutes} min, ${level.platform})\n  ${level.purpose}\n`);
    for (const gateId of level.gates) process.stdout.write(`  - ${gateId}: ${catalog.gates[gateId].command}\n`);
  }
}

if (argv[0] === "--list") {
  describeCatalog(argv.includes("--json"));
  process.exit(0);
}

const levelId = argv.shift();
if (!levelId) {
  process.stderr.write("verification level is required: edit, pr, checkpoint, or --list\n");
  process.exit(2);
}
if (levelId === "release") {
  process.stderr.write("release evidence uses `npm run acceptance:release` so Windows artifacts and the acceptance report are captured.\n");
  process.exit(2);
}

let options;
try {
  options = parseVerificationArguments(argv);
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(2);
}
if (levelId === "edit" && options.focusedArgs.length === 0) {
  process.stderr.write("edit level requires focused pytest paths after `--`.\n");
  process.exit(2);
}

const baseRef = process.env.VERIFY_BASE_REF || "origin/main";
const changedPaths = [...new Set([
  ...(gitOutput(["diff", "--name-only", "--find-renames", `${baseRef}...HEAD`]) ?? "").split(/\r?\n/),
  ...(gitOutput(["diff", "--name-only", "HEAD"]) ?? "").split(/\r?\n/),
  ...(gitOutput(["ls-files", "--others", "--exclude-standard"]) ?? "").split(/\r?\n/),
].filter(Boolean))].sort();
let plan;
try {
  plan = buildVerificationPlan({
    catalog,
    requestedLevel: levelId,
    changedPaths,
    focusedArgs: options.focusedArgs,
    manualRiskOverrides: options.risks,
    manualOverrideReason: options.reason,
    baseRef,
    commitSha: gitOutput(["rev-parse", "HEAD"]),
    ci: process.env.GITHUB_ACTIONS === "true",
  });
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(2);
}
plan = {
  ...plan,
  verificationCatalogSha256: catalogSha256,
  label: getVerificationLevel(catalog, levelId).label,
};

function printPlan(value, asJson) {
  if (asJson) return process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
  process.stdout.write(`\n${value.label}: ${value.riskCategories.join(", ")}\n`);
  process.stdout.write(`Execution level: ${value.executionLevel}; selected evidence level: ${value.selectedLevel}\n`);
  if (value.completion === "direct_evidence_required") {
    process.stdout.write(`DIRECT EVIDENCE REQUIRED: ${value.incompleteReasons.join("; ")}\n`);
    for (const item of value.directEvidenceRequirements) {
      process.stdout.write(`- ${item.command} (${item.reason})\n`);
    }
  } else if (value.completion === "follow_up") {
    process.stdout.write(`FOLLOW-UP PLANNED: ${value.incompleteReasons.join("; ")}\n`);
    for (const item of value.requiredFollowUps) {
      process.stdout.write(`- ${item.command} (owner: ${item.owner})\n`);
    }
  }
  process.stdout.write(`Focused tests: ${value.focusedTests.tests.join(", ") || "none"} (${value.focusedTests.source})\n`);
  process.stdout.write(`Full suite owner: ${value.fullSuiteOwner.owner} @ ${value.fullSuiteOwner.commitSha ?? "unknown"}\n`);
  process.stdout.write("Selected gates:\n");
  for (const gate of value.requiredGates) process.stdout.write(`- ${gate.id}: ${gate.reasons.join("; ")}\n`);
  if (value.requiredManualGates.length > 0) process.stdout.write(`Manual evidence still required: ${value.requiredManualGates.map((gate) => gate.id).join(", ")}\n`);
  process.stdout.write("Skipped gates:\n");
  for (const gate of value.skippedGates) process.stdout.write(`- ${gate.id}: ${gate.reason}\n`);
}

if (options.planOnly) {
  printPlan(plan, options.asJson);
  process.exit(0);
}

printPlan(plan, false);
const startedAt = new Date();
let results = [];
let exitCode = 0;
const currentPlatform = process.platform === "win32" ? "windows" : process.platform;
for (const gateId of plan.selectedGateIds) {
  const gate = catalog.gates[gateId];
  const resolvedRunner = resolveRunner(gate, { focusedArgs: plan.focusedTests.tests, baseRef: plan.baseRef });
  const executable = resolveExecutable(resolvedRunner.executable);
  const args = [...executable.prefix, ...resolvedRunner.args];
  const grouped = process.env.GITHUB_ACTIONS === "true";
  process.stdout.write(grouped ? `::group::${gateId}\n` : `\n== ${gateId} ==\n`);
  const gateStartedAt = new Date();
  const platformSupported = gateRunsOnPlatform(gate.platform, currentPlatform);
  const result = platformSupported
    ? spawnSync(executable.command, args, { stdio: "inherit", env: process.env })
    : {
        status: 1,
        error: new Error(
          `${gateId} requires ${gate.platform}; current runner is ${currentPlatform}`,
        ),
      };
  const gateFinishedAt = new Date();
  if (grouped) process.stdout.write("::endgroup::\n");
  const status = result.error || result.status !== 0 ? "failed" : "passed";
  results.push({ id: gateId, status, command: [executable.command, ...args].join(" "), exitCode: result.status ?? (result.error ? 1 : 0), durationSeconds: Number(((gateFinishedAt - gateStartedAt) / 1_000).toFixed(3)), error: result.error?.message ?? null });
  if (status === "failed") { exitCode = result.status ?? 1; break; }
}
if (exitCode !== 0) results = appendNotRunResults(plan.selectedGateIds, results, catalog);
const finishedAt = new Date();
const outcome = evaluateVerificationOutcome({ plan, gateResults: results });
const evidenceMarkdown = verificationEvidenceMarkdown(outcome);
const report = {
  ...plan,
  startedAt: startedAt.toISOString(),
  finishedAt: finishedAt.toISOString(),
  durationSeconds: Number(((finishedAt - startedAt) / 1_000).toFixed(3)),
  status: outcome.outcome,
  ...outcome,
  pr_body_evidence: evidenceMarkdown,
  gates: results,
};
const artifactDirectory = resolve("artifacts", "verification");
mkdirSync(artifactDirectory, { recursive: true });
writeFileSync(resolve(artifactDirectory, `latest-${levelId}.json`), `${JSON.stringify(report, null, 2)}\n`);
process.stdout.write(`\nVerification report: artifacts/verification/latest-${levelId}.json\n`);
process.stdout.write(`${evidenceMarkdown}\n`);
if (process.env.GITHUB_STEP_SUMMARY) {
  appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${evidenceMarkdown}\n`);
}
process.exit(outcome.outcome === "failed" ? (exitCode || 1) : outcome.outcome === "pending" ? 2 : 0);
