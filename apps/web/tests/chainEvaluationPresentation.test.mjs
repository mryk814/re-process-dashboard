import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/features/projects/ChainEvaluationPanel.tsx", import.meta.url),
  "utf8",
);
const projectHubSource = readFileSync(
  new URL("../src/features/projects/ProjectHub.tsx", import.meta.url),
  "utf8",
);

test("chain evaluation keeps stage-only and end-to-end metrics separate", () => {
  assert.match(source, /段単体と通しを分けて評価/);
  assert.match(source, /target\.stage_only\.rmse/);
  assert.match(source, /target\.end_to_end\.rmse/);
  assert.match(source, /一つの精度には合成しません/);
});

test("chain evaluation exposes output-specific cohort and split evidence", () => {
  assert.match(source, /target\.observations/);
  assert.match(source, /target\.split_groups/);
  assert.match(source, /target\.cohort/);
  assert.match(source, /report\.split\.group_key/);
  assert.match(source, /inner OOF/);
  assert.match(source, /outer-train/);
});

test("project switch resolves Chain identity from the requested project before loading data", () => {
  assert.match(projectHubSource, /project\?\.id === activeProjectId/);
  assert.match(projectHubSource, /projects\.find\(\(item\) => item\.id === activeProjectId\)/);
  assert.match(projectHubSource, /if \(chainIdentity\) \{/);
  assert.match(projectHubSource, /projectChainEvaluation\(activeProjectId/);
  assert.match(projectHubSource, /setChainEvaluation\(\{ projectId: activeProjectId, value: item \}\)/);
  assert.match(projectHubSource, /chainEvaluation\?\.projectId === activeProjectId/);
});
