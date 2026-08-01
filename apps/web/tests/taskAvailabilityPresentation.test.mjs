import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { projectScientificSettingsReadOnly } from "../src/features/projects/projectSettingsState.ts";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const projectHub = readFileSync(
  new URL("../src/features/projects/ProjectHub.tsx", import.meta.url),
  "utf8",
);
const session = readFileSync(
  new URL("../src/features/workbench/useWorkbenchSession.ts", import.meta.url),
  "utf8",
);
const developerAdmin = readFileSync(
  new URL("../src/features/admin/DeveloperAdminPage.tsx", import.meta.url),
  "utf8",
);

test("unavailable tasks keep overview and read-only diagnostics while replacing inference surfaces", () => {
  assert.match(app, /TaskUnavailablePanel/);
  assert.match(app, /保存済みの候補・予測・実測・判断履歴/);
  assert.match(app, /item\.id === "project"/);
  assert.match(app, /chainProject && \(item\.id === "project" \|\| item\.id === "candidates" \|\| item\.id === "chain-graph" \|\| item\.id === "project-settings"\)/);
  assert.match(session, /resolved\.availability\.status === "unavailable"/);
  assert.match(app, /tab !== "workspace"/);
  assert.match(app, /tab === "workspace"/);
  assert.equal(projectScientificSettingsReadOnly(true, false), true);
  assert.equal(projectScientificSettingsReadOnly(false, true), true);
  assert.equal(projectScientificSettingsReadOnly(false, false), false);
  // The reason is rendered by the panels that stay available, not pushed through a notice.
  assert.match(app, /taskAvailability\?\.message/);
  assert.match(projectHub, /taskAvailability\.message/);
});

test("the unavailable panel uses Japanese and opens reference diagnostics", () => {
  assert.match(app, /予測タスクの利用状況/);
  assert.doesNotMatch(app, /TASK UNAVAILABLE/);
  assert.match(app, /参照状態を確認する/);
  assert.match(app, /onOpenSettings/);
  assert.match(app, /projectSettings: "task"/);
});

test("chain projects load their immutable revision without entering the single-task candidate runtime", () => {
  assert.match(session, /project\?\.scientific_identity\?\.identity_kind === "chain"/);
  assert.match(session, /setTaskDefinition\(null\)/);
  assert.match(session, /setResolvedTaskDefinition\(null\)/);
  assert.match(session, /editor\.acceptServerCandidates\(\[\]\)/);
  assert.doesNotMatch(session, /Chain Revisionを固定しました/);
  assert.match(app, /const chainProject = activeProject\?\.scientific_identity\?\.identity_kind === "chain"/);
  assert.match(app, /chainProject && \(item\.id === "project" \|\| item\.id === "candidates" \|\| item\.id === "chain-graph" \|\| item\.id === "project-settings"\)/);
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
  assert.match(app, /tab === "candidate-review" \|\| tab === "explore"/);
  assert.match(app, /&& !chainProject && !taskUnavailable/);
  assert.match(app, /tab === "lineage" && !taskUnavailable && !chainProject/);
  assert.match(app, /tab === "quality" && !taskUnavailable && !chainProject/);
  assert.doesNotMatch(app, /tab === "settings"/);
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
  // The guard may carry more conditions; it must still gate on availability.
  assert.match(projectHub, /if \(!taskUnavailable\b/);
});

test("developer settings lead with diagnostics and guard every project mutation in read-only mode", () => {
  assert.match(developerAdmin, /admin-availability-diagnostic/);
  assert.match(developerAdmin, /availabilityStageLabel\(availability\?\.stage\)/);
  for (const reference of [
    "project?.task_id",
    "project?.dataset_view_revision_id",
    "project?.model_package_ref_id",
    "project?.model_package_manifest_digest",
    "availability?.resource_id",
    "availability?.expected_locator",
    "availability?.recovery_hint",
  ]) {
    assert.ok(developerAdmin.includes(reference), `${reference} is visible in the diagnostic`);
  }
  assert.match(developerAdmin, /section !== "model" \|\| readOnly/);
  assert.match(developerAdmin, /if \(readOnly\) return;/);
  assert.match(developerAdmin, /disabled=\{readOnly \|\| saving\}/);
  assert.match(developerAdmin, /disabled=\{readOnly\}/);
  assert.match(developerAdmin, /readOnly=\{readOnly\}/);
});

test("unresolved fixed references retain the raw project identifiers", () => {
  assert.match(projectHub, /unresolvedReferenceLabel\("Dataset View", project\.dataset_view_revision_id\)/);
  assert.match(projectHub, /unresolvedReferenceLabel\("Model Package", project\.model_package_ref_id\)/);
  assert.match(projectHub, /\["Dataset View Revision", project\.dataset_view_revision_id\]/);
  assert.match(projectHub, /\["Model Package Ref", project\.model_package_ref_id\]/);
  assert.match(projectHub, /manifest: \$\{project\.model_package_manifest_digest\}/);
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

test("the project overview presents next work before goals and keeps fixed references in settings", () => {
  const goalPosition = projectHub.indexOf("project-goal-strip");
  const nextWorkPosition = projectHub.indexOf('className="project-next-actions"');
  const fixedReferencesPosition = projectHub.indexOf('className="project-reference-details"');

  assert.ok(goalPosition >= 0, "the goal strip is present");
  assert.ok(nextWorkPosition >= 0 && nextWorkPosition < goalPosition, "next work precedes the goal");
  assert.ok(fixedReferencesPosition > nextWorkPosition, "fixed references follow the user actions");
  assert.match(
    projectHub,
    /surface === "settings" && effectiveSettingsCategory === "evidence" && project && <details className="project-reference-details" open>/,
  );
  assert.match(projectHub, /surface === "overview" && unresolvedReferences\.length > 0/);
});
