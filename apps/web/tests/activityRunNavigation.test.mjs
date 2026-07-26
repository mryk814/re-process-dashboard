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
  assert.match(panel, /savedRuns\.find\(\(run\) => run\.id === requestedRunId\)/);
  assert.match(panel, /requestedRun\?\.definition\.activity_id \?\? requestedActivityId/);
  assert.match(panel, /onStateChange\(selectedId, activeRunId \?\? undefined\)/);
  // Switching activity must not keep a run that belongs to another activity.
  assert.match(panel, /setSelectedId\(item\.definition\.activity_id\);\s*\n\s*setActiveRunId\(null\);/);
});

test("run selection lives in the panel so every activity shares one link", async () => {
  for (const view of ["RobustnessActivityView", "CounterfactualActivityView", "CandidateDifferenceActivityView"]) {
    const content = await source(`../src/features/workbench/decisionActivities/${view}.tsx`);
    assert.doesNotMatch(content, /useState<string \| null>\(null\)/, `${view} still owns run selection`);
    assert.match(content, /onSelectRun\(run\.id\)/);
  }
});

test("a shared link opens the activity panel without a second click", async () => {
  const page = await source("../src/features/workbench/WorkbenchPage.tsx");
  assert.match(page, /const activityPanelOpen = activityOpen \|\| Boolean\(activityId \|\| activityRunId\)/);
  assert.match(page, /requestedRunId=\{activityRunId\}/);
  assert.match(page, /onActivityStateChange\(undefined, undefined\)/);
});
