import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("two-variable sensitivity stays distinct from compact multi-candidate response curves", async () => {
  const source = await readFile(new URL("../src/features/workbench/ResponseCurvePanels.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../src/features/workbench/workbench.css", import.meta.url), "utf8");

  assert.match(source, /二変数感度/);
  assert.match(source, /!ready \|\| !axisPath \|\| !varyId \|\| !outputs\.length/);
  assert.match(source, /比較する変数を選ぶと/);
  assert.match(styles, /\.curve-family-panel \.response-curve-card svg \{ width: min\(100%, 400px\); margin-inline: auto; \}/);
  assert.match(styles, /\.response-curve-card svg \{ display: block; width: 100%; height: auto; \}/);
});

test("response curves use an adaptive default and expose calculation-range gaps", async () => {
  const source = await readFile(new URL("../src/features/workbench/ResponseCurvePanels.tsx", import.meta.url), "utf8");
  const workbench = await readFile(new URL("../src/features/workbench/WorkbenchPage.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../src/features/workbench/workbench.css", import.meta.url), "utf8");

  assert.match(source, /Object\.keys\(responseCurveRanges\.y \?\? \{\}\)\.length \? "configured" : "full"/);
  assert.match(source, /<option value="full">曲線に合わせる<\/option><option value="preferred">基準範囲<\/option>/);
  assert.match(source, /現在値は計算範囲外/);
  assert.match(source, /網掛けは学習範囲外/);
  assert.match(source, /className="curve-extrapolation-region"/);
  assert.match(source, /defaultResponseCurveRange\(selectedInput, project\?\.input_ranges\?\.\[activeVariableId\]\)/);
  assert.match(source, /className="curve-endpoint"/);
  assert.doesNotMatch(workbench, /<UnavailablePanel title="応答曲線"/);
  assert.match(workbench, /no-response-curves/);
  assert.match(styles, /\.response-curves-panel > \.panel-title h2 span \{ display: inline-block; \}/);
});

test("a target that does not use the selected variable is shown as not applicable", async () => {
  const source = await readFile(new URL("../src/features/workbench/ResponseCurvePanels.tsx", import.meta.url), "utf8");

  assert.match(source, /responseCurveNotApplicable/);
  assert.match(source, /入力に使わないため応答曲線を作成できません/);
  assert.match(source, /選択した変数をこのモデルの入力に使わないため、応答曲線はありません。/);
  assert.match(source, /resolvedCurveCount = loadedCurveCount \+ notApplicableCount/);
});

test("heat-pattern axes keep labels inside the plot and anchor edge ticks inward", async () => {
  const source = await readFile(new URL("../src/features/workbench/HeatPatternPanel.tsx", import.meta.url), "utf8");

  assert.match(source, /const height = 228/);
  assert.match(source, /bottom: 42/);
  assert.match(source, /index === 0 \? "start" : index === timeTicks\.length - 1 \? "end"/);
  assert.match(source, /時間（min）/);
});

test("similar evidence uses task outputs and always keeps the candidate action visible", async () => {
  const source = await readFile(new URL("../src/features/workbench/SimilarityEvidencePanel.tsx", import.meta.url), "utf8");

  assert.match(source, /モデル入力が近い実測条件です/);
  assert.match(source, /visibleOutputs\.map\(\(output\) => <th className="similar-output-header"/);
  assert.match(source, /<th className="similar-action-header"/);
  assert.match(source, /<td className="similar-action-cell"/);
  assert.match(source, /候補にする/);
  assert.doesNotMatch(source, /canAddCandidates/);
  const styles = await readFile(new URL("../src/features/workbench/workbench.css", import.meta.url), "utf8");
  assert.match(styles, /\.similar-action-header\s*\{[^}]*position: sticky;[^}]*right: 0;/s);
  assert.match(styles, /\.similar-action-cell\s*\{[^}]*position: sticky;[^}]*right: 0;/s);
});

test("charts with focusable points are groups of labelled parts, not one image", async () => {
  const files = [
    "../src/features/workbench/ResponseCurvePanels.tsx",
    "../src/features/workbench/HeatPatternPanel.tsx",
    "../src/features/lineage/LineagePage.tsx",
    "../src/features/screening/ScreeningPage.tsx",
  ];
  for (const file of files) {
    const content = await readFile(new URL(file, import.meta.url), "utf8");
    if (!content.includes("svg-chart-hit-target") && !content.includes("screen-map-point")) continue;
    assert.match(content, /role="group"/, `${file} exposes its chart as a group`);
    assert.doesNotMatch(
      content,
      /<svg[^>]*role="img"/,
      `${file} must not hide focusable chart points behind role="img"`,
    );
  }
});

test("the response curve controls wrap instead of widening the page", async () => {
  const css = await readFile(new URL("../src/features/workbench/workbench.css", import.meta.url), "utf8");
  assert.match(css, /\.response-curve-controls \{[^}]*flex-wrap: wrap/);
  assert.match(css, /\.response-curves-panel > \.panel-title \{[^}]*flex-wrap: wrap/);
});
