import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const projectHub = readFileSync(
  new URL("../src/features/projects/ProjectHub.tsx", import.meta.url),
  "utf8",
);
const session = readFileSync(
  new URL("../src/features/workbench/useWorkbenchSession.ts", import.meta.url),
  "utf8",
);

test("unavailable tasks keep the overview while replacing mutation and inference surfaces", () => {
  assert.match(app, /TaskUnavailablePanel/);
  assert.match(app, /保存済みの候補・予測・実測・判断履歴/);
  assert.match(app, /item\.id === "project"/);
  assert.match(app, /chainProject && item\.id === "candidates"/);
  assert.match(session, /resolved\.availability\.status === "unavailable"/);
  // The reason is rendered by the panels that stay available, not pushed through a notice.
  assert.match(app, /taskAvailability\?\.message/);
  assert.match(projectHub, /taskAvailability\.message/);
});

test("chain projects load their immutable revision without entering the single-task candidate runtime", () => {
  assert.match(session, /project\?\.scientific_identity\?\.identity_kind === "chain"/);
  assert.match(session, /setTaskDefinition\(null\)/);
  assert.match(session, /setResolvedTaskDefinition\(null\)/);
  assert.match(session, /editor\.acceptServerCandidates\(\[\]\)/);
  assert.doesNotMatch(session, /Chain Revisionを固定しました/);
  assert.match(app, /const chainProject = activeProject\?\.scientific_identity\?\.identity_kind === "chain"/);
  assert.match(app, /chainProject && item\.id === "candidates"/);
  assert.match(app, /tab === "candidates" && chainProject/);
});

test("chain projects are labelled by template, revision, and stages instead of unresolved single-task data", () => {
  assert.match(projectHub, /revision\.template\.definition\.label/);
  assert.match(projectHub, /revision\.revision\.stages\.map\(\(stage\) => stage\.stage_id\)\.join\(" → "\)/);
  assert.match(projectHub, /Chain Revisionを固定したプロジェクトです/);
});

test("chain projects explain single-task-only views instead of failing inside them", () => {
  assert.match(app, /const chainScopedTab = chainProject/);
  assert.match(app, /ChainModeUnavailablePanel/);
  assert.match(app, /tab === "explore" && !taskUnavailable && !chainProject/);
  assert.match(app, /tab === "lineage" && !taskUnavailable && !chainProject/);
  assert.match(app, /tab === "quality" && !taskUnavailable && !chainProject/);
  assert.match(app, /tab === "settings" && !taskUnavailable && !chainProject/);
});

test("chain overview offers the chain work surface instead of single-task next actions", () => {
  assert.match(projectHub, /chainIdentity\s*\n?\s*\? <div className="project-action-grid">/);
  assert.match(projectHub, /Chain候補を開く/);
  assert.match(projectHub, /このモードでは範囲探索とデータ探索を利用できません/);
});

test("a project without a single fixed task explains why no starter candidate is possible", () => {
  assert.match(session, /このプロジェクトは単一の予測タスクを固定していないため/);
  assert.match(session, /この予測タスクには基準候補の定義がありません/);
  assert.doesNotMatch(session, /\} catch \{\s*\n\s*setNotice\("基準候補を作成できませんでした/);
});

test("project history shows a Japanese reason and disables changes for unavailable tasks", () => {
  assert.match(projectHub, /この予測タスクは一時的に利用できません/);
  assert.match(projectHub, /推論と変更操作は停止しています/);
  // Unavailable tasks and an unreachable API both make changes inert.
  assert.match(projectHub, /disabled=\{taskUnavailable \|\| offline\}/);
  assert.match(projectHub, /if \(!taskUnavailable\)/);
});

test("the fixed reference strip reads in Japanese and keeps digests in one collapsed block", () => {
  const strip = projectHub.slice(
    projectHub.indexOf('className="project-reference-strip"'),
    projectHub.indexOf('className="chain-evaluation-panel loading"'),
  );
  for (const label of ["参照データセット", "予測タスク", "予測モデル", "探索範囲（Design Space）", "判断基準（Objective）", "検討グループ", "参照Chain", "固定した版"]) {
    assert.ok(strip.includes(`<span>${label}</span>`), `${label} is a Japanese strip heading`);
  }
  assert.doesNotMatch(strip, /<span>(参照Dataset|Prediction Task|Model Package|Design Space|Objective|Chain Template|Chain Revision)<\/span>/);
  assert.doesNotMatch(strip, /legacy/);
  assert.doesNotMatch(strip, /digest\.slice|\.slice\(0, 1[0-9]\)/);
  assert.match(projectHub, /<ReferenceIdentityDetails items=/);
});
