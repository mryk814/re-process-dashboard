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
  assert.match(app, /!taskUnavailable \|\| item\.id === "project"/);
  assert.match(session, /resolved\.availability\.status === "unavailable"/);
  assert.match(session, /resolved\.availability\.message/);
});

test("project history shows a Japanese reason and disables changes for unavailable tasks", () => {
  assert.match(projectHub, /この予測タスクは一時的に利用できません/);
  assert.match(projectHub, /推論と変更操作は停止しています/);
  assert.match(projectHub, /disabled=\{taskUnavailable\}/);
  assert.match(projectHub, /if \(!taskUnavailable\)/);
});
