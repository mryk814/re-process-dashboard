import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import test from "node:test";
import {
  isolatedSpecs,
  parallelDedicatedSpecs,
  sharedReadOnlySpecs,
  suiteInventory,
  suiteKinds,
} from "./suite-inventory.mjs";

test("every Playwright spec has one explicit isolation and cleanup owner", () => {
  const specsOnDisk = readdirSync(new URL(".", import.meta.url))
    .filter((entry) => entry.endsWith(".spec.ts"))
    .sort();
  assert.deepEqual(Object.keys(suiteInventory).sort(), specsOnDisk);

  for (const [filename, entry] of Object.entries(suiteInventory)) {
    assert.ok(suiteKinds.has(entry.kind), `${filename} has a known execution kind`);
    assert.ok(entry.cleanupOwner.length > 0, `${filename} has a cleanup owner`);
    assert.ok(entry.reason.length > 0, `${filename} records why it has this boundary`);
  }
});

test("parallel selections contain only deliberately isolated or read-only specs", () => {
  assert.deepEqual(sharedReadOnlySpecs, [
    "microstructure-evidence.spec.ts",
    "responsive-navigation.spec.ts",
    "series-library.spec.ts",
    "workspace-scope.spec.ts",
  ]);
  assert.deepEqual(isolatedSpecs, [
    "analysis-navigation-resume.spec.ts",
    "annealing-time-basis.spec.ts",
    "chain-graph-viewer.spec.ts",
    "chain-input-contract.spec.ts",
    "dataset-disposition.spec.ts",
    "profile-workbench-authoring.spec.ts",
    "screening-workbench.spec.ts",
  ]);
  assert.deepEqual(parallelDedicatedSpecs, [
    "source-lifecycle.spec.ts",
  ]);
});
