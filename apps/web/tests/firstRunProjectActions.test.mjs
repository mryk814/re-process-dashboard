import assert from "node:assert/strict";
import test from "node:test";

import {
  candidateQuestionActions,
  candidateQuestionState,
} from "../src/shared/projectActionQuestions.ts";

async function navigationModule(search) {
  globalThis.window = { location: { search, pathname: "/", hash: "" } };
  return import(`../src/app/navigation.ts?first-run=${encodeURIComponent(search)}`);
}

test("an empty or invalid location starts from the project overview", async () => {
  assert.equal((await navigationModule("")).readNavigationIntent().view, "project");
  assert.equal(
    (await navigationModule("?view=unknown")).readNavigationIntent().view,
    "project",
  );
});

test("candidate questions explain why they are disabled before a candidate exists", () => {
  assert.deepEqual(candidateQuestionState(undefined, false), {
    disabled: true,
    reason: "先に候補が必要です",
  });
  assert.deepEqual(candidateQuestionState("candidate-1", false), {
    disabled: false,
  });
});

test("candidate review exposes the three user questions", () => {
  assert.deepEqual(
    candidateQuestionActions.map((item) => item.title),
    ["入力ばらつきに強いか", "2案の差は何が効いているか", "目標へ届くには何を変えるか"],
  );
});

test("the actual measurement destination survives a candidate deep link", async () => {
  const { navigationUrl, readNavigationIntent } = await navigationModule(
    "?view=candidates&project=p1&candidate=c1&candidate_section=actuals",
  );
  const intent = readNavigationIntent();
  assert.equal(intent.candidateSection, "actuals");
  assert.match(navigationUrl(intent), /candidate_section=actuals/);
});

test("prepared Project binding identity survives reload and history navigation", async () => {
  const { navigationUrl, readNavigationIntent } = await navigationModule("?view=project");
  const preparedProjectBinding = {
    datasetViewId: "view:r1",
    datasetRevisionId: "dataset:r3",
    taskId: "task-v1",
    taskLabel: "新しいTask",
    modelPackageRefId: "package-ref:1",
    sourceSha256: "a".repeat(64),
    sourceFilename: "private.csv",
    estimatorId: "ridge.v1",
    estimatorLabel: "Ridge回帰",
    preparationResult: "reused",
    workspaceKind: "branch",
    workspaceDatabasePath: "C:\\workspace\\branch.db",
    reloaded: true,
  };
  const url = navigationUrl({
    view: "project",
    projectId: "project-1",
    preparedProjectBinding,
  });
  const restored = (await navigationModule(url.slice(1))).readNavigationIntent();
  assert.deepEqual(restored.preparedProjectBinding, preparedProjectBinding);
  assert.equal(restored.projectId, "project-1");
});
