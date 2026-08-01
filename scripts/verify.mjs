import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import {
  appendNotRunResults,
  buildVerificationPlan,
  catalogPath,
  getVerificationLevel,
  loadVerificationCatalog,
  parseVerificationArguments,
  resolveRunner,
} from "./verification-gates.mjs";

const argv = process.argv.slice(2);
const catalog = loadVerificationCatalog();
const npmCli = process.env.npm_execpath;
const catalogSha256 = createHash("sha256").update(readFileSync(catalogPath)).digest("hex");

function executableFor(name) {
  if (name === "npm") return npmCli ? { command: process.execPath, prefix: [npmCli] } : { command: process.platform === "win32" ? "npm.cmd" : "npm", prefix: [] };
  if (name === "npx") return { command: process.platform === "win32" ? "npx.cmd" : "npx", prefix: [] };
  if (name === "powershell") return { command: process.platform === "win32" ? "powershell.exe" : "pwsh", prefix: [] };
  return { command: name, prefix: [] };
}

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
  if (value.completion === "incomplete") {
    process.stdout.write(`INCOMPLETE: ${value.incompleteReasons.join("; ")}\n`);
    process.stdout.write(`Required follow-up: ${value.requiredFollowUp.command} (${value.requiredFollowUp.reason})\n`);
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
for (const gateId of plan.selectedGateIds) {
  const gate = catalog.gates[gateId];
  const resolvedRunner = resolveRunner(gate, { focusedArgs: plan.focusedTests.tests, baseRef: plan.baseRef });
  const executable = executableFor(resolvedRunner.executable);
  const args = [...executable.prefix, ...resolvedRunner.args];
  const grouped = process.env.GITHUB_ACTIONS === "true";
  process.stdout.write(grouped ? `::group::${gateId}\n` : `\n== ${gateId} ==\n`);
  const gateStartedAt = new Date();
  const result = spawnSync(executable.command, args, { stdio: "inherit", env: process.env });
  const gateFinishedAt = new Date();
  if (grouped) process.stdout.write("::endgroup::\n");
  const status = result.error || result.status !== 0 ? "failed" : "passed";
  results.push({ id: gateId, status, command: [executable.command, ...args].join(" "), exitCode: result.status ?? (result.error ? 1 : 0), durationSeconds: Number(((gateFinishedAt - gateStartedAt) / 1_000).toFixed(3)), error: result.error?.message ?? null });
  if (status === "failed") { exitCode = result.status ?? 1; break; }
}
if (exitCode !== 0) results = appendNotRunResults(plan.selectedGateIds, results, catalog);
const finishedAt = new Date();
const report = {
  ...plan,
  startedAt: startedAt.toISOString(),
  finishedAt: finishedAt.toISOString(),
  durationSeconds: Number(((finishedAt - startedAt) / 1_000).toFixed(3)),
  status: exitCode === 0 && plan.completion === "ready" ? "passed" : exitCode === 0 ? "incomplete" : "failed",
  gates: results,
};
const artifactDirectory = resolve("artifacts", "verification");
mkdirSync(artifactDirectory, { recursive: true });
writeFileSync(resolve(artifactDirectory, `latest-${levelId}.json`), `${JSON.stringify(report, null, 2)}\n`);
process.stdout.write(`\nVerification report: artifacts/verification/latest-${levelId}.json\n`);
process.exit(exitCode === 0 && plan.completion === "incomplete" ? 3 : exitCode);
