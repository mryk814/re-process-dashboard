import { spawnSync } from "node:child_process";
import { resolveDevWorkspace } from "./dev-workspace.mjs";

const workspace = resolveDevWorkspace();
if (workspace.source !== "branch-default") {
  process.stderr.write(
    "samplesはbranch既定のreview Workspaceだけを変更できます。"
    + "WORKBENCH_DB_PATH/WORKBENCH_DATA_LIBRARY_PATHを解除してください。\n",
  );
  process.exit(1);
}

const result = spawnSync(
  "uv",
  [
    "run",
    "python",
    "backend/scripts/sample_gallery.py",
    ...process.argv.slice(2),
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
