import assert from "node:assert/strict";
import test from "node:test";
import {
  projectGroupMembershipState,
  ungroupedMembershipValue,
} from "../src/features/projects/projectSettingsState.ts";

test("an empty selection is only a placeholder and cannot move a Project", () => {
  assert.equal(projectGroupMembershipState({
    selectedSeriesId: "",
    currentSeriesId: "series-a",
    currentSeriesProjectCount: 2,
  }).changed, false);
});

test("leaving a group sends null and is offered only to grouped Projects", () => {
  const grouped = projectGroupMembershipState({
    selectedSeriesId: ungroupedMembershipValue,
    currentSeriesId: "series-a",
    currentSeriesProjectCount: 2,
  });
  assert.equal(grouped.targetSeriesId, null);
  assert.equal(grouped.changed, true);
  assert.equal(grouped.showUngroupOption, true);
  assert.equal(projectGroupMembershipState({
    selectedSeriesId: "",
    currentSeriesId: null,
    currentSeriesProjectCount: 0,
  }).showUngroupOption, false);
});

test("selecting the current group is not a mutation", () => {
  assert.equal(projectGroupMembershipState({
    selectedSeriesId: "series-a",
    currentSeriesId: "series-a",
    currentSeriesProjectCount: 3,
  }).changed, false);
});

test("moving to another group preserves the selected target", () => {
  const state = projectGroupMembershipState({
    selectedSeriesId: "series-b",
    currentSeriesId: "series-a",
    currentSeriesProjectCount: 2,
  });
  assert.equal(state.targetSeriesId, "series-b");
  assert.equal(state.changed, true);
  assert.equal(state.emptiesCurrentSeries, false);
});

test("moving the last Project marks its current group as empty", () => {
  assert.equal(projectGroupMembershipState({
    selectedSeriesId: ungroupedMembershipValue,
    currentSeriesId: "series-a",
    currentSeriesProjectCount: 1,
  }).emptiesCurrentSeries, true);
});
