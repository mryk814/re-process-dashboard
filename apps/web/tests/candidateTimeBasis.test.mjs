import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `
      export {
        candidateSaveContractError,
        fromApiCandidate,
        scaleHeatTimesForLineSpeed,
        toApiCandidate,
      } from "./features/candidates/candidateModel.ts";
    `,
    resolveDir: sourceRoot,
    loader: "ts",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(
  module,
  module.exports,
  createRequire(import.meta.url),
);
const {
  candidateSaveContractError,
  fromApiCandidate,
  scaleHeatTimesForLineSpeed,
  toApiCandidate,
} = module.exports;

const apiCandidate = (basis) => ({
  id: "candidate-1",
  project_id: "project-1",
  revision: 1,
  name: "候補",
  inputs: {
    composition: { C: 0.1 },
    process: { ls_mpm: 60 },
    categorical: {},
    heat_pattern: [
      { time_s: 10, temperature_c: 25 },
      { time_s: 70, temperature_c: 800 },
      { time_s: 130, temperature_c: 400 },
    ],
    ...(basis ? { heat_time_basis: basis } : {}),
  },
  provenance: { source_kind: "direct" },
});

test("missing heat time basis defaults to line speed and is persisted", () => {
  const candidate = fromApiCandidate(apiCandidate());
  assert.equal(candidate.heatTimeBasis, "line_speed");
  assert.equal(toApiCandidate(candidate).inputs.heat_time_basis, "line_speed");
});

test("line-speed scaling preserves the first timestamp and inverse-scales offsets", () => {
  const candidate = fromApiCandidate(apiCandidate("line_speed"));
  const scaled = scaleHeatTimesForLineSpeed(candidate.heat, 60, 120);
  const expected = [10 / 60, 40 / 60, 70 / 60];
  scaled.forEach((point, index) => assert.ok(Math.abs(point.time - expected[index]) < 1e-12));
});

test("elapsed-time basis round-trips without changing heat times", () => {
  const candidate = fromApiCandidate(apiCandidate("elapsed_time"));
  const payload = toApiCandidate(candidate);
  assert.equal(payload.inputs.heat_time_basis, "elapsed_time");
  assert.deepEqual(payload.inputs.heat_pattern.map((point) => point.time_s), [10, 70, 130]);
});

test("rejects a save response from an API that drops the requested time basis", () => {
  const candidate = fromApiCandidate(apiCandidate("line_speed"));
  candidate.heatTimeBasis = "elapsed_time";
  const requested = toApiCandidate(candidate);
  const savedByOldApi = apiCandidate();

  assert.match(
    candidateSaveContractError(savedByOldApi, requested),
    /APIが時間基準の保存に対応していません/,
  );
  assert.equal(
    candidateSaveContractError(apiCandidate("elapsed_time"), requested),
    null,
  );
});
