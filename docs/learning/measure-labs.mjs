import crypto from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";

const learningRoot = resolve(dirname(fileURLToPath(import.meta.url)));
const repositoryRoot = resolve(learningRoot, "..", "..");
const manifestPath = join(learningRoot, "labs", "manifest.json");
const reportPath = join(learningRoot, "labs", "reproducibility.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));

function sha256(filename) {
  return `sha256:${crypto.createHash("sha256").update(readFileSync(filename)).digest("hex")}`;
}

function payloadFiles(lab) {
  const files = new Set([
    "docs/learning/labs/manifest.json",
    `docs/learning/${lab.document}`,
    ...lab.fixtures,
  ]);
  for (const command of Object.values(lab.commands)) {
    const match = command?.match(/\b(docs\/learning\/labs\/scripts\/[^\s]+\.mjs)\b/);
    if (match) files.add(match[1]);
  }
  return [...files].sort();
}

function runCommand(command, timeoutSeconds) {
  const started = performance.now();
  const result = spawnSync(command, {
    cwd: repositoryRoot,
    encoding: "utf8",
    shell: true,
    timeout: timeoutSeconds * 1000,
  });
  const durationMs = Math.round((performance.now() - started) * 100) / 100;
  if (result.status !== 0) {
    throw new Error(`${command} failed (${result.status}):\n${result.stderr || result.stdout}`);
  }
  return { duration_ms: durationMs, stdout: result.stdout.trim() };
}

function measureExecutable(lab) {
  const outputPath = join(repositoryRoot, ...lab.writes[0].split("/"), "result.json");
  const cycles = [];
  try {
    runCommand(lab.commands.reset, lab.timeout_seconds);
    for (let cycle = 1; cycle <= 2; cycle += 1) {
      const steps = {};
      for (const name of ["setup", "run", "verify"]) {
        steps[name] = runCommand(lab.commands[name], lab.timeout_seconds).duration_ms;
      }
      const resultHash = sha256(outputPath);
      steps.reset = runCommand(lab.commands.reset, lab.timeout_seconds).duration_ms;
      cycles.push({
        cycle,
        duration_ms: Math.round(Object.values(steps).reduce((sum, value) => sum + value, 0) * 100) / 100,
        steps_ms: steps,
        result_hash: resultHash,
      });
    }
  } finally {
    const cleanup = spawnSync(lab.commands.reset, {
      cwd: repositoryRoot,
      encoding: "utf8",
      shell: true,
      timeout: lab.timeout_seconds * 1000,
    });
    if (cleanup.status !== 0) {
      throw new Error(`Cleanup failed for ${lab.lab_id}: ${cleanup.stderr || cleanup.stdout}`);
    }
  }
  return {
    method: "two fresh setup-run-verify-reset cycles",
    cycles,
    reproducible: cycles[0].result_hash === cycles[1].result_hash,
  };
}

const measuredAt = new Date().toISOString();
const labs = manifest.labs.map((lab) => {
  const files = payloadFiles(lab);
  const payloadBytes = files.reduce(
    (sum, path) => sum + statSync(join(repositoryRoot, ...path.split("/"))).size,
    0,
  );
  return {
    lab_id: lab.lab_id,
    mode: lab.mode,
    setup_payload_bytes: payloadBytes,
    setup_payload_files: files,
    expected_reader_minutes: lab.expected_minutes,
    execution:
      lab.mode === "executable"
        ? measureExecutable(lab)
        : {
            method: "guided procedure; no machine setup command",
            cycles: [],
            reproducible: null,
          },
  };
});

const executableCycles = labs.flatMap((lab) => lab.execution.cycles);
const report = {
  schema_version: "learning-lab-reproducibility/v1",
  measured_at: measuredAt,
  measured_commit: execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: repositoryRoot,
    encoding: "utf8",
  }).trim(),
  methodology: {
    setup_size: "UTF-8/source file bytes for each Lab document, shared manifest, fixtures, and executable Lab script; dependencies and repository checkout are excluded",
    runtime: "wall-clock milliseconds on this workstation; only executable Labs have machine runtime",
    reproducibility: "two fresh executable cycles must produce byte-identical result.json; guided Labs are reviewed procedures and are not classified as machine-reproducible",
  },
  summary: {
    lab_count: labs.length,
    guided_count: labs.filter((lab) => lab.mode === "guided").length,
    executable_count: labs.filter((lab) => lab.mode === "executable").length,
    sum_of_per_lab_setup_payload_bytes: labs.reduce((sum, lab) => sum + lab.setup_payload_bytes, 0),
    executable_cycle_duration_ms:
      Math.round(executableCycles.reduce((sum, cycle) => sum + cycle.duration_ms, 0) * 100) / 100,
    executable_result_hashes_match: executableCycles.length === 2 &&
      executableCycles[0].result_hash === executableCycles[1].result_hash,
  },
  labs,
};

if (process.argv.includes("--write")) {
  mkdirSync(dirname(reportPath), { recursive: true });
  writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
}
console.log(JSON.stringify(report, null, 2));
