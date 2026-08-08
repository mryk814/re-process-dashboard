import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { materializeDevWorkspace } from "./dev-workspace.mjs";


function fixture(parent, name) {
  const workspaceRoot = path.join(parent, name);
  const manifest = {
    schema_version: "dev-workspace-manifest/v1",
    workspace_id: name,
    workspace_kind: "branch-default",
    checkout_identity: "fixture",
    checkout_root: parent,
    branch_identity: "fixture",
    resources: {
      database: "workspace.db",
      data_library: "data-library",
      profiles: "profiles",
      tasks: "tasks",
      models: "models",
    },
  };
  return {
    source: "branch-default",
    workspaceRoot,
    workspaceManifestPath: path.join(workspaceRoot, "workspace-manifest.json"),
    dataLibrary: path.join(workspaceRoot, "data-library"),
    personalProfileStore: path.join(workspaceRoot, "profiles"),
    personalTaskStore: path.join(workspaceRoot, "tasks"),
    personalModelStore: path.join(workspaceRoot, "models"),
    manifest,
  };
}


test("materializer creates a new marker but never rewrites an existing identity", () => {
  const parent = mkdtempSync(path.join(os.tmpdir(), "dev-workspace-marker-"));
  try {
    const workspace = fixture(parent, "workspace");
    materializeDevWorkspace(workspace);
    const original = readFileSync(workspace.workspaceManifestPath, "utf8");
    materializeDevWorkspace(workspace);
    assert.equal(readFileSync(workspace.workspaceManifestPath, "utf8"), original);

    writeFileSync(
      workspace.workspaceManifestPath,
      `${JSON.stringify({ schema_version: "older-marker/v1" })}\n`,
      "utf8",
    );
    assert.throws(
      () => materializeDevWorkspace(workspace),
      /markerが現在のidentityと一致しません/,
    );
    assert.equal(
      readFileSync(workspace.workspaceManifestPath, "utf8"),
      `${JSON.stringify({ schema_version: "older-marker/v1" })}\n`,
    );
  } finally {
    rmSync(parent, { recursive: true, force: true });
  }
});


test("materializer rejects a workspace root junction before reading its marker", () => {
  const parent = mkdtempSync(path.join(os.tmpdir(), "dev-workspace-junction-"));
  try {
    const target = path.join(parent, "target");
    mkdirSync(target);
    const workspace = fixture(parent, "workspace");
    symlinkSync(target, workspace.workspaceRoot, "junction");
    assert.throws(
      () => materializeDevWorkspace(workspace),
      /symlink\/reparse point/,
    );
  } finally {
    rmSync(parent, { recursive: true, force: true });
  }
});
