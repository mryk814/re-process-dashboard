import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const source = (relative) => readFile(new URL(relative, import.meta.url), "utf8");

test("decision replay presents historical evidence before hindsight evidence", async () => {
  const panel = await source("../src/features/workbench/DecisionReplayPanel.tsx");
  const historical = panel.indexOf("判断時点で利用できた証拠");
  const retrospective = panel.indexOf("実測と現在の見方");

  assert.ok(historical > 0);
  assert.ok(retrospective > historical);
  assert.match(panel, /decision-replay-layer historical/);
  assert.match(panel, /decision-replay-layer retrospective/);
  assert.match(panel, /選択した後発Project\/Packageでの再評価（hindsight）/);
  assert.match(panel, /hindsight_project_id: hindsightProjectId/);
  assert.match(panel, /Actualはまだ到着していません/);
});

test("decision case creation derives before and after evidence from one cutoff", async () => {
  const panel = await source("../src/features/workbench/DecisionReplayPanel.tsx");

  assert.match(panel, /Date\.parse\(snapshot\.created_at\) > cutoffTime/);
  assert.match(panel, /Date\.parse\(actual\.created_at\) > cutoffTime/);
  assert.match(panel, /snapshot_ids: evidence\.map/);
  assert.match(panel, /attachDecisionCaseActual/);
  assert.match(panel, /decisionReplayHindsightProjectOptions/);
  assert.match(panel, /status: "no_decision"/);
});

test("review workbench exposes replay without replacing candidate activities", async () => {
  const page = await source("../src/features/workbench/WorkbenchPage.tsx");
  const activities = page.indexOf("<DecisionActivityPanel");
  const replay = page.indexOf("<DecisionReplayPanel");

  assert.ok(activities > 0);
  assert.ok(replay > activities);
  assert.match(page, /mode === "review" && taskDefinition && <DecisionReplayPanel/);
});
