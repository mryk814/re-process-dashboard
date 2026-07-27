import assert from "node:assert/strict";
import test from "node:test";
import {
  isCurrentProjectSettingsRequest,
  projectSettingsControlsDisabled,
  projectSettingsSaveDisabled,
  shouldShowProjectSettings,
} from "../src/features/projects/projectSettingsState.ts";

test("Project settings are rendered only for an open panel with a Project", () => {
  assert.equal(shouldShowProjectSettings({ open: true, hasProject: true }), true);
  assert.equal(shouldShowProjectSettings({ open: false, hasProject: true }), false);
  assert.equal(shouldShowProjectSettings({ open: true, hasProject: false }), false);
});

test("Project settings disable mutations while unavailable or loading", () => {
  assert.equal(projectSettingsControlsDisabled({
    loading: false,
    disabled: false,
  }), false);
  assert.equal(projectSettingsControlsDisabled({
    loading: true,
    disabled: false,
  }), true);
  assert.equal(projectSettingsControlsDisabled({
    loading: false,
    disabled: true,
  }), true);
});

test("Project settings reject an empty name and invalid target ranges", () => {
  const available = {
    loading: false,
    disabled: false,
    projectName: "材料条件の再検討",
    invalidTargetRange: false,
  };
  assert.equal(projectSettingsSaveDisabled(available), false);
  assert.equal(projectSettingsSaveDisabled({
    ...available,
    projectName: "  ",
  }), true);
  assert.equal(projectSettingsSaveDisabled({
    ...available,
    invalidTargetRange: true,
  }), true);
  assert.equal(projectSettingsSaveDisabled({
    ...available,
    loading: true,
  }), true);
});

test("a delayed settings mutation cannot write into the next Project", () => {
  assert.equal(isCurrentProjectSettingsRequest("project-a", "project-a"), true);
  assert.equal(isCurrentProjectSettingsRequest("project-a", "project-b"), false);
});
