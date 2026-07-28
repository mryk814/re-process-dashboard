import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import {
  catalogPath,
  getVerificationLevel,
  loadVerificationCatalog,
  resolveRunner,
} from "./verification-gates.mjs";

const argv = process.argv.slice(2);
const catalog = loadVerificationCatalog();
const npmCli = process.env.npm_execpath;
const catalogSha256 = createHash("sha256")
  .update(readFileSync(catalogPath))
  .digest("hex");

function executableFor(name) {
  if (name === "npm") {
    return npmCli
      ? { command: process.execPath, prefix: [npmCli] }
      : { command: process.platform === "win32" ? "npm.cmd" : "npm", prefix: [] };
  }
  if (name === "npx") {
    return {
      command: process.platform === "win32" ? "npx.cmd" : "npx",
      prefix: [],
    };
  }
  if (name === "powershell") {
    return {
      command: process.platform === "win32" ? "powershell.exe" : "pwsh",
      prefix: [],
    };
  }
  return { command: name, prefix: [] };
}

function gitOutput(args) {
  const result = spawnSync("git", args, { encoding: "utf8" });
  return result.status === 0 ? result.stdout.trim() : null;
}

function describeCatalog(asJson) {
  if (asJson) {
    process.stdout.write(`${JSON.stringify(catalog, null, 2)}\n`);
    return;
  }
  for (const level of catalog.levels) {
    process.stdout.write(
      `${level.label} (${level.estimatedMinutes} min, ${level.platform})\n`,
    );
    process.stdout.write(`  ${level.purpose}\n`);
    for (const gateId of level.gates) {
      process.stdout.write(`  - ${gateId}: ${catalog.gates[gateId].command}\n`);
    }
  }
}

if (argv[0] === "--list") {
  describeCatalog(argv.includes("--json"));
  process.exit(0);
}

const levelId = argv.shift();
if (!levelId) {
  process.stderr.write(
    "verification level is required: edit, pr, checkpoint, or --list\n",
  );
  process.exit(2);
}
if (levelId === "release") {
  process.stderr.write(
    "release evidence uses `npm run acceptance:release` so Windows artifacts and the acceptance report are captured.\n",
  );
  process.exit(2);
}

const planOnly = argv.includes("--plan");
const asJson = argv.includes("--json");
const separator = argv.indexOf("--");
const focusedArgs =
  separator >= 0
    ? argv.slice(separator + 1)
    : argv.filter((arg) => !["--plan", "--json"].includes(arg));
const level = getVerificationLevel(catalog, levelId);
if (levelId === "edit" && focusedArgs.length === 0) {
  process.stderr.write(
    "edit level requires focused pytest paths after `--`.\n",
  );
  process.exit(2);
}

const selectedGateIds = [...level.gates];
if (
  levelId === "pr"
  && focusedArgs.length > 0
  && !selectedGateIds.includes("focused-pytest")
) {
  selectedGateIds.unshift("focused-pytest");
}
const omittedGates = Object.keys(catalog.gates)
  .filter((gateId) => !selectedGateIds.includes(gateId))
  .map((gateId) => ({
    id: gateId,
    status: "not_run",
    reason: "selected verification level does not include this gate",
  }));
if (levelId === "pr" && focusedArgs.length === 0) {
  const focused = omittedGates.find((gate) => gate.id === "focused-pytest");
  focused.reason = "no focused pytest paths were supplied; record affected tests separately";
}

const plan = {
  schemaVersion: "verification-run/v1",
  level: levelId,
  label: level.label,
  purpose: level.purpose,
  estimatedMinutes: level.estimatedMinutes,
  verificationCatalogSha256: catalogSha256,
  baseRef: process.env.VERIFY_BASE_REF || "origin/main",
  focusedArgs,
  selectedGates: selectedGateIds.map((id) => ({
    id,
    command: catalog.gates[id].command,
  })),
  omittedGates,
};
if (planOnly) {
  process.stdout.write(
    asJson
      ? `${JSON.stringify(plan, null, 2)}\n`
      : `${level.label}\n${plan.selectedGates.map((gate) => `- ${gate.id}: ${gate.command}`).join("\n")}\n`,
  );
  process.exit(0);
}

process.stdout.write(
  `\n${level.label}: ${level.purpose}\n`
  + `Selected gates: ${selectedGateIds.join(", ")}\n`
  + `Not run by this level: ${omittedGates.map((gate) => gate.id).join(", ")}\n`,
);

const startedAt = new Date();
const results = [];
let exitCode = 0;
for (const gateId of selectedGateIds) {
  const gate = catalog.gates[gateId];
  const resolvedRunner = resolveRunner(gate, {
    focusedArgs,
    baseRef: plan.baseRef,
  });
  const executable = executableFor(resolvedRunner.executable);
  const args = [...executable.prefix, ...resolvedRunner.args];
  const grouped = process.env.GITHUB_ACTIONS === "true";
  process.stdout.write(
    grouped ? `::group::${gateId}\n` : `\n== ${gateId} ==\n`,
  );
  const gateStartedAt = new Date();
  const result = spawnSync(executable.command, args, {
    stdio: "inherit",
    env: process.env,
  });
  const gateFinishedAt = new Date();
  if (grouped) process.stdout.write("::endgroup::\n");
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
  });
  if (status === "failed") {
    exitCode = result.status ?? 1;
    break;
  }
}

const finishedAt = new Date();
const report = {
  ...plan,
  testedCommit: gitOutput(["rev-parse", "HEAD"]),
  startedAt: startedAt.toISOString(),
  finishedAt: finishedAt.toISOString(),
  durationSeconds: Number(((finishedAt - startedAt) / 1_000).toFixed(3)),
  status: exitCode === 0 ? "passed" : "failed",
  gates: results,
};
const artifactDirectory = resolve("artifacts", "verification");
mkdirSync(artifactDirectory, { recursive: true });
writeFileSync(
  resolve(artifactDirectory, `latest-${levelId}.json`),
  `${JSON.stringify(report, null, 2)}\n`,
);
process.stdout.write(
  `\nVerification report: artifacts/verification/latest-${levelId}.json\n`,
);
process.exit(exitCode);
