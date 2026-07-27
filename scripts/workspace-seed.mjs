import { spawnSync } from "node:child_process";
import { resolveDevWorkspace } from "./dev-workspace.mjs";

const workspace = resolveDevWorkspace();
if (workspace.source !== "branch-default") {
  process.stderr.write(
    "workspace:seedはbranch既定のreview Workspaceだけを初期化できます。"
    + "WORKBENCH_DB_PATH/WORKBENCH_DATA_LIBRARY_PATHを解除してください。\n",
  );
  process.exit(1);
}
if (process.argv.includes("--check")) {
  process.stdout.write(JSON.stringify({
    allowed: true,
    workspaceDatabase: workspace.database,
    workspaceDataLibrary: workspace.dataLibrary,
    workspaceSource: workspace.source,
  }));
  process.exit(0);
}
const result = spawnSync(
  "uv",
  [
    "run",
    "python",
    "backend/scripts/seed_review_workspace.py",
    "--database",
    workspace.database,
    "--data-library",
    workspace.dataLibrary,
  ],
  {
    cwd: workspace.repositoryRoot,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
    },
    stdio: "inherit",
  },
);
if (result.error) throw result.error;
process.exit(result.status ?? 1);
