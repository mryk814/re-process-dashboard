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
  assert.match(session, /resolved\.availability\.message/);
});

test("chain projects load their immutable revision without entering the single-task candidate runtime", () => {
  assert.match(session, /project\?\.scientific_identity\?\.identity_kind === "chain"/);
  assert.match(session, /setTaskDefinition\(null\)/);
  assert.match(session, /setResolvedTaskDefinition\(null\)/);
  assert.match(session, /editor\.acceptServerCandidates\(\[\]\)/);
  assert.match(session, /setNotice\("Chain Revisionを固定しました/);
  assert.match(app, /const chainProject = activeProject\?\.scientific_identity\?\.identity_kind === "chain"/);
  assert.match(app, /chainProject && item\.id === "candidates"/);
  assert.match(app, /tab === "candidates" && chainProject/);
});

test("chain projects are labelled by template, revision, and stages instead of unresolved single-task data", () => {
  assert.match(projectHub, /revision\.template\.definition\.label/);
  assert.match(projectHub, /revision\.revision\.stages\.map\(\(stage\) => stage\.stage_id\)\.join\(" → "\)/);
  assert.match(projectHub, /Chain Revisionを固定したプロジェクトです/);
});

test("project history shows a Japanese reason and disables changes for unavailable tasks", () => {
  assert.match(projectHub, /この予測タスクは一時的に利用できません/);
  assert.match(projectHub, /推論と変更操作は停止しています/);
  assert.match(projectHub, /disabled=\{taskUnavailable\}/);
  assert.match(projectHub, /if \(!taskUnavailable\)/);
});
