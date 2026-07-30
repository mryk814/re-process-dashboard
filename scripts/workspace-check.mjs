import { spawnSync } from "node:child_process";
import { resolveDevWorkspace } from "./dev-workspace.mjs";

const workspace = resolveDevWorkspace({
  mainWorkspace: process.argv.includes("--main-workspace"),
});
const result = spawnSync(
  "uv",
  [
    "run",
    "python",
    "backend/scripts/operations/workspace_check.py",
    "--database",
    workspace.database,
    ...process.argv.slice(2).filter((value) => value !== "--main-workspace"),
  ],
  {
    cwd: workspace.repositoryRoot,
    env: {
      ...process.env,
      WORKBENCH_DB_PATH: workspace.database,
      WORKBENCH_DATA_LIBRARY_PATH: workspace.dataLibrary,
      PYTHONUTF8: "1",
    },
    stdio: "inherit",
  },
);
if (result.error) throw result.error;
process.exit(result.status ?? 1);
