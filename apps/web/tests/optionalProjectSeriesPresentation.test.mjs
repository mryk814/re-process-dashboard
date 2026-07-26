import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const projectHub = readFileSync(
  new URL("../src/features/projects/ProjectHub.tsx", import.meta.url),
  "utf8",
);

test("project creation presents no group, existing group, and named new group as explicit choices", () => {
  assert.match(projectHub, /useState<"none" \| "existing" \| "new">\("none"\)/);
  for (const label of ["グループなし", "既存グループ", "新しい検討グループ"]) {
    assert.match(projectHub, new RegExp(label));
  }
  assert.match(projectHub, /新しい検討グループ名<input required/);
  assert.match(projectHub, /new_project_series: newProjectGroupChoice === "new"/);
  assert.match(projectHub, /project_series_id: newProjectGroupChoice === "existing"/);
  assert.doesNotMatch(projectHub, /プロジェクト名で新しいグループを作成/);
});

test("unassigned projects stay direct instead of sharing a synthetic group", () => {
  assert.match(projectHub, /id: `project:\$\{item\.id\}`/);
  assert.match(projectHub, /projects: \[item\]/);
  assert.doesNotMatch(projectHub, /その他の検討/);
});

test("series chrome stays hidden until a series contains more than one active project", () => {
  assert.match(projectHub, /fixedSeriesProjectCount > 1/);
  assert.match(projectHub, /\{showActiveSeriesMembership && <div><span>検討グループ/);
  assert.match(projectHub, /!showActiveSeriesMembership && !groupSettingsOpen/);
  assert.match(projectHub, /ほかの検討とまとめる/);
});

test("series grouping is explained separately from continuation and only inherited when present", () => {
  assert.match(projectHub, /同じ目的で続けた複数の検討をまとめます。続き元の関係とは別です。/);
  const inheritedChoices = projectHub.match(
    /setNewProjectGroupChoice\(project\.project_series_id \? "existing" : "none"\)/g,
  ) ?? [];
  assert.equal(inheritedChoices.length, 2);
  assert.match(projectHub, /design_space: project\?\.task_id === taskId/);
});
