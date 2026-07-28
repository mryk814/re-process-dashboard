import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = (relativePath) => readFile(new URL(relativePath, import.meta.url), "utf8");

test("Decision Activities share one readable visual hierarchy", async () => {
  const shell = await source("../src/features/workbench/DecisionActivityPanel.tsx");
  const history = await source("../src/features/workbench/decisionActivities/ActivityRunEvidence.tsx");
  const counterfactual = await source("../src/features/workbench/decisionActivities/CounterfactualActivityView.tsx");
  const styles = await source("../src/features/workbench/workbench.css");

  assert.match(shell, /aria-current=\{item\.definition\.activity_id === selectedId \? "page"/);
  assert.match(shell, /実行前に必要な準備/);
  assert.match(history, /activity-run-history-title/);
  assert.match(history, /保存結果/);
  assert.match(counterfactual, /availability\.available[\s\S]*activity-settings-collapsed/);

  assert.match(styles, /\.candidate-difference-run-settings \{ grid-template-columns: minmax\(240px, 1fr\) auto; \}/);
  assert.match(styles, /\.activity-run-settings select \{ min-width: 0; width: 100%; min-height: 38px;/);
  assert.match(styles, /\.activity-targets \{ display: grid; grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(styles, /@container activity-panel \(max-width: 720px\)[\s\S]*\.activity-targets \{ grid-template-columns: 1fr; \}/);
});
