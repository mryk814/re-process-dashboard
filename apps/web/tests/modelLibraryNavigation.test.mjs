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

test("Data Library can open Model Library with the selected Dataset context", async () => {
  const navigation = await navigationModule("?view=data-library");
  const intent = {
    view: "model-library",
    modelLibraryTab: "packages",
    modelLibraryData: {
      datasetRevisionId: "dataset-r3",
    },
  };

  const roundTrip = navigation.readNavigationIntent(
    new URL(navigation.navigationUrl(intent), "http://localhost").search,
  );
  assert.equal(roundTrip.modelLibraryTab, "packages");
  assert.equal(roundTrip.modelLibraryData?.datasetRevisionId, "dataset-r3");
  assert.equal(roundTrip.modelLibraryData?.packageReferenceId, undefined);
});

test("a changed package handoff clears completed focus so Back can focus again", async () => {
  const { clearFocusedPackageIntentOnChange } = await import("../src/shared/modelLibrary.ts");
  const focusA = "dataset-a:package-a";
  const unresolvedFocusB = "dataset-b:package-b";

  let focusedIdentity = focusA;
  focusedIdentity = clearFocusedPackageIntentOnChange(focusedIdentity, unresolvedFocusB);
  assert.equal(focusedIdentity, undefined);

  focusedIdentity = clearFocusedPackageIntentOnChange(focusedIdentity, focusA);
  assert.equal(focusedIdentity, undefined);

  focusedIdentity = focusA;
  assert.equal(
    clearFocusedPackageIntentOnChange(focusedIdentity, focusA),
    focusA,
  );
});

test("describes quantile Package semantics without implying normal uncertainty", async () => {
  const { packagePredictiveMeaning } = await import("../src/shared/modelLibrary.ts");
  assert.equal(
    packagePredictiveMeaning([{
      runtime_type: "builtin.quantile_linear.v1",
      predictive_family: "empirical_quantiles",
    }]),
    "中央値とq05／q95を直接学習 · q05–q95は正規分布の90%区間ではありません · 分位点交差は補正せず利用不能",
  );
  assert.equal(
    packagePredictiveMeaning([{
      runtime_type: "builtin.linear.v1",
      predictive_family: "empirical_quantiles",
    }]),
    null,
  );
});

test("a new Graph draft keeps the published Decision Output evidence boundary", async () => {
  const { draftDefinitionFromCatalog } = await import("../src/shared/modelLibrary.ts");
  const definition = {
    schema_version: "prediction-graph-definition/v1",
    graph_id: "graph-v1",
    label: "根拠を持つGraph",
    stages: [],
    inputs: [],
    bindings: [],
    decision_outputs: [{
      output_id: "strength",
      source_stage_id: "stage-a",
      source_output_key: "strength_mpa",
      label: "強さ",
      group: "mechanical",
      role: "primary_objective",
      required_for_complete_result: true,
      evidence: {
        evidence_kind: "synthetic_demonstration",
        unit_or_scale: "MPa",
        goal_direction: "at_least",
        source_variables: ["candidate.composition"],
        causal_claim: "none",
        production_use: "prohibited",
        limitation: "教育用の合成根拠です",
      },
    }],
  };

  assert.deepEqual(
    draftDefinitionFromCatalog(definition).decision_outputs[0].evidence,
    definition.decision_outputs[0].evidence,
  );
});
