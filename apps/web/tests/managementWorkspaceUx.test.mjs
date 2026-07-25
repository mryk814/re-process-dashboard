import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = (relativePath) => readFile(new URL(relativePath, import.meta.url), "utf8");

test("developer overview uses a semantic staged flow and collapsible filtered projects", async () => {
  const content = await source("../src/features/admin/DeveloperControlCenter.tsx");
  assert.match(content, /<ol className="developer-flow"/);
  assert.match(content, /filterDeveloperOverviewItems/);
  assert.match(content, /type="search"/);
  assert.match(content, /filteredOverviewItems\.map\(\(item\) => <details/);
  assert.match(content, /条件に合うProjectはありません/);
  assert.doesNotMatch(content, /flow-down|flow-project-row|↘|↙/);
});

test("training data distinguishes unopened, loading, empty, and loaded states", async () => {
  const content = await source("../src/features/admin/ModelTrainingDataInspector.tsx");
  assert.match(content, /"未読込"/);
  assert.match(content, /"読み込み中"/);
  assert.match(content, /"0行 · データなし"/);
  assert.match(content, /page && page\.total > 0 && !error/);
  assert.match(content, /この段階に該当する行はありません/);
});

test("observation training inspector exposes family cohorts, split groups, and exclusions", async () => {
  const center = await source("../src/features/admin/DeveloperControlCenter.tsx");
  const inspector = await source("../src/features/admin/ObservationTrainingInspector.tsx");
  assert.match(center, /\["training", "学習View"\]/);
  assert.match(inspector, /観測family別 学習View/);
  assert.match(inspector, /relationは結合索引としてだけ使い/);
  assert.match(inspector, /施工group/);
  assert.match(inspector, /目的変数で除外/);
  assert.match(inspector, /page\.exclusion_reasons/);
  assert.match(inspector, /Object\.keys\(page\?\.rows\[0\]\?\.inputs/);
  assert.match(inspector, /row\.provenance\.entity_keys\.weld_metal/);
});

test("data library collapses an empty comparison area and moves state changes into management menus", async () => {
  const content = await source("../src/features/data-library/DataLibraryPage.tsx");
  assert.match(content, /comparisonSets\.length === 0 \? "comparison-empty"/);
  assert.match(content, /className="resource-manage-menu"/);
  assert.match(content, /利用停止にする/);
  assert.match(content, /利用可能に戻す/);
  assert.match(content, /件のプロジェクトが参照中のため利用停止できません/);
});

test("Profile Workbench keeps numbering in one stepper and states the next action", async () => {
  const content = await source("../src/features/data-library/ProfileWorkbenchPage.tsx");
  assert.match(content, /className="profile-next-action"/);
  assert.match(content, /aria-current=\{currentStep === index \+ 1 \? "step"/);
  assert.doesNotMatch(content, /<b>1<\/b><span><strong>/);
  assert.doesNotMatch(content, />3  内容を確認</);
  assert.doesNotMatch(content, />4  この内容で登録</);
});
