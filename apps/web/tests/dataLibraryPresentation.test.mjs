import test from "node:test";
import assert from "node:assert/strict";
import {
  compatiblePackagesForDatasetTask,
  compatibleTaskIdsForDataset,
  modelPackageDisplayName,
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
