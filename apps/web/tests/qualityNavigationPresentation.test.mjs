import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("quality summary hands off to the issue list and distinguishes zero states", async () => {
  const source = await readFile(new URL("../src/features/quality/QualityPages.tsx", import.meta.url), "utf8");

  assert.match(source, /問題一覧で確認/);
  assert.match(source, /現在の検出ルールでは問題は見つかりませんでした/);
  assert.match(source, /絞り込みに一致する問題はありません/);
  assert.match(source, /すべての問題を表示/);
  assert.match(source, /未実行ではありません/);
});

test("candidate summary routes unresolved decisions to their settings", async () => {
  const source = await readFile(new URL("../src/features/candidates/CandidateUi.tsx", import.meta.url), "utf8");
  const app = await readFile(new URL("../src/app/App.tsx", import.meta.url), "utf8");

  assert.match(source, /onConfigureGoals/);
  assert.match(source, /onConfigureSupport/);
  assert.match(source, /goalDirectionLabel/);
  assert.match(app, /projectSettings: "targets"/);
  assert.match(app, /adminSection: "ranges"/);
});
