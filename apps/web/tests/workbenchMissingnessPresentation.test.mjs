import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const source = fs.readFileSync(
  new URL("../src/features/workbench/WorkbenchPage.tsx", import.meta.url),
  "utf8",
);

test("provisional prediction warning precedes the comparison evidence", () => {
  const warning = source.indexOf("補完を含む暫定予測");
  const comparison = source.indexOf("<ComparisonTable");

  assert.ok(warning >= 0);
  assert.ok(comparison > warning);
  assert.match(source, /uncertainty_propagated/);
  assert.match(source, /欠損値のばらつきは予測区間へ追加していません/);
});
