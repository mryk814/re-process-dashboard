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
