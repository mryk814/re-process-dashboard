import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("two-variable sensitivity stays distinct from compact multi-candidate response curves", async () => {
  const source = await readFile(new URL("../src/features/workbench/ResponseCurvePanels.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../src/features/workbench/workbench.css", import.meta.url), "utf8");

  assert.match(source, /二変数感度/);
  assert.match(source, /!ready \|\| !axisPath \|\| !varyId \|\| !outputs\.length/);
  assert.match(source, /比較する変数を選ぶと/);
  assert.match(styles, /\.curve-family-panel \.response-curve-card svg \{ width: min\(100%, 400px\); margin-inline: auto; \}/);
  assert.match(styles, /\.response-curve-card svg \{ display: block; width: 100%; height: auto; \}/);
});
