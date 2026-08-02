import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `export { provenanceLabel } from "./shared/candidateProvenance.ts";`,
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
const { provenanceLabel } = module.exports;

test("historical observation provenance is not rendered as direct input or lineage", () => {
  assert.equal(provenanceLabel({
    source_kind: "historical_observation",
    source_ref: {
      observation_id: "slump-042",
      parent_key: "mix-042",
      source_label: "concrete slump",
      dataset_view_revision_id: "dataset-rev-1",
      source_sha256: "a".repeat(64),
      actual_outputs: { slump: 15.2 },
    },
  }), "過去の実測record slump-042");
});
