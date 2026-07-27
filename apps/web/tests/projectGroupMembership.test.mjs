import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const projectHub = readFileSync(
  new URL("../src/features/projects/ProjectHub.tsx", import.meta.url),
  "utf8",
);
const collapsed = projectHub.replace(/\s+/g, " ");

test("leaving a group is an explicit option that is distinct from the unselected placeholder", () => {
  assert.match(collapsed, /ungroupedMembershipValue = "[^"]+"/);
  assert.match(collapsed, /選択してください/);
  assert.match(collapsed, /value=\{ungroupedMembershipValue\}>グループなし/);
});

test("the request sends null instead of an empty group id when leaving a group", () => {
  assert.match(collapsed, /ungroupedMembershipValue \? null :/);
  assert.match(collapsed, /project_series_id: membershipTargetSeriesId/);
  assert.match(collapsed, /expected_project_series_id: project\.project_series_id \?\? null/);
});

test("the move action stays disabled until the selection differs from the current membership", () => {
  assert.match(collapsed, /membershipChanged = Boolean\(groupMembershipId\)/);
  assert.match(collapsed, /membershipTargetSeriesId !== \(project\?\.project_series_id \?\? null\)/);
  assert.match(collapsed, /disabled=\{!membershipChanged\}/);
  assert.match(collapsed, /!membershipChanged\) return/);
});

test("the ungroup option only appears for a project that belongs to a group", () => {
  const optionIndex = collapsed.indexOf("value={ungroupedMembershipValue}>");
  assert.ok(optionIndex > 0);
  assert.match(collapsed.slice(Math.max(0, optionIndex - 60), optionIndex), /project\.project_series_id/);
});

test("a late group list does not overwrite the membership the user just picked", () => {
  const initializer = /setGroupMembershipId\(project\?\.project_series_id \?\? ""\);?\s*\}, \[([^\]]*)\]/.exec(collapsed);
  assert.ok(initializer, "membership initializer effect not found");
  assert.doesNotMatch(initializer[1], /fixedSeries/);
});

test("emptying the last group by leaving it is announced before the action", () => {
  assert.match(collapsed, /membershipEmptiesFixedSeries = .*fixedSeriesProjectCount === 1/);
  assert.match(collapsed, /membershipEmptiesFixedSeries &&/);
  assert.match(collapsed, /所属プロジェクトが無くなり/);
});
