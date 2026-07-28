import assert from "node:assert/strict";
import test from "node:test";
import {
  allowedDocsRootFiles,
  validateDocsRoot,
} from "./check-doc-structure.mjs";

function file(name) {
  return {
    name,
    isFile: () => true,
    isSymbolicLink: () => false,
  };
}

test("the exact two documentation entry files are allowed at docs root", () => {
  assert.deepEqual(
    validateDocsRoot([...allowedDocsRootFiles].map(file)),
    { unexpected: [], missing: [] },
  );
});

for (const unexpected of [
  "notes.md",
  "2026-07-28-report.md",
  "task-inventory.json",
  "readme.md",
]) {
  test(`docs root rejects ${unexpected}`, () => {
    const result = validateDocsRoot([
      ...[...allowedDocsRootFiles].map(file),
      file(unexpected),
    ]);
    assert.deepEqual(result.unexpected, [unexpected]);
  });
}
