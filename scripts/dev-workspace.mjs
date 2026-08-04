import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  mkdirSync,
  existsSync,
  lstatSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const WORKSPACE_MANIFEST_SCHEMA_VERSION = "dev-workspace-manifest/v1";

function branchName() {
  try {
    const branch = execFileSync("git", ["branch", "--show-current"], {
      cwd: repositoryRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    if (branch) return branch;
    const revision = execFileSync("git", ["rev-parse", "--short", "HEAD"], {
      cwd: repositoryRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    return `detached-${revision}`;
  } catch {
    return "unknown-checkout";
  }
}

function safeWorkspaceName(value) {
  const normalized = value
    .normalize("NFKC")
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  const digest = createHash("sha256").update(value).digest("hex").slice(0, 8);
  return `${normalized || "unknown-checkout"}-${digest}`;
}

function digest(value) {
  return createHash("sha256").update(value).digest("hex").slice(0, 8);
}

function canonicalCheckoutIdentity(root) {
  const canonical = realpathSync.native(root);
  const normalized = process.platform === "win32"
    ? canonical.replaceAll("\\", "/").toLowerCase()
    : canonical;
  return {
    canonical,
    digest: digest(normalized),
  };
}

function resolveFromRoot(value) {
  return path.resolve(repositoryRoot, value);
}

function personalDataLibrary() {
  const localAppData = process.env.LOCALAPPDATA?.trim();
  if (localAppData) return path.join(localAppData, "Material Decision Workbench", "data-library");
  const xdgDataHome = process.env.XDG_DATA_HOME?.trim();
  return path.join(xdgDataHome || path.join(os.homedir(), ".local", "share"), "material-decision-workbench", "data-library");
}

function branchWorkspace() {
  const branch = branchName();
  const checkout = canonicalCheckoutIdentity(repositoryRoot);
  const workspaceName = `${safeWorkspaceName(branch)}-${checkout.digest}`;
  const root = path.join(repositoryRoot, ".dev-workspaces", workspaceName);
  return {
    branch,
    checkout,
    workspaceName,
    root,
  };
}

function manifestPayload(workspace) {
  return {
    schema_version: WORKSPACE_MANIFEST_SCHEMA_VERSION,
    workspace_id: workspace.workspaceName,
    workspace_kind: "branch-default",
    checkout_identity: workspace.checkout.digest,
    checkout_root: workspace.checkout.canonical,
    branch_identity: workspace.branch,
    resources: {
      database: "workspace.db",
      data_library: "data-library",
      profiles: "profiles",
      tasks: "tasks",
      models: "models",
    },
    lifecycle: {
      persistence: "checkout-local-disposable",
      cleanup: "explicit-workspace-prune-only",
      backup_root: ".",
      automatic_migration: false,
    },
  };
}

export function materializeDevWorkspace(workspace) {
  if (workspace.source !== "branch-default" || !workspace.workspaceRoot) return;
  const expected = `${JSON.stringify(workspace.manifest, null, 2)}\n`;
  if (existsSync(workspace.workspaceRoot)) {
    if (lstatSync(workspace.workspaceRoot).isSymbolicLink()) {
      throw new Error(
        `開発Workspace rootにsymlink/reparse pointは使用できません: ${workspace.workspaceRoot}`,
      );
    }
    let current;
    try {
      current = readFileSync(workspace.workspaceManifestPath, "utf8");
    } catch (error) {
      throw new Error(
        `既存の開発Workspaceにlauncher markerがありません: ${workspace.workspaceRoot}`,
        { cause: error },
      );
    }
    if (current !== expected) {
      throw new Error(
        `既存の開発Workspace markerが現在のidentityと一致しません: ${workspace.workspaceManifestPath}`,
      );
    }
  } else {
    mkdirSync(workspace.workspaceRoot, { recursive: true });
    writeFileSync(workspace.workspaceManifestPath, expected, {
      encoding: "utf8",
      flag: "wx",
    });
  }
  for (const resource of [
    workspace.dataLibrary,
    workspace.personalProfileStore,
    workspace.personalTaskStore,
    workspace.personalModelStore,
  ]) {
    mkdirSync(resource, { recursive: true });
  }
}

export function resolveDevWorkspace({ mainWorkspace = false } = {}) {
  const explicitDatabase = process.env.WORKBENCH_DB_PATH?.trim();
  const branchDefault = branchWorkspace();
  const workspaceName = branchDefault.workspaceName;
  const defaultDatabase = mainWorkspace
    ? path.join(repositoryRoot, "data", "workbench.db")
    : path.join(branchDefault.root, "workspace.db");
  const database = !mainWorkspace && explicitDatabase
    ? resolveFromRoot(explicitDatabase)
    : defaultDatabase;
  const explicitLibrary = process.env.WORKBENCH_DATA_LIBRARY_PATH?.trim();
  const dataLibrary = !mainWorkspace && explicitLibrary
    ? resolveFromRoot(explicitLibrary)
    : mainWorkspace
      ? personalDataLibrary()
      : path.join(branchDefault.root, "data-library");
  const source = mainWorkspace
    ? "main"
    : explicitDatabase || explicitLibrary
      ? "environment"
      : "branch-default";
  const personalRoot = source === "branch-default" ? branchDefault.root : undefined;
  const manifest = source === "branch-default"
    ? manifestPayload(branchDefault)
    : undefined;
  return {
    repositoryRoot,
    workspaceName,
    workspaceRoot: personalRoot,
    workspaceManifestPath: personalRoot
      ? path.join(personalRoot, "workspace-manifest.json")
      : undefined,
    checkoutIdentity: source === "branch-default"
      ? branchDefault.checkout.digest
      : undefined,
    branchIdentity: source === "branch-default" ? branchDefault.branch : undefined,
    database,
    dataLibrary,
    personalProfileStore: personalRoot ? path.join(personalRoot, "profiles") : undefined,
    personalTaskStore: personalRoot ? path.join(personalRoot, "tasks") : undefined,
    personalModelStore: personalRoot ? path.join(personalRoot, "models") : undefined,
    manifest,
    source,
  };
}
