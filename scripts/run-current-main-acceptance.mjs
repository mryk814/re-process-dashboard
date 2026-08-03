import { createHash, randomUUID } from "node:crypto";
import { createServer } from "node:net";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execFileSync, spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

import {
  CHECK_CONTRACTS,
  CURRENT_MAIN_ACCEPTANCE_SCHEMA,
  createCurrentMainAcceptanceReceipt,
  notRun,
  validateCurrentMainAcceptanceReceipt,
} from "../e2e/current-main-acceptance-report.mjs";

export const currentMainAcceptanceSpecs = Object.freeze([
  "current-main-acceptance-single-task.spec.ts",
  "current-main-acceptance-prediction-graph.spec.ts",
]);

export function capabilityAtlasDigest(path = join(process.cwd(), "docs", "contracts", "capability-atlas.json")) {
  if (!existsSync(path)) throw new Error(`Capability Atlas is missing: ${path}`);
  return `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex")}`;
}

export function validateAcceptanceEnvironment(env = process.env, actualDigest = capabilityAtlasDigest()) {
  const expected = env.CURRENT_MAIN_CAPABILITY_ATLAS_DIGEST;
  if (!/^sha256:[a-f0-9]{64}$/.test(expected ?? "")) {
    throw new Error("CURRENT_MAIN_CAPABILITY_ATLAS_DIGEST must be sha256:<64 lowercase hex>");
  }
  if (expected !== actualDigest) {
    throw new Error(`Capability Atlas digest mismatch: expected ${expected}, actual ${actualDigest}`);
  }
  const retries = Number(env.CURRENT_MAIN_ACCEPTANCE_RETRIES ?? "0");
  if (!Number.isInteger(retries) || retries !== 0) {
    throw new Error("CURRENT_MAIN_ACCEPTANCE_RETRIES must be 0; A/B each run once against fresh resources");
  }
  return { atlasDigest: actualDigest, retries };
}

export function validateReceipt(receipt, { spec, commit, atlasDigest }) {
  if (receipt?.schema_version !== CURRENT_MAIN_ACCEPTANCE_SCHEMA) {
    throw new Error(`${spec}: missing ${CURRENT_MAIN_ACCEPTANCE_SCHEMA} receipt`);
  }
  const validated = validateCurrentMainAcceptanceReceipt(receipt);
  if (validated.status !== "passed") throw new Error(`${spec}: receipt is ${validated.status}`);
  if (validated.tested_commit !== commit) throw new Error(`${spec}: tested commit mismatch`);
  if (validated.capability_atlas_digest !== atlasDigest) throw new Error(`${spec}: Atlas digest mismatch`);
  if (validated.tested_tree.status !== "clean" || validated.tested_tree.porcelain !== "") {
    throw new Error(`${spec}: tested tree is not clean`);
  }
  return validated;
}

export function gitTree(root = process.cwd()) {
  const porcelain = execFileSync("git", ["status", "--porcelain"], {
    cwd: root,
    encoding: "utf8",
  });
  return { status: porcelain.length === 0 ? "clean" : "dirty", porcelain };
}

export function assertCleanTree(tree, phase) {
  if (tree.status !== "clean" || tree.porcelain !== "") {
    throw new Error(`${phase}: git tree must be clean\n${tree.porcelain}`);
  }
}

export function failureReceipt({
  journey,
  atlasDigest,
  commit,
  tree,
  phase,
  code,
  message,
  childExitCode,
}) {
  return createCurrentMainAcceptanceReceipt({
    journey,
    atlasDigest,
    commit,
    testedTree: tree,
    resources: [{
      kind: "acceptance_failure",
      id: `${journey}-${phase}`,
      identity: {
        phase,
        code,
        message,
        child_exit_code: childExitCode,
      },
    }],
    checks: Object.keys(CHECK_CONTRACTS[journey]).map((id) => notRun(
      id,
      `acceptance stopped at ${phase}: ${message}`,
      "current-main-acceptance follow-up",
    )),
    diagnostic: {
      phase,
      code,
      message,
      child_exit_code: childExitCode,
      git_porcelain: tree.porcelain,
    },
  });
}

export function aggregateReceipt({ results, commit, atlasDigest }) {
  return {
    schema_version: "current-main-acceptance-aggregate/v1",
    status: results.length === currentMainAcceptanceSpecs.length
      && results.every(({ receipt }) => receipt.status === "passed")
      ? "passed"
      : "incomplete",
    tested_commit: commit,
    capability_atlas_digest: atlasDigest,
    journeys: results.map(({ spec, receipt }) => ({
      spec,
      journey: receipt.journey,
      status: receipt.status,
      tested_tree: receipt.tested_tree,
      checks: receipt.checks,
      ...(receipt.diagnostic ? { diagnostic: receipt.diagnostic } : {}),
    })),
  };
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") return reject(new Error("could not reserve port"));
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

function findReceipt(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      const nested = findReceipt(path);
      if (nested) return nested;
    } else if (entry.name === "current-main-acceptance-receipt.json") {
      return path;
    }
  }
  return undefined;
}

async function runSpec({ root, cli, spec, index, reportDirectory, atlasDigest, commit }) {
  const [apiPort, webPort] = await Promise.all([reservePort(), reservePort()]);
  const outputDir = join(reportDirectory, spec.replace(/\.spec\.ts$/, ""));
  mkdirSync(outputDir, { recursive: true });
  const runId = `${Date.now()}-${process.pid}-${index}-${randomUUID()}`;
  const code = await new Promise((resolve) => {
    const child = spawn(process.execPath, [
      cli, "test", `e2e/${spec}`,
      "--config", "playwright.current-main-acceptance.config.ts",
      "--workers=1", "--retries=0",
    ], {
      cwd: root,
      env: {
        ...process.env,
        CURRENT_MAIN_CAPABILITY_ATLAS_DIGEST: atlasDigest,
        PLAYWRIGHT_API_PORT: String(apiPort),
        PLAYWRIGHT_WEB_PORT: String(webPort),
        PLAYWRIGHT_OUTPUT_DIR: outputDir,
        PLAYWRIGHT_E2E_RUN_ID: runId,
      },
      stdio: "inherit",
    });
    child.once("error", () => resolve(1));
    child.once("exit", (value) => resolve(value ?? 1));
  });
  const tree = gitTree(root);
  const journey = spec.includes("prediction-graph") ? "prediction-graph" : "single-task";
  const base = { spec, code, outputDir, runId, apiPort, webPort, tree };
  if (code !== 0) {
    return {
      ...base,
      receipt: failureReceipt({
        journey, atlasDigest, commit, tree, phase: "spec_execution",
        code: "playwright_failed", message: `${spec} exited ${code}`, childExitCode: code,
      }),
      canContinue: tree.status === "clean",
    };
  }
  if (tree.status !== "clean") {
    return {
      ...base,
      receipt: failureReceipt({
        journey, atlasDigest, commit, tree, phase: "post_spec_tree",
        code: "dirty_tested_tree", message: `${spec} changed tracked files`, childExitCode: code,
      }),
      canContinue: false,
    };
  }
  const receiptPath = findReceipt(outputDir);
  if (!receiptPath) {
    return {
      ...base,
      receipt: failureReceipt({
        journey, atlasDigest, commit, tree, phase: "receipt_collection",
        code: "receipt_missing", message: `${spec} did not write a receipt`, childExitCode: code,
      }),
      canContinue: true,
    };
  }
  try {
    const receipt = validateReceipt(JSON.parse(readFileSync(receiptPath, "utf8")), {
      spec, commit, atlasDigest,
    });
    return { ...base, receiptPath, receipt, canContinue: true };
  } catch (error) {
    return {
      ...base,
      receiptPath,
      receipt: failureReceipt({
        journey, atlasDigest, commit, tree, phase: "receipt_validation",
        code: "receipt_invalid", message: String(error), childExitCode: code,
      }),
      canContinue: true,
    };
  }
}

async function main() {
  const root = process.cwd();
  const { atlasDigest, retries } = validateAcceptanceEnvironment();
  const commit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
  const reportDirectory = join(root, "test-results", `current-main-acceptance-${Date.now()}-${process.pid}`);
  mkdirSync(reportDirectory, { recursive: true });
  const cli = join(root, "node_modules", "@playwright", "test", "cli.js");
  const results = [];
  const startTree = gitTree(root);
  if (startTree.status !== "clean") {
    for (const spec of currentMainAcceptanceSpecs) {
      const journey = spec.includes("prediction-graph") ? "prediction-graph" : "single-task";
      results.push({
        spec,
        code: 1,
        tree: startTree,
        receipt: failureReceipt({
          journey, atlasDigest, commit, tree: startTree, phase: "runner_start",
          code: "dirty_start_tree", message: "runner refused a dirty tested tree", childExitCode: null,
        }),
      });
    }
  }
  if (results.length === 0) {
    for (let index = 0; index < currentMainAcceptanceSpecs.length; index += 1) {
      const result = await runSpec({
        root, cli, spec: currentMainAcceptanceSpecs[index], index, reportDirectory, atlasDigest, commit,
      });
      results.push(result);
      if (!result.canContinue && index + 1 < currentMainAcceptanceSpecs.length) {
        const spec = currentMainAcceptanceSpecs[index + 1];
        const journey = spec.includes("prediction-graph") ? "prediction-graph" : "single-task";
        results.push({
          spec,
          code: 1,
          tree: result.tree,
          receipt: failureReceipt({
            journey, atlasDigest, commit, tree: result.tree, phase: "prior_spec_dirty_tree",
            code: "blocked_by_dirty_tree", message: "prior journey changed tracked files", childExitCode: null,
          }),
        });
        break;
      }
    }
  }
  const report = {
    schema_version: "current-main-acceptance-run/v1",
    tested_commit: commit,
    capability_atlas_digest: atlasDigest,
    retries,
    fresh_process_per_journey: true,
    specs: results,
    aggregate_receipt: aggregateReceipt({ results, commit, atlasDigest }),
  };
  const reportPath = join(reportDirectory, "report.json");
  writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`Current-main acceptance report: ${reportPath}\n`);
  if (results.some(({ code, receipt }) => code !== 0 || receipt.status !== "passed")) process.exitCode = 1;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  await main();
}
