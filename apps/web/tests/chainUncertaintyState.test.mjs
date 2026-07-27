import assert from "node:assert/strict";
import test from "node:test";

import {
  chainUncertaintyLabel,
  chainUncertaintyStageNote,
  chainUncertaintyStatus,
} from "../src/features/workbench/chainUncertaintyState.ts";

test("a point-only Chain run says the uncertainty is not computed, not that none exists", () => {
  const status = chainUncertaintyStatus(false, { runComputed: false }, "B");
  assert.equal(status, "not_computed");
  assert.equal(chainUncertaintyLabel(status), "不確かさ未計算");
  assert.match(chainUncertaintyStageNote({ runComputed: false }, "B"), /未計算/);
});

test("a stage the propagation run cannot cover is stated as point-only", () => {
  const availability = { supportedStages: { B: false, C: true }, runComputed: false };
  assert.equal(chainUncertaintyStatus(false, availability, "B"), "point_only");
  assert.equal(
    chainUncertaintyLabel(chainUncertaintyStatus(false, availability, "B")),
    "区間なし（このStageは点推定のみ）",
  );
  assert.match(chainUncertaintyStageNote(availability, "B"), /点推定のみ/);
  assert.equal(chainUncertaintyStatus(false, availability, "C"), "not_computed");
});

test("after a run, an output without an interval is distinguished from the not-computed state", () => {
  const availability = { supportedStages: { C: true }, runComputed: true };
  assert.equal(chainUncertaintyStatus(false, availability, "C"), "outside_run");
  assert.equal(
    chainUncertaintyLabel(chainUncertaintyStatus(false, availability, "C")),
    "この出力は伝播Runの対象外",
  );
  assert.match(chainUncertaintyStageNote(availability, "C"), /計算済み/);
});

test("an interval on the point estimate keeps its own wording", () => {
  assert.equal(chainUncertaintyStatus(true, { runComputed: false }, "C"), "interval");
  assert.equal(chainUncertaintyLabel("interval"), "");
});

test("the three states never share one label", () => {
  const labels = ["not_computed", "point_only", "outside_run"].map(chainUncertaintyLabel);
  assert.equal(new Set(labels).size, labels.length);
  assert.ok(labels.every((label) => label.length > 0));
});
