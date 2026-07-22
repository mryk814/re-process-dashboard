import test from "node:test";
import assert from "node:assert/strict";
import {
  compatiblePackagesForTask,
  compatibleTaskIdsForDataset,
  initialProjectBindingForDataset,
} from "../src/shared/dataLibraryPresentation.ts";

const dataset = {
  supported_task_ids: ["annealed", "hot-rolled"],
};

const options = {
  task_contract_digests: { annealed: "digest-a", "hot-rolled": "digest-h" },
  model_packages: [
    { id: "a-current", task_id: "annealed", task_contract_digest: "digest-a" },
    { id: "a-stale", task_id: "annealed", task_contract_digest: "old-digest" },
    { id: "h-current", task_id: "hot-rolled", task_contract_digest: "digest-h" },
  ],
};

test("filters stale Model Packages using the active Prediction Task contract", () => {
  assert.deepEqual(
    compatiblePackagesForTask("annealed", options).map((item) => item.id),
    ["a-current"],
  );
});

test("does not silently choose between multiple compatible Prediction Tasks", () => {
  assert.deepEqual(compatibleTaskIdsForDataset(dataset, options), ["annealed", "hot-rolled"]);
  assert.deepEqual(initialProjectBindingForDataset(dataset, options), {
    taskId: "",
    modelPackageRefId: "",
  });
});

test("preselects the only compatible task and package", () => {
  const singleTaskDataset = { supported_task_ids: ["annealed"] };
  assert.deepEqual(initialProjectBindingForDataset(singleTaskDataset, options), {
    taskId: "annealed",
    modelPackageRefId: "a-current",
  });
});
