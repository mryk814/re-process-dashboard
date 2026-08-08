import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { isAbsolute, relative, resolve } from "node:path";
import {
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
  verificationGateEnvironment,
} from "./verification-gates.mjs";
import {
  createVerificationReceipt,
  createVerificationReceiptIdentity,
  findReusableVerificationReceipt,
  normalizeReceiptRelativePath,
  writeReceiptOutput,
  writeVerificationReceipt,
} from "./verification-receipts.mjs";
import { classifyProcessResult, runStreamingCommand } from "./verification-process.mjs";

const argv = process.argv.slice(2);
const catalog = loadVerificationCatalog();
const catalogSha256 = verificationCatalogSha256();
const maximumSemanticDiffBytes = 256 * 1024;

function gitOutput(args) {
  const result = spawnSync("git", args, { encoding: "utf8" });
  return result.status === 0 ? result.stdout.trim() : null;
}

function gitFileText(baseRef, path) {
  const result = spawnSync("git", ["show", `${baseRef}:${path}`], {
    encoding: "utf8",
    maxBuffer: maximumSemanticDiffBytes,
  });
  return result.status === 0 ? result.stdout : null;
}

function boundedFileText(path) {
  if (!existsSync(path) || statSync(path).size > maximumSemanticDiffBytes) return null;
  return readFileSync(path, "utf8");
}

function collectSemanticDiffs(baseRef, changedPaths) {
  return Object.fromEntries(
    changedPaths
      .filter((path) => path === "package.json" || path.endsWith("/package.json"))
      .map((path) => [path, {
        beforeText: gitFileText(baseRef, path),
        afterText: boundedFileText(resolve(path)),
      }]),
  );
}

function receiptInputPaths(plan) {
  const focusedPaths = plan.focusedTests.tests
    .filter((value) => !String(value).startsWith("-"))
    .map((value) => String(value).split("::", 1)[0])
    .filter((value) => /(?:^|[\\/])tests?(?:[\\/]|$)|\.(?:py|mjs|cjs|js|jsx|ts|tsx)$/i.test(value));
  const focusedNodePaths = (plan.focusedNodeTests ?? [])
    .map((value) => String(value).split("::", 1)[0]);
  const repoRoot = resolve(".");
  return [...new Set([...plan.changedPaths, ...focusedPaths, ...focusedNodePaths])].flatMap((value) => {
    try {
      return [normalizeReceiptRelativePath(value)];
    } catch {
      const relativePath = relative(repoRoot, resolve(value));
      if (relativePath === "" || relativePath.startsWith("..") || isAbsolute(relativePath)) return [];
      try {
        return [normalizeReceiptRelativePath(relativePath)];
      } catch {
        return [];
      }
    }
  });
}

function receiptDirectory() {
  return resolve(
    process.env.VERIFICATION_RECEIPTS_DIR ?? "artifacts/verification/receipts",
  );
}

function commandText(command, args) {
  return [command, ...args].join(" ");
}

function gateIdentity({ gateId, plan, commandArgv, environment }) {
  return createVerificationReceiptIdentity({
    repoRoot: resolve("."),
    commitSha: plan.fullSuiteOwner.commitSha ?? gitOutput(["rev-parse", "HEAD"]),
    gateId,
    commandArgv,
    inputPaths: receiptInputPaths(plan),
    catalogDigest: catalogSha256,
    environmentOptions: { env: environment },
  });
}

function writeGateReceipt({
  identity,
  status,
  exitCode,
  signal,
  durationSeconds,
  stdout,
  stderr,
}) {
  const receiptsDirectory = receiptDirectory();
  const stdoutLocator = writeReceiptOutput({
    receiptsDirectory,
    receiptId: identity.receipt_identity_digest,
    kind: "stdout",
    output: stdout,
  });
  const stderrLocator = writeReceiptOutput({
    receiptsDirectory,
    receiptId: identity.receipt_identity_digest,
    kind: "stderr",
    output: stderr,
  });
  const receipt = createVerificationReceipt({
    identity,
    status,
    exitCode,
    signal,
    durationSeconds,
    artifacts: { stdout: stdoutLocator, stderr: stderrLocator },
  });
  writeVerificationReceipt({ receipt, receiptsDirectory });
  return receipt;
}

function receiptSummary(results) {
  const summary = {
    executed: 0,
    reused: 0,
    not_run: 0,
    failed: 0,
    classification_required: 0,
  };
  for (const result of results) {
    if (result.execution && Object.hasOwn(summary, result.execution)) summary[result.execution] += 1;
    if (["failed", "timeout", "interrupted"].includes(result.status)) summary.failed += 1;
  }
  return summary;
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
const semanticDiffs = collectSemanticDiffs(baseRef, changedPaths);
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
    semanticDiffs,
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
  } else if (value.completion === "classification_required") {
    process.stdout.write(`CLASSIFICATION REQUIRED: ${value.incompleteReasons.join("; ")}\n`);
    for (const item of value.classificationRequirements) {
      process.stdout.write(`- ${item.path ?? "backend authority"}: ${item.reason}\n`);
    }
  }
  process.stdout.write(`Focused tests: ${value.focusedTests.tests.join(", ") || "none"} (${value.focusedTests.source})\n`);
  process.stdout.write(`Focused Node tests: ${(value.focusedNodeTests ?? []).join(", ") || "none"}\n`);
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
let results = plan.classificationRequired
  ? [{
      id: "classification-required",
      status: "classification_required",
      execution: "classification_required",
      command: null,
      command_argv: [],
      exitCode: null,
      durationSeconds: 0,
      error: "semantic authority classification is required before verification evidence can be complete",
      candidates: plan.classificationRequirements.flatMap((item) => item.candidates ?? []),
      receipt: null,
    }]
  : [];
let exitCode = 0;
const currentPlatform = process.platform === "win32" ? "windows" : process.platform;
const plannedReceipts = new Map();
const receiptsDirectory = receiptDirectory();
for (const gateId of plan.selectedGateIds) {
  const gate = catalog.gates[gateId];
  const resolvedRunner = resolveRunner(gate, {
    focusedArgs: plan.focusedTests.tests,
    focusedNodeArgs: plan.focusedNodeTests ?? [],
    changedPaths: plan.changedPaths,
    baseRef: plan.baseRef,
  });
  const executable = resolveExecutable(resolvedRunner.executable);
  const args = [...executable.prefix, ...resolvedRunner.args];
  const gateEnvironment = verificationGateEnvironment(
    process.env,
    gateId,
    plan.selectedGateIds,
  );
  plannedReceipts.set(gateId, {
    command: [executable.command, ...args],
    identity: gateIdentity({
      gateId,
      plan,
      commandArgv: [executable.command, ...args],
      environment: gateEnvironment,
    }),
  });
}
for (const gateId of plan.selectedGateIds) {
  const gate = catalog.gates[gateId];
  const plannedReceipt = plannedReceipts.get(gateId);
  const [executableCommand, ...executableArgs] = plannedReceipt.command;
  const grouped = process.env.GITHUB_ACTIONS === "true";
  process.stdout.write(grouped ? `::group::${gateId}\n` : `\n== ${gateId} ==\n`);
  if (levelId === "pr") {
    const reusable = findReusableVerificationReceipt({
      identity: plannedReceipt.identity,
      receiptsDirectory,
    });
    if (reusable.kind === "reused") {
      const reused = {
        id: gateId,
        status: "passed",
        execution: "reused",
        command: commandText(executableCommand, executableArgs),
        command_argv: plannedReceipt.identity.command_argv,
        exitCode: 0,
        durationSeconds: reusable.receipt.duration_seconds,
        error: null,
        receipt: {
          kind: "reused",
          receipt_id: reusable.receipt_id,
          created_at: reusable.created_at,
          identity_matches: reusable.identity_matches,
        },
      };
      results.push(reused);
      process.stdout.write(`reused receipt ${reusable.receipt_id}\n`);
      if (grouped) process.stdout.write("::endgroup::\n");
      continue;
    }
  }
  const gateStartedAt = new Date();
  const platformSupported = gateRunsOnPlatform(gate.platform, currentPlatform);
  const gateEnvironment = verificationGateEnvironment(
    process.env,
    gateId,
    plan.selectedGateIds,
  );
  const result = platformSupported
    ? await runStreamingCommand({
        command: executableCommand,
        args: executableArgs,
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
  if (grouped) process.stdout.write("::endgroup::\n");
  const status = classifyProcessResult(result);
  const durationSeconds = Number(((gateFinishedAt - gateStartedAt) / 1_000).toFixed(3));
  const gateReceipt = writeGateReceipt({
    identity: plannedReceipt.identity,
    status,
    exitCode: result.status ?? (result.error || result.signal ? 1 : 0),
    signal: result.signal ?? null,
    durationSeconds,
    stdout,
    stderr: `${stderr}${result.error?.message ? `${result.error.message}\n` : ""}`,
  });
  results.push({
    id: gateId,
    status,
    execution: "executed",
    command: commandText(executableCommand, executableArgs),
    command_argv: plannedReceipt.identity.command_argv,
    exitCode: gateReceipt.exit_code,
    signal: gateReceipt.signal,
    durationSeconds,
    error: result.error?.message ?? null,
    receipt: { kind: "executed", receipt_id: gateReceipt.receipt_id, created_at: gateReceipt.created_at },
  });
  if (status !== "passed") { exitCode = gateReceipt.exit_code || 1; break; }
}
if (exitCode !== 0) {
  const completed = new Set(results.map((result) => result.id));
  for (const gateId of plan.selectedGateIds.filter((id) => !completed.has(id))) {
    const plannedReceipt = plannedReceipts.get(gateId);
    const receipt = writeGateReceipt({
      identity: plannedReceipt.identity,
      status: "not_run",
      exitCode: null,
      signal: null,
      durationSeconds: 0,
      stdout: "",
      stderr: "",
    });
    results.push({
      id: gateId,
      status: "not_run",
      execution: "not_run",
      command: commandText(plannedReceipt.command[0], plannedReceipt.command.slice(1)),
      command_argv: plannedReceipt.identity.command_argv,
      exitCode: null,
      durationSeconds: 0,
      error: "an earlier selected gate failed",
      receipt: { kind: "not_run", receipt_id: receipt.receipt_id, created_at: receipt.created_at },
    });
  }
}
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
  receipt_summary: receiptSummary(results),
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
