import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

async function navigationModule(search) {
  globalThis.window = { location: { search, pathname: "/", hash: "" } };
  return import(`../src/app/navigation.ts?quality=${encodeURIComponent(search)}`);
}

test("legacy quality admin intent resolves to the canonical quality view", async () => {
  const { isLegacyQualityAdminNavigation, navigationUrl, readNavigationIntent } = await navigationModule(
    "?view=settings&admin=quality&project=p1&quality_type=duplicate_key",
  );
  const intent = readNavigationIntent();

  assert.equal(isLegacyQualityAdminNavigation(), true);
  assert.equal(intent.view, "quality");
  assert.equal(intent.adminSection, undefined);
  assert.equal(intent.qualityType, "duplicate_key");
  assert.equal(navigationUrl(intent), "/?view=quality&project=p1&quality_type=duplicate_key");
});

test("quality uses one screen for summary, filters, export, and zero states", async () => {
  const source = await readFile(new URL("../src/features/quality/QualityPages.tsx", import.meta.url), "utf8");

  assert.match(source, /<h2>データ品質<\/h2>/);
  assert.match(source, /quality-summary/);
  assert.match(source, /quality-filters/);
  assert.match(source, /検出結果をCSV出力/);
  assert.match(source, /絞り込みに一致する問題はありません/);
  assert.match(source, /すべての問題を表示/);
  assert.doesNotMatch(source, /mode === "summary"/);
});

test("candidate summary routes unresolved decisions to their settings", async () => {
  const source = await readFile(new URL("../src/features/candidates/CandidateUi.tsx", import.meta.url), "utf8");
  const app = await readFile(new URL("../src/app/App.tsx", import.meta.url), "utf8");

  assert.match(source, /onConfigureGoals/);
  assert.match(source, /onConfigureSupport/);
  assert.match(source, /goalDirectionLabel/);
  assert.match(app, /projectSettings: "targets"/);
  assert.match(app, /projectSettings: "ranges"/);
});
