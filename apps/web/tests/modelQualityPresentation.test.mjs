import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/features/admin/DeveloperAdminPage.tsx", import.meta.url),
  "utf8",
);

test("model quality shows evaluation evidence and marks small samples", () => {
  assert.match(source, /独立条件 n=/);
  assert.match(source, /評価点 n=/);
  assert.match(source, /quality\.parent_conditions < 20/);
  assert.match(source, />参考値</);
  assert.match(source, /coverageMethodLabel\(quality\.interval_coverage_method\)/);
});

test("bernoulli predictors do not present regression interval coverage", () => {
  assert.match(source, /predictiveFamily\.startsWith\("bernoulli"\)/);
  assert.match(source, /分類では非該当/);
});
