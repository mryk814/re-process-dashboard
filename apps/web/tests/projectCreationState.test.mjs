import assert from "node:assert/strict";
import test from "node:test";
import { projectCreationSubmitDisabled } from "../src/features/projects/projectCreationState.ts";

const completeTaskProject = {
  loading: false,
  disabled: false,
  projectName: "焼鈍条件の再検討",
  datasetViewId: "dataset-view-1",
  mode: "empty",
  copyTaskId: undefined,
  taskId: "hardness",
  modelPackageRefId: "package-1",
  chainId: "",
  chainRevisionId: "",
  groupChoice: "none",
  projectSeriesId: "",
  projectSeriesName: "",
};

test("Project creation enables a complete Task binding", () => {
  assert.equal(projectCreationSubmitDisabled(completeTaskProject), false);
});

test("Project creation requires the selected binding and group name", () => {
  assert.equal(projectCreationSubmitDisabled({ ...completeTaskProject, taskId: "" }), true);
  assert.equal(projectCreationSubmitDisabled({ ...completeTaskProject, groupChoice: "existing" }), true);
  assert.equal(projectCreationSubmitDisabled({ ...completeTaskProject, groupChoice: "new", projectSeriesName: "  " }), true);
});

test("Project creation accepts a complete Chain binding without a Task package", () => {
  assert.equal(projectCreationSubmitDisabled({
    ...completeTaskProject,
    taskId: "",
    modelPackageRefId: "",
    chainId: "chain-1",
    chainRevisionId: "chain-1:r2",
  }), false);
  assert.equal(projectCreationSubmitDisabled({
    ...completeTaskProject,
    chainId: "chain-1",
  }), true);
  assert.equal(projectCreationSubmitDisabled({
    ...completeTaskProject,
    taskId: "",
    modelPackageRefId: "",
    chainId: "chain-1",
    chainRevisionId: "chain-1:r2",
    groupChoice: "existing",
  }), true);
  assert.equal(projectCreationSubmitDisabled({
    ...completeTaskProject,
    taskId: "",
    modelPackageRefId: "",
    chainId: "chain-1",
    chainRevisionId: "chain-1:r2",
    groupChoice: "new",
    projectSeriesName: " ",
  }), true);
});

test("Project creation cannot submit while its surface is unavailable", () => {
  assert.equal(projectCreationSubmitDisabled({
    ...completeTaskProject,
    disabled: true,
  }), true);
});
