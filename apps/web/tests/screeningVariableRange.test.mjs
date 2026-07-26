import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { safeExplorationRange } from "../src/features/screening/screeningVariableRange.ts";

test("the default exploration range is the overlap of practical and training range", () => {
  assert.deepEqual(
    safeExplorationRange({ min: 0, max: 0.2 }, { min: 0.025, max: 0.15 }),
    { min: 0.025, max: 0.15 },
  );
  assert.deepEqual(
    safeExplorationRange({ min: 0.05, max: 3.2 }, { min: 0.12, max: 0.654 }),
    { min: 0.12, max: 0.654 },
  );
});

test("a practical range inside the training range is kept as it is", () => {
  assert.deepEqual(
    safeExplorationRange({ min: 800, max: 900 }, { min: 700, max: 1000 }),
    { min: 800, max: 900 },
  );
});

test("without a training range the practical range is the only basis", () => {
  assert.deepEqual(safeExplorationRange({ min: 1, max: 2 }, undefined), { min: 1, max: 2 });
  assert.equal(safeExplorationRange(undefined, undefined), undefined);
});

test("disjoint ranges fall back to the training range instead of proposing extrapolation", () => {
  assert.deepEqual(
    safeExplorationRange({ min: 2000, max: 3000 }, { min: 700, max: 1000 }),
    { min: 700, max: 1000 },
  );
});

test("a degenerate range never becomes the default", () => {
  assert.deepEqual(safeExplorationRange({ min: 5, max: 5 }, { min: 1, max: 9 }), { min: 1, max: 9 });
  assert.equal(safeExplorationRange({ min: 5, max: 5 }, { min: 3, max: 3 }), undefined);
});

test("the screening page builds its variable defaults from the safe range", async () => {
  const source = await readFile(new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url), "utf8");
  assert.match(source, /defaultRange: safeExplorationRange\(field\.default_range, field\.training_range\)/);
});

test("a run without a primary goal asks before ranking anything", async () => {
  const source = await readFile(new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url), "utf8");
  assert.match(source, /!fixedObjective && !screeningGoalFromDraft\(targetGoal\) && !allowWithoutGoal/);
  assert.match(source, /主目標が未設定です/);
  assert.match(source, /目標なしで分布を見る/);
  assert.match(source, /onConfigureGoals/);
});

test("the score legend states the contract semantic instead of promising promise", async () => {
  const source = await readFile(new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url), "utf8");
  assert.match(source, /const scoreLabel = result\?\.score_contract\?\.display_label \?\? "探索スコア"/);
  assert.doesNotMatch(source, /目標に対する有望度/);
  assert.doesNotMatch(source, /目標に対して有望/);
  assert.doesNotMatch(source, /色が濃いほど目標方向に有望/);
});
