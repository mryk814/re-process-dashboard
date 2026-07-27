import test from "node:test";
import assert from "node:assert/strict";
import {
  compatiblePackagesForDatasetTask,
  compatibleTaskIdsForDataset,
  modelPackageDisplayName,
  projectDatasetChoices,
} from "../src/shared/dataLibraryPresentation.ts";

const dataset = {
  supported_task_ids: ["annealed", "hot-rolled"],
  data_asset: { sha256: "dataset-a" },
  profile_revision: { profile_digest: "profile-a" },
};

const provenance = {
  provenance: {
    training_data_id: "sha256:dataset-a",
    dataset_profile_id: "profile-a",
  },
};
const options = {
  task_contract_digests: { annealed: "digest-a", "hot-rolled": "digest-h" },
  model_packages: [
    { id: "a-current", task_id: "annealed", task_contract_digest: "digest-a", manifest_json: provenance },
    { id: "a-other-data", task_id: "annealed", task_contract_digest: "digest-a", manifest_json: { provenance: { training_data_id: "sha256:dataset-b", dataset_profile_id: "profile-a" } } },
    { id: "a-other-profile", task_id: "annealed", task_contract_digest: "digest-a", manifest_json: { provenance: { training_data_id: "sha256:dataset-a", dataset_profile_id: "profile-b" } } },
    { id: "a-stale", task_id: "annealed", task_contract_digest: "old-digest", manifest_json: provenance },
    { id: "h-current", task_id: "hot-rolled", task_contract_digest: "digest-h", manifest_json: provenance },
  ],
};

test("filters Model Packages by Dataset, Profile, and active Prediction Task contract", () => {
  assert.deepEqual(
    compatiblePackagesForDatasetTask(dataset, "annealed", options).map((item) => item.id),
    ["a-current"],
  );
});

test("shows only Prediction Tasks with a Package trained on the selected Dataset and Profile", () => {
  assert.deepEqual(compatibleTaskIdsForDataset(dataset, options), ["annealed", "hot-rolled"]);
});

test("uses model-family names instead of Package ids", () => {
  assert.equal(modelPackageDisplayName({
    package_id: "stable-ard-package",
    manifest_json: {
      predictors: [{
        runtime_type: "builtin.exact_gp.v1",
        architecture_id: "exact_rbf_grouped_v1",
        config: { kernel: "ARD-RBF" },
      }],
    },
  }), "GP（安定ARD）");
  assert.equal(modelPackageDisplayName({
    package_id: "opaque-versioned-package-id",
    manifest_json: {
      predictors: [{ runtime_type: "builtin.exact_gp.v1", architecture_id: "exact_rbf_grouped_v1" }],
    },
  }), "GP");
  assert.equal(modelPackageDisplayName({
    package_id: "opaque-versioned-package-id",
    manifest_json: {
      predictors: [{ runtime_type: "sklearn.skops.v1", architecture_id: "lightgbm_regressor_v1" }],
    },
  }), "LightGBM");
  assert.equal(modelPackageDisplayName({
    package_id: "opaque-versioned-package-id",
    manifest_json: {
      predictors: [{ runtime_type: "builtin.linear.v1", architecture_id: "linear_v1" }],
    },
  }), "線形回帰");
});

const datasetChoiceFixture = (viewId, filename, tasks = ["annealed"]) => ({
  data_asset: { original_filename: filename },
  dataset_views: [{ id: viewId }],
  supported_task_ids: tasks,
});

test("groups Project Dataset choices by active use and sorts by use count then registration time", () => {
  const choices = projectDatasetChoices({
    datasets: [
      datasetChoiceFixture("view-used-old", "used-old.xlsx"),
      datasetChoiceFixture("view-used-new", "used-new.xlsx"),
      datasetChoiceFixture("view-unused", "unused.xlsx"),
    ],
    views: [
      { id: "view-unused", kind: "single", name: "unused", created_at: "2026-07-27T03:00:00Z" },
      { id: "view-used-old", kind: "single", name: "used-old", created_at: "2026-07-25T03:00:00Z" },
      { id: "view-used-new", kind: "single", name: "used-new", created_at: "2026-07-26T03:00:00Z" },
      { id: "comparison", kind: "cohort_comparison", name: "comparison", created_at: "2026-07-28T03:00:00Z" },
    ],
    projects: [
      { id: "project-b", name: "検討B", dataset_view_revision_id: "view-used-old", archived_at: null },
      { id: "project-a", name: "検討A", dataset_view_revision_id: "view-used-old", archived_at: null },
      { id: "project-new", name: "新しい検討", dataset_view_revision_id: "view-used-new", archived_at: null },
      { id: "project-archived", name: "停止済み", dataset_view_revision_id: "view-unused", archived_at: "2026-07-27T04:00:00Z" },
    ],
    taskLabels: new Map([["annealed", "焼鈍後特性"]]),
  });

  assert.deepEqual(choices.map((choice) => choice.id), [
    "view-used-old",
    "view-used-new",
    "view-unused",
  ]);
  assert.deepEqual(choices[0].projectNames, ["検討A", "検討B"]);
  assert.equal(choices[0].group, "used");
  assert.equal(choices[2].group, "unused");
  assert.match(choices[0].label, /^焼鈍後特性 — used-old（利用中2件）$/);
});

test("distinguishes duplicate filenames without exposing Profile ids", () => {
  const choices = projectDatasetChoices({
    datasets: [
      datasetChoiceFixture("dataset-view-revision-111111", "same.xlsx"),
      datasetChoiceFixture("dataset-view-revision-222222", "same.xlsx"),
    ],
    views: [
      { id: "dataset-view-revision-111111", kind: "single", name: "same-a", created_at: "2026-07-26T03:00:00Z" },
      { id: "dataset-view-revision-222222", kind: "single", name: "same-b", created_at: "2026-07-27T03:00:00Z" },
    ],
    projects: [],
    taskLabels: new Map([["annealed", "焼鈍後特性"]]),
  });

  assert.equal(new Set(choices.map((choice) => choice.label)).size, 2);
  assert.match(choices[0].label, /焼鈍後特性 — same（未使用・登録 .+・…222222）/);
  assert.match(choices[1].label, /焼鈍後特性 — same（未使用・登録 .+・…111111）/);
  assert.doesNotMatch(choices.map((choice) => choice.label).join(" "), /profile/i);
});

test("uses the Chain purpose for Dataset views without a single-Task purpose", () => {
  const choices = projectDatasetChoices({
    datasets: [datasetChoiceFixture("battery-view", "battery.csv", [])],
    views: [{ id: "battery-view", kind: "single", name: "battery_raw", created_at: "2026-07-27T03:00:00Z" }],
    projects: [{ id: "chain-project", name: "電池の検討", dataset_view_revision_id: null, archived_at: null }],
    taskLabels: new Map(),
    chainLabelsByViewId: new Map([["battery-view", ["電池寿命評価（Chain）"]]]),
    datasetViewIdsByProjectId: new Map([["chain-project", ["battery-view", "battery-view"]]]),
  });

  assert.match(choices[0].label, /^電池寿命評価（Chain） — battery（利用中1件）$/);
  assert.deepEqual(choices[0].projectNames, ["電池の検討"]);
});

test("extends a duplicate suffix until Dataset View choices are unique", () => {
  const choices = projectDatasetChoices({
    datasets: [
      datasetChoiceFixture("dataset-view-alpha123456", "same.xlsx"),
      datasetChoiceFixture("dataset-view-bravo123456", "same.xlsx"),
    ],
    views: [
      { id: "dataset-view-alpha123456", kind: "single", name: "same-a", created_at: "2026-07-26T03:00:00Z" },
      { id: "dataset-view-bravo123456", kind: "single", name: "same-b", created_at: "2026-07-27T03:00:00Z" },
    ],
    projects: [],
    taskLabels: new Map([["annealed", "焼鈍後特性"]]),
  });

  assert.equal(new Set(choices.map((choice) => choice.label)).size, 2);
  assert.match(choices[0].label, /…vo123456）$/);
  assert.match(choices[1].label, /…ha123456）$/);
});
