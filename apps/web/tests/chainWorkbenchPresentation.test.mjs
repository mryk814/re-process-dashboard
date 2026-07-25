import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(
  new URL("../src/features/workbench/ChainWorkbenchPage.tsx", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../src/features/workbench/chain-workbench.css", import.meta.url),
  "utf8",
);
const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("Chain project has a dedicated candidate work surface", () => {
  assert.match(app, /tab === "candidates" && chainProject/);
  assert.match(app, /<ChainWorkbenchPage/);
  assert.match(source, /CHAIN WORKBENCH/);
  assert.match(source, /\(\["A", "B", "C"\] as const\)/);
});

test("freshness and actual source are separate labels", () => {
  for (const label of ["最新", "再計算中", "古い", "失敗"]) {
    assert.match(source, new RegExp(label));
  }
  assert.match(source, /実測照合あり/);
  assert.match(source, /別analysisあり/);
  assert.match(source, /実測を使用した別分析/);
  assert.match(source, /通常Chainを上書きしません/);
});

test("editing keeps reserved layout surfaces while recomputation changes state", () => {
  assert.match(source, /編集停止後に自動保存・再計算します/);
  assert.match(source, /window\.setTimeout/);
  assert.match(styles, /\.chain-status-line\s*\{[^}]*min-height:/s);
  assert.match(styles, /\.chain-result-card\s*\{[^}]*min-height:/s);
  assert.match(styles, /\.chain-stage-rail\s*\{[^}]*position:\s*sticky/s);
});

test("actual-conditioned analysis requires an immutable comparison snapshot", () => {
  assert.match(source, /comparison_snapshot_id:\s*snapshots\[0\]\.snapshot_id/);
  assert.match(source, /実測Bを使ってStage Cを別分析/);
  assert.match(source, /不足分を予測値で補いません/);
  assert.match(source, /stageBKeys\.map\(\(key\) => \[key, ""\]\)/);
  assert.doesNotMatch(source, /String\(stageBPredictions\[key\]/);
  assert.match(source, /!actualDraft\[key\]\?\.trim\(\)/);
  assert.match(source, /<details className="chain-actual-panel">/);
  assert.match(source, /<details className="chain-variant-history">/);
});

test("blank numeric drafts never become zero and Stage A reuses the sparse blend editor", () => {
  assert.match(source, /if \(!rawValue\.trim\(\)\)/);
  assert.match(source, /空欄は0として保存しません/);
  assert.match(source, /LatestSaveQueue<ApiCandidate>/);
  assert.match(source, /rebaseChangedFields/);
  assert.match(source, /saveQueue\.current\.supersede\(selected\.id\)/);
  assert.match(source, /<BlendEditorPanel/);
  assert.match(source, /chainMode/);
  assert.match(source, /contract\.starter_candidate/);
  assert.match(source, /固定契約から基準配合を作成/);
});
