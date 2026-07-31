import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

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

function resolveFromRoot(value) {
  return path.resolve(repositoryRoot, value);
}

function personalDataLibrary() {
  const localAppData = process.env.LOCALAPPDATA?.trim();
  if (localAppData) return path.join(localAppData, "Material Decision Workbench", "data-library");
  const xdgDataHome = process.env.XDG_DATA_HOME?.trim();
  return path.join(xdgDataHome || path.join(os.homedir(), ".local", "share"), "material-decision-workbench", "data-library");
}

function personalDevWorkspaceRoot(workspaceName) {
  const localAppData = process.env.LOCALAPPDATA?.trim();
  const base = localAppData
    ? path.join(localAppData, "Material Decision Workbench")
    : path.join(
      process.env.XDG_DATA_HOME?.trim() || path.join(os.homedir(), ".local", "share"),
      "material-decision-workbench",
    );
  return path.join(base, "dev-workspaces", workspaceName);
}

export function resolveDevWorkspace({ mainWorkspace = false } = {}) {
  const explicitDatabase = process.env.WORKBENCH_DB_PATH?.trim();
  const workspaceName = safeWorkspaceName(branchName());
  const defaultDatabase = mainWorkspace
    ? path.join(repositoryRoot, "data", "workbench.db")
    : path.join(repositoryRoot, ".dev-workspaces", `${workspaceName}.db`);
  const database = !mainWorkspace && explicitDatabase
    ? resolveFromRoot(explicitDatabase)
    : defaultDatabase;
  const explicitLibrary = process.env.WORKBENCH_DATA_LIBRARY_PATH?.trim();
  const dataLibrary = !mainWorkspace && explicitLibrary
    ? resolveFromRoot(explicitLibrary)
    : mainWorkspace
      ? personalDataLibrary()
      : path.join(repositoryRoot, ".dev-workspaces", `${workspaceName}-data-library`);
  const source = mainWorkspace
    ? "main"
    : explicitDatabase || explicitLibrary
      ? "environment"
      : "branch-default";
  const personalRoot = source === "branch-default"
    ? personalDevWorkspaceRoot(workspaceName)
    : undefined;
  return {
    repositoryRoot,
    workspaceName,
    database,
    dataLibrary,
    personalTaskStore: personalRoot ? path.join(personalRoot, "tasks") : undefined,
    personalModelStore: personalRoot ? path.join(personalRoot, "models") : undefined,
    source,
  };
}
