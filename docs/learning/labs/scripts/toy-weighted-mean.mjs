import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
export const repositoryRoot = path.resolve(scriptDirectory, "..", "..", "..", "..");
const learningRoot = path.join(repositoryRoot, "docs", "learning");
const manifest = JSON.parse(
  fs.readFileSync(path.join(learningRoot, "labs", "manifest.json"), "utf8"),
);
const labId = "toy-weighted-mean";
const allowedRoot = path.resolve(repositoryRoot, ...manifest.allowed_write_root.split("/"));
const outputRoot = path.join(allowedRoot, labId);
const fixturePath = path.join(
  learningRoot,
  "labs",
  "fixtures",
  "toy-weighted-mean.json",
);
const sandboxRecordPath = path.join(outputRoot, "sandbox.json");
const resultPath = path.join(outputRoot, "result.json");

function isWithin(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

export function assertAllowedWritePath(candidate, root = repositoryRoot) {
  const resolvedAllowedRoot = path.resolve(root, ...manifest.allowed_write_root.split("/"));
  const resolved = path.resolve(candidate);
  if (!isWithin(resolvedAllowedRoot, resolved)) {
    throw new Error(`Lab write path is outside ${manifest.allowed_write_root}: ${resolved}`);
  }
  for (const protectedPath of manifest.protected_paths) {
    const protectedTarget = path.resolve(root, ...protectedPath.split("/"));
    if (
      resolved === protectedTarget ||
      isWithin(resolved, protectedTarget) ||
      isWithin(protectedTarget, resolved)
    ) {
      throw new Error(`Lab write path overlaps protected path ${protectedPath}`);
    }
  }
  return resolved;
}

function fileEntries(filename, relative = "") {
  if (!fs.existsSync(filename)) return [[relative, "<missing>"]];
  const stat = fs.statSync(filename);
  if (stat.isFile()) {
    return [[relative, crypto.createHash("sha256").update(fs.readFileSync(filename)).digest("hex")]];
  }
  if (!stat.isDirectory()) return [[relative, `<${stat.mode}>`]];
  return fs
    .readdirSync(filename, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))
    .flatMap((entry) =>
      fileEntries(
        path.join(filename, entry.name),
        relative ? `${relative}/${entry.name}` : entry.name,
      ),
    );
}

export function protectedFingerprint(root = repositoryRoot) {
  const digest = crypto.createHash("sha256");
  for (const protectedPath of manifest.protected_paths) {
    const filename = path.resolve(root, ...protectedPath.split("/"));
    digest.update(`${protectedPath}\0`);
    for (const [relative, hash] of fileEntries(filename)) {
      digest.update(`${relative}\0${hash}\0`);
    }
  }
  return `sha256:${digest.digest("hex")}`;
}

function writeJson(filename, value) {
  const safeOutput = assertAllowedWritePath(filename);
  fs.mkdirSync(path.dirname(safeOutput), { recursive: true });
  fs.writeFileSync(safeOutput, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function readFixture() {
  const fixtureRoot = path.join(learningRoot, "labs", "fixtures");
  if (!isWithin(fixtureRoot, fixturePath)) {
    throw new Error("Toy fixture escaped labs/fixtures");
  }
  return JSON.parse(fs.readFileSync(fixturePath, "utf8"));
}

export function calculateWeightedMean(fixture) {
  if (fixture.schema_version !== "toy-weighted-mean/v1") {
    throw new Error("Unsupported toy fixture schema_version");
  }
  if (!Array.isArray(fixture.measurements) || fixture.measurements.length === 0) {
    throw new Error("Toy fixture needs measurements");
  }
  let totalWeight = 0;
  let weightedSum = 0;
  for (const measurement of fixture.measurements) {
    if (
      typeof measurement.value !== "number" ||
      typeof measurement.weight !== "number" ||
      !Number.isFinite(measurement.value) ||
      !Number.isFinite(measurement.weight) ||
      measurement.weight <= 0
    ) {
      throw new Error(`Invalid measurement ${measurement.id ?? "<unknown>"}`);
    }
    totalWeight += measurement.weight;
    weightedSum += measurement.value * measurement.weight;
  }
  return {
    schema_version: "toy-weighted-mean-result/v1",
    count: fixture.measurements.length,
    total_weight: totalWeight,
    weighted_sum: weightedSum,
    weighted_mean: weightedSum / totalWeight,
    value_unit: fixture.value_unit,
  };
}

function assertProtectedUnchanged(expected) {
  const actual = protectedFingerprint();
  if (actual !== expected) {
    throw new Error(`Protected path fingerprint changed: ${expected} -> ${actual}`);
  }
  return actual;
}

function setup() {
  assertAllowedWritePath(outputRoot);
  const before = protectedFingerprint();
  fs.mkdirSync(outputRoot, { recursive: true });
  const after = assertProtectedUnchanged(before);
  writeJson(sandboxRecordPath, {
    schema_version: "learning-lab-sandbox/v1",
    lab_id: labId,
    allowed_output: path.relative(repositoryRoot, outputRoot).split(path.sep).join("/"),
    protected_fingerprint_before: before,
    protected_fingerprint_after: after,
  });
  console.log(`Prepared ${path.relative(repositoryRoot, outputRoot)}`);
}

function run() {
  if (!fs.existsSync(sandboxRecordPath)) {
    throw new Error("Run setup before run");
  }
  const record = JSON.parse(fs.readFileSync(sandboxRecordPath, "utf8"));
  assertProtectedUnchanged(record.protected_fingerprint_before);
  const result = calculateWeightedMean(readFixture());
  writeJson(resultPath, result);
  assertProtectedUnchanged(record.protected_fingerprint_before);
  console.log(JSON.stringify(result));
}

function verify() {
  if (!fs.existsSync(sandboxRecordPath) || !fs.existsSync(resultPath)) {
    throw new Error("Run setup and run before verify");
  }
  const fixture = readFixture();
  const record = JSON.parse(fs.readFileSync(sandboxRecordPath, "utf8"));
  const result = JSON.parse(fs.readFileSync(resultPath, "utf8"));
  assertProtectedUnchanged(record.protected_fingerprint_before);
  for (const key of ["count", "total_weight", "weighted_sum"]) {
    if (result[key] !== fixture.expected[key]) {
      throw new Error(`${key}: expected ${fixture.expected[key]}, found ${result[key]}`);
    }
  }
  const error = Math.abs(result.weighted_mean - fixture.expected.weighted_mean);
  if (error > fixture.expected.absolute_tolerance) {
    throw new Error(
      `weighted_mean: expected ${fixture.expected.weighted_mean} ± ${fixture.expected.absolute_tolerance}, found ${result.weighted_mean}`,
    );
  }
  console.log(
    `Verified ${result.count} measurements: total weight ${result.total_weight}, weighted mean ${result.weighted_mean} ${result.value_unit}.`,
  );
}

function reset() {
  assertAllowedWritePath(outputRoot);
  const before = protectedFingerprint();
  if (fs.existsSync(outputRoot)) fs.rmSync(outputRoot, { recursive: true, force: false });
  assertProtectedUnchanged(before);
  console.log(`Removed ${path.relative(repositoryRoot, outputRoot)}`);
}

function main() {
  const command = process.argv[2];
  if (command === "setup") setup();
  else if (command === "run") run();
  else if (command === "verify") verify();
  else if (command === "reset") reset();
  else throw new Error("Usage: toy-weighted-mean.mjs <setup|run|verify|reset>");
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))
) {
  main();
}
