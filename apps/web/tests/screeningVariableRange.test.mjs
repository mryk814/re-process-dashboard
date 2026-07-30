import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { safeExplorationRange } from "../src/features/screening/screeningVariableRange.ts";
import { screeningVariableError } from "../src/features/screening/screeningVariableValidation.ts";

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

test("variable validation preserves Task numeric and categorical constraints", () => {
  assert.equal(
    screeningVariableError(
      { mode: "range", first: "-1", second: "5" },
      { categorical: false, choices: [], allowedRange: { min: 0, max: 10 } },
    ),
    "許容範囲 0–10 内で入力してください。",
  );
  assert.equal(
    screeningVariableError(
      { mode: "list", first: "GI,XX", second: "" },
      { categorical: true, choices: ["GI", "GA"] },
    ),
    "選択肢にない値「XX」が含まれています。",
  );
  assert.equal(
    screeningVariableError(
      { mode: "range", first: "5", second: "1" },
      { categorical: false, choices: [], allowedRange: { min: 0, max: 10 } },
    ),
    "最小値は最大値より小さくしてください。",
  );
  assert.equal(
    screeningVariableError(
      { mode: "fixed", first: "1,2", second: "" },
      { categorical: false, choices: [] },
    ),
    "数値を入力してください。",
  );
});

test("the screening page builds its variable defaults from the safe range", async () => {
  const source = await readFile(new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url), "utf8");
  assert.match(source, /defaultRange: safeExplorationRange\(field\.default_range, field\.training_range\)/);
});

test("screening variable rows expose their field, mode, and values by name", async () => {
  const source = await readFile(new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url), "utf8");
  assert.match(source, /aria-label=\{`\$\{index \+ 1\}行目の探索変数`\}/);
  assert.match(source, /aria-label=\{`\$\{option\?\.label \?\? row\.field\}の指定方法`\}/);
  assert.match(source, /row\.mode === "fixed"[\s\S]*?"値"[\s\S]*?row\.mode === "range"[\s\S]*?"最小"[\s\S]*?"列挙値"/);
  assert.match(source, /aria-label=\{`\$\{option\?\.label \?\? row\.field\}の最大`\}/);
  assert.match(source, /aria-label=\{`\$\{option\?\.label \?\? row\.field\}を削除`\}/);
  assert.match(source, /pendingVariableFocusIndex\.current = variables\.length/);
  assert.match(source, /pendingVariableFocusIndex\.current = Math\.min\(index, variables\.length - 2\)/);
  assert.match(source, /aria-describedby=\{variableErrors\[index\] \? errorId : undefined\}/);
  assert.match(source, /role="alert">\{variableErrors\[index\]\}/);
  assert.match(source, /<th>学習範囲<\/th>/);
  assert.match(source, /学習範囲外を含む/);
});

test("the compact variable table wins over shared quality-table spacing", async () => {
  const styles = await readFile(new URL("../src/features/screening/screening.css", import.meta.url), "utf8");
  assert.match(styles, /\.screening-variable-editor \.variable-table th,[\s\S]*?padding: 3px 5px;/);
  assert.match(styles, /\.screening-variable-editor \.variable-table thead th \{[\s\S]*?position: sticky;/);
  assert.match(styles, /\.screening-variable-editor > \.screening-add-variable \{[\s\S]*?width: auto;/);
  assert.match(styles, /\.screening-variable-table-scroll \{[\s\S]*?max-height: 320px;[\s\S]*?overflow: auto;/);
});

test("a run without a primary goal asks before ranking anything", async () => {
  const source = await readFile(new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url), "utf8");
  assert.match(source, /screeningMode !== "landscape" && !fixedObjective && !screeningGoalFromDraft\(targetGoal\)/);
  assert.match(source, /有望候補を探すには主目標を入力してください/);
  assert.match(source, /label: "領域を見る"/);
  assert.match(source, /onConfigureGoals/);
});

test("the score legend states the contract semantic instead of promising promise", async () => {
  const source = await readFile(new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url), "utf8");
  assert.match(source, /const scoreLabel = result\?\.score_contract\?\.display_label \?\? "探索スコア"/);
  assert.doesNotMatch(source, /目標に対する有望度/);
  assert.doesNotMatch(source, /目標に対して有望/);
  assert.doesNotMatch(source, /色が濃いほど目標方向に有望/);
});
