import test from "node:test";
import assert from "node:assert/strict";

async function navigationModule(search) {
  globalThis.window = { location: { search, pathname: "/", hash: "" } };
  return import(`../src/app/navigation.ts?model-library=${encodeURIComponent(search)}`);
}

test("Model Library asset tab survives a shareable URL round trip", async () => {
  const navigation = await navigationModule(
    "?view=model-library&asset=graphs",
  );
  const intent = navigation.readNavigationIntent();

  assert.equal(intent.view, "model-library");
  assert.equal(intent.modelLibraryTab, "graphs");
  assert.equal(
    navigation.navigationUrl(intent),
    "/?view=model-library&asset=graphs",
  );
});

test("unknown Model Library asset tab normalizes to Task assets", async () => {
  const navigation = await navigationModule(
    "?view=model-library&asset=unknown",
  );
  const intent = navigation.readNavigationIntent();

  assert.equal(intent.modelLibraryTab, "tasks");
  assert.equal(navigation.navigationUrl(intent), "/?view=model-library");
  assert.equal(
    navigation.navigationLocationNeedsNormalization(intent),
    true,
  );
});

test("Model Library selection is discarded when leaving the library", async () => {
  const navigation = await navigationModule(
    "?view=model-library&asset=packages",
  );
  const project = navigation.withView(
    navigation.readNavigationIntent(),
    "project",
  );

  assert.equal(project.modelLibraryTab, undefined);
});

test("Model Library single-task Project handoff preserves every fixed identity", async () => {
  const navigation = await navigationModule("?view=model-library&asset=packages");
  const intent = {
    view: "project",
    modelLibraryProject: {
      kind: "single_task",
      datasetViewRevisionId: "view-r7",
      datasetRevisionId: "dataset-r3",
      taskId: "task-v1",
      packageReferenceId: "package-ref",
      packageManifestDigest: "sha256:manifest",
    },
  };
  const url = navigation.navigationUrl(intent);
  const roundTrip = navigation.readNavigationIntent(new URL(url, "http://localhost").search);

  assert.deepEqual(roundTrip.modelLibraryProject, intent.modelLibraryProject);
});

test("saved Graph draft and immutable Project handoffs round trip independently", async () => {
  const navigation = await navigationModule("?view=model-library&asset=graphs");
  const draft = {
    view: "chain-studio",
    draftId: "graph-draft-abc",
  };
  const project = {
    view: "project",
    modelLibraryProject: {
      kind: "graph",
      graphId: "graph-v1",
      definitionId: "definition-abc",
      revisionId: "graph-v1:r4",
      revisionDigest: "sha256:revision",
      datasetViewRevisionId: "view-r7",
    },
  };

  assert.deepEqual(
    navigation.readNavigationIntent(new URL(navigation.navigationUrl(draft), "http://localhost").search).draftId,
    draft.draftId,
  );
  assert.deepEqual(
    navigation.readNavigationIntent(new URL(navigation.navigationUrl(project), "http://localhost").search).modelLibraryProject,
    project.modelLibraryProject,
  );
});

test("Data Library deep link keeps dataset and package focus", async () => {
  const navigation = await navigationModule("?view=model-library&asset=packages");
  const intent = {
    view: "data-library",
    modelLibraryData: {
      datasetRevisionId: "dataset-r3",
      packageReferenceId: "package-ref",
    },
  };

  assert.deepEqual(
    navigation.readNavigationIntent(new URL(navigation.navigationUrl(intent), "http://localhost").search).modelLibraryData,
    intent.modelLibraryData,
  );
});
