import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = async (relativePath) => {
  const content = await readFile(new URL(relativePath, import.meta.url), "utf8");
  return content.split("\r\n").join("\n");
};

// The navigation module reads window.location, so it is exercised through a stub.
async function navigationModule(search) {
  globalThis.window = { location: { search, pathname: "/", hash: "" } };
  const module = await import(`../src/app/navigation.ts?case=${encodeURIComponent(search)}`);
  return module;
}

test("a saved activity run is part of the location", async () => {
  const { readNavigationIntent, navigationUrl } = await navigationModule(
    "?view=candidates&project=p1&candidate=c1&activity=robustness-analysis-v1&activity_run=activity-abc",
  );
  const intent = readNavigationIntent();
  assert.equal(intent.activityId, "robustness-analysis-v1");
  assert.equal(intent.activityRunId, "activity-abc");
  const url = navigationUrl(intent);
  assert.match(url, /activity=robustness-analysis-v1/);
  assert.match(url, /activity_run=activity-abc/);
});

test("the activity state stays with the comparison view", async () => {
  const { readNavigationIntent, withView } = await navigationModule(
    "?view=candidates&project=p1&activity=robustness-analysis-v1&activity_run=activity-abc",
  );
  const intent = readNavigationIntent();
  assert.equal(withView(intent, "candidates").activityRunId, "activity-abc");
  assert.equal(withView(intent, "project").activityRunId, undefined);
  assert.equal(withView(intent, "explore").activityId, undefined);
});

test("a candidate made by an activity navigates back to that run", async () => {
  // The module imports React-side siblings, so its contract is checked at the source.
  const provenance = await source("../src/app/candidateProvenance.ts");
  const activityCase = provenance.slice(
    provenance.indexOf('case "decision_activity":'),
    provenance.indexOf('case "snapshot":'),
  );
  assert.match(activityCase, /candidateId: provenance\.source_ref\.base_candidate_id/);
  assert.match(activityCase, /activityRunId: provenance\.source_ref\.run_id/);
});

test("the panel resolves a requested run to the activity that owns it", async () => {
  const panel = await source("../src/features/workbench/DecisionActivityPanel.tsx");
  assert.match(panel, /runs\.find\(\(run\) => run\.id === requestedRunId\)/);
  assert.match(panel, /setSelectedId\(requestedRun\.definition\.activity_id\)/);
  assert.match(panel, /setActiveRunId\(requestedRun\.id\)/);
  assert.match(panel, /\[activities, identity, loadedIdentity, requestedActivityId, requestedRunId, runs\]/);
  assert.match(panel, /onStateChange\(selectedId, activeRunId \?\? undefined\)/);
  // Switching activity must not keep a run that belongs to another activity.
  assert.match(panel, /setSelectedId\(item\.definition\.activity_id\);\s*\n\s*setActiveRunId\(null\);/);
});

test("an unknown run is reported instead of falling back to another result", async () => {
  const panel = await source("../src/features/workbench/DecisionActivityPanel.tsx");
  assert.match(panel, /requestedRunId && !requestedRun/);
  assert.match(panel, /保存済みRun.+この候補では見つかりません/);
  assert.match(panel, /selected && View && !locationError/);
  assert.match(panel, /if \(!selectedId \|\| locationError\) return/);
});

test("run selection lives in the panel so every activity shares one link", async () => {
  for (const view of ["RobustnessActivityView", "CounterfactualActivityView", "CandidateDifferenceActivityView"]) {
    const content = await source(`../src/features/workbench/decisionActivities/${view}.tsx`);
    assert.doesNotMatch(content, /useState<string \| null>\(null\)/, `${view} still owns run selection`);
    assert.match(content, /<ActivityRunHistory.+onSelectRun=\{onSelectRun\}/);
  }
  const evidence = await source("../src/features/workbench/decisionActivities/ActivityRunEvidence.tsx");
  assert.match(evidence, /onSelectRun\(index === 0 \? null : run\.id\)/);
});

test("developer tab and guide form a restorable location", async () => {
  const { readNavigationIntent, navigationUrl, withView } = await navigationModule(
    "?view=settings&project=p1&admin=developer&developer_tab=guide&developer_guide=decision-activity-new",
  );
  const intent = readNavigationIntent();
  assert.equal(intent.developerTab, "guide");
  assert.equal(intent.developerGuideId, "decision-activity-new");
  assert.match(navigationUrl(intent), /developer_tab=guide/);
  assert.match(navigationUrl(intent), /developer_guide=decision-activity-new/);
  assert.equal(withView(intent, "settings").developerGuideId, "decision-activity-new");
  assert.equal(withView(intent, "candidates").developerGuideId, undefined);
});

test("an unknown developer tab is reported instead of silently selected", async () => {
  const { readNavigationIntent } = await navigationModule(
    "?view=settings&project=p1&admin=developer&developer_tab=missing",
  );
  const intent = readNavigationIntent();
  assert.equal(intent.developerTab, undefined);
  assert.equal(intent.developerTabError, "missing");
});

test("all saved activity runs and their provenance stay reachable", async () => {
  const evidence = await source("../src/features/workbench/decisionActivities/ActivityRunEvidence.tsx");
  assert.match(evidence, /runs\.map\(/);
  assert.doesNotMatch(evidence, /runs\.slice\(/);
  assert.match(evidence, /aria-current=/);
  assert.match(evidence, /この結果の再現情報/);
  assert.match(evidence, /Model Package/);
  assert.match(evidence, /Feature pipeline/);
  assert.match(evidence, /記録なし/);
});

test("a shared link opens the activity panel without a second click", async () => {
  const page = await source("../src/features/workbench/WorkbenchPage.tsx");
  assert.match(page, /const activityPanelOpen = activityOpen \|\| Boolean\(activityId \|\| activityRunId\)/);
  assert.match(page, /requestedRunId=\{activityRunId\}/);
  assert.match(page, /onActivityStateChange\(undefined, undefined\)/);
});
