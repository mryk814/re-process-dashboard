import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const screeningSource = await readFile(new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url), "utf8");
const screeningStyles = await readFile(new URL("../src/features/screening/screening.css", import.meta.url), "utf8");
const lineageSource = await readFile(new URL("../src/features/lineage/LineagePage.tsx", import.meta.url), "utf8");
const lineageStyles = await readFile(new URL("../src/features/lineage/lineage.css", import.meta.url), "utf8");

test("screening separates selection criteria from project goals and maps counts to the candidate action", () => {
  assert.match(screeningSource, />\s*選別する特性\s*</);
  assert.match(screeningSource, /label=\{`主目標: \$\{targetDefinition\.label\}`\}/);
  assert.match(screeningSource, /label=\{`副条件: \$\{output\.label\}`\}/);
  assert.match(screeningSource, /<dt>選択<\/dt>/);
  assert.match(screeningSource, /<dt>新規<\/dt>/);
  assert.match(screeningSource, /<dt>今回追加可能<\/dt>/);
  assert.match(screeningSource, /\{addableSelectedCount\}件を候補へ追加/);
  assert.match(screeningStyles, /\.screening-primary-settings > label \{[\s\S]*?min-width: 0;/);
});

test("screening limits the map to metric, proposal, and selection channels", () => {
  assert.match(screeningSource, /<span className="selection-key" \/>/);
  assert.match(screeningSource, /className="screen-map-selection-ring"/);
  assert.match(screeningSource, /className="screen-map-proposal-marker"/);
  assert.match(screeningSource, /stroke="#345b85"/);
  assert.doesNotMatch(screeningSource, /supportStroke/);
  assert.match(screeningSource, /point\.support\.message/);
  assert.match(screeningSource, /aria-pressed=\{selected\}/);
  assert.match(screeningStyles, /\.screen-map-selection-ring \{[\s\S]*?stroke-dasharray:/);
});

test("screening keeps the first result surface compact and offers an explicit expansion", () => {
  assert.doesNotMatch(screeningSource, /まず、いま知りたいことを選びます/);
  assert.match(screeningSource, /`\$\{samples\}点を評価`/);
  assert.match(screeningSource, /chartExpanded \? "図を元の大きさに戻す" : "図を拡大"/);
  assert.match(screeningSource, /setChartExpanded\(false\)/);
  assert.match(screeningSource, /screen-map\$\{chartExpanded \? " expanded" : ""\}/);
  assert.match(screeningStyles, /\.screen-map \{[\s\S]*?height: clamp\(280px, 36vw, 350px\);/);
  assert.match(screeningStyles, /\.screen-map\.expanded \{[\s\S]*?height: clamp\(440px, 56vw, 620px\);/);
});

test("screening separates map, proposals, and evaluated evidence surfaces", () => {
  assert.match(screeningSource, /<ScreeningResultSurfaceTabs/);
  assert.match(screeningSource, /displayedResultSurface === "map"/);
  assert.match(screeningSource, /displayedResultSurface === "proposals"/);
  assert.match(screeningSource, /displayedResultSurface === "evaluated"/);
  assert.match(screeningSource, /inverse_distance_weighted_display|interpolation\.method/);
  assert.match(screeningSource, /modelの追加予測ではありません/);
  assert.match(screeningSource, /固定断面ではありません/);
  assert.match(screeningStyles, /\.screening-result-tabs \{/);
  assert.match(screeningStyles, /\.screening-map-layout \{[\s\S]*?grid-template-columns:/);
});

test("lineage uses one normalized time contract for both heat points and stage intervals", () => {
  assert.match(lineageSource, /function normalizedTimePosition\(time: number, maxTime: number\)/);
  assert.match(lineageSource, /const heatX = \(time: number\) => HEAT_PLOT_LEFT \+ normalizedTimePosition\(time, maxTime\) \* HEAT_PLOT_WIDTH/);
  assert.match(lineageSource, /left: `\$\{normalizedTimePosition\(stage\.startTime, maxTime\) \* 100\}%`/);
  assert.match(lineageSource, /width: `\$\{\(normalizedTimePosition\(stage\.endTime, maxTime\) - normalizedTimePosition\(stage\.startTime, maxTime\)\) \* 100\}%`/);
  assert.match(lineageStyles, /\.lineage-process-segment \{[\s\S]*?position: absolute;/);
});

test("lineage avoids an empty detail column and summarizes a single observation once", () => {
  assert.match(lineageSource, /lineage-workspace\$\{data \? "" : " no-detail"\}/);
  assert.match(lineageStyles, /\.lineage-workspace\.no-detail \{\s*grid-template-columns: 220px minmax\(0, 1fr\);/);
  assert.match(lineageSource, /group\.count === 1/);
  assert.match(lineageSource, /colSpan=\{4\} className="lineage-single-observation"/);
  assert.match(lineageSource, /単一の実測値/);
});

test("lineage presents cleaned condition labels while retaining exact source headers as secondary detail", () => {
  assert.match(lineageSource, /function primaryConditionPresentation\(sourceColumn: string\)/);
  assert.match(lineageSource, /<span>\{presentation\.label\}<\/span>/);
  assert.match(lineageSource, /<code title="元データの列名">\{presentation\.sourceColumn\}<\/code>/);
});
